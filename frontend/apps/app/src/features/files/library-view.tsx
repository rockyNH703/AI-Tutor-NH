"use client";

import { Button } from "@v1/ui/button";
import { ConfirmDialog } from "@v1/ui/confirm-dialog";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { cn } from "@v1/ui/utils";
import { Loader2, MessageSquarePlus, RotateCcw, Trash2, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { useAttachments, useShowStorageFull, useStores } from "@/features/chat/chat-context";
import { describeError } from "@/lib/api/errors";
import { FILE_ACCEPT, deleteFile, describeFileFailure, formatBytes, getFile, listFiles, retryFile } from "@/lib/api/files";
import type { FileList, LibraryFile } from "@/lib/api/types";
import { AttachmentChip, FileKindIcon } from "./attachment-chip";

/** Uploads started here share the attachment store's queue (one at a time) under this key. */
const LIBRARY_KEY = "library";
const POLL_MS = 3000;

const STATUS: Record<LibraryFile["status"], string> = {
  processing: "Reading…",
  ready: "Ready",
  failed: "Failed",
  stored: "Stored — can't be read right now",
};

/**
 * The student's File Library: storage used, upload, and every file with its state. Nothing is
 * ever deleted for them; "Ask in chat" starts a new chat with the file attached.
 */
export function LibraryView() {
  const router = useRouter();
  const { chat, attachments } = useStores();
  const showStorageFull = useShowStorageFull();
  const uploads = useAttachments(LIBRARY_KEY);
  const [list, setList] = useState<FileList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<LibraryFile | null>(null);
  const [pending, setPending] = useState(false);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      setList(await listFiles());
      setLoadError(null);
    } catch (error) {
      setLoadError(describeError(error).message);
    }
  }, []);

  useEffect(() => {
    void load();
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  // An upload from here is done (stored and queued, or already in the library): show the list's row instead.
  useEffect(() => {
    const done = uploads.filter((chip) => chip.fileId);
    if (!done.length) return;
    for (const chip of done) attachments.remove(LIBRARY_KEY, chip.localId);
    void load();
  }, [uploads, attachments, load]);

  // Files being read are checked until they are ready or failed (only those, not the whole list).
  const reading = list?.items.filter((file) => file.status === "processing").map((file) => file.id) ?? [];
  const readingKey = reading.join(",");
  useEffect(() => {
    if (!readingKey) return;
    const timer = setInterval(async () => {
      const ids = readingKey.split(",");
      const fresh = await Promise.all(ids.map((id) => getFile(id).catch(() => null)));
      setList((current) =>
        current
          ? {
              ...current,
              items: current.items.map((file) => fresh.find((f) => f?.id === file.id) ?? file),
            }
          : current,
      );
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [readingKey]);

  const add = (files: globalThis.FileList | null) => {
    if (!files?.length) return;
    if (list && list.usage.remainingBytes <= 0) {
      showStorageFull();
      return;
    }
    const { rejected } = attachments.add(LIBRARY_KEY, Array.from(files));
    const first = rejected[0];
    setNotice(
      first
        ? `${first.name} ${
            first.reason === "type"
              ? "isn't a supported file type"
              : first.reason === "size"
                ? "is too large"
                : "wasn't added: add up to 5 files at a time"
          }.${rejected.length > 1 ? ` ${rejected.length - 1} more not added.` : ""}`
        : null,
    );
  };

  const askInChat = (file: LibraryFile) => {
    const key = chat.freshDraftKey(); // the new chat "/" opens with
    attachments.attachExisting(key, file);
    router.push("/");
  };

  const retry = async (file: LibraryFile) => {
    try {
      const updated = await retryFile(file.id);
      setList((current) =>
        current ? { ...current, items: current.items.map((f) => (f.id === updated.id ? updated : f)) } : current,
      );
    } catch (error) {
      toast.error(describeError(error).title);
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    setPending(true);
    try {
      await deleteFile(deleting.id);
      toast.success(`Deleted ${deleting.filename}`);
      setDeleting(null);
      await load();
    } catch (error) {
      toast.error(describeError(error).title);
    } finally {
      setPending(false);
    }
  };

  const usage = list?.usage;
  const share = usage ? Math.min(100, (usage.usedBytes / usage.quotaBytes) * 100) : 0;

  return (
    <div
      className={cn("page-container page-wide flex flex-col gap-4", dragging && "rounded-2xl ring-2 ring-brand/40")}
      onDragOver={(event) => {
        if (!Array.from(event.dataTransfer.types).includes("Files")) return;
        event.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setDragging(false);
      }}
      onDrop={(event) => {
        if (!event.dataTransfer.files.length) return;
        event.preventDefault();
        setDragging(false);
        add(event.dataTransfer.files);
      }}
    >
      {/* One toolbar: what you have, how much room is left, and the way to add more. */}
      <section aria-labelledby="storage-heading" className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex min-w-0 flex-1 flex-col gap-1.5 sm:max-w-xs">
          <div className="flex items-baseline justify-between gap-3">
            <h2 id="storage-heading" className="text-sm font-bold text-foreground">
              Storage
            </h2>
            <p className="text-xs tabular-nums text-text-secondary">
              {usage ? (
                <>
                  <span className="font-semibold text-foreground">{formatBytes(usage.usedBytes)}</span> of{" "}
                  {formatBytes(usage.quotaBytes)} · {formatBytes(usage.remainingBytes)} left
                </>
              ) : (
                "Loading…"
              )}
            </p>
          </div>
          <div
            className="h-1.5 overflow-hidden rounded-full bg-surface-2"
            role="meter"
            aria-label="Storage used"
            aria-valuemin={0}
            aria-valuemax={usage?.quotaBytes ?? 100}
            aria-valuenow={usage?.usedBytes ?? 0}
            aria-valuetext={usage ? `${formatBytes(usage.usedBytes)} of ${formatBytes(usage.quotaBytes)}` : undefined}
          >
            <div className={cn("h-full rounded-full", share >= 90 ? "bg-destructive" : "bg-brand")} style={{ width: `${share}%` }} />
          </div>
        </div>
        <div className="flex items-center gap-3 sm:ml-auto">
          <Button type="button" onClick={() => input.current?.click()}>
            <Upload aria-hidden className="mr-2 h-4 w-4" />
            Upload
          </Button>
          <input
            ref={input}
            type="file"
            multiple
            accept={FILE_ACCEPT}
            hidden
            onChange={(event) => {
              add(event.target.files);
              event.target.value = "";
            }}
          />
        </div>
      </section>

      {notice && (
        <p role="status" className="rounded-xl border border-border bg-surface-2 px-3 py-2 text-sm text-foreground">
          {notice}
        </p>
      )}
      {uploads.length > 0 && (
        <div className="flex flex-wrap gap-2" role="group" aria-label="Uploading">
          {uploads.map((chip) => (
            <AttachmentChip
              key={chip.localId}
              chip={chip}
              onRemove={() => attachments.remove(LIBRARY_KEY, chip.localId)}
              onRetry={() => void attachments.retry(LIBRARY_KEY, chip.localId)}
            />
          ))}
        </div>
      )}

      <section aria-labelledby="files-heading">
        <h2 id="files-heading" className="sr-only">
          Your files
        </h2>
        {loadError ? (
          <div role="alert" className="rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive">
            <p>{loadError}</p>
            <Button type="button" variant="outline" className="mt-3" onClick={() => void load()}>
              Try again
            </Button>
          </div>
        ) : !list ? (
          <SkeletonGroup label="Loading your files" className="space-y-2">
            {[0, 1, 2].map((row) => (
              <Skeleton key={row} className="h-16 w-full rounded-xl" />
            ))}
          </SkeletonGroup>
        ) : list.items.length === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-2xl border border-dashed border-border bg-card/60 px-6 py-10 text-center">
            <Upload aria-hidden className="h-6 w-6 text-brand" />
            <div>
              <p className="font-semibold text-foreground">No files yet</p>
              <p className="mt-1 text-sm text-text-secondary">Drop configs, logs, diagrams or documentation here, then ask about them.</p>
            </div>
          </div>
        ) : (
          <ul className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-card">
            {list.items.map((file) => (
              <li key={file.id} className="flex flex-wrap items-center gap-3 p-3 sm:flex-nowrap">
                <FileKindIcon kind={file.kind} className="h-5 w-5 shrink-0 text-text-secondary" />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-foreground" title={file.filename}>
                    {file.filename}
                  </p>
                  <p className="flex flex-wrap items-center gap-x-2 text-xs text-text-secondary">
                    <span>{formatBytes(file.sizeBytes)}</span>
                    <span aria-hidden>·</span>
                    <span
                      role="status"
                      className={cn(
                        "inline-flex items-center gap-1",
                        file.status === "ready" && "text-success",
                        (file.status === "failed" || file.status === "stored") && "text-destructive",
                      )}
                    >
                      {file.status === "processing" && <Loader2 aria-hidden className="h-3 w-3 motion-safe:animate-spin" />}
                      {STATUS[file.status]}
                    </span>
                  </p>
                  {file.status === "failed" && (
                    <p className="mt-0.5 text-xs text-text-secondary">{describeFileFailure(file.errorCode)}</p>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-1">
                  {file.status === "ready" && (
                    <Button type="button" variant="outline" size="sm" className="h-11 sm:h-9" onClick={() => askInChat(file)}>
                      <MessageSquarePlus aria-hidden className="mr-1.5 h-4 w-4" />
                      Ask in chat
                    </Button>
                  )}
                  {file.status === "failed" && file.retryable && (
                    <Button type="button" variant="outline" size="sm" className="h-11 sm:h-9" onClick={() => void retry(file)}>
                      <RotateCcw aria-hidden className="mr-1.5 h-4 w-4" />
                      Retry
                    </Button>
                  )}
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-11 w-11 p-0 sm:h-9 sm:w-9"
                    onClick={() => setDeleting(file)}
                    aria-label={`Delete ${file.filename}`}
                  >
                    <Trash2 aria-hidden className="h-4 w-4" />
                  </Button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <p className="text-xs text-text-secondary">
        PDF, Word, PowerPoint up to 25 MB · Excel, images up to 10 MB · CSV, text up to 2 MB. Drop files anywhere on this page.
      </p>

      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && !pending && setDeleting(null)}
        title={deleting ? `Delete ${deleting.filename}?` : "Delete file?"}
        description={
          deleting
            ? `This frees ${formatBytes(deleting.sizeBytes)}. NetSec AI won't use it any more, and chats that used it will show it as deleted.`
            : undefined
        }
        confirmLabel="Delete"
        destructive
        pending={pending}
        onConfirm={() => void confirmDelete()}
      />
    </div>
  );
}
