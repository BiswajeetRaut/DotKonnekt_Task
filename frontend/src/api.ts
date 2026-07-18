export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
export const WS_URL = API_URL.replace(/^http/, "ws") + "/ws/alerts";

export interface Expense {
  id: number;
  amount: string;
  category: string;
  description: string | null;
  occurred_at: string;
  created_at: string;
  updated_at: string;
  version: number;
}

export interface Alert {
  id: number;
  expense_id: number;
  reason: string;
  severity: "warning" | "critical";
  z_score: string | null;
  created_at: string;
  acknowledged: boolean;
}

export interface ExpenseInput {
  amount: number;
  category: string;
  description: string | null;
  occurred_at: string;
}

class ConflictError extends Error {
  constructor() {
    super("This expense was changed by someone else. Refresh and try again.");
    this.name = "ConflictError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (res.status === 409) {
    throw new ConflictError();
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  if (res.status === 204) {
    return undefined as T;
  }
  return res.json();
}

export const api = {
  listExpenses: () => request<Expense[]>("/expenses"),
  createExpense: (input: ExpenseInput) =>
    request<Expense>("/expenses", { method: "POST", body: JSON.stringify(input) }),
  updateExpense: (id: number, input: ExpenseInput & { version: number }) =>
    request<Expense>(`/expenses/${id}`, { method: "PUT", body: JSON.stringify(input) }),
  deleteExpense: (id: number) => request<void>(`/expenses/${id}`, { method: "DELETE" }),
  listAlerts: () => request<Alert[]>("/alerts"),
  acknowledgeAlert: (id: number) => request<Alert>(`/alerts/${id}/ack`, { method: "PATCH" }),
};

export { ConflictError };
