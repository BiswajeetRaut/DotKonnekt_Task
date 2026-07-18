import { AuthPage } from "./auth/AuthPage";
import { useAuth } from "./auth/AuthContext";
import { Dashboard } from "./Dashboard";

export default function App() {
  const { status } = useAuth();

  if (status === "loading") {
    return (
      <div className="app-loading">
        <p>Loading…</p>
      </div>
    );
  }

  if (status === "anonymous") {
    return <AuthPage />;
  }

  return <Dashboard />;
}
