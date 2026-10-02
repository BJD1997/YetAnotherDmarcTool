import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import TrendLine from "./TrendLine";

const trend = (state: "up" | "stable" | "down" | "insufficient_data") => ({
  state,
  recent_pass_pct: state === "insufficient_data" ? null : 91.2,
  baseline_pass_pct: state === "insufficient_data" ? null : 99.1,
  computed_at: null,
});

describe("TrendLine", () => {
  it("says which way and by how much", () => {
    render(<TrendLine trend={trend("down")} />);
    expect(screen.getByTestId("trend-line")).toHaveTextContent("Trending down: 91.2% this week, 99.1% the 4 weeks before");
  });

  it("shows stable and up", () => {
    const { rerender } = render(<TrendLine trend={trend("stable")} />);
    expect(screen.getByText("Stable")).toBeInTheDocument();
    rerender(<TrendLine trend={trend("up")} />);
    expect(screen.getByText("Trending up")).toBeInTheDocument();
  });

  it("says when there isn't enough mail yet", () => {
    render(<TrendLine trend={trend("insufficient_data")} />);
    expect(screen.getByTestId("trend-line")).toHaveTextContent("Not enough mail yet to show a trend");
  });
});
