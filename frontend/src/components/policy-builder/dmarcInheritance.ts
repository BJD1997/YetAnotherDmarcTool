import type { DmarcPolicy } from "../../api/policyBuilder";

// Pure DMARC policy-inheritance rules behind the Policy Builder's
// "this weakens your policy" warning. The same rules live in the backend
// (backend/app/services/dns_checks/dmarc_record.py's effective_policies);
// shared/dmarc-inheritance-cases.json is run against both, so they can't
// drift apart unnoticed.

const VALID_POLICIES: DmarcPolicy[] = ["none", "quarantine", "reject"];

// A published record's tags come from a generic key=value parser (see
// backend/app/services/dns_checks/dmarc_record.py's parse_dmarc_tags) with
// no validation — a malformed or unrecognized value (a typo, an old draft
// tag) must fall back rather than get carried into the generated record
// unexamined.
export function asPolicy(value: string | undefined): DmarcPolicy | undefined {
  return VALID_POLICIES.includes(value as DmarcPolicy) ? (value as DmarcPolicy) : undefined;
}

const POLICY_STRENGTH: Record<DmarcPolicy, number> = { none: 0, quarantine: 1, reject: 2 };

function policyStrength(value: DmarcPolicy): number {
  return POLICY_STRENGTH[value];
}

// True when the generated value is weaker than what's already published —
// used to make a relaxation explicit rather than silent, per the same
// pattern the Policy Builder uses for a dropped pct=. Both sides must already be
// RESOLVED (inheritance applied, see effectiveCurrentPolicies/
// effectiveGeneratedPolicies below) — comparing a blank/unset tag directly
// would treat "inherits reject" as if it were p=none, the weakest possible
// reading, which is wrong whenever the thing it inherits from is itself
// strong. `undefined` current (no usable policy anywhere in the chain, or
// no current record at all) isn't a relaxation of anything.
export function isRelaxation(current: DmarcPolicy | undefined, generated: DmarcPolicy): boolean {
  return current !== undefined && policyStrength(generated) < policyStrength(current);
}

// A tag's effective value plus whether it came from inheritance rather than
// being set explicitly — the warning needs both to describe the change
// accurately. `policy` undefined = no usable value (see below).
export interface ResolvedPolicy {
  policy: DmarcPolicy | undefined;
  inherited: boolean;
}

// RFC 7489/9989 inheritance: sp= with no value published inherits p=; np=
// with no value published inherits sp= (which may itself be inherited from
// p=). A tag that IS present but unrecognized doesn't inherit anything —
// it's unknown, so it can't be the basis of a "this weakens" claim.
function resolveCurrentTag(raw: string | undefined, parent: ResolvedPolicy): ResolvedPolicy {
  if (raw === undefined || raw === "") return { policy: parent.policy, inherited: true };
  return { policy: asPolicy(raw), inherited: false };
}

export function effectiveCurrentPolicies(tags: Record<string, string> | undefined) {
  const p: ResolvedPolicy = { policy: asPolicy(tags?.p), inherited: false };
  const sp = resolveCurrentTag(tags?.sp, p);
  const np = resolveCurrentTag(tags?.np, sp);
  return { p, sp, np };
}

// Same resolution for the record about to be GENERATED — "" (the "Same as
// main/subdomain policy" option) inherits from whichever of policy/sp this
// form's own state is currently set to, not a hardcoded weakest value.
export function effectiveGeneratedPolicies(policy: DmarcPolicy, sp: DmarcPolicy | "", np: DmarcPolicy | "") {
  const p: ResolvedPolicy = { policy, inherited: false };
  const effSp: ResolvedPolicy = sp ? { policy: sp, inherited: false } : { policy, inherited: true };
  const effNp: ResolvedPolicy = np ? { policy: np, inherited: false } : { policy: effSp.policy, inherited: true };
  return { p, sp: effSp, np: effNp };
}

function describe(tag: string, resolved: ResolvedPolicy): string {
  return `${tag}=${resolved.policy}${resolved.inherited ? " (inherited)" : ""}`;
}

export function findRelaxations(currentTags: Record<string, string> | undefined, policy: DmarcPolicy, sp: DmarcPolicy | "", np: DmarcPolicy | ""): string[] {
  const current = effectiveCurrentPolicies(currentTags);
  const proposed = effectiveGeneratedPolicies(policy, sp, np);
  return (["p", "sp", "np"] as const)
    .filter((tag) => {
      const proposedPolicy = proposed[tag].policy;
      return proposedPolicy !== undefined && isRelaxation(current[tag].policy, proposedPolicy);
    })
    .map((tag) => `${describe(tag, current[tag])} → ${describe(tag, proposed[tag])}`);
}
