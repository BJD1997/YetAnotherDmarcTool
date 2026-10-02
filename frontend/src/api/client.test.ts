import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "./client";

afterEach(() => vi.unstubAllGlobals());

describe("API client", () => {
  it("sends cookies and leaves safe GET requests without the CSRF header", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get<{ status: string }>("/health")).resolves.toEqual({ status: "ok" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/health");
    expect(init.credentials).toBe("include");
    expect(new Headers(init.headers).has("X-Requested-With")).toBe(false);
  });

  it("adds JSON and CSRF headers to state-changing requests", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.post("/domains", { name: "example.com" })).resolves.toBeUndefined();

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const headers = new Headers(init.headers);
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ name: "example.com" }));
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(headers.get("X-Requested-With")).toBe("yetanotherdmarctool");
  });

  it("preserves the HTTP status and API detail on failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "domain already exists" }), {
          status: 409,
          statusText: "Conflict",
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const error = await api.post("/domains", { name: "example.com" }).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 409, message: "domain already exists" });
  });

  it("turns a validation error list into readable text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: [
              {
                type: "value_error",
                loc: ["body", "email"],
                msg: "value is not a valid email address: The part after the @-sign is a special-use or reserved name that cannot be used with email.",
              },
              { type: "value_error", loc: ["body", "new_password"], msg: "Value error, password must be at least 12 characters" },
            ],
          }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const error = await api.post("/admin/organizations/o1/users", { email: "qa@x.invalid" }).catch((e: unknown) => e);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(422);
    expect((error as ApiError).message).toBe(
      "value is not a valid email address: The part after the @-sign is a special-use or reserved name that cannot be used with email.; password must be at least 12 characters",
    );
  });
});

