import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, ApiError } from "../api/client";
import { useAdminAuth } from "../auth/AdminAuthContext";

export default function AdminLogin() {
  const { refetch } = useAdminAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await api.post("/admin/login", { email, password });
      await refetch();
      navigate("/admin");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="sidebar-brand-mark">D</span>
          <span className="sidebar-brand-text" style={{ fontSize: "1.1rem" }}>
            Platform Admin
          </span>
        </div>
        <form onSubmit={handleSubmit} className="auth-form">
          <input
            className="input"
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
          />
          <input
            className="input"
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
          <button type="submit" className="btn btn--primary" disabled={submitting} style={{ padding: "0.65rem" }}>
            Sign in
          </button>
        </form>
      </div>
    </main>
  );
}
