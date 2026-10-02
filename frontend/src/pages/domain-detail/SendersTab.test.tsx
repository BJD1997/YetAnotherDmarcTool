import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { renderWithAppProviders } from "../../test/render";
import SendersTab from "./SendersTab";

const outlet = vi.hoisted(() => ({ domain: { id: "d1", name: "example.com", verification_status: "pending" } }));
vi.mock("react-router-dom", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react-router-dom")>()),
  useOutletContext: () => outlet.domain,
}));

describe("SendersTab", () => {
  it("waits for verification before listing senders", () => {
    renderWithAppProviders(<SendersTab />);
    expect(screen.getByText("Senders show up once this domain is verified.")).toBeInTheDocument();
  });
});
