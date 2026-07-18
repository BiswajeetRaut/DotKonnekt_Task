export const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";
export const WS_URL = API_URL.replace(/^http/, "ws") + "/ws/alerts";

export interface Category {
  id: number;
  name: string;
  created_at: string;
}

export interface Expense {
  id: number;
  amount: string;
  category: Category;
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
  source: "rule" | "llm";
  z_score: string | null;
  created_at: string;
  acknowledged: boolean;
}

export interface ExpenseInput {
  amount: number;
  category_id: number;
  description: string | null;
  occurred_at: string;
}

export interface AuthUser {
  id: number;
  email: string;
  created_at: string;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

class ConflictError extends Error {
  constructor() {
    super("This expense was changed by someone else. Refresh and try again.");
    this.name = "ConflictError";
  }
}

class AuthError extends Error {
  constructor(message = "Not authenticated") {
    super(message);
    this.name = "AuthError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    // Send/receive the httpOnly session cookie — required since the API and
    // dashboard are different origins (different ports), even though both
    // are on localhost.
    credentials: "include",
    ...options,
  });
  if (res.status === 409) {
    throw new ConflictError();
  }
  if (res.status === 401) {
    throw new AuthError();
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
  register: (email: string, password: string) =>
    request<AuthUser>("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
  login: (email: string, password: string) =>
    request<AuthUser>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => request<void>("/auth/logout", { method: "POST" }),
  me: () => request<AuthUser>("/auth/me"),

  listCategories: () => request<Category[]>("/categories"),
  createCategory: (name: string) =>
    request<Category>("/categories", { method: "POST", body: JSON.stringify({ name }) }),
  deleteCategory: (id: number) => request<void>(`/categories/${id}`, { method: "DELETE" }),

  listExpenses: () => request<Expense[]>("/expenses"),
  createExpense: (input: ExpenseInput) =>
    request<Expense>("/expenses", { method: "POST", body: JSON.stringify(input) }),
  updateExpense: (id: number, input: ExpenseInput & { version: number }) =>
    request<Expense>(`/expenses/${id}`, { method: "PUT", body: JSON.stringify(input) }),
  deleteExpense: (id: number) => request<void>(`/expenses/${id}`, { method: "DELETE" }),
  listAlerts: () => request<Alert[]>("/alerts"),
  acknowledgeAlert: (id: number) => request<Alert>(`/alerts/${id}/ack`, { method: "PATCH" }),

  getChatHistory: () => request<ChatMessage[]>("/chat/history"),
  sendChatMessage: (message: string) =>
    request<{ reply: string }>("/chat", { method: "POST", body: JSON.stringify({ message }) }),
};

export { AuthError, ConflictError };
