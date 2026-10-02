import { describe, expect, it } from "vitest";

import shared from "../../../../shared/dmarc-inheritance-cases.json";
import { effectiveCurrentPolicies } from "./dmarcInheritance";

describe("DMARC inheritance (shared cases with the backend)", () => {
  it.each(shared.cases)("$name", ({ tags, expected }) => {
    const effective = effectiveCurrentPolicies(tags as Record<string, string>);
    for (const tag of ["p", "sp", "np"] as const) {
      expect({ tag, ...effective[tag], policy: effective[tag].policy ?? null }).toEqual({ tag, ...expected[tag] });
    }
  });
});
