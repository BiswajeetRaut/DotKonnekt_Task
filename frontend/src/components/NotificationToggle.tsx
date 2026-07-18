import { useState } from "react";
import {
  getNotificationPermission,
  isNotificationSupported,
  requestNotificationPermission,
} from "../notifications";

export function NotificationToggle() {
  const [permission, setPermission] = useState(getNotificationPermission());

  if (!isNotificationSupported()) return null;

  async function enable() {
    const result = await requestNotificationPermission();
    setPermission(result);
  }

  if (permission === "granted") {
    return <span className="notif-status notif-status--on">🔔 Notifications on</span>;
  }
  if (permission === "denied") {
    return (
      <span className="notif-status notif-status--off">
        🔕 Notifications blocked (enable in browser settings)
      </span>
    );
  }
  return (
    <button type="button" className="notif-enable-button" onClick={enable}>
      Enable browser notifications
    </button>
  );
}
