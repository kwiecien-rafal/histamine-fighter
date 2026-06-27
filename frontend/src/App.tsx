import { Navigate, Route, Routes } from "react-router-dom";

import { Admin } from "./pages/Admin";
import { DailyBoard } from "./pages/DailyBoard";
import { DishLookup } from "./pages/DishLookup";

// The whole app is a single bundle. The public pages share a top navbar that links
// the admin route; the backend gate, not obscurity, is what protects it.
export function App() {
  return (
    <Routes>
      <Route path="/" element={<DishLookup />} />
      <Route path="/daily" element={<DailyBoard />} />
      <Route path="/admin" element={<Admin />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
