import { useEffect, useRef } from "react";
import { Alert, WS_URL } from "../api";

/**
 * Opens a persistent WebSocket to the analytics service's alert stream and
 * invokes onAlert for each pushed alert. Reconnects with backoff on drop —
 * alerts are low-volume/push-shaped, so a live socket beats polling here
 * (see README for the full trade-off write-up).
 */
export function useWebSocketAlerts(
  onAlert: (alert: Alert) => void,
  onStatusChange?: (connected: boolean) => void
) {
  const onAlertRef = useRef(onAlert);
  onAlertRef.current = onAlert;
  const onStatusRef = useRef(onStatusChange);
  onStatusRef.current = onStatusChange;

  useEffect(() => {
    let socket: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let closedByCleanup = false;

    function connect() {
      socket = new WebSocket(WS_URL);

      socket.onopen = () => onStatusRef.current?.(true);

      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === "alert") {
            onAlertRef.current(data as Alert);
          }
        } catch {
          // ignore malformed frames
        }
      };

      socket.onclose = () => {
        onStatusRef.current?.(false);
        if (!closedByCleanup) {
          retryTimer = setTimeout(connect, 2000);
        }
      };
    }

    connect();

    return () => {
      closedByCleanup = true;
      if (retryTimer) clearTimeout(retryTimer);
      socket?.close();
    };
  }, []);
}
