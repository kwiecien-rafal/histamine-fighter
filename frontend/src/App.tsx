import { useEffect } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { Admin } from "./pages/Admin";
import { DailyBoard } from "./pages/DailyBoard";
import { DishLookup } from "./pages/DishLookup";
import { Home } from "./pages/Home";
import { Learn } from "./pages/Learn";
import { Login } from "./pages/Login";
import { LoginComplete, LoginVerify } from "./pages/LoginVerify";
import { MealDetail } from "./pages/MealDetail";
import { MealsBrowse } from "./pages/MealsBrowse";
import { Privacy } from "./pages/Privacy";
import { Terms } from "./pages/Terms";
import { useSessionStore } from "./store/session";

// The whole app is a single bundle. Public pages share the Layout shell (navbar,
// footer with the medical disclaimer); admin keeps its own chrome and sits outside.
// The backend gate, not obscurity, is what protects the admin route.
export function App() {
  const bootstrap = useSessionStore((s) => s.bootstrap);

  // Recover the cookie session once on load; the store latches, so StrictMode's
  // double-fired effect stays one /me call.
  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/lookup" element={<DishLookup />} />
        <Route path="/daily" element={<DailyBoard />} />
        <Route path="/meals" element={<MealsBrowse />} />
        <Route path="/meals/:id" element={<MealDetail />} />
        <Route path="/learn" element={<Learn />} />
        <Route path="/login" element={<Login />} />
        <Route path="/login/verify" element={<LoginVerify />} />
        <Route path="/login/complete" element={<LoginComplete />} />
        <Route path="/privacy" element={<Privacy />} />
        <Route path="/terms" element={<Terms />} />
      </Route>
      <Route path="/admin" element={<Admin />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
