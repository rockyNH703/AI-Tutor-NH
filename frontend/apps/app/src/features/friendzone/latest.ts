/**
 * Latest wins: reads that overlap (a 15-second pulse, a reload after a change, a visibility change) may answer out of
 * order. Each read takes a ticket; only the newest ticket may apply its answer, so an older answer never puts back
 * state that a newer read (or a save) already replaced.
 */
export function sequence(): () => () => boolean {
  let latest = 0;
  return () => {
    const mine = ++latest;
    return () => mine === latest;
  };
}

/** A read's answer when it fails: a failed refresh keeps what is shown; only a first load (or a retry) shows the error. */
export function keepOnFailure<S extends { status: string }>(current: S, failed: S): S {
  return current.status === "ready" ? current : failed;
}
