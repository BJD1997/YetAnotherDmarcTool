// From/To date pickers for the log pages (sign-ins, job runs). Dates are the
// viewer's local days: "to 5 Oct" includes all of 5 Oct in their timezone.

export interface DateRange {
  from: string; // yyyy-mm-dd, or "" for open-ended
  to: string;
}

export const EMPTY_DATE_RANGE: DateRange = { from: "", to: "" };

/** Adds `from`/`to` (ISO timestamps, `to` exclusive) to the query params. */
export function setDateRangeParams(params: URLSearchParams, range: DateRange): void {
  if (range.from) params.set("from", new Date(`${range.from}T00:00:00`).toISOString());
  if (range.to) {
    const end = new Date(`${range.to}T00:00:00`);
    end.setDate(end.getDate() + 1);
    params.set("to", end.toISOString());
  }
}

export function DateRangeFilter({ value, onChange }: { value: DateRange; onChange: (range: DateRange) => void }) {
  return (
    <>
      <label className="muted" style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.82rem" }}>
        From
        <input
          type="date"
          className="input"
          value={value.from}
          max={value.to || undefined}
          onChange={(e) => onChange({ ...value, from: e.target.value })}
        />
      </label>
      <label className="muted" style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.82rem" }}>
        To
        <input
          type="date"
          className="input"
          value={value.to}
          min={value.from || undefined}
          onChange={(e) => onChange({ ...value, to: e.target.value })}
        />
      </label>
      {(value.from || value.to) && (
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => onChange(EMPTY_DATE_RANGE)}>
          Clear dates
        </button>
      )}
    </>
  );
}
