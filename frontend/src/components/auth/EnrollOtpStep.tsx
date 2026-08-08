import { useEffect, useState, type FormEvent } from "react";
import { api, ApiError } from "../../api/client";
import type { EnrollOtpConfirmResponse, EnrollOtpResponse } from "../../api/localAuth";

// TOTP enrollment is mandatory for every local-auth account — reused by
// both Login.tsx (a local-auth user who somehow never finished enrolling)
// and SetPassword.tsx (a brand-new account's first-ever login). Both call
// this right after proving password ownership, while the backend's
// mfa_pending cookie from that step is still active.
export default function EnrollOtpStep({ onComplete }: { onComplete: (recoveryCodes: string[]) => void }) {
  const [enrollment, setEnrollment] = useState<EnrollOtpResponse | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api
      .post<EnrollOtpResponse>("/auth/enroll-otp")
      .then(setEnrollment)
      .catch((err) => setError(err instanceof ApiError ? err.message : "couldn't start enrollment"));
  }, []);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!enrollment) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.post<EnrollOtpConfirmResponse>("/auth/enroll-otp/confirm", {
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
