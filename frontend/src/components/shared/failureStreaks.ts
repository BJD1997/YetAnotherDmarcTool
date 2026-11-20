/** Folds repeated failures of the same job into one row. During an outage a
 * mailbox poll fails every few minutes (the 12/13 Nov 2026 Microsoft outage
 * gave about 85 identical rows); one "failed 85×" row with the time span
 * says the same thing and keeps the rest of the list readable.
 *
 * `runs` is newest first, as the APIs return it. A failure joins the open
 * streak of the same job (per `jobKey`); a success of that job closes it.
 * Other jobs' runs in between don't break a streak, so this also works on
 * the admin list where many organizations' runs interleave. */
export interface RunRow<T> {
  /** The newest run of the row. */
  run: T;
  /** Every run in the streak, newest first; only set for 2 or more failures. */
  streak?: T[];
}

export function groupFailureStreaks<T extends { status: string }>(runs: T[], jobKey: (run: T) => string): RunRow<T>[] {
  const rows: RunRow<T>[] = [];
  const open = new Map<string, RunRow<T>>();
  for (const run of runs) {
    const key = jobKey(run);
    if (run.status !== "failure") {
      open.delete(key);
      rows.push({ run });
      continue;
    }
    const current = open.get(key);
    if (current) {
      current.streak = current.streak ? [...current.streak, run] : [current.run, run];
      continue;
    }
    const row: RunRow<T> = { run };
    open.set(key, row);
    rows.push(row);
  }
  return rows;
}
