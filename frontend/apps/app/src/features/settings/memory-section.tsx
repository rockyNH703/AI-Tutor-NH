"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { type FormEvent, useCallback, useEffect, useId, useRef, useState } from "react";
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
import { Disclosure, Notice, SectionHeading, SettingsCard, ToggleRow } from "./primitives";

const KIND_LABELS: Record<MemoryKind, string> = {
  learning_context: "Goals & preferences",
  study_context: "Environment & projects",
};

const selectClass = "focus-field h-10 w-full rounded-xl border border-input bg-card px-3 text-base sm:text-sm";
const textClass = "focus-field min-h-24 w-full resize-y rounded-xl border border-input bg-card px-3 py-2 text-base sm:text-sm";
const inputClass = "focus-field h-10 w-full rounded-xl border border-input bg-card px-3 text-base sm:text-sm";
const itemText = "whitespace-pre-wrap break-words text-sm leading-relaxed text-foreground";
/** 44px on phones, as the Button's default size does; 36px from sm up. */
const TAP = "h-11 sm:h-9";
const SNIPPET_LENGTH = 48;

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

/** A row's text, short enough to name its buttons ("Delete: Revising subnetting…"). */
function snippet(content: string): string {
  const text = content.replace(/\s+/g, " ").trim();
  return text.length > SNIPPET_LENGTH ? `${text.slice(0, SNIPPET_LENGTH - 1).trimEnd()}…` : text;
}

function conflictMessage(error: unknown): string | null {
  return error instanceof ApiError && (error.code === "version_conflict" || error.status === 409)
    ? "This changed on another device. Reloaded the latest version; review it and try again."
    : null;
}

/** The id of the item after `id` (else the one before): the row that takes a removed row's place. */
function neighbour(items: readonly { id: string }[], id: string): string | undefined {
  const index = items.findIndex((item) => item.id === id);
  return (items[index + 1] ?? items[index - 1])?.id;
}

function rowButton(id: string | undefined): HTMLElement | null {
  return id ? document.querySelector<HTMLElement>(`[data-row="${CSS.escape(id)}"] button`) : null;
}

function LoadFailure({ title, error, retry }: { title: string; error: unknown; retry: () => void }) {
  return (
    <SettingsCard title={title}>
      <p role="alert" className="text-sm text-text-secondary">
        {describeError(error).message}
      </p>
      <Button type="button" size="sm" variant="outline" className={`${TAP} self-start`} onClick={retry}>
        Try again
      </Button>
    </SettingsCard>
  );
}

/** One line of text's height inside a card description, so the card does not grow when the text arrives. */
function LoadingLine() {
  return (
    <span aria-hidden className="block h-5">
      <span className="block h-4 w-32 max-w-full translate-y-0.5 animate-soft-pulse rounded-md bg-surface-2" />
    </span>
  );
}

type Fact = { term: string; value: string; showTerm?: boolean };

/** An item's facts on one line, a dot between them. A term the value already explains is for screen readers only. */
function Facts({ facts }: { facts: (Fact | null)[] }) {
  return (
    <dl className="flex flex-wrap gap-x-1.5 gap-y-0.5 text-xs text-text-secondary">
      {facts.map((fact) =>
        fact ? (
          <div key={fact.term} className="before:mr-1.5 before:content-['·'] first:before:content-none">
            <dt className={fact.showTerm ? "inline" : "sr-only"}>{`${fact.term}: `}</dt>
            <dd className="inline">{fact.value}</dd>
          </div>
        ) : null,
      )}
    </dl>
  );
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
            <input
              id={expiryId}
              className={inputClass}
              type="date"
              value={expiresAt}
              aria-describedby={`${expiryId}-hint`}
              onChange={(event) => setExpiresAt(event.target.value)}
            />
            <p id={`${expiryId}-hint`} className="text-xs text-text-secondary">After this date, NetSec AI will not recall it.</p>
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

/** A suggestion to review: its text, its facts, why it was proposed, and the three answers. */
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
  const name = snippet(proposal.content);
  return (
    <li data-row={proposal.id} className="flex flex-col gap-1.5 py-3">
      <p className={itemText}>{proposal.content}</p>
      <Facts
        facts={[
          { term: "Category", value: KIND_LABELS[proposal.kind] },
          { term: "Type", value: proposal.operation === "add" ? "New" : "Correction" },
          { term: "Confidence", value: `${Math.round(proposal.confidence * 100)}% confidence` },
          { term: "Observed", value: shortDate(proposal.observedAt), showTerm: true },
          proposal.expiresAt ? { term: "Expires", value: shortDate(proposal.expiresAt), showTerm: true } : null,
        ]}
      />
      <p className="text-xs leading-relaxed text-text-secondary">
        <span className="font-bold text-foreground">Why proposed:</span> {proposal.evidenceReason}
      </p>
      <div className="mt-1.5 flex flex-wrap gap-2">
        <Button type="button" size="sm" className={TAP} onClick={onApprove} disabled={busy} aria-label={`Approve: ${name}`}>
          Approve
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className={TAP}
          onClick={onEdit}
          disabled={busy}
          aria-label={`Edit & approve: ${name}`}
        >
          Edit & approve
        </Button>
        <Button type="button" size="sm" variant="ghost" className={TAP} onClick={onReject} disabled={busy} aria-label={`Reject: ${name}`}>
          Reject
        </Button>
      </div>
    </li>
  );
}

