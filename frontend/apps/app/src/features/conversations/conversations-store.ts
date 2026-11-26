/**
 * The sidebar's list of conversations: newest activity first, paged by cursor.
 *
 * The first page arrives with the page itself (fetched by the server layout), so
 * the sidebar is filled on first paint. After that the list is kept current from
 * what the chat already knows — a new conversation from the stream's `meta`, a
 * new title or activity from `done` — and refetched when the window regains
 * focus, so a chat started in another tab shows up.
 *
 * Rename and delete are optimistic and roll back (with the error rethrown for a
 * toast) when the API refuses. Nothing here swallows a failure.
 *
 * A title renamed in this tab wins over a stream's copy of the conversation, which may have been
 * read just before the rename was saved (the API never names a renamed chat again), until the next
 * refresh brings the server's own titles.
 *
 * Pinned chats: `items` holds every chat that is not archived (pinned ones too, as the API lists them), and
 * `pinned` the pinned ones read on their own (GET ?view=pinned), so a pin on a chat older than the loaded pages
 * still shows. The sidebar draws pinned chats once, at the top, and the rest below. Pin, unpin and archive are
 * optimistic and roll back (rethrowing) when refused; an archived chat leaves both lists.
 *
 * Ordering: a chat changed in this tab is "local" until a list read that STARTED after the change was confirmed comes
 * back. Until then a read (refresh, Load more, the pinned list) or a stream's copy cannot undo it: an archived chat
 * does not come back from a page read just before the archive committed, and an unpin is not undone by an older
 * pinned list. Of two changes to one chat, only the latest one's answer is applied. A chat the API no longer has
 * (404), or one archived elsewhere (422 `archived`), leaves the lists instead of coming back on rollback.
 */

import { ApiError } from "@/lib/api/errors";
import type { Conversation, ConversationPage, ConversationView } from "@/lib/api/types";

export type ConversationsApi = {
  listConversations: (options: { limit?: number; cursor?: string | null; view?: ConversationView }) => Promise<ConversationPage>;
  renameConversation: (id: string, title: string) => Promise<Conversation>;
  updateConversation?: (id: string, changes: { pinned?: boolean; archived?: boolean }) => Promise<Conversation>;
  /** The pinned chats (GET ?view=pinned); without it the store has no Pinned list of its own. */
  listPinned?: () => Promise<ConversationPage>;
};

export type ConversationsState = Readonly<{
  items: readonly Conversation[];
  nextCursor: string | null;
  status: "ready" | "loading" | "error";
  loadingMore: boolean;
  /** The pinned chats, newest pin first (read on their own; also marked in `items`). */
  pinned: readonly Conversation[];
}>;

/** Pinned first by when they were pinned; the sidebar's Pinned section. */
export function pinnedChats(state: ConversationsState): Conversation[] {
  const byId = new Map<string, Conversation>();
  for (const item of state.pinned) byId.set(item.id, item);
  // The history's copy is the one the latest local change wrote: it wins, pinned or not.
  for (const item of state.items) {
    if (item.pinnedAt) byId.set(item.id, item);
    else byId.delete(item.id);
  }
  return [...byId.values()]
    .filter((item) => item.pinnedAt && !item.archivedAt)
    .sort((a, b) => (b.pinnedAt ?? "").localeCompare(a.pinnedAt ?? "") || b.id.localeCompare(a.id));
}

export const PAGE_SIZE = 30;

const byActivity = (a: Conversation, b: Conversation) =>
  b.updatedAt.localeCompare(a.updatedAt) || b.id.localeCompare(a.id);

export class ConversationsStore {
  private state: ConversationsState;
  private readonly listeners = new Set<() => void>();
  private lastRefresh = 0;
  /** Titles set by a rename in this tab, by conversation id. */
  private readonly renamed = new Map<string, string>();
  /** A clock for ordering local changes against list reads. */
  private tick = 0;
  /** Chats with a pin, unpin or archive in flight: the latest change's token. */
  private readonly inFlight = new Map<string, number>();
  /** When a local change to a chat was confirmed (or dropped): reads that started before it can't undo it. */
  private readonly settledAt = new Map<string, number>();

  constructor(
    private readonly api: ConversationsApi,
    initial: ConversationPage | null,
    private readonly now: () => number = () => Date.now(),
  ) {
    this.state = initial
      ? { items: initial.items, nextCursor: initial.nextCursor, status: "ready", loadingMore: false, pinned: [] }
      : { items: [], nextCursor: null, status: "loading", loadingMore: false, pinned: [] };
    if (initial) this.lastRefresh = now();
  }

