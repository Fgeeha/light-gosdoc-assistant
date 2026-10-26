import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { api, type User } from "./api";
import "./index.css";
import Layout from "./Layout";
import Admin from "./pages/Admin";
import Check from "./pages/Check";
import Letter from "./pages/Letter";
import Login from "./pages/Login";
import Norms from "./pages/Norms";
import Table from "./pages/Table";
import { Spinner } from "./ui";

function App() {
  const [user, setUser] = useState<User | null | undefined>(undefined);

  useEffect(() => {
    api<User>("/auth/me").then(setUser, () => setUser(null));
    const drop = () => setUser(null);
    window.addEventListener("gosdoc:unauthorized", drop);
    return () => window.removeEventListener("gosdoc:unauthorized", drop);
  }, []);

  if (user === undefined)
    return (
      <div className="grid min-h-dvh place-items-center text-muted">
        <Spinner />
      </div>
    );
  if (user === null) return <Login onLogin={setUser} />;

  const logout = () => api("/auth/logout", { method: "POST" }).finally(() => setUser(null));
  return (
    <Routes>
      <Route element={<Layout user={user} onLogout={logout} />}>
        <Route path="/check" element={<Check />} />
        <Route path="/letter" element={<Letter />} />
        <Route path="/table" element={<Table />} />
        {user.is_admin && <Route path="/norms" element={<Norms />} />}
        {user.is_admin && <Route path="/admin" element={<Admin me={user} />} />}
        <Route path="*" element={<Navigate to="/check" replace />} />
      </Route>
    </Routes>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
