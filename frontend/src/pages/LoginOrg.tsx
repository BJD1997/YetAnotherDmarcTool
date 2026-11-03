import { useEffect, useState } from "react";
import { Navigate, useParams } from "react-router-dom";
import { api } from "../api/client";

// /login/<slug>: straight to that organization's sign-in provider.
export default function LoginOrg() {
  const { slug = "" } = useParams();
  const [missing, setMissing] = useState(false);

  useEffect(() => {
    api
      .get<{ url: string }>(`/auth/org/${encodeURIComponent(slug)}`)
      .then(({ url }) => window.location.replace(url))
      .catch(() => setMissing(true));
  }, [slug]);

  if (missing) return <Navigate to="/login?error=organization_not_provisioned" replace />;
  return (
    <p className="muted" style={{ padding: "2rem", textAlign: "center" }}>
      Taking you to your sign-in page…
    </p>
  );
}
