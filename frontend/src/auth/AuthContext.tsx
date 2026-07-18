import { createContext, ReactNode, useContext, useEffect, useState } from "react";
import { AuthError, AuthUser, api } from "../api";

interface AuthContextValue {
  user: AuthUser | null;
  status: "loading" | "authenticated" | "anonymous";
  error: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<"loading" | "authenticated" | "anonymous">("loading");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .me()
      .then((u) => {
        setUser(u);
        setStatus("authenticated");
      })
      .catch(() => setStatus("anonymous"));
  }, []);

  async function login(email: string, password: string) {
    setError(null);
    try {
      const u = await api.login(email, password);
      setUser(u);
      setStatus("authenticated");
    } catch (err) {
      setError(err instanceof AuthError ? "Incorrect email or password" : (err as Error).message);
      throw err;
    }
  }

  async function register(email: string, password: string) {
    setError(null);
    try {
      const u = await api.register(email, password);
      setUser(u);
      setStatus("authenticated");
    } catch (err) {
      setError((err as Error).message);
      throw err;
    }
  }

  async function logout() {
    await api.logout();
    setUser(null);
    setStatus("anonymous");
  }

  return (
    <AuthContext.Provider value={{ user, status, error, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
