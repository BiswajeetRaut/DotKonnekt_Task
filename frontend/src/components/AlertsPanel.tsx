import { Alert } from "../api";

interface Props {
  alerts: Alert[];
  onAck: (id: number) => void;
  connected: boolean;
}

export function AlertsPanel({ alerts, onAck, connected }: Props) {
  return (
    <div className="card">
      <h2>
        Live alerts{" "}
        <span className={`status-dot ${connected ? "status-dot--live" : "status-dot--down"}`} />
      </h2>
      <ul className="alert-list">
        {alerts.map((alert) => (
          <li key={alert.id} className={`alert alert--${alert.severity}`}>
            <div>
              <strong>{alert.severity.toUpperCase()}</strong> — {alert.reason}
              <div className="alert-meta">
                expense #{alert.expense_id} · {new Date(alert.created_at).toLocaleString()}
              </div>
            </div>
            {!alert.acknowledged && <button onClick={() => onAck(alert.id)}>Acknowledge</button>}
          </li>
        ))}
        {alerts.length === 0 && <li>No alerts yet.</li>}
      </ul>
    </div>
  );
}
