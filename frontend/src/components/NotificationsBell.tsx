import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Bell, X } from "lucide-react";

import { useAuth } from "../auth/AuthContext";
import { useDismissNotification, useNotifications, useNotificationsCount } from "../hooks/useNotifications";

function age(iso: string): string {
  const minutes = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 48 * 60) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / (24 * 60))}d ago`;
}

/** The bell in the sidebar header / mobile top bar: a red dot while any
 *  notification is open, a panel listing them. Everyone sees it; org admins
 *  can also dismiss. `align` keeps the panel on screen: "left" opens it
 *  towards the page from the sidebar, "right" from the top bar's right edge. */
export default function NotificationsBell({ align = "left", onNavigate }: { align?: "left" | "right"; onNavigate?: () => void }) {
  const { user } = useAuth();
  const canDismiss = user?.role === "org_admin";
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const { data: count } = useNotificationsCount();
  const { data: items, isLoading } = useNotifications(open);
  const dismiss = useDismissNotification();
  const openCount = count?.open ?? 0;

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    const onClick = (e: MouseEvent) => {
      if (wrapperRef.current && !wrapperRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onClick);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onClick);
    };
  }, [open]);

  const openItems = (items ?? []).filter((n) => !n.resolved_at);
  const resolvedItems = (items ?? []).filter((n) => n.resolved_at);

  return (
    <div className="notif-bell" ref={wrapperRef}>
      <button
        className="icon-btn"
        aria-label={openCount ? `Notifications, ${openCount} open` : "Notifications"}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Bell size={18} />
        {openCount > 0 && <span className="notif-dot" data-testid="notif-dot" />}
      </button>
      {open && (
        <div className={`notif-panel notif-panel--${align}`} role="dialog" aria-label="Notifications">
          <div className="notif-panel-header">
            <strong>Notifications</strong>
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              {openCount ? `${openCount} open` : "All caught up"}
            </span>
          </div>
          {isLoading && <p className="muted notif-empty">Loading…</p>}
          {!isLoading && openItems.length === 0 && <p className="muted notif-empty">Nothing new in your reports.</p>}
          <ul className="notif-list">
            {openItems.map((n) => (
              <li key={n.id} className="notif-item">
                <Link
                  to={n.link_path}
                  className="notif-link"
                  onClick={() => {
                    setOpen(false);
                    onNavigate?.();
                  }}
                >
                  <span className="notif-title">{n.title}</span>
                  <span className="notif-detail">
                    {n.detail} · {age(n.created_at)}
                  </span>
                </Link>
                {canDismiss && (
                  <button
                    className="icon-btn notif-dismiss"
                    aria-label={`Dismiss: ${n.title}`}
                    title="Dismiss"
                    disabled={dismiss.isPending}
                    onClick={() => dismiss.mutate(n.id)}
                  >
                    <X size={14} />
                  </button>
                )}
              </li>
            ))}
          </ul>
          {resolvedItems.length > 0 && (
            <details className="notif-resolved">
              <summary>Recently handled ({resolvedItems.length})</summary>
              <ul className="notif-list">
                {resolvedItems.map((n) => (
                  <li key={n.id} className="notif-item notif-item--resolved">
                    <span className="notif-title">{n.title}</span>
                    <span className="notif-detail">{n.resolved_reason === "dismissed" ? "Dismissed" : "Handled"}</span>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
