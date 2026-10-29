"use client";

import { Badge } from "@v1/ui/badge";
import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { type FormEvent, useCallback, useEffect, useId, useState } from "react";
import { toast } from "sonner";

import { ApiError, describeError } from "@/lib/api/errors";
import {
  MEMORY_KINDS,
  approveMemoryProposal,
  clearMemories,
  correctMemory,
  createMemory,
  deleteMemory,
  exportMemories,
  getMemoryPreferences,
  listMemories,
  listMemoryProposals,
  rejectMemoryProposal,
  updateMemoryPreferences,
} from "@/lib/api/memory";
import type {
  ApproveMemoryProposalRequest,
  MemoryItem,
  MemoryKind,
  MemoryPreferences,
  MemoryProposal,
} from "@/lib/api/types";
import { shortDate } from "@/lib/time";
import { Notice, SectionHeading, SettingsCard, ToggleRow } from "./primitives";

const KIND_LABELS: Record<MemoryKind, string> = {
  learning_context: "Goals & preferences",
  study_context: "Environment & projects",
};

const selectClass = "focus-field h-10 w-full rounded-xl border border-input bg-card px-3 text-base sm:text-sm";
const textClass = "focus-field min-h-24 w-full resize-y rounded-xl border border-input bg-card px-3 py-2 text-base sm:text-sm";
const inputClass = "focus-field h-10 w-full rounded-xl border border-input bg-card px-3 text-base sm:text-sm";

