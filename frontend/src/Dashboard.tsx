import { useCallback, useEffect, useState } from "react";
import { Alert, api, Category, Expense, ExpenseInput } from "./api";
import { AlertsPanel } from "./components/AlertsPanel";
import { CategoryManager } from "./components/CategoryManager";
import { ChatPanel } from "./components/ChatPanel";
import { ExpenseForm } from "./components/ExpenseForm";
import { ExpenseTable } from "./components/ExpenseTable";
import { SpendingChart } from "./components/SpendingChart";
import { useAuth } from "./auth/AuthContext";
import { useWebSocketAlerts } from "./hooks/useWebSocketAlerts";

type LoadState = "loading" | "ready" | "error";

export function Dashboard() {
  const { user, logout } = useAuth();
  const [expenses, setExpenses] = useState<Expense[]>([]);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [expensesState, setExpensesState] = useState<LoadState>("loading");
  const [alertsState, setAlertsState] = useState<LoadState>("loading");
  const [categoriesState, setCategoriesState] = useState<LoadState>("loading");
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

  const loadCategories = useCallback(async () => {
    setCategoriesState("loading");
    try {
      const data = await api.listCategories();
      setCategories(data);
      setCategoriesState("ready");
    } catch {
      setCategoriesState("error");
    }
  }, []);

  useEffect(() => {
    loadExpenses();
    loadAlerts();
    loadCategories();
  }, [loadExpenses, loadAlerts, loadCategories]);

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

  async function handleCreateCategory(name: string) {
    await api.createCategory(name);
    await loadCategories();
  }

  async function handleDeleteCategory(id: number) {
    await api.deleteCategory(id);
    await loadCategories();
  }

  return (
    <div className="app">
      <header className="app-header">
        <div>
          <h1>Live Expense Tracker</h1>
          <p className="subtitle">React dashboard · FastAPI · PostgreSQL · anomaly alerts</p>
        </div>
        <div className="header-account">
          <span>{user?.email}</span>
          <button type="button" onClick={logout}>
            Sign out
          </button>
        </div>
      </header>

      <ChatPanel />

      <div className="layout">
        <div className="column">
          {categoriesState === "ready" && (
            <ExpenseForm categories={categories} onCreate={handleCreate} />
          )}
          {expensesState === "loading" && <p>Loading expenses…</p>}
          {expensesState === "error" && (
            <p className="error-text">Couldn't load expenses. <button type="button" onClick={loadExpenses}>Retry</button></p>
          )}
          {expensesState === "ready" && categoriesState === "ready" && (
            <ExpenseTable
              expenses={expenses}
              categories={categories}
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
          {categoriesState === "ready" && (
            <CategoryManager
              categories={categories}
              onCreate={handleCreateCategory}
              onDelete={handleDeleteCategory}
            />
          )}
        </div>
      </div>
    </div>
  );
}
