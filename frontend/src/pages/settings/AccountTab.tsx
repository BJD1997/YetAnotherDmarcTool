import { useState, type FormEvent } from "react";
import { useOutletContext } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { EnrollOtpResponse } from "../../api/localAuth";
import type { Organization } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import RecoveryCodesStep from "../../components/auth/RecoveryCodesStep";

function errorText(err: unknown, fallback: string) {
  return err instanceof ApiError ? err.message : fallback;
}

function ChangePasswordSection() {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (next !== confirm) {
      setError("New passwords don't match");
      return;
    }
    setSubmitting(true);
    setError(null);
    setDone(false);
    try {
      await api.post("/auth/change-password", { current_password: current, new_password: next });
      setCurrent("");
      setNext("");
      setConfirm("");
      setDone(true);
    } catch (err) {
      setError(errorText(err, "couldn't change password"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="auth-form" style={{ maxWidth: "24rem" }}>
      <input
        className="input"
        type="password"
        autoComplete="current-password"
        placeholder="Current password"
        value={current}
        onChange={(e) => setCurrent(e.target.value)}
        required
      />
      <input
        className="input"
        type="password"
        autoComplete="new-password"
        placeholder="New password (at least 12 characters)"
        value={next}
        onChange={(e) => setNext(e.target.value)}
        minLength={12}
        required
      />
      <input
        className="input"
        type="password"
        autoComplete="new-password"
        placeholder="Confirm new password"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
        required
      />
      {error && (
        <div className="alert alert--critical" style={{ margin: 0 }}>
          {error}
        </div>
      )}
      {done && (
        <div className="alert alert--good" style={{ margin: 0 }}>
          Password changed. Any other devices you were signed in on have been signed out.
        </div>
      )}
      <button type="submit" className="btn btn--primary btn--sm" disabled={submitting}>
        Change password
      </button>
    </form>
  );
}

type MfaPhase = "idle" | "password" | "scan" | "recovery" | "done";

function ReplaceAuthenticatorSection() {
  const [phase, setPhase] = useState<MfaPhase>("idle");
  const [password, setPassword] = useState("");
  const [enrollment, setEnrollment] = useState<EnrollOtpResponse | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function reset(nextPhase: MfaPhase) {
    setPhase(nextPhase);
    setPassword("");
    setEnrollment(null);
    setCode("");
    setError(null);
  }

  async function handleStart(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      setEnrollment(await api.post<EnrollOtpResponse>("/auth/mfa/reset/start", { current_password: password }));
      setPhase("scan");
    } catch (err) {
      setError(errorText(err, "couldn't start"));
    } finally {
      setSubmitting(false);
    }
  }

  async function handleConfirm(e: FormEvent) {
    e.preventDefault();
    if (!enrollment) return;
    setSubmitting(true);
    setError(null);
    try {
      const result = await api.post<{ recovery_codes: string[] }>("/auth/mfa/reset/confirm", {
        current_password: password,
        secret: enrollment.secret,
        code,
      });
      setRecoveryCodes(result.recovery_codes);
      reset("recovery");
    } catch (err) {
      setError(errorText(err, "couldn't confirm the new authenticator"));
    } finally {
      setSubmitting(false);
    }
  }

  const errorAlert = error && (
    <div className="alert alert--critical" style={{ margin: 0 }}>
      {error}
    </div>
  );

  if (phase === "idle" || phase === "done") {
    return (
      <>
        {phase === "done" && (
          <div className="alert alert--good">
            Your new authenticator is active. The old one and your old recovery codes no longer work.
          </div>
        )}
        <button className="btn btn--secondary btn--sm" onClick={() => reset("password")}>
          Replace authenticator
        </button>
      </>
    );
  }

  if (phase === "password") {
    return (
      <form onSubmit={handleStart} className="auth-form" style={{ maxWidth: "24rem" }}>
        <input
          className="input"
          type="password"
          autoComplete="current-password"
          placeholder="Confirm with your current password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoFocus
          required
        />
        {errorAlert}
        <div className="chip-row">
          <button type="submit" className="btn btn--primary btn--sm" disabled={submitting}>
            Continue
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => reset("idle")}>
            Cancel
          </button>
        </div>
      </form>
    );
  }

  if (phase === "scan" && enrollment) {
    return (
      <div style={{ maxWidth: "24rem" }}>
        <p className="section-hint">
          Scan this with your new authenticator app, then enter the code it shows. Your current authenticator keeps
          working until you confirm.
        </p>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: "0.75rem" }}>
          <img src={enrollment.qr_code_data_uri} alt="TOTP QR code" width={180} height={180} />
        </div>
        <p className="section-hint" style={{ textAlign: "center" }}>
          Can't scan? Enter this key manually: <code>{enrollment.secret}</code>
        </p>
        <form onSubmit={handleConfirm} className="auth-form">
          <input
            className="input"
            inputMode="numeric"
            placeholder="6-digit code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoFocus
            required
          />
          {errorAlert}
          <div className="chip-row">
            <button type="submit" className="btn btn--primary btn--sm" disabled={submitting}>
              Confirm new authenticator
            </button>
            <button type="button" className="btn btn--ghost btn--sm" onClick={() => reset("idle")}>
              Cancel
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div style={{ maxWidth: "24rem" }}>
      <RecoveryCodesStep codes={recoveryCodes} onContinue={() => reset("done")} continueLabel="Done" />
    </div>
  );
}

export default function AccountTab() {
  const { user } = useAuth();
  const org = useOutletContext<Organization | undefined>();

  if (org?.is_demo_read_only) {
    return (
      <section>
        <h3 className="section-title">Sign-in</h3>
        <p className="section-hint">This is a shared demo account — its password and two-factor authentication can't be changed.</p>
      </section>
    );
  }

  if (user?.auth_method !== "local") {
    return (
      <section>
        <h3 className="section-title">Sign-in</h3>
        <p className="section-hint">
          You sign in with Microsoft, so your password and two-factor authentication are managed by your organization's
          Microsoft Entra account — change them there.
        </p>
      </section>
    );
  }

  return (
    <section>
      <h3 className="section-title">Password</h3>
      <p className="section-hint">Changing it signs you out on every other device.</p>
      <ChangePasswordSection />

      <hr className="divider" />
      <h3 className="section-title">Two-factor authentication</h3>
      <p className="section-hint">
        Moving to a new phone or authenticator app? Set it up here — you'll get new recovery codes and be signed out on
        every other device. Lost your authenticator entirely? Sign in with a recovery code, or ask an org admin to reset
        it for you.
      </p>
      <ReplaceAuthenticatorSection />
    </section>
  );
}
