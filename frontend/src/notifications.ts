import { Alert } from "./api";

export function isNotificationSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function getNotificationPermission(): NotificationPermission | "unsupported" {
  if (!isNotificationSupported()) return "unsupported";
  return Notification.permission;
}

export async function requestNotificationPermission(): Promise<NotificationPermission> {
  if (!isNotificationSupported()) return "denied";
  return Notification.requestPermission();
}

/**
 * Fires a native browser notification for a newly-pushed alert. Only when
 * the tab isn't focused — if the user is already looking at the dashboard,
 * the in-app AlertsPanel already showed it; a desktop notification on top of
 * that would just be noise (same convention as Slack/Gmail).
 */
export function notifyAlert(alert: Alert): void {
  if (!isNotificationSupported() || Notification.permission !== "granted") return;
  if (document.visibilityState === "visible") return;

  const notification = new Notification(`${alert.severity.toUpperCase()} spending alert`, {
    body: alert.reason,
    tag: `alert-${alert.id}`,
  });
  notification.onclick = () => {
    window.focus();
    notification.close();
  };
}