  getSnapshot = (): ConversationsState => this.state;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  find(id: string): Conversation | undefined {
    return this.state.items.find((item) => item.id === id);
  }

  /** Reload the first page. `minIntervalMs` throttles focus-driven refreshes. */
  async refresh(minIntervalMs = 0): Promise<void> {
    if (minIntervalMs && this.now() - this.lastRefresh < minIntervalMs) return;
    this.lastRefresh = this.now();
    void this.refreshPinned();
    const started = this.read();
    try {
      const page = await this.api.listConversations({ limit: PAGE_SIZE });
      for (const item of page.items) this.renamed.delete(item.id);
      // The first page replaces what we had, so renames and deletions made in
      // another tab show up. Pages loaded beyond it ("Load more") are kept, and
      // so are chats changed here that this read may predate.
      const fresh = page.items.filter((item) => !this.isLocal(item.id, started));
      const loadedBeyondFirstPage = this.state.items.length > PAGE_SIZE;
      const freshIds = new Set(fresh.map((item) => item.id));
      const rest = this.state.items.filter(
        (item) => !freshIds.has(item.id) && (loadedBeyondFirstPage || this.isLocal(item.id, started)),
      );
      this.confirmedBy(started);
      this.set({
        items: [...fresh, ...rest].sort(byActivity),
        nextCursor: loadedBeyondFirstPage ? this.state.nextCursor : page.nextCursor,
        status: "ready",
      });
    } catch {
      if (this.state.status === "loading") this.set({ status: "error" });
    }
  }

  async loadMore(): Promise<void> {
    if (!this.state.nextCursor || this.state.loadingMore) return;
    this.set({ loadingMore: true });
    const started = this.read();
    try {
      const page = await this.api.listConversations({ limit: PAGE_SIZE, cursor: this.state.nextCursor });
      const known = new Set(this.state.items.map((item) => item.id));
      this.set({
        items: [
          ...this.state.items,
          ...page.items.filter((item) => !known.has(item.id) && !this.isLocal(item.id, started)),
        ],
        nextCursor: page.nextCursor,
        loadingMore: false,
      });
    } catch (error) {
      this.set({ loadingMore: false });
      throw error;
    }
  }

  /** A conversation was created or changed (title, activity): put it in its place. */
  upsert(conversation: Conversation): void {
    if (conversation.program) return; // a program chat lives in its month's history, never in this list
    // A stream's copy may predate a pin or archive made here: those stay as this tab set them.
    if (this.isLocal(conversation.id, Number.NEGATIVE_INFINITY)) {
      const local = this.snapshotOf(conversation.id);
      if (!local) return; // archived or deleted here
      this.place({ ...conversation, pinnedAt: local.pinnedAt ?? null, archivedAt: local.archivedAt ?? null });
      return;
    }
    this.place(conversation); // an archived chat (written to from its own page) stays out of the sidebar
  }

  remove(id: string): void {
    this.settledAt.set(id, ++this.tick); // a read that started before this can't bring it back
    if (!this.snapshotOf(id)) return;
    this.set({
      items: this.state.items.filter((item) => item.id !== id),
      pinned: this.state.pinned.filter((item) => item.id !== id),
    });
  }

  /** Optimistic rename; rolls back and rethrows if the API refuses. */
  async rename(id: string, title: string): Promise<void> {
    const before = this.snapshotOf(id);
    if (!before) return;
    const trimmed = title.trim();
    if (!trimmed || trimmed === before.title) return;
    const renamedBefore = this.renamed.get(id);
    this.renamed.set(id, trimmed);
    this.set({
      items: this.state.items.map((item) => (item.id === id ? { ...item, title: trimmed } : item)),
      pinned: this.state.pinned.map((item) => (item.id === id ? { ...item, title: trimmed } : item)),
    });
    try {
      const saved = await this.api.renameConversation(id, trimmed);
      this.renamed.set(id, saved.title);
      this.set({
        items: this.state.items.map((item) => (item.id === id ? saved : item)),
        pinned: this.state.pinned.map((item) => (item.id === id ? saved : item)),
      });
    } catch (error) {
      if (renamedBefore === undefined) this.renamed.delete(id);
      else this.renamed.set(id, renamedBefore);
      this.set({
        items: this.state.items.map((item) => (item.id === id ? before : item)),
        pinned: this.state.pinned.map((item) => (item.id === id ? before : item)),
      });
      throw error;
    }
  }

  /** Put back a conversation whose deletion failed. */
  restore(conversation: Conversation): void {
    this.upsert(conversation);
  }

