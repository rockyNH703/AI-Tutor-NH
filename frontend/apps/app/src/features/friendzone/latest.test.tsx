import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { FriendZoneMe } from "./api";
import { FriendZoneHub } from "./friend-zone-hub";
import { keepOnFailure, sequence } from "./latest";
import type { MeLoad } from "./model";

/** A promise resolved by hand, to answer reads in any order. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

/** A hook's read as the hooks do it: take a ticket, apply the answer only while it is still the newest. */
function read<T>(ticket: () => () => boolean, answer: Promise<T>, apply: (value: T) => void): Promise<void> {
  const newest = ticket();
  return answer.then((value) => {
    if (newest()) apply(value);
  });
}

describe("overlapping reads", () => {
  test("two reads answering out of order keep the newer one", async () => {
    const ticket = sequence();
    let shown = 0;
    const older = deferred<number>();
    const newer = deferred<number>();
    const first = read(ticket, older.promise, (value) => {
      shown = value;
    });
    const second = read(ticket, newer.promise, (value) => {
      shown = value;
    });
    newer.resolve(2);
    await second;
    older.resolve(1); // the pulse that started first answers last
    await first;
    expect(shown).toBe(2);
  });

  test("a save takes a ticket too: a read started before it can't put the old profile back", async () => {
    const ticket = sequence();
    let version = 1;
    const pulse = deferred<number>();
    const inFlight = read(ticket, pulse.promise, (value) => {
      version = value;
    });
    ticket(); // setMe after a save stored version 2
    version = 2;
    pulse.resolve(1);
    await inFlight;
    expect(version).toBe(2);
  });
});

const ME: FriendZoneMe = {
  enabled: true,
  eligible: true,
  member: true,
  suspended: false,
  profile: { playerId: "11111111-1111-4111-8111-111111111111", displayName: "Asha", discoverable: false, showProgram: false, showStats: false, version: 2, qa: false },
  serverTime: "2026-11-24T10:00:00Z",
};
const PROGRAM = { slug: "ccna", title: "CCNA", months: [{ month: 1, title: "Basics", material: { documents: 1, lessons: 1, questions: 1 } }] };
const noop = () => {};

describe("a failed /me pulse", () => {
  test("keeps ready (only a first load or a retry shows the error)", () => {
    const ready: MeLoad = { status: "ready", me: ME };
    const failed: MeLoad = { status: "error", error: new Error("offline") };
    expect(keepOnFailure<MeLoad>(ready, failed)).toBe(ready);
    expect(keepOnFailure<MeLoad>({ status: "loading" }, failed)).toBe(failed);
  });

  test("leaves a live battle and the panel mounted", () => {
    const after = keepOnFailure<MeLoad>({ status: "ready", me: ME }, { status: "error", error: new Error("offline") });
    const html = renderToStaticMarkup(
      <FriendZoneHub
        load={after}
        retry={noop}
        refresh={noop}
        setMe={noop}
        onPractice={noop}
        social={{ program: PROGRAM, month: 1, pulse: 3, battleId: "33333333-3333-4333-8333-333333333333", onBattle: noop }}
      />,
    );
    expect(html).toContain("Back to Friend Zone"); // the battle view
    expect(html).not.toContain("couldn&#x27;t be loaded");
  });
});
