// GET /notifications — see backend app/routers/notifications.py.
export interface AppNotification {
  id: string;
  kind: "domain_detected" | "dkim_selector_detected";
  title: string;
  detail: string;
  link_path: string;
  created_at: string;
  resolved_at: string | null;
  resolved_reason: "handled" | "dismissed" | null;
}
