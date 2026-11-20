/** Folds consecutive failures of the same job (per `jobKey`) into one row.
 * `runs` is newest first; a success of that job ends the streak, other
 * jobs' runs in between don't. */
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
