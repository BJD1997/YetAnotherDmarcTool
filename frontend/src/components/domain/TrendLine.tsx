import { Minus, TrendingDown, TrendingUp } from "lucide-react";
import type { DomainTrend } from "../../api/overview";

/** One line under a domain's stats: is its DMARC pass rate trending? See
 *  backend app/services/rating/trend.py for when a change counts. */
export default function TrendLine({ trend }: { trend: DomainTrend }) {
  const rates =
    trend.recent_pass_pct !== null && trend.baseline_pass_pct !== null
      ? `${trend.recent_pass_pct}% this week, ${trend.baseline_pass_pct}% the 4 weeks before`
      : null;
  const { icon, label, color } =
    trend.state === "down"
      ? { icon: <TrendingDown size={16} />, label: "Trending down", color: "var(--critical-text)" }
      : trend.state === "up"
        ? { icon: <TrendingUp size={16} />, label: "Trending up", color: "var(--good-text)" }
        : trend.state === "stable"
          ? { icon: <Minus size={16} />, label: "Stable", color: "var(--ink-secondary)" }
          : { icon: <Minus size={16} />, label: "Not enough mail yet to show a trend", color: "var(--ink-muted)" };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem", color }} data-testid="trend-line">
      {icon}
      <span>
        <strong style={{ fontWeight: 600 }}>{label}</strong>
        {trend.state !== "insufficient_data" && rates && <span style={{ color: "var(--ink-secondary)" }}>: {rates}</span>}
      </span>
    </div>
  );
}
