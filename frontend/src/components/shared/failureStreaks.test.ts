import { describe, expect, it } from "vitest";

import { groupFailureStreaks } from "./failureStreaks";

const run = (id: string, status: string, job = "a") => ({ id, status, job });

describe("groupFailureStreaks", () => {
  it("folds consecutive failures of one job into the newest one", () => {
    const rows = groupFailureStreaks([run("4", "success"), run("3", "failure"), run("2", "failure"), run("1", "failure"), run("0", "success")], (r) => r.job);

    expect(rows.map((r) => r.run.id)).toEqual(["4", "3", "0"]);
    expect(rows[1].streak?.map((r) => r.id)).toEqual(["3", "2", "1"]);
  });

  it("keeps a single failure as a plain row and starts a new streak after a success", () => {
    const rows = groupFailureStreaks([run("3", "failure"), run("2", "success"), run("1", "failure"), run("0", "failure")], (r) => r.job);

    expect(rows.map((r) => [r.run.id, r.streak?.length])).toEqual([["3", undefined], ["2", undefined], ["1", 2]]);
  });

  it("isn't broken up by other jobs' runs in between", () => {
    const rows = groupFailureStreaks([run("3", "failure", "a"), run("2", "success", "b"), run("1", "failure", "a"), run("0", "failure", "b")], (r) => r.job);

    expect(rows.map((r) => [r.run.id, r.streak?.length])).toEqual([["3", 2], ["2", undefined], ["0", undefined]]);
  });
});
