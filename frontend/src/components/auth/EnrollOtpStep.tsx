import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import type { EnrollOtpConfirmResponse, EnrollOtpResponse } from "../../api/localAuth";

// TOTP enrollment is mandatory for every local-auth account — reused by
// Login.tsx (a local-auth org user who somehow never finished enrolling),
// SetPassword.tsx (a brand-new account's first-ever login), and
// AdminLogin.tsx (a platform admin's first-ever login). All three call
// this right after proving password ownership, while the backend's
// mfa_pending cookie from that step is still active. basePath picks which
// mfa_pending cookie/table the backend checks against — "/auth" for org
// users, "/admin" for platform admins (see platform_admin.py's own
// enroll-otp routes, which mirror auth.py's exactly).
export default function EnrollOtpStep({
  onComplete,
  basePath = "/auth",
}: {
  onComplete: (recoveryCodes: string[]) => void;
  basePath?: "/auth" | "/admin";
}) {
  const [enrollment, setEnrollment] = useState<EnrollOtpResponse | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .post<EnrollOtpResponse>(`${basePath}/enroll-otp`)
      .then(setEnrollment)
      .catch((err) => setError(err instanceof ApiError ? err.message : "couldn't start enrollment"));
  }, [basePath]);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!enrollment) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.post<EnrollOtpConfirmResponse>(`${basePath}/enroll-otp/confirm`, {
        secret: enrollment.secret,
        code,
      });
      onComplete(result.recovery_codes);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "enrollment failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <p className="page-subtitle" style={{ marginBottom: "1rem" }}>
        Scan this with an authenticator app (Google Authenticator, Authy, 1Password, etc.) — a code is required at
        every sign-in.
      </p>
      {!enrollment && !error && <p className="muted">Loading…</p>}
      {enrollment && (
        <>
          <div style={{ display: "flex", justifyContent: "center", marginBottom: "0.75rem" }}>
            <img src={enrollment.qr_code_data_uri} alt="TOTP QR code" width={180} height={180} />
          </div>
          <p className="section-hint" style={{ textAlign: "center", marginBottom: "1rem" }}>
            Can't scan? Enter this key manually: <code>{enrollment.secret}</code>
          </p>
          <form onSubmit={handleSubmit} className="auth-form">
            <input
              className="input"
              inputMode="numeric"
              placeholder="6-digit code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              autoFocus
              required
            />
            {error && (
              <div className="alert alert--critical" style={{ margin: 0 }}>
                {error}
              </div>
            )}
            <button type="submit" className="btn btn--primary" disabled={submitting} style={{ padding: "0.65rem" }}>
              Confirm and continue
            </button>
          </form>
        </>
      )}
      {error && !enrollment && <div className="alert alert--critical">{error}</div>}
    </div>
  );
}
