const CSRF_HEADER = "X-Requested-With";
const CSRF_HEADER_VALUE = "yetanotherdmarctool";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// FastAPI sends `detail` as a string for errors the app raises itself, but
// as a list of {loc, msg, ...} objects when request validation fails (a
// malformed email, a too-short password) — flatten that to readable text
// instead of letting it stringify to "[object Object]".
function errorText(detail: unknown): string | undefined {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" && "msg" in item ? String(item.msg) : null))
      .filter((msg): msg is string => !!msg)
      .map((msg) => msg.replace(/^Value error, /, ""));
    return messages.length ? messages.join("; ") : undefined;
  }
  return undefined;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers = new Headers(init.headers);
  if (method !== "GET") {
    headers.set(CSRF_HEADER, CSRF_HEADER_VALUE);
  }
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`/api${path}`, { ...init, method, headers, credentials: "include" });

  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = errorText(body.detail) ?? detail;
    } catch {
      // ignore non-JSON error bodies
    }
    throw new ApiError(res.status, detail);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: "PUT", body: body !== undefined ? JSON.stringify(body) : undefined }),
  delete: <T>(path: string) => request<T>(path, { method: "DELETE" }),
};
