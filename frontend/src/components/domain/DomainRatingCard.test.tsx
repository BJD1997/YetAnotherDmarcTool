import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DomainRating } from "../../api/dmarc";
import DomainRatingCard from "./DomainRatingCard";

const SETUP = {
  score: 84, grade: "B",
  factors: [
    { factor: "spf", weight: 10, score_pct: 60, detail: "warn" },
    { factor: "dkim", weight: 10, score_pct: 100, detail: "pass" },
  ],
};

function rating(overrides: Partial<DomainRating>): DomainRating {
  return {
    not_verified: false, insufficient_data: true, score: null, grade: null, factors: [],
    setup: SETUP, delivery: null, sends_mail: true, ...overrides,
  };
}

describe("DomainRatingCard", () => {
  it("rates the setup of a new domain while waiting for reports", () => {
    render(<DomainRatingCard rating={rating({})} />);

    expect(screen.getByText("84/100")).toBeInTheDocument();
    expect(screen.getByText("1 to improve")).toBeInTheDocument();
    expect(screen.getByText(/waiting for the first reports/)).toBeInTheDocument();
    expect(screen.getByText(/overall grade follows once both are in/)).toBeInTheDocument();

    // Open while it's all there is; it closes on click.
    expect(screen.getByText("SPF")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Setup/ }));
    expect(screen.queryByText("SPF")).not.toBeInTheDocument();
  });

  it("shows the overall grade with both parts", () => {
    render(
      <DomainRatingCard
        rating={rating({
          insufficient_data: false, score: 88, grade: "B",
          delivery: { score: 97, grade: "A", factors: [{ factor: "dmarc_pass_rate", weight: 100, score_pct: 97, detail: "97% of 1,200 messages" }] },
        })}
      />,
    );

    expect(screen.getByText("88/100")).toBeInTheDocument();
    expect(screen.getByText("97/100")).toBeInTheDocument();
    expect(screen.queryByText(/overall grade follows/)).not.toBeInTheDocument();
  });

  it("doesn't wait for reports on a domain that doesn't send mail", () => {
    render(<DomainRatingCard rating={rating({ sends_mail: false })} />);
    expect(screen.getByText(/doesn't send mail/)).toBeInTheDocument();
  });
});
