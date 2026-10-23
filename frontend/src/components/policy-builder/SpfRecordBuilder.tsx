import { useEffect, useState } from "react";
import { X, Copy, RefreshCw, Check, Plus } from "lucide-react";
import { useClipboardFeedback } from "../../hooks/useClipboardFeedback";
import { useSpfBuilder, useSpfEvaluation } from "../../hooks/usePolicyBuilders";
import { describeSpfTerm, ipTerm, isHostname, lookupsLabel, parseSpf } from "./spfTerms";

// A TXT string holds at most 255 characters; longer values must be split
// into several quoted strings in one record, which some DNS panels do for
// you and others don't.
const TXT_STRING_MAX = 255;

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return debounced;
}

export default function SpfRecordBuilder({ domainId, domainName, onClose }: { domainId: string; domainName: string; onClose: () => void }) {
  const { data, isLoading, refetch, isFetching } = useSpfBuilder(domainId);
  const { copied, copy } = useClipboardFeedback();

  const [providers, setProviders] = useState<string[]>([]);
  const [includes, setIncludes] = useState<string[]>([]);
  const [keptIncludes, setKeptIncludes] = useState<string[]>([]);
  const [ips, setIps] = useState<string[]>([]);
  const [useA, setUseA] = useState(false);
  const [useMx, setUseMx] = useState(false);
  const [others, setOthers] = useState<string[]>([]);
  const [keptOthers, setKeptOthers] = useState<string[]>([]);
  const [all, setAll] = useState<"-all" | "~all">("~all");
  const [newInclude, setNewInclude] = useState("");
  const [newIp, setNewIp] = useState("");
  const [inputError, setInputError] = useState<string | null>(null);

  // Seed once, when data first loads: the published record, split into the
  // services the builder knows and everything else (kept as written).
  useEffect(() => {
    if (!data) return;
    const parsed = parseSpf(data.current_record);
    const known = new Map(data.providers.map((p) => [p.include, p.key]));
    setProviders(parsed.includes.filter((i) => known.has(i)).map((i) => known.get(i)!));
    const unknown = parsed.includes.filter((i) => !known.has(i));
    setIncludes(unknown);
    setKeptIncludes(unknown);
    setIps(parsed.ips);
    setUseA(parsed.a);
    setUseMx(parsed.mx);
    setOthers(parsed.others);
    setKeptOthers(parsed.others);
    setAll(parsed.all);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data === undefined]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const providerIncludes = (data?.providers ?? []).filter((p) => providers.includes(p.key)).map((p) => `include:${p.include}`);
  const hasRedirect = keptOthers.some((t) => t.toLowerCase().startsWith("redirect="));
  // With redirect=, the other domain's record decides about everything
  // else; an "all" here would switch the redirect off.
  const terms = [
    ...ips,
    ...(useA ? ["a"] : []),
    ...(useMx ? ["mx"] : []),
    ...providerIncludes,
    ...keptIncludes.map((i) => `include:${i}`),
    ...keptOthers,
    ...(hasRedirect ? [] : [all]),
  ];
  const generated = ["v=spf1", ...terms].join(" ");
  const hasSenders = terms.some((t) => !/^[-~?+]?all$/i.test(t));
  const evaluation = useSpfEvaluation(domainId, useDebounced(data ? generated : null, 400));
  const counted = evaluation.data;
  const isCurrent = data?.current_record === generated;
  const suggestions = (data?.suggestions ?? []).filter((s) => !providers.includes(s.provider_key));

  function toggleProvider(key: string, on: boolean) {
    setProviders((prev) => (on ? [...prev, key] : prev.filter((k) => k !== key)));
  }

  function addInclude() {
    const value = newInclude.trim().toLowerCase().replace(/^include:/, "");
    if (!isHostname(value)) {
      setInputError(`"${newInclude.trim()}" isn't a domain name. Use the value your mail service gives, e.g. spf.example.net.`);
      return;
    }
    const provider = data?.providers.find((p) => p.include === value);
    if (provider) toggleProvider(provider.key, true);
    else if (!includes.includes(value)) {
      setIncludes((prev) => [...prev, value]);
      setKeptIncludes((prev) => [...prev, value]);
    }
    setNewInclude("");
    setInputError(null);
  }

  function addIp() {
    const term = ipTerm(newIp);
    if (!term) {
      setInputError(`"${newIp.trim()}" isn't an IP address or range, e.g. 192.0.2.10 or 2001:db8::/32.`);
      return;
    }
    if (!ips.includes(term)) setIps((prev) => [...prev, term]);
    setNewIp("");
    setInputError(null);
  }

  const lookupRole = !counted ? "neutral" : counted.lookup_count > counted.limit ? "critical" : counted.lookup_count >= counted.limit - 2 ? "warning" : "good";

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-card" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2 style={{ margin: 0, fontSize: "1.1rem" }}>Build SPF record — {domainName}</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close">
            <X />
          </button>
        </div>
        {isLoading && <p className="muted">Loading…</p>}

        {data && (
          <>
            <p className="section-hint" style={{ marginTop: 0 }}>
              SPF lists the servers allowed to send mail as <code>{domainName}</code>. Pick the services you send
              with; the builder starts from what's published now.
            </p>

            {data.current_record_count > 1 && (
              <div className="alert alert--critical">
                This domain has {data.current_record_count} SPF records. Receivers treat that as an error, so SPF fails
                for all your mail. Replace them all with the one record below.
              </div>
            )}

            {suggestions.map((s) => {
              const provider = data.providers.find((p) => p.key === s.provider_key);
              if (!provider) return null;
              return (
                <div key={s.provider_key} className="alert alert--warning" style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
                  <span style={{ flex: 1 }}>
                    You approved <strong>{s.sender_label}</strong> ({s.messages.toLocaleString()} messages), and it sends
                    with <code>{domainName}</code> as its return address, but SPF doesn't allow it yet.
                  </span>
                  <button className="btn btn--secondary btn--sm" onClick={() => toggleProvider(provider.key, true)}>
                    <Plus />
                    Add {provider.label}
                  </button>
                </div>
              );
            })}

            <div style={{ marginBottom: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Mail services
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(12rem, 1fr))", gap: "0.3rem 0.8rem" }}>
                {data.providers.map((p) => (
                  <label key={p.key} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }} title={`include:${p.include}`}>
                    <input type="checkbox" checked={providers.includes(p.key)} onChange={(e) => toggleProvider(p.key, e.target.checked)} />
                    {p.label}
                  </label>
                ))}
              </div>
            </div>

            <div style={{ marginBottom: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Other services and your own servers
              </div>
              {includes.length + ips.length + others.length > 0 && (
                <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem", marginBottom: "0.5rem" }}>
                  {includes.map((i) => (
                    <label key={i} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                      <input
                        type="checkbox"
                        checked={keptIncludes.includes(i)}
                        onChange={(e) => setKeptIncludes((prev) => (e.target.checked ? [...prev, i] : prev.filter((k) => k !== i)))}
                      />
                      <code>include:{i}</code>
                    </label>
                  ))}
                  {ips.map((ip) => (
                    <div key={ip} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                      <code>{ip}</code>
                      <button className="icon-btn" aria-label={`Remove ${ip}`} onClick={() => setIps((prev) => prev.filter((k) => k !== ip))}>
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                  {others.map((t) => (
                    <label key={t} style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                      <input
                        type="checkbox"
                        checked={keptOthers.includes(t)}
                        onChange={(e) => setKeptOthers((prev) => (e.target.checked ? [...prev, t] : prev.filter((k) => k !== t)))}
                      />
                      <code>{t}</code>
                      <span className="muted" style={{ fontSize: "0.78rem" }}>
                        {describeSpfTerm(t, null)}
                      </span>
                    </label>
                  ))}
                </div>
              )}
              <div className="field-row" style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap" }}>
                <input
                  className="input"
                  style={{ flex: "1 1 12rem", minWidth: 0 }}
                  placeholder="Include, e.g. spf.example.net"
                  value={newInclude}
                  onChange={(e) => setNewInclude(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addInclude()}
                  aria-label="Include to add"
                />
                <button className="btn btn--ghost btn--sm" onClick={addInclude} disabled={!newInclude.trim()}>
                  <Plus />
                  Add include
                </button>
              </div>
              <div className="field-row" style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginTop: "0.4rem" }}>
                <input
                  className="input"
                  style={{ flex: "1 1 12rem", minWidth: 0 }}
                  placeholder="IP address or range, e.g. 192.0.2.10"
                  value={newIp}
                  onChange={(e) => setNewIp(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && addIp()}
                  aria-label="IP address to add"
                />
                <button className="btn btn--ghost btn--sm" onClick={addIp} disabled={!newIp.trim()}>
                  <Plus />
                  Add address
                </button>
              </div>
              {inputError && (
                <p className="section-hint" style={{ color: "var(--critical-text)", marginTop: "0.3rem", marginBottom: 0 }}>
                  {inputError}
                </p>
              )}
              <div className="chip-row" style={{ marginTop: "0.5rem" }}>
                <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                  <input type="checkbox" checked={useA} onChange={(e) => setUseA(e.target.checked)} />
                  The domain's own address (<code>a</code>)
                </label>
                <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                  <input type="checkbox" checked={useMx} onChange={(e) => setUseMx(e.target.checked)} />
                  Your incoming mail servers (<code>mx</code>)
                </label>
              </div>
            </div>

            <div style={{ marginBottom: "1rem" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Everything else
              </div>
              {hasRedirect ? (
                <p className="section-hint" style={{ margin: 0 }}>
                  Your record hands over to another domain with <code>redirect=</code>: that domain's record decides
                  about everything else.
                </p>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "0.3rem" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                    <input type="radio" name="spf-all" checked={all === "~all"} onChange={() => setAll("~all")} />
                    <span>
                      <code>~all</code> softfail: mark it as suspicious. Your DMARC policy decides what happens to it.
                    </span>
                  </label>
                  <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
                    <input type="radio" name="spf-all" checked={all === "-all"} onChange={() => setAll("-all")} />
                    <span>
                      <code>-all</code> hardfail: refuse it. Strictest, but forwarded mail may bounce before DMARC is checked.
                    </span>
                  </label>
                </div>
              )}
            </div>

            <div style={{ marginTop: "1.25rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
              <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
                Generated record
              </div>
              <div
                style={{
                  fontFamily: "monospace",
                  fontSize: "0.82rem",
                  background: "var(--plane)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  padding: "0.6rem 0.75rem",
                  overflowWrap: "anywhere",
                }}
              >
                {generated}
              </div>
              <div className="chip-row" style={{ marginTop: "0.5rem" }}>
                {counted && (
                  <span className={`badge badge--${lookupRole}`}>
                    {counted.capped ? `${counted.lookup_count}+` : counted.lookup_count} of {counted.limit} DNS lookups
                  </span>
                )}
                {counted && counted.void_lookup_count > counted.void_limit && (
                  <span className="badge badge--critical">{counted.void_lookup_count} includes don't exist</span>
                )}
                {isCurrent && <span className="badge badge--good">Same as published</span>}
                {evaluation.isFetching && <span className="muted" style={{ fontSize: "0.8rem" }}>Counting…</span>}
              </div>
              {counted && counted.lookup_count > counted.limit && (
                <p className="section-hint" style={{ color: "var(--critical-text)" }}>
                  Over the limit of {counted.limit}: receivers stop checking and SPF fails for all your mail. Drop
                  services you no longer use, or replace includes with the IP ranges they stand for.
                </p>
              )}
              {counted?.notes.map((n) => (
                <p key={n.message} className="section-hint" style={{ color: n.status === "fail" ? "var(--critical-text)" : "var(--warning-text)" }}>
                  {n.message}
                </p>
              ))}
              {!hasSenders && (
                <p className="section-hint" style={{ color: "var(--warning-text)" }}>
                  This record allows no server at all. That's right for a domain that never sends mail; otherwise pick
                  your mail services above.
                </p>
              )}
              {generated.length > TXT_STRING_MAX && (
                <p className="section-hint" style={{ color: "var(--warning-text)" }}>
                  Longer than {TXT_STRING_MAX} characters: some DNS providers need it split into several quoted parts
                  within one TXT record.
                </p>
              )}
              {counted && counted.terms.length > 0 && (
                <ul style={{ margin: "0.4rem 0 0", padding: "0 0 0 1.1rem", fontSize: "0.8rem", color: "var(--ink-secondary)" }}>
                  {counted.terms.map((t) => (
                    <li key={t.term}>
                      <code>{t.term}</code> — {describeSpfTerm(t.term, t.service)}
                      {t.lookups > 0 && <span className="muted"> ({lookupsLabel(t.lookups)})</span>}
                    </li>
                  ))}
                </ul>
              )}
              <div className="chip-row" style={{ marginTop: "0.6rem" }}>
                <button className="btn btn--primary btn--sm" onClick={() => void copy(generated)}>
                  {copied ? <Check /> : <Copy />}
                  {copied ? "Copied" : "Copy DNS value"}
                </button>
                <button className="btn btn--ghost btn--sm" onClick={() => refetch()} disabled={isFetching}>
                  <RefreshCw />
                  {isFetching ? "Rechecking…" : "Recheck DNS"}
                </button>
              </div>
              <p className="section-hint" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
                Publish this as a TXT record at <code>{domainName}</code> (often written as <code>@</code>).{" "}
                <strong>Replace</strong> the existing <code>v=spf1</code> record rather than adding a second one: a
                domain may only have one. DNS changes can take a few minutes; use "Recheck DNS" after publishing.
              </p>
            </div>

            {data.current_record && (
              <div style={{ marginTop: "1rem" }}>
                <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
                  Current record
                </div>
                <div className="muted" style={{ fontFamily: "monospace", fontSize: "0.78rem", overflowWrap: "anywhere" }}>
                  {data.current_record}
                </div>
              </div>
            )}
            {data.current_record_lookup_error && (
              <p className="section-hint" style={{ marginTop: "0.5rem", color: "var(--warning-text)" }}>
                Couldn't look up the current record right now — try "Recheck DNS".
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
