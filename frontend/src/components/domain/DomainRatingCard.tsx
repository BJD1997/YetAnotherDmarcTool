import { ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";
import type { DomainRating, RatingFactor, RatingPart } from "../../api/dmarc";

const FACTOR_LABELS: Record<string, string> = {
  dmarc_policy: "DMARC policy",
  dmarc_pass_rate: "DMARC pass",
  spf_pass_rate: "SPF pass",
  dkim_pass_rate: "DKIM pass",
  spf: "SPF",
  dkim: "DKIM",
  mx: "MX",
  starttls: "STARTTLS",
  mta_sts: "MTA-STS",
  dane: "DANE",
  tls_rpt: "TLS-RPT",
};

function gradeRole(grade: string): "good" | "warning" | "critical" {
  if (grade === "A" || grade === "B") return "good";
  if (grade === "C") return "warning";
  return "critical";
}

function Factors({ factors }: { factors: RatingFactor[] }) {
  return (
    <div className="rating-factors">
      {factors.map((f) => (
        <div key={f.factor} style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
          <span className="secondary-text">{FACTOR_LABELS[f.factor] ?? f.factor}</span>
          <span className="num" style={{ whiteSpace: "nowrap" }}>{f.detail}</span>
        </div>
      ))}
    </div>
  );
}

// One half of the rating as a compact line; the breakdown opens on click,
// so the card stays small.
function Part({
  label,
  hint,
  part,
  empty,
  defaultOpen = false,
}: {
  label: string;
  hint: string;
  part: RatingPart | null;
  empty: string;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const toImprove = part ? part.factors.filter((f) => f.weight > 0 && f.score_pct < 100).length : 0;

  return (
    <div className="rating-part">
      <button
        type="button"
        className="rating-part__head"
        onClick={() => setOpen(!open)}
        disabled={!part}
        aria-expanded={part ? open : undefined}
      >
        {part ? open ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : <span style={{ width: 14 }} />}
        <span style={{ fontWeight: 600 }}>{label}</span>
        <span className="muted" style={{ fontSize: "0.8rem" }}>{hint}</span>
        <span style={{ marginLeft: "auto", display: "flex", gap: "0.5rem", alignItems: "center" }}>
          {part ? (
            <>
              {label === "Setup" && (
                <span className="muted" style={{ fontSize: "0.8rem" }}>
                  {toImprove === 0 ? "all checks pass" : `${toImprove} to improve`}
                </span>
              )}
              <span className={`badge badge--${gradeRole(part.grade)}`}>{part.grade}</span>
              <span className="num" style={{ fontSize: "0.85rem", minWidth: "3.5rem", textAlign: "right" }}>
                {part.score}/100
              </span>
            </>
          ) : (
            <span className="muted" style={{ fontSize: "0.8rem" }}>{empty}</span>
          )}
        </span>
      </button>
      {part && open && <Factors factors={part.factors} />}
    </div>
  );
}

export default function DomainRatingCard({ rating }: { rating: DomainRating | undefined }) {
  if (!rating) return null;

  if (rating.not_verified) {
    return (
      <div className="card">
        <p className="muted" style={{ margin: 0 }}>Verify this domain to see a rating.</p>
      </div>
    );
  }

  const rated = !rating.insufficient_data && rating.score !== null && rating.grade !== null;
  const role = rated ? gradeRole(rating.grade!) : null;

  return (
    <div className="card rating-card">
      <div
        className="rating-card__overall"
        style={{ background: role ? `var(--${role}-wash)` : "var(--plane)", color: role ? `var(--${role}-text)` : "var(--ink-muted)" }}
        title="Overall: setup and delivery together"
      >
        <div style={{ fontSize: "1.5rem", fontWeight: 700, lineHeight: 1 }}>{rated ? rating.grade : "–"}</div>
        <div style={{ fontSize: "0.68rem" }} className="num">
          {rated ? `${rating.score}/100` : "overall"}
        </div>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        {/* Open while it's all there is to see, so a new domain's page isn't bare. */}
        <Part label="Setup" hint="DNS records" part={rating.setup} empty="after the first DNS check" defaultOpen={!rating.delivery} />
        <Part
          label="Delivery"
          hint="how your mail fares at receivers"
          part={rating.delivery}
          empty={rating.sends_mail ? "waiting for the first reports (usually 24–48 hours)" : "not rated: this domain doesn't send mail"}
        />
        {!rated && (
          <p className="muted" style={{ margin: "0.5rem 0 0", fontSize: "0.8rem" }}>
            The overall grade follows once both are in.
          </p>
        )}
      </div>
    </div>
  );
}