function dateInputValue(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function dateInputIso(value: string): string | null {
  return value ? new Date(`${value}T23:59:59.999Z`).toISOString() : null;
}

function formatDate(value: string | null): string {
  return value ? shortDate(value) : "No expiry";
}

function conflictMessage(error: unknown): string | null {
  return error instanceof ApiError && (error.code === "version_conflict" || error.status === 409)
    ? "This changed on another device. Reloaded the latest version; review it and try again."
    : null;
}

function LoadFailure({ title, error, retry }: { title: string; error: unknown; retry: () => void }) {
  return (
    <SettingsCard title={title}>
      <p role="alert" className="text-sm text-text-secondary">
        {describeError(error).message}
      </p>
      <Button type="button" size="sm" variant="outline" className="self-start" onClick={retry}>
        Try again
      </Button>
    </SettingsCard>
  );
}

function EmptyState({ children }: { children: string }) {
  return <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-sm text-text-secondary">{children}</p>;
}

function saveExport(data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `netsec-ai-memory-${new Date().toISOString().slice(0, 10)}.json`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function EditorDialog({
  title,
  description,
  initialKind,
  initialContent,
  initialExpiresAt,
  pending,
  error,
  onOpenChange,
  onSave,
}: {
  title: string;
  description: string;
  initialKind: MemoryKind;
  initialContent: string;
  initialExpiresAt: string | null;
  pending: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onSave: (values: { kind: MemoryKind; content: string; expiresAt: string | null }) => Promise<void>;
}) {
  const kindId = useId();
  const contentId = useId();
  const expiryId = useId();
  const [kind, setKind] = useState(initialKind);
  const [content, setContent] = useState(initialContent);
  const [expiresAt, setExpiresAt] = useState(dateInputValue(initialExpiresAt));

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!content.trim()) return;
    await onSave({ kind, content: content.trim(), expiresAt: dateInputIso(expiresAt) });
  };

  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <form onSubmit={submit} className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor={kindId} className="text-sm font-bold text-foreground">Category</label>
            <select id={kindId} className={selectClass} value={kind} onChange={(event) => setKind(event.target.value as MemoryKind)}>
              {MEMORY_KINDS.map((value) => <option key={value} value={value}>{KIND_LABELS[value]}</option>)}
            </select>
          </div>
          <div className="space-y-1.5">
            <label htmlFor={contentId} className="text-sm font-bold text-foreground">What to remember</label>
            <textarea id={contentId} className={textClass} required value={content} onChange={(event) => setContent(event.target.value)} />
          </div>
          <div className="space-y-1.5">
            <label htmlFor={expiryId} className="text-sm font-bold text-foreground">Expires (optional)</label>
            <input id={expiryId} className={inputClass} type="date" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} />
            <p className="text-xs text-text-secondary">After this date, NetSec AI will not recall it.</p>
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>Cancel</Button>
            <Button type="submit" disabled={pending || !content.trim()}>{pending ? "Saving…" : "Save"}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ConfirmDialog({
  title,
  description,
  action,
  pending,
  error,
  onOpenChange,
  onConfirm,
}: {
  title: string;
  description: string;
  action: string;
  pending: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => Promise<void>;
}) {
  return (
    <Dialog open onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => onOpenChange(false)} disabled={pending}>Cancel</Button>
          <Button type="button" variant="destructive" onClick={() => void onConfirm()} disabled={pending}>
            {pending ? "Working…" : action}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function MemoryProposalDetails({
  proposal,
  busy,
  onApprove,
  onEdit,
  onReject,
}: {
  proposal: MemoryProposal;
  busy: boolean;
  onApprove: () => void;
  onEdit: () => void;
  onReject: () => void;
}) {
  return (
    <li className="rounded-xl border border-border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="brand">{KIND_LABELS[proposal.kind]}</Badge>
        <Badge variant="neutral">{proposal.operation === "add" ? "New" : "Correction"}</Badge>
        <span className="text-xs text-text-secondary">{Math.round(proposal.confidence * 100)}% confidence</span>
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-foreground">{proposal.content}</p>
      <dl className="mt-3 grid gap-1 text-xs text-text-secondary sm:grid-cols-2">
        <div><dt className="inline font-bold text-foreground">Observed: </dt><dd className="inline">{shortDate(proposal.observedAt)}</dd></div>
        <div><dt className="inline font-bold text-foreground">Expires: </dt><dd className="inline">{formatDate(proposal.expiresAt)}</dd></div>
        <div className="sm:col-span-2"><dt className="inline font-bold text-foreground">Why proposed: </dt><dd className="inline">{proposal.evidenceReason}</dd></div>
      </dl>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={onApprove} disabled={busy}>Approve</Button>
        <Button type="button" size="sm" variant="outline" onClick={onEdit} disabled={busy}>Edit & approve</Button>
        <Button type="button" size="sm" variant="ghost" onClick={onReject} disabled={busy}>Reject</Button>
      </div>
    </li>
  );
}

export function MemoryItemDetails({ memory, busy, onEdit, onDelete }: { memory: MemoryItem; busy: boolean; onEdit: () => void; onDelete: () => void }) {
  const origin = memory.origin === "approved_extraction" ? "Approved suggestion" : memory.origin === "explicit_chat" ? "Saved from chat" : "Added here";
  return (
    <li className="rounded-xl border border-border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant="brand">{KIND_LABELS[memory.kind]}</Badge>
        <Badge variant="source">{origin}</Badge>
      </div>
      <p className="mt-3 whitespace-pre-wrap text-sm leading-relaxed text-foreground">{memory.content}</p>
      <dl className="mt-3 grid gap-1 text-xs text-text-secondary sm:grid-cols-2">
        <div><dt className="inline font-bold text-foreground">Confirmed: </dt><dd className="inline">{shortDate(memory.lastConfirmedAt)}</dd></div>
        <div><dt className="inline font-bold text-foreground">Expires: </dt><dd className="inline">{formatDate(memory.expiresAt)}</dd></div>
        {memory.validFrom && <div><dt className="inline font-bold text-foreground">Valid from: </dt><dd className="inline">{shortDate(memory.validFrom)}</dd></div>}
        {memory.validTo && <div><dt className="inline font-bold text-foreground">Valid until: </dt><dd className="inline">{shortDate(memory.validTo)}</dd></div>}
      </dl>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={onEdit} disabled={busy}>Correct</Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDelete} disabled={busy}>Delete</Button>
      </div>
    </li>
  );
}

