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

function gradeColor(grade: string): [string, string] {
  if (grade === "A" || grade === "B") return ["#dcfce7", "#166534"];
  if (grade === "C") return ["#fef3c7", "#92400e"];
  return ["#fee2e2", "#991b1b"];
}

export default function DomainRatingCard({ rating }: { rating: DomainRating | undefined }) {
  if (!rating) return null;

  if (rating.not_verified) {
    return (
      <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: "1rem", marginBottom: "1.5rem" }}>
        <p style={{ color: "#92400e", margin: 0 }}>Verify this domain to see a rating.</p>
      </div>
    );
  }

  if (rating.insufficient_data || rating.score === null || rating.grade === null) {
    return (
      <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: "1rem", marginBottom: "1.5rem" }}>
        <p style={{ color: "#6b7280", margin: 0 }}>
          Not yet rated — no aggregate reports received yet, so a DMARC pass rate can't be computed.
        </p>
        {rating.factors.length > 0 && (
          <div style={{ marginTop: "0.75rem", fontSize: "0.85rem" }}>
            {rating.factors.map((f) => (
              <div key={f.factor} style={{ color: "#4b5563" }}>
                {FACTOR_LABELS[f.factor] ?? f.factor}: {f.detail}
              </div>
            ))}
          </div>
        )}
      </div>
    );
  }

  const [background, color] = gradeColor(rating.grade);

  return (
    <div
      style={{
        display: "flex",
        gap: "1.5rem",
        alignItems: "flex-start",
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        padding: "1rem",
        marginBottom: "1.5rem",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          width: "4.5rem",
          height: "4.5rem",
          borderRadius: "50%",
          background,
          color,
          flexShrink: 0,
        }}
      >
        <div style={{ fontSize: "1.5rem", fontWeight: 700, lineHeight: 1 }}>{rating.grade}</div>
        <div style={{ fontSize: "0.7rem" }}>{rating.score}/100</div>
      </div>
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.35rem 1.5rem", fontSize: "0.85rem" }}>
        {rating.factors.map((f) => (
          <div key={f.factor} style={{ display: "flex", justifyContent: "space-between" }}>
            <span style={{ color: "#4b5563" }}>{FACTOR_LABELS[f.factor] ?? f.factor}</span>
            <span style={{ color: "#111827" }}>{f.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
