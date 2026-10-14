import { fireEvent, screen, waitFor } from "@testing-library/react";
import { Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "../../api/client";
import type { ActionItem } from "../../api/overview";
import { renderWithAppProviders } from "../../test/render";
import AskAiButton from "./AskAiButton";
import { IssueRow } from "./IssueRow";

vi.mock("../../api/client", () => ({
  api: { get: vi.fn() },
  ApiError: class ApiError extends Error {},
}));
const getMock = vi.mocked(api.get);
const PROMPT = "I run DMARC monitoring for my domain.\nDomain: example.com & co";
const enabled = vi.hoisted(() => ({ value: true }));

beforeEach(() => {
  enabled.value = true;
  getMock.mockReset();
  getMock.mockImplementation(async (url: string) =>
    url.startsWith("/organizations/current") ? { name: "Org", ask_ai_enabled: enabled.value } : { prompt: PROMPT },
  );
});

const HINT = { kind: "sender" as const, subject: "mailer.example" };

describe("AskAiButton", () => {
  it("isn't shown until the organization turns Ask AI on", async () => {
    enabled.value = false;
    renderWithAppProviders(<AskAiButton domainId="d1" hint={HINT} />);
    await waitFor(() => expect(getMock).toHaveBeenCalledWith("/organizations/current"));
    expect(screen.queryByRole("button", { name: /Ask AI/ })).not.toBeInTheDocument();
  });

  it("previews the prompt and links to Claude and ChatGPT with it", async () => {
    renderWithAppProviders(<AskAiButton domainId="d1" hint={HINT} />);
    fireEvent.click(await screen.findByRole("button", { name: /Ask AI/ }));

    expect(await screen.findByLabelText("Prompt")).toHaveValue(PROMPT);
    expect(getMock).toHaveBeenCalledWith("/ask-ai/prompt?kind=sender&domain_id=d1&subject=mailer.example");
  });

  it("passes the issue along", async () => {
    renderWithAppProviders(<AskAiButton domainId="d1" hint={HINT} issue="Sender fails DMARC" />);
    fireEvent.click(await screen.findByRole("button", { name: /Ask AI/ }));
    await screen.findByLabelText("Prompt");
    expect(getMock).toHaveBeenCalledWith(
      "/ask-ai/prompt?kind=sender&domain_id=d1&subject=mailer.example&issue=Sender+fails+DMARC",
    );
    const encoded = encodeURIComponent(PROMPT);
    expect(screen.getByRole("link", { name: "Open in Claude" })).toHaveAttribute("href", `https://claude.ai/new?q=${encoded}`);
    expect(screen.getByRole("link", { name: "Open in ChatGPT" })).toHaveAttribute("href", `https://chatgpt.com/?q=${encoded}`);
  });

  it("copies the prompt", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    renderWithAppProviders(<AskAiButton domainId="d1" hint={HINT} />);
    fireEvent.click(await screen.findByRole("button", { name: /Ask AI/ }));
    fireEvent.click(await screen.findByRole("button", { name: /Copy prompt/ }));

    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PROMPT));
    expect(await screen.findByRole("button", { name: /Copied/ })).toBeInTheDocument();
  });

  it("doesn't follow the Action queue row's link", async () => {
    const item: ActionItem = {
      severity: "warning", category: 1, title: "example.com sender", action_hint: "fix it",
      domain_id: "d1", link_path: "/somewhere", evidence: null, ask_ai: HINT,
    } as ActionItem;
    renderWithAppProviders(
      <Routes>
        <Route path="/" element={<IssueRow item={item} linkTo="/somewhere" />} />
        <Route path="/somewhere" element={<p>navigated</p>} />
      </Routes>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /Ask AI/ }));
    await screen.findByLabelText("Prompt");
    fireEvent.click(screen.getByLabelText("Close"));
    expect(screen.queryByText("navigated")).not.toBeInTheDocument();
  });
});