/** An active memory as one row: its text and facts, with Correct and Delete beside it from sm up. */
export function MemoryItemDetails({ memory, busy, onEdit, onDelete }: { memory: MemoryItem; busy: boolean; onEdit: () => void; onDelete: () => void }) {
  const origin = memory.origin === "approved_extraction" ? "Approved suggestion" : memory.origin === "explicit_chat" ? "Saved from chat" : "Added here";
  const name = snippet(memory.content);
  return (
    <li data-row={memory.id} className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-start sm:gap-4">
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className={itemText}>{memory.content}</p>
        <Facts
          facts={[
            { term: "Category", value: KIND_LABELS[memory.kind] },
            { term: "Source", value: origin },
            { term: "Confirmed", value: shortDate(memory.lastConfirmedAt), showTerm: true },
            memory.expiresAt ? { term: "Expires", value: shortDate(memory.expiresAt), showTerm: true } : null,
            memory.validFrom ? { term: "Valid from", value: shortDate(memory.validFrom), showTerm: true } : null,
            memory.validTo ? { term: "Valid until", value: shortDate(memory.validTo), showTerm: true } : null,
          ]}
        />
      </div>
      <div className="flex shrink-0 gap-2">
        <Button type="button" size="sm" variant="outline" className={TAP} onClick={onEdit} disabled={busy} aria-label={`Correct: ${name}`}>
          Correct
        </Button>
        <Button type="button" size="sm" variant="ghost" className={TAP} onClick={onDelete} disabled={busy} aria-label={`Delete: ${name}`}>
          Delete
        </Button>
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
  const addButton = useRef<HTMLButtonElement>(null);
  const suggestionsCard = useRef<HTMLDivElement>(null);
  const nextFocus = useRef<(() => HTMLElement | null | undefined) | null>(null);

  // A removed row takes its focused button with it: once the lists have re-rendered, focus goes to the next action
  // (the neighbouring row, else the list's own control) instead of falling to the page.
  useEffect(() => {
    if (!memories || !proposals) return;
    nextFocus.current?.()?.focus();
    nextFocus.current = null;
  }, [memories, proposals]);

  const focusAfterMemory = (id: string) => {
    const next = neighbour(memories ?? [], id);
    nextFocus.current = () => rowButton(next) ?? addButton.current;
  };
  const focusAfterProposal = (id: string) => {
    const next = neighbour(proposals ?? [], id);
    nextFocus.current = () => rowButton(next) ?? suggestionsCard.current?.querySelector<HTMLElement>('[role="switch"]');
  };

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
      focusAfterProposal(proposal.id);
      setProposals((current) => current?.filter((item) => item.id !== proposal.id) ?? []);
      setMemories((current) => [saved, ...(current ?? [])]);
      setEditor(null);
      toast.success("Suggestion approved.");
    } catch (caught) {
      if (await refreshConflict(caught)) return;
      // From the row's Approve there is no dialog to show the error in.
      if (editor) setDialogError(describeError(caught).message);
      else toast.error(describeError(caught).message);
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
        // The first row moves the card's buttons under the list (a new element): keep focus on "Add memory".
        nextFocus.current = () => addButton.current;
        setMemories((current) => [saved, ...(current ?? [])]);
        toast.success("Memory added.");
      } else if (editor.type === "memory") {
        const saved = await correctMemory(editor.item.id, { expectedVersion: editor.item.version, ...values });
        nextFocus.current = () => rowButton(saved.id);
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
        focusAfterMemory(confirm.item.id);
        setMemories((current) => current?.filter((item) => item.id !== confirm.item.id) ?? []);
        toast.success("Memory deleted.");
      } else if (confirm.type === "reject") {
        await rejectMemoryProposal(confirm.item.id, confirm.item.version);
        focusAfterProposal(confirm.item.id);
        setProposals((current) => current?.filter((item) => item.id !== confirm.item.id) ?? []);
        toast.success("Suggestion rejected.");
      } else {
        await clearMemories();
        nextFocus.current = () => addButton.current;
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
  const nothingToClear =
    !preferences || !proposals || !memories || (memories.length === 0 && proposals.length === 0 && !preferences.automaticProposals);

  return (
    <>
      <SectionHeading title="Memory" description="What NetSec AI may recall in future chats." />
      {error ? (
        <LoadFailure title="Couldn't load memory" error={error} retry={() => void load()} />
      ) : (
        // Every card is drawn at once, its contents loading in place: nothing below moves when they arrive.
        <div className="flex flex-col gap-4">
          {notice && <Notice tone="warning">{notice}</Notice>}
          <div ref={suggestionsCard}>
            <SettingsCard title={proposals?.length ? `Suggestions (${proposals.length})` : "Suggestions"}>
              {preferences ? (
                <ToggleRow
                  label="Suggest memories from chats"
                  description="You approve each one here before it's saved."
                  checked={preferences.automaticProposals}
                  disabled={busy === "preferences"}
                  onCheckedChange={(checked) => void changeAutomatic(checked)}
                />
              ) : (
                // As tall as the switch row: its label line and one line of description.
                <SkeletonGroup label="Loading memory settings">
                  <Skeleton className="h-[2.625rem] w-full rounded-lg" />
                </SkeletonGroup>
              )}
              {proposals && proposals.length > 0 && (
                <ul aria-label="Memory suggestions" className="divide-y divide-border border-y border-border">
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
              <Disclosure label="How memory works">
                <ul className="flex list-disc flex-col gap-1 pl-5 text-sm leading-relaxed text-text-secondary">
                  <li>Only active, unexpired memories are recalled.</li>
                  <li>A correction replaces the earlier version.</li>
                  <li>Suggestions come from eligible completed chats. Rejected ones are never recalled.</li>
                  <li>Turning suggestions off stops new ones.</li>
                </ul>
              </Disclosure>
            </SettingsCard>
          </div>

          <SettingsCard
            title={memories ? `Active memory (${memories.length})` : "Active memory"}
            description={!memories ? <LoadingLine /> : memories.length === 0 ? "Nothing saved yet." : undefined}
            footer={
              <>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  className={TAP}
                  onClick={() => void download()}
                  disabled={busy === "export" || !memories?.length}
                >
                  {busy === "export" ? "Exporting…" : "Export JSON"}
                </Button>
                <Button
                  ref={addButton}
                  type="button"
                  size="sm"
                  className={TAP}
                  disabled={!memories}
                  onClick={() => { setDialogError(null); setEditor({ type: "new" }); }}
                >
                  Add memory
                </Button>
              </>
            }
          >
            {memories && memories.length > 0 && (
              <ul aria-label="Active memories" className="divide-y divide-border">
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
            description="Deletes all memories and suggestions, and turns suggestions off. Can't be undone."
            tone="danger"
            footer={
              <Button
                type="button"
                size="sm"
                variant="destructive"
                className={TAP}
                disabled={nothingToClear}
                onClick={() => { setDialogError(null); setConfirm({ type: "clear" }); }}
              >
                Clear all memory
              </Button>
            }
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
