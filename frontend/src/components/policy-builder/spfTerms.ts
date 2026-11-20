// What one part of an SPF record means, in plain words — for the SPF check's
// breakdown and the SPF record builder.

function qualifierOf(term: string): { qualifier: string; body: string } {
  const first = term.charAt(0);
  return "+-~?".includes(first) && term.length > 1 ? { qualifier: first, body: term.slice(1) } : { qualifier: "+", body: term };
}

const ALL_MEANING: Record<string, string> = {
  "-": "everything else fails SPF (hardfail)",
  "~": "everything else is marked suspicious (softfail)",
  "?": "says nothing about everything else (neutral)",
  "+": "allows every server in the world — anyone can send as you",
};

export function describeSpfTerm(term: string, service: string | null): string {
  const { qualifier, body } = qualifierOf(term);
  // "include:x", "redirect=x", "a", "a:host", "a/24": the mechanism is
  // everything before the first ":", "=" or "/".
  const sep = body.search(/[:=/]/);
  const mech = (sep === -1 ? body : body.slice(0, sep)).toLowerCase();
  const hasValue = sep !== -1 && body[sep] !== "/";
  const value = hasValue ? body.slice(sep + 1) : "";
  let meaning: string;
  switch (mech) {
    case "all":
      return ALL_MEANING[qualifier];
    case "include":
      meaning = service ? `${service}'s mail servers` : `the servers listed by ${value}`;
      break;
    case "redirect":
      return service ? `uses ${service}'s SPF record instead of its own` : `uses the SPF record of ${value} instead of its own`;
    case "ip4":
    case "ip6":
      meaning = value.includes("/") ? `the address range ${value}` : `the address ${value}`;
      break;
    case "a":
      meaning = hasValue ? `the address of ${value}` : "the address of the domain itself";
      break;
    case "mx":
      meaning = hasValue ? `the incoming mail servers (MX) of ${value}` : "your own incoming mail servers (MX)";
      break;
    case "ptr":
      return "matches by reverse DNS (outdated, avoid)";
    case "exists":
      return `allows a server when ${value} exists (advanced)`;
    case "exp":
      return "the explanation shown to rejected senders";
    default:
      return "unknown part";
  }
  if (qualifier === "-") return `refuses ${meaning}`;
  if (qualifier === "~") return `marks ${meaning} as suspicious`;
  if (qualifier === "?") return `says nothing about ${meaning}`;
  return `allows ${meaning}`;
}

export function lookupsLabel(lookups: number): string {
  return lookups === 1 ? "1 lookup" : `${lookups} lookups`;
}

const HOSTNAME = /^(?=.{1,253}$)[A-Za-z0-9_-]+(\.[A-Za-z0-9_-]+)*\.[A-Za-z]{2,63}$/;
const IPV4 = /^(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)(\.(25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}(\/(3[0-2]|[12]?\d))?$/;
const IPV6_PREFIX = /^(12[0-8]|1[01]\d|[1-9]?\d)$/;

function isIpv6(value: string): boolean {
  const [address, prefix, ...rest] = value.split("/");
  if (rest.length > 0 || (prefix !== undefined && !IPV6_PREFIX.test(prefix))) return false;
  if (!/^[0-9A-Fa-f:]+$/.test(address)) return false;
  const halves = address.split("::");
  if (halves.length > 2) return false;
  const groups = halves.flatMap((h) => (h === "" ? [] : h.split(":")));
  if (groups.some((g) => !/^[0-9A-Fa-f]{1,4}$/.test(g))) return false;
  return halves.length === 2 ? groups.length < 8 : groups.length === 8;
}

export function isHostname(value: string): boolean {
  return HOSTNAME.test(value);
}

// "ip4:…" / "ip6:…" for an address or range someone typed, or null when it
// isn't one.
export function ipTerm(value: string): string | null {
  const v = value.trim();
  if (IPV4.test(v)) return `ip4:${v}`;
  if (isIpv6(v)) return `ip6:${v.toLowerCase()}`;
  return null;
}

export interface ParsedSpf {
  includes: string[];
  ips: string[];
  a: boolean;
  mx: boolean;
  // Everything else (a:host, mx:host, exists:, ptr, redirect=, exp=, parts
  // with an unusual qualifier) — kept as written.
  others: string[];
  all: "-all" | "~all";
}

export function parseSpf(record: string | null): ParsedSpf {
  const parsed: ParsedSpf = { includes: [], ips: [], a: false, mx: false, others: [], all: "~all" };
  if (!record) return parsed;
  for (const term of record.split(/\s+/).slice(1)) {
    const lower = term.toLowerCase();
    if (lower === "-all" || lower === "~all") parsed.all = lower;
    else if (/^[?+]?all$/.test(lower)) continue; // ?all/+all: replaced by the qualifier picked in the builder
    else if (lower.startsWith("include:") || lower.startsWith("+include:")) parsed.includes.push(lower.slice(lower.indexOf(":") + 1));
    else if (/^\+?ip[46]:/.test(lower)) parsed.ips.push(lower.replace(/^\+/, ""));
    else if (lower === "a" || lower === "+a") parsed.a = true;
    else if (lower === "mx" || lower === "+mx") parsed.mx = true;
    else if (term) parsed.others.push(term);
  }
  return parsed;
}
