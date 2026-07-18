import { useCallback, useEffect, useState } from "react";
import { Alert, api, Expense, ExpenseInput } from "./api";
import { AlertsPanel } from "./components/AlertsPanel";
import { ExpenseForm } from "./components/ExpenseForm";
import { ExpenseTable } from "./components/ExpenseTable";
import { SpendingChart } from "./components/SpendingChart";
import { useWebSocketAlerts } from "./hooks/useWebSocketAlerts";

type LoadState = "loading" | "ready" | "error";

export default function App() {
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [expensesState, setExpensesState] = useState<LoadState>("loading");
  const [alertsState, setAlertsState] = useState<LoadState>("loading");
  const [wsConnected, setWsConnected] = useState(false);

  const loadExpenses = useCallback(async () => {
    setExpensesState("loading");
    try {
      const data = await api.listExpenses();
      setExpenses(data);
      setExpensesState("ready");
    } catch {
      setExpensesState("error");
    }
  }, []);

  const loadAlerts = useCallback(async () => {
    setAlertsState("loading");
    try {
      const data = await api.listAlerts();
      setAlerts(data);
      setAlertsState("ready");
    } catch {
      setAlertsState("error");
    }
  }, []);

  useEffect(() => {
    loadExpenses();
    loadAlerts();
  }, [loadExpenses, loadAlerts]);

  useWebSocketAlerts(
    (alert) => {
      setAlerts((prev) => [alert, ...prev.filter((a) => a.id !== alert.id)]);
    },
    setWsConnected
  );

  async function handleCreate(input: ExpenseInput) {
    await api.createExpense(input);
    await loadExpenses();
    // Give the background anomaly check a beat, then refresh alerts too, in
    // case the WebSocket push races with this refetch.
    setTimeout(loadAlerts, 500);
  }

  async function handleUpdate(id: number, input: ExpenseInput & { version: number }) {
    await api.updateExpense(id, input);
    await loadExpenses();
  }

  async function handleDelete(id: number) {
    await api.deleteExpense(id);
    await loadExpenses();
  }

  async function handleAck(id: number) {
    await api.acknowledgeAlert(id);
    setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, acknowledged: true } : a)));
  }

  return (
    <div className="app">
      <header>
        <h1>Live Expense Tracker</h1>
        <p className="subtitle">React dashboard · FastAPI · PostgreSQL · anomaly alerts</p>
      </header>

      <div className="layout">
        <div className="column">
          <ExpenseForm onCreate={handleCreate} />
          {expensesState === "loading" && <p>Loading expenses…</p>}
          {expensesState === "error" && (
            <p className="error-text">Couldn't load expenses. <button type="button" onClick={loadExpenses}>Retry</button></p>
          )}
          {expensesState === "ready" && (
            <ExpenseTable
              expenses={expenses}
              onUpdate={handleUpdate}
              onDelete={handleDelete}
              onConflict={loadExpenses}
            />
          )}
        </div>

        <div className="column">
          {expensesState === "ready" && <SpendingChart expenses={expenses} />}
          {alertsState === "loading" && <p>Loading alerts…</p>}
          {alertsState === "error" && (
            <p className="error-text">Couldn't load alerts. <button type="button" onClick={loadAlerts}>Retry</button></p>
          )}
          {alertsState === "ready" && (
            <AlertsPanel alerts={alerts} onAck={handleAck} connected={wsConnected} />
          )}
        </div>
      </div>
    </div>
  );
}
