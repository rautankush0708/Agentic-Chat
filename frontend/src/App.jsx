import { useEffect, useState } from "react";
import AiAgenticChat from "./components/AiAgenticChat/AiAgenticChat.jsx";

const ROLES = ["Admin", "Demand Planner", "Supply Planner", "Category Manager", "Analyst", "Executive"];

function App() {
  const [role, setRole] = useState(() => localStorage.getItem("roleName") || "Demand Planner");

  useEffect(() => {
    localStorage.setItem("roleName", role);
  }, [role]);

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <h1>Demand Forecasting Copilot</h1>
        <label className="role-picker">
          Viewing as
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>
      </header>
      <main className="app-body">
        <AiAgenticChat currentUserRole={role} />
      </main>
    </div>
  );
}

export default App;
