import { Navigate, Route, Routes } from "react-router-dom";

import { Layout } from "./components/Layout";
import { Admin } from "./pages/Admin";
import { DailyBoard } from "./pages/DailyBoard";
import { DishLookup } from "./pages/DishLookup";
import { Home } from "./pages/Home";
import { Learn } from "./pages/Learn";
import { MealDetail } from "./pages/MealDetail";
import { MealsBrowse } from "./pages/MealsBrowse";

// The whole app is a single bundle. Public pages share the Layout shell (navbar,
// footer with the medical disclaimer); admin keeps its own chrome and sits outside.
// The backend gate, not obscurity, is what protects the admin route.
export function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<Home />} />
        <Route path="/lookup" element={<DishLookup />} />
        <Route path="/daily" element={<DailyBoard />} />
        <Route path="/meals" element={<MealsBrowse />} />
        <Route path="/meals/:id" element={<MealDetail />} />
        <Route path="/learn" element={<Learn />} />
      </Route>
      <Route path="/admin" element={<Admin />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
