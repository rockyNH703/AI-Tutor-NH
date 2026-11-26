import { describe, expect, test } from "bun:test";
import { ApiError } from "@/lib/api/errors";
import type { Conversation, ConversationPage } from "@/lib/api/types";
import { type ConversationsApi, ConversationsStore, PAGE_SIZE, pinnedChats } from "./conversations-store";

const convo = (n: number, updated: string, title = `Chat ${n}`): Conversation => ({
  id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`,
  title,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: updated,
});

function fakeApi(pages: Record<string, ConversationPage>, options: { failRename?: boolean } = {}) {
  const calls: string[] = [];
  const api: ConversationsApi = {
    listConversations: async ({ cursor }) => {
      calls.push(`list:${cursor ?? ""}`);
      const page = pages[cursor ?? ""];
      if (!page) throw new ApiError(503, "service_unavailable");
      return page;
    },
    renameConversation: async (id, title) => {
      calls.push(`rename:${title}`);
      if (options.failRename) throw new ApiError(422, "validation_error");
      return { ...convo(0, "2026-02-01T00:00:00Z"), id, title };
    },
  };
  return { api, calls };
}

describe("ConversationsStore", () => {
  test("starts from the server-rendered first page without fetching", () => {
    const { api, calls } = fakeApi({});
    const store = new ConversationsStore(api, { items: [convo(1, "2026-01-02T00:00:00Z")], nextCursor: null });
    expect(store.getSnapshot().items.length).toBe(1);
    expect(store.getSnapshot().status).toBe("ready");
    expect(calls).toEqual([]);
  });

  test("a new or active conversation moves to the top", () => {
    const { api } = fakeApi({});
    const store = new ConversationsStore(api, {
      items: [convo(1, "2026-01-03T00:00:00Z"), convo(2, "2026-01-02T00:00:00Z")],
      nextCursor: null,
    });
    store.upsert(convo(3, "2026-01-04T00:00:00Z", "New"));
    store.upsert({ ...convo(2, "2026-01-05T00:00:00Z"), title: "Renamed by activity" });
    expect(store.getSnapshot().items.map((item) => item.title)).toEqual(["Renamed by activity", "New", "Chat 1"]);
  });

  test("rename is optimistic and rolls back when refused — the error is not swallowed", async () => {
    const { api } = fakeApi({}, { failRename: true });
    const store = new ConversationsStore(api, { items: [convo(1, "2026-01-01T00:00:00Z", "Old")], nextCursor: null });
    const renaming = store.rename(convo(1, "").id, "New title");
    expect(store.getSnapshot().items[0]?.title).toBe("New title");
    await expect(renaming).rejects.toBeInstanceOf(ApiError);
    expect(store.getSnapshot().items[0]?.title).toBe("Old");
  });

  test("a rename wins over a stream's older copy of the conversation, until the next refresh", async () => {
    const first = convo(1, "2026-01-02T00:00:00Z", "New chat");
    const { api } = fakeApi({ "": { items: [convo(1, "2026-01-04T00:00:00Z", "From the server")], nextCursor: null } });
    const store = new ConversationsStore(api, { items: [first], nextCursor: null });
    await store.rename(first.id, "My biology notes");
    // The reply's `done`, read just before the rename was saved, carries the automatic title.
    store.upsert({ ...first, title: "Photosynthesis Explained", updatedAt: "2026-01-03T00:00:00Z" });
    expect(store.getSnapshot().items[0]?.title).toBe("My biology notes");
    expect(store.getSnapshot().items[0]?.updatedAt).toBe("2026-01-03T00:00:00Z"); // activity still moves
    await store.refresh();
    expect(store.getSnapshot().items[0]?.title).toBe("From the server");
    store.upsert({ ...first, title: "Named elsewhere" });
    expect(store.getSnapshot().items[0]?.title).toBe("Named elsewhere");
  });

  test("a refused rename is forgotten, so the stream's title shows", async () => {
    const first = convo(1, "2026-01-02T00:00:00Z", "New chat");
    const { api } = fakeApi({}, { failRename: true });
    const store = new ConversationsStore(api, { items: [first], nextCursor: null });
    await expect(store.rename(first.id, "Mine")).rejects.toThrow();
    store.upsert({ ...first, title: "Photosynthesis Explained" });
    expect(store.getSnapshot().items[0]?.title).toBe("Photosynthesis Explained");
  });

  test("an empty or unchanged title is not sent", async () => {
    const { api, calls } = fakeApi({});
    const store = new ConversationsStore(api, { items: [convo(1, "2026-01-01T00:00:00Z", "Same")], nextCursor: null });
    await store.rename(convo(1, "").id, "   ");
    await store.rename(convo(1, "").id, "Same");
    expect(calls).toEqual([]);
  });

  test("load more appends the next page without duplicates", async () => {
    const { api } = fakeApi({
      c1: { items: [convo(2, "2026-01-01T00:00:00Z"), convo(3, "2025-12-31T00:00:00Z")], nextCursor: null },
    });
    const store = new ConversationsStore(api, {
      items: [convo(1, "2026-01-02T00:00:00Z"), convo(2, "2026-01-01T00:00:00Z")],
      nextCursor: "c1",
    });
    await store.loadMore();
    expect(store.getSnapshot().items.map((item) => item.id)).toEqual([convo(1, "").id, convo(2, "").id, convo(3, "").id]);
    expect(store.getSnapshot().nextCursor).toBeNull();
  });

  test("refresh replaces the first page (deletions in another tab disappear)", async () => {
    const { api } = fakeApi({ "": { items: [convo(2, "2026-01-05T00:00:00Z")], nextCursor: null } });
    const store = new ConversationsStore(api, {
      items: [convo(1, "2026-01-03T00:00:00Z"), convo(2, "2026-01-02T00:00:00Z")],
      nextCursor: null,
    });
    await store.refresh();
    expect(store.getSnapshot().items.map((item) => item.id)).toEqual([convo(2, "").id]);
  });

  test("focus refreshes are throttled", async () => {
    let now = 1_000_000;
    const { api, calls } = fakeApi({ "": { items: [], nextCursor: null } });
    const store = new ConversationsStore(api, { items: [], nextCursor: null }, () => now);
    await store.refresh(30_000);
    expect(calls).toEqual([]);
    now += 31_000;
    await store.refresh(30_000);
    expect(calls).toEqual(["list:"]);
  });

  test("a failed first load reports an error instead of an empty list", async () => {
    const { api } = fakeApi({});
    const store = new ConversationsStore(api, null);
    expect(store.getSnapshot().status).toBe("loading");
    await store.refresh();
    expect(store.getSnapshot().status).toBe("error");
  });

  test("page size matches the server-rendered first page", () => {
    expect(PAGE_SIZE).toBe(30);
  });
});

describe("pin and archive", () => {
  const pinnedAt = "2026-03-01T00:00:00Z";
  function store(options: { fail?: boolean; pinnedPage?: Conversation[] } = {}) {
    const calls: string[] = [];
    const api: ConversationsApi = {
      listConversations: async () => ({ items: [], nextCursor: null }),
      renameConversation: async (id, title) => ({ ...convo(0, "2026-02-01T00:00:00Z"), id, title }),
      updateConversation: async (id, changes) => {
        calls.push(`update:${JSON.stringify(changes)}`);
        if (options.fail) throw new ApiError(503, "service_unavailable");
        const base = { ...convo(Number(id.slice(-12)), "2026-01-02T00:00:00Z"), id };
        return { ...base, pinnedAt: changes.pinned ? pinnedAt : null, archivedAt: changes.archived ? pinnedAt : null };
      },
      listPinned: async () => ({ items: options.pinnedPage ?? [], nextCursor: null }),
    };
    const items = [convo(1, "2026-01-03T00:00:00Z"), convo(2, "2026-01-02T00:00:00Z")];
    return { store: new ConversationsStore(api, { items, nextCursor: null }), calls };
  }

  test("pinning moves a chat to the pinned list once, and unpinning puts it back", async () => {
    const { store: s } = store();
    const chat = s.getSnapshot().items[1] as Conversation;
    await s.setPinned(chat, true);
    expect(pinnedChats(s.getSnapshot()).map((item) => item.id)).toEqual([chat.id]);
    expect(s.getSnapshot().items.filter((item) => item.id === chat.id)).toHaveLength(1); // never duplicated
    await s.setPinned(chat, false);
    expect(pinnedChats(s.getSnapshot())).toEqual([]);
  });

  test("a refused pin or archive rolls back and rethrows", async () => {
    const { store: s } = store({ fail: true });
    const chat = s.getSnapshot().items[0] as Conversation;
    await expect(s.setPinned(chat, true)).rejects.toBeInstanceOf(ApiError);
    expect(pinnedChats(s.getSnapshot())).toEqual([]);
    const archiving = s.archive(chat);
    expect(s.getSnapshot().items.map((item) => item.id)).not.toContain(chat.id); // optimistic
    await expect(archiving).rejects.toBeInstanceOf(ApiError);
    expect(s.getSnapshot().items.map((item) => item.id)).toContain(chat.id);
  });

  test("an archived chat leaves both lists and does not come back from a stream's copy", async () => {
    const { store: s } = store();
    const chat = s.getSnapshot().items[0] as Conversation;
    await s.setPinned(chat, true);
    await s.archive(chat);
    expect(s.getSnapshot().items.map((item) => item.id)).not.toContain(chat.id);
    expect(pinnedChats(s.getSnapshot())).toEqual([]);
    s.upsert({ ...chat, archivedAt: pinnedAt, updatedAt: "2026-05-01T00:00:00Z" });
    expect(s.getSnapshot().items.map((item) => item.id)).not.toContain(chat.id);
    s.unarchived({ ...chat, archivedAt: null, pinnedAt: null });
    expect(s.getSnapshot().items.map((item) => item.id)).toContain(chat.id);
  });

  test("a pin older than the loaded pages still shows, read on its own", async () => {
    const old = { ...convo(9, "2025-01-01T00:00:00Z", "Old pinned"), pinnedAt };
    const { store: s } = store({ pinnedPage: [old] });
    await s.refreshPinned();
    expect(pinnedChats(s.getSnapshot()).map((item) => item.title)).toEqual(["Old pinned"]);
  });
});

describe("pin and archive under races", () => {
  const at = "2026-03-01T00:00:00Z";
  type Deferred<T> = { promise: Promise<T>; resolve: (value: T) => void; reject: (error: unknown) => void };
  const deferred = <T>(): Deferred<T> => {
    let resolve!: (value: T) => void;
    let reject!: (error: unknown) => void;
    const promise = new Promise<T>((ok, fail) => {
      resolve = ok;
      reject = fail;
    });
    return { promise, resolve, reject };
  };
  // Every API call waits until the test answers it, in the order the test chooses.
  function controlled() {
    const updates: Deferred<Conversation>[] = [];
    const lists: Deferred<ConversationPage>[] = [];
    const pins: Deferred<ConversationPage>[] = [];
    const api: ConversationsApi = {
      listConversations: () => {
        const next = deferred<ConversationPage>();
        lists.push(next);
        return next.promise;
      },
      renameConversation: async (id, title) => ({ ...convo(0, "2026-02-01T00:00:00Z"), id, title }),
      updateConversation: () => {
        const next = deferred<Conversation>();
        updates.push(next);
        return next.promise;
      },
      listPinned: () => {
        const next = deferred<ConversationPage>();
        pins.push(next);
        return next.promise;
      },
    };
    const a = { ...convo(1, "2026-01-03T00:00:00Z"), pinnedAt: null, archivedAt: null };
    const b = { ...convo(2, "2026-01-02T00:00:00Z"), pinnedAt: null, archivedAt: null };
    const s = new ConversationsStore(api, { items: [a, b], nextCursor: null });
    const ids = () => s.getSnapshot().items.map((item) => item.id);
    const pinnedIds = () => pinnedChats(s.getSnapshot()).map((item) => item.id);
    return { s, a, b, updates, lists, pins, ids, pinnedIds };
  }

  test("a pin answered after an archive of the same chat does not bring it back", async () => {
    const { s, a, updates, ids, pinnedIds } = controlled();
    const pinning = s.setPinned(a, true);
    const archiving = s.archive(a);
    updates[0]?.resolve({ ...a, pinnedAt: at }); // the pin's answer arrives after the archive was sent
    await pinning;
    expect(ids()).not.toContain(a.id);
    updates[1]?.resolve({ ...a, pinnedAt: null, archivedAt: at });
    await archiving;
    expect(ids()).not.toContain(a.id);
    expect(pinnedIds()).toEqual([]);
  });

  test("a page read before an archive committed does not bring the chat back; a later read is the server's", async () => {
    const { s, a, b, updates, lists, pins, ids } = controlled();
    const archiving = s.archive(a);
    const stale = s.refresh(); // starts while the archive is in flight
    updates[0]?.resolve({ ...a, archivedAt: at });
    await archiving;
    lists[0]?.resolve({ items: [a, b], nextCursor: null }); // read before the archive committed: still lists it
    pins[0]?.resolve({ items: [], nextCursor: null });
    await stale;
    expect(ids()).toEqual([b.id]);
    // A read started after the archive was confirmed is believed again (here: restored in another tab).
    const later = s.refresh();
    lists[1]?.resolve({ items: [a, b], nextCursor: null });
    pins[1]?.resolve({ items: [], nextCursor: null });
    await later;
    expect(ids()).toEqual([a.id, b.id]);
  });

  test("an older pinned list does not undo an unpin made here", async () => {
    const { s, a, updates, pins, pinnedIds } = controlled();
    const pinning = s.setPinned(a, true);
    updates[0]?.resolve({ ...a, pinnedAt: at });
    await pinning;
    const read = s.refreshPinned(); // starts while the chat is pinned
    const unpinning = s.setPinned({ ...a, pinnedAt: at }, false);
    pins[0]?.resolve({ items: [{ ...a, pinnedAt: at }], nextCursor: null });
    await read;
    expect(pinnedIds()).toEqual([]);
    updates[1]?.resolve({ ...a, pinnedAt: null });
    await unpinning;
    expect(pinnedIds()).toEqual([]);
  });

  test("a chat deleted or archived elsewhere leaves the lists instead of coming back on a refused pin", async () => {
    const { s, a, b, updates, ids } = controlled();
    const gone = s.setPinned(a, true);
    updates[0]?.reject(new ApiError(404, "not_found"));
    await expect(gone).rejects.toBeInstanceOf(ApiError);
    expect(ids()).toEqual([b.id]);
    const archived = s.setPinned(b, true);
    updates[1]?.reject(new ApiError(422, "archived"));
    await expect(archived).rejects.toBeInstanceOf(ApiError);
    expect(ids()).toEqual([]);
  });

  test("a stream's copy keeps a pin made here, and an archived chat's stream copy stays out", async () => {
    const { s, a, b, updates, ids, pinnedIds } = controlled();
    const pinning = s.setPinned(a, true);
    s.upsert({ ...a, title: "Named by the stream", pinnedAt: null }); // read before the pin
    expect(pinnedIds()).toEqual([a.id]);
    updates[0]?.resolve({ ...a, pinnedAt: at });
    await pinning;
    const archiving = s.archive(b);
    s.upsert({ ...b, updatedAt: "2026-05-01T00:00:00Z", archivedAt: null });
    expect(ids()).not.toContain(b.id);
    updates[1]?.resolve({ ...b, archivedAt: at });
    await archiving;
    expect(ids()).not.toContain(b.id);
  });
});