export function MemorySection() {
  const [preferences, setPreferences] = useState<MemoryPreferences | null>(null);
  const [proposals, setProposals] = useState<MemoryProposal[] | null>(null);
  const [memories, setMemories] = useState<MemoryItem[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [editor, setEditor] = useState<{ type: "new" } | { type: "memory"; item: MemoryItem } | { type: "proposal"; item: MemoryProposal } | null>(null);
  const [confirm, setConfirm] = useState<{ type: "delete"; item: MemoryItem } | { type: "reject"; item: MemoryProposal } | { type: "clear" } | null>(null);
  const [dialogError, setDialogError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const [nextPreferences, nextProposals, nextMemories] = await Promise.all([
        getMemoryPreferences(),
        listMemoryProposals({ limit: 100 }),
        listMemories({ limit: 100 }),
      ]);
      setPreferences(nextPreferences);
      setProposals(nextProposals.items);
      setMemories(nextMemories.items);
      setNotice(nextProposals.nextCursor || nextMemories.nextCursor ? "Showing the 100 most recent items." : null);
    } catch (caught) {
      setError(caught);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const refreshConflict = async (caught: unknown) => {
    const message = conflictMessage(caught);
    if (!message) return false;
    await load();
    setNotice(message);
    setEditor(null);
    setConfirm(null);
    return true;
  };

  const changeAutomatic = async (automaticProposals: boolean) => {
    if (!preferences) return;
    setBusy("preferences");
    setNotice(null);
    try {
      setPreferences(await updateMemoryPreferences({ automaticProposals, expectedVersion: preferences.version }));
      toast.success(automaticProposals ? "Memory suggestions are on." : "Memory suggestions are off.");
    } catch (caught) {
      if (conflictMessage(caught)) {
        setNotice(conflictMessage(caught));
        await load();
      } else toast.error(describeError(caught).message);
    } finally {
      setBusy(null);
    }
  };

  const approve = async (proposal: MemoryProposal, edits: Omit<ApproveMemoryProposalRequest, "expectedVersion"> = {}) => {
    setBusy(proposal.id);
    setDialogError(null);
    try {
      const saved = await approveMemoryProposal(proposal.id, { expectedVersion: proposal.version, ...edits });
      setProposals((current) => current?.filter((item) => item.id !== proposal.id) ?? []);
      setMemories((current) => [saved, ...(current ?? [])]);
      setEditor(null);
      toast.success("Suggestion approved.");
    } catch (caught) {
      if (!(await refreshConflict(caught))) setDialogError(describeError(caught).message);
    } finally {
      setBusy(null);
    }
  };

  const saveEditor = async (values: { kind: MemoryKind; content: string; expiresAt: string | null }) => {
    if (!editor) return;
    setBusy(editor.type === "new" ? "new" : editor.item.id);
    setDialogError(null);
    try {
      if (editor.type === "new") {
        const saved = await createMemory(values);
        setMemories((current) => [saved, ...(current ?? [])]);
        toast.success("Memory added.");
      } else if (editor.type === "memory") {
        const saved = await correctMemory(editor.item.id, { expectedVersion: editor.item.version, ...values });
        setMemories((current) => [saved, ...(current?.filter((item) => item.id !== editor.item.id) ?? [])]);
        toast.success("Memory corrected.");
      } else {
        await approve(editor.item, values);
        return;
      }
      setEditor(null);
    } catch (caught) {
      if (!(await refreshConflict(caught))) setDialogError(describeError(caught).message);
    } finally {
      setBusy(null);
    }
  };

  const runConfirm = async () => {
    if (!confirm) return;
    const id = confirm.type === "clear" ? "clear" : confirm.item.id;
    setBusy(id);
    setDialogError(null);
    try {
      if (confirm.type === "delete") {
        await deleteMemory(confirm.item.id, confirm.item.version);
        setMemories((current) => current?.filter((item) => item.id !== confirm.item.id) ?? []);
        toast.success("Memory deleted.");
      } else if (confirm.type === "reject") {
        await rejectMemoryProposal(confirm.item.id, confirm.item.version);
        setProposals((current) => current?.filter((item) => item.id !== confirm.item.id) ?? []);
        toast.success("Suggestion rejected.");
      } else {
        await clearMemories();
        setMemories([]);
        setProposals([]);
        setPreferences((current) => current ? { ...current, automaticProposals: false, version: current.version + 1 } : current);
        toast.success("All memory cleared.");
      }
      setConfirm(null);
    } catch (caught) {
      if (!(await refreshConflict(caught))) setDialogError(describeError(caught).message);
    } finally {
      setBusy(null);
    }
  };

  const download = async () => {
    setBusy("export");
    try {
      saveExport(await exportMemories());
      toast.success("Memory export downloaded.");
    } catch (caught) {
      toast.error(describeError(caught).message);
    } finally {
      setBusy(null);
    }
  };

  const editorItem = editor && editor.type !== "new" ? editor.item : null;

  return (
    <>
      <SectionHeading title="Memory" description="Review and control the context NetSec AI may recall in future chats." />
      {error ? (
        <LoadFailure title="Couldn't load memory" error={error} retry={() => void load()} />
      ) : !preferences || !proposals || !memories ? (
        <SkeletonGroup label="Loading memory settings" className="space-y-3">
          <Skeleton className="h-32 w-full rounded-2xl" />
          <Skeleton className="h-64 w-full rounded-2xl" />
        </SkeletonGroup>
      ) : (
        <div className="flex flex-col gap-4">
          {notice && <Notice tone="warning">{notice}</Notice>}
          <SettingsCard title="Suggestions" description="NetSec AI never saves automatic suggestions until you approve them.">
            <ToggleRow
              label="Suggest memories from chats"
              description="When on, eligible completed chats may create review items here. Turning it off stops future automatic proposals."
              checked={preferences.automaticProposals}
              disabled={busy === "preferences"}
              onCheckedChange={(checked) => void changeAutomatic(checked)}
            />
          </SettingsCard>

          <SettingsCard title={`Review suggestions (${proposals.length})`} description="Approve as written, edit first, or reject. Rejected suggestions are not recalled.">
            {proposals.length === 0 ? <EmptyState>No suggestions need review.</EmptyState> : (
              <ul aria-label="Memory suggestions" className="space-y-3">
                {proposals.map((proposal) => (
                  <MemoryProposalDetails
                    key={proposal.id}
                    proposal={proposal}
                    busy={busy === proposal.id}
                    onApprove={() => void approve(proposal)}
                    onEdit={() => { setDialogError(null); setEditor({ type: "proposal", item: proposal }); }}
                    onReject={() => { setDialogError(null); setConfirm({ type: "reject", item: proposal }); }}
                  />
                ))}
              </ul>
            )}
          </SettingsCard>

          <SettingsCard
            title={`Active memory (${memories.length})`}
            description="Only active, unexpired items are used for recall. Corrections replace the earlier version."
            footer={
              <div className="flex flex-wrap justify-end gap-2">
                <Button type="button" size="sm" variant="outline" onClick={() => void download()} disabled={busy === "export" || memories.length === 0}>
                  {busy === "export" ? "Exporting…" : "Export JSON"}
                </Button>
                <Button type="button" size="sm" onClick={() => { setDialogError(null); setEditor({ type: "new" }); }}>Add memory</Button>
              </div>
            }
          >
            {memories.length === 0 ? <EmptyState>No active memories yet. Add one, or approve a suggestion.</EmptyState> : (
              <ul aria-label="Active memories" className="space-y-3">
                {memories.map((memory) => (
                  <MemoryItemDetails
                    key={memory.id}
                    memory={memory}
                    busy={busy === memory.id}
                    onEdit={() => { setDialogError(null); setEditor({ type: "memory", item: memory }); }}
                    onDelete={() => { setDialogError(null); setConfirm({ type: "delete", item: memory }); }}
                  />
                ))}
              </ul>
            )}
          </SettingsCard>

          <SettingsCard
            title="Clear memory"
            description="Permanently deletes active memory and pending suggestions, and turns automatic suggestions off. This cannot be undone."
            tone="danger"
            footer={<Button type="button" size="sm" variant="destructive" disabled={memories.length === 0 && proposals.length === 0 && !preferences.automaticProposals} onClick={() => { setDialogError(null); setConfirm({ type: "clear" }); }}>Clear all memory</Button>}
          />
        </div>
      )}

      {editor && (
        <EditorDialog
          title={editor.type === "new" ? "Add memory" : editor.type === "proposal" ? "Edit and approve" : "Correct memory"}
          description={editor.type === "proposal" ? "Review every detail before this becomes active memory." : "Store only context that will be useful in future chats."}
          initialKind={editorItem?.kind ?? "learning_context"}
          initialContent={editorItem?.content ?? ""}
          initialExpiresAt={editorItem?.expiresAt ?? null}
          pending={busy === (editor.type === "new" ? "new" : editor.item.id)}
          error={dialogError}
          onOpenChange={(open) => { if (!open && !busy) setEditor(null); }}
          onSave={saveEditor}
        />
      )}

      {confirm && (
        <ConfirmDialog
          title={confirm.type === "clear" ? "Clear all memory?" : confirm.type === "reject" ? "Reject this suggestion?" : "Delete this memory?"}
          description={confirm.type === "clear" ? "All active memory and pending suggestions will be permanently deleted. Automatic suggestions will be turned off." : confirm.type === "reject" ? "This suggestion will be removed and will not become active memory." : "This memory and its correction history will be permanently deleted."}
          action={confirm.type === "clear" ? "Clear all memory" : confirm.type === "reject" ? "Reject" : "Delete"}
          pending={busy === (confirm.type === "clear" ? "clear" : confirm.item.id)}
          error={dialogError}
          onOpenChange={(open) => { if (!open && !busy) setConfirm(null); }}
          onConfirm={runConfirm}
        />
      )}
    </>
  );
}
