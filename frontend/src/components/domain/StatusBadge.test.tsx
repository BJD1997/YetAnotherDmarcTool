import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "./shared";

describe("StatusBadge", () => {
  it("shows a pending check as neutral, neither passed nor failed", () => {
    render(<StatusBadge status="pending" />);
    expect(screen.getByText("pending")).toHaveClass("badge--neutral");
  });
});
