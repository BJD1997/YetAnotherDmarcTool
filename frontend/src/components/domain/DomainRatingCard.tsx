import type { DomainRating } from "../../api/dmarc";

const FACTOR_LABELS: Record<string, string> = {
  dmarc_policy: "DMARC policy",
  dmarc_pass_rate: "DMARC pass rate",
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

export default function DomainRatingCard({ rating }: { rating: DomainRating | undefined }) {
  if (!rating) return null;

  if (rating.not_verified) {
    return (
      <div className="card">
        <p className="muted" style={{ margin: 0 }}>Verify this domain to see a rating.</p>
      </div>
    );
  }

  if (rating.insufficient_data || rating.score === null || rating.grade === null) {
    return (
      <div className="card">
        <p className="muted" style={{ margin: 0 }}>
          Not yet rated — no aggregate reports received yet, so a DMARC pass rate can't be computed.
        </p>
        {rating.factors.length > 0 && (
          <div style={{ marginTop: "0.75rem", fontSize: "0.85rem", display: "grid", gap: "0.25rem" }}>
            {rating.factors.map((f) => (
              <div key={f.factor} className="secondary-text">
                {FACTOR_LABELS[f.factor] ?? f.factor}: {f.detail}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  const role = gradeRole(rating.grade);

  return (
    <div className="card" style={{ display: "flex", gap: "1.75rem", alignItems: "flex-start" }}>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          width: "4.5rem",
          height: "4.5rem",
          borderRadius: "50%",
          background: `var(--${role}-wash)`,
          color: `var(--${role}-text)`,
          flexShrink: 0,
        }}
      >
        <div style={{ fontSize: "1.5rem", fontWeight: 700, lineHeight: 1 }}>{rating.grade}</div>
        <div style={{ fontSize: "0.68rem" }} className="num">
          {rating.score}/100
        </div>
      </div>
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.4rem 1.5rem", fontSize: "0.85rem" }}>
        {rating.factors.map((f) => (
          <div key={f.factor} style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
            <span className="secondary-text">{FACTOR_LABELS[f.factor] ?? f.factor}</span>
            <span className="num">{f.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