  /** The pinned chats, read on their own (a failure leaves what was shown). */
  async refreshPinned(): Promise<void> {
    if (!this.api.listPinned) return;
    const started = this.read();
    try {
      const page = await this.api.listPinned();
      const fresh = page.items.filter((item) => !this.isLocal(item.id, started));
      const freshIds = new Set(fresh.map((item) => item.id));
      const kept = this.state.pinned.filter((item) => !freshIds.has(item.id) && this.isLocal(item.id, started));
      this.set({ pinned: [...fresh, ...kept] });
    } catch {
      // The pins already shown stay; the next refresh tries again.
    }
  }

  /**
   * Pin or unpin (optimistic): the chat moves between the Pinned section and the rest at once and goes back if the
   * API refuses (the error is rethrown for a toast).
   */
  async setPinned(conversation: Conversation, pinned: boolean): Promise<void> {
    const update = this.api.updateConversation;
    if (!update) return;
    const id = conversation.id;
    const before = this.snapshotOf(id) ?? conversation;
    const token = this.begin(id);
    this.place({ ...before, pinnedAt: pinned ? new Date().toISOString() : null });
    try {
      const saved = await update(id, { pinned });
      if (this.end(id, token)) this.place(saved);
    } catch (error) {
      if (this.end(id, token)) this.undo(before, error);
      throw error;
    }
  }

  /** Archive (optimistic): the chat leaves the sidebar; it comes back if the API refuses. */
  async archive(conversation: Conversation): Promise<void> {
    const update = this.api.updateConversation;
    if (!update) return;
    const id = conversation.id;
    const before = this.snapshotOf(id) ?? conversation;
    const token = this.begin(id);
    this.set({
      items: this.state.items.filter((item) => item.id !== id),
      pinned: this.state.pinned.filter((item) => item.id !== id),
    });
    try {
      const saved = await update(id, { archived: true });
      if (this.end(id, token)) this.place(saved); // archived: stays out of both lists
    } catch (error) {
      if (this.end(id, token)) this.undo(before, error);
      throw error;
    }
  }

  /** A chat restored from Archived chats (already saved): back in its place by activity. */
  unarchived(conversation: Conversation): void {
    this.settledAt.set(conversation.id, ++this.tick); // an older read (when it was archived) can't hide it again
    this.place({ ...conversation, archivedAt: null });
  }

  /** A read starts now: its answer can't undo changes confirmed after this point. */
  private read(): number {
    return ++this.tick;
  }

  /** Whether this tab's state of the chat wins over a read that started at `started`. */
  private isLocal(id: string, started: number): boolean {
    if (this.inFlight.has(id)) return true;
    const at = this.settledAt.get(id);
    return at !== undefined && at > started;
  }

  /** A read that started at `started` came back: changes confirmed before it are the server's again. */
  private confirmedBy(started: number): void {
    for (const [id, at] of this.settledAt) if (at < started && !this.inFlight.has(id)) this.settledAt.delete(id);
  }

  private begin(id: string): number {
    const token = ++this.tick;
    this.inFlight.set(id, token);
    return token;
  }

  /** The change with `token` finished: false when a later change to the same chat has taken over (its answer rules). */
  private end(id: string, token: number): boolean {
    if (this.inFlight.get(id) !== token) return false;
    this.inFlight.delete(id);
    this.settledAt.set(id, ++this.tick);
    return true;
  }

  /** Roll a refused change back, unless the chat is gone (404) or archived elsewhere: then it leaves the lists. */
  private undo(before: Conversation, error: unknown): void {
    const gone = error instanceof ApiError && (error.status === 404 || (error.status === 422 && error.code === "archived"));
    if (!gone) {
      this.place(before);
      return;
    }
    this.set({
      items: this.state.items.filter((item) => item.id !== before.id),
      pinned: this.state.pinned.filter((item) => item.id !== before.id),
    });
  }

  private snapshotOf(id: string): Conversation | undefined {
    return this.find(id) ?? this.state.pinned.find((item) => item.id === id);
  }

  /** A chat as it now is, in both lists: `items` by activity, `pinned` only while pinned. */
  private place(conversation: Conversation): void {
    if (conversation.program) return;
    const title = this.renamed.get(conversation.id);
    const next = title === undefined ? conversation : { ...conversation, title };
    const items = next.archivedAt
      ? this.state.items.filter((item) => item.id !== next.id)
      : [next, ...this.state.items.filter((item) => item.id !== next.id)].sort(byActivity);
    const pinned = this.state.pinned.filter((item) => item.id !== next.id);
    this.set({ items, pinned: next.pinnedAt && !next.archivedAt ? [next, ...pinned] : pinned });
  }

  private set(patch: Partial<ConversationsState>): void {
    this.state = { ...this.state, ...patch };
    for (const listener of [...this.listeners]) listener();
  }
}
