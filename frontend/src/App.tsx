import { Route, Routes } from "react-router-dom";

import { Admin } from "./pages/Admin";
import { DailyBoard } from "./pages/DailyBoard";
import { DishLookup } from "./pages/DishLookup";

// The whole app is a single bundle; the admin route is unlinked from the public
// UI and reached by URL. The backend gate, not obscurity, is what protects it.
export function App() {
  return (
    <Routes>
      <Route path="/" element={<DishLookup />} />
      <Route path="/daily" element={<DailyBoard />} />
      <Route path="/admin" element={<Admin />} />
    </Routes>
  );
}
