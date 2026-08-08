import { useMemo, useState, type MouseEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { TrendPoint } from "../../api/overview";

const SERIES = [
  { key: "dmarc_pass_pct", label: "DMARC pass", color: "var(--cat-1)" },
  { key: "spf_aligned_pct", label: "SPF aligned", color: "var(--cat-2)" },
  { key: "dkim_aligned_pct", label: "DKIM aligned", color: "var(--cat-3)" },
  { key: "rejected_pct", label: "Rejected", color: "var(--cat-4)" },
] as const;

type SeriesKey = (typeof SERIES)[number]["key"];

interface Row {
  date: string;
  total: number;
  dmarc_pass_pct: number;
  spf_aligned_pct: number;
  dkim_aligned_pct: number;
  rejected_pct: number;
}

function round1(n: number): number {
  return Math.round(n * 10) / 10;
}

function toRows(points: TrendPoint[]): Row[] {
  return points.map((p) => ({
    date: p.date,
    total: p.total,
    dmarc_pass_pct: p.total ? round1((p.dmarc_pass / p.total) * 100) : 0,
    spf_aligned_pct: p.total ? round1((p.spf_aligned / p.total) * 100) : 0,
    dkim_aligned_pct: p.total ? round1((p.dkim_aligned / p.total) * 100) : 0,
    rejected_pct: p.total ? round1((p.rejected / p.total) * 100) : 0,
  }));
}

const WIDTH = 720;
const HEIGHT = 200;
const PAD = { top: 16, right: 16, bottom: 28, left: 34 };

export default function TrendChart({ domainId, days }: { domainId: string | null; days: number }) {
  const [tableView, setTableView] = useState(false);

  const { data, isLoading } = useQuery({
    queryKey: ["dmarc-trend", domainId, days],
    queryFn: () => api.get<TrendPoint[]>(`/dmarc/trend?days=${days}${domainId ? `&domain_id=${domainId}` : ""}`),
  });

  const rows = useMemo(() => toRows(data ?? []), [data]);

  return (
    <div className="card">
      <div className="card-header">
        <h3>Authentication trend</h3>
        {rows.length > 0 && (
          <button className="btn btn--ghost btn--sm" onClick={() => setTableView((v) => !v)}>
            {tableView ? "Show chart" : "Show table"}
          </button>
        )}
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && rows.length === 0 && <p className="empty-state">No aggregate reports in this range.</p>}
      {!isLoading && rows.length > 0 && (tableView ? <TrendTable rows={rows} /> : <TrendSvg rows={rows} />)}
    </div>
  );
}

function TrendTable({ rows }: { rows: Row[] }) {
  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Messages</th>
            {SERIES.map((s) => (
              <th key={s.key}>{s.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.date}>
              <td>{r.date}</td>
              <td className="num">{r.total.toLocaleString()}</td>
              {SERIES.map((s) => (
                <td key={s.key} className="num">
                  {r[s.key]}%
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TrendSvg({ rows }: { rows: Row[] }) {
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const innerWidth = WIDTH - PAD.left - PAD.right;
  const innerHeight = HEIGHT - PAD.top - PAD.bottom;

  const xFor = (i: number) => PAD.left + (rows.length === 1 ? innerWidth / 2 : (i / (rows.length - 1)) * innerWidth);
  const yFor = (pct: number) => PAD.top + innerHeight - (pct / 100) * innerHeight;

  function pathFor(key: SeriesKey): string {
    return rows.map((r, i) => `${i === 0 ? "M" : "L"}${xFor(i)},${yFor(r[key])}`).join(" ");
  }

  function handleMove(e: MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * WIDTH;
    const idx = Math.round(((relX - PAD.left) / innerWidth) * (rows.length - 1));
    setHoverIndex(Math.max(0, Math.min(rows.length - 1, idx)));
  }

  const gridLines = [0, 25, 50, 75, 100];
  const hovered = hoverIndex !== null ? rows[hoverIndex] : null;
  const labelStride = Math.max(1, Math.ceil(rows.length / 8));

  return (
    <div>
      <div className="chip-row" style={{ marginBottom: "0.75rem" }}>
        {SERIES.map((s) => (
          <span
            key={s.key}
            className="chip-row"
            style={{ gap: "0.35rem", fontSize: "0.8rem", color: "var(--ink-secondary)" }}
          >
            <span
              style={{ width: 10, height: 10, borderRadius: 2, background: s.color, display: "inline-block" }}
            />
            {s.label}
          </span>
        ))}
      </div>
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        style={{ width: "100%", height: "auto", overflow: "visible", display: "block" }}
        onMouseMove={handleMove}
        onMouseLeave={() => setHoverIndex(null)}
      >
        {gridLines.map((g) => (
          <g key={g}>
            <line x1={PAD.left} x2={WIDTH - PAD.right} y1={yFor(g)} y2={yFor(g)} stroke="var(--border)" strokeWidth={1} />
            <text x={PAD.left - 8} y={yFor(g)} textAnchor="end" dominantBaseline="middle" fontSize={10} fill="var(--ink-muted)">
              {g}
            </text>
          </g>
        ))}

        {rows.map((r, i) =>
          i % labelStride === 0 ? (
            <text key={r.date} x={xFor(i)} y={HEIGHT - 8} textAnchor="middle" fontSize={10} fill="var(--ink-muted)">
              {r.date.slice(5)}
            </text>
          ) : null,
        )}

        {SERIES.map((s) => (
          <path key={s.key} d={pathFor(s.key)} fill="none" stroke={s.color} strokeWidth={2} />
        ))}

        {hovered && hoverIndex !== null && (
          <line
            x1={xFor(hoverIndex)}
            x2={xFor(hoverIndex)}
            y1={PAD.top}
            y2={HEIGHT - PAD.bottom}
            stroke="var(--border-strong)"
            strokeWidth={1}
            strokeDasharray="3,3"
          />
        )}
      </svg>
      {hovered && (
        <div className="table-wrap" style={{ marginTop: "0.5rem" }}>
          <table className="table">
            <tbody>
              <tr>
                <td className="muted">{hovered.date}</td>
                <td className="num">{hovered.total.toLocaleString()} msgs</td>
                {SERIES.map((s) => (
                  <td key={s.key} className="num" style={{ color: s.color }}>
                    {hovered[s.key]}%
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
