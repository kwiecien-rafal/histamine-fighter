import { useState } from "react";
import { Link, NavLink } from "react-router-dom";

import { useAdminSession } from "../hooks/useAdminSession";
import { SettingsDrawer } from "./SettingsDrawer";

// The shared public top bar. It lifts the LLM settings drawer in, so settings are
// reachable from every public page, and reads the admin session for the account slot.
// The admin page keeps its own section nav and does not render this.
export function Navbar() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { user } = useAdminSession();
  const isAdmin = user?.role === "admin";

  return (
    <>
      <header className="sticky top-0 z-40 border-b border-stone-200 bg-stone-50/80 backdrop-blur">
        <div className="max-w-5xl mx-auto flex items-center justify-between gap-4 px-6 py-3">
          <Link
            to="/"
            className="font-serif text-lg font-semibold tracking-tight text-emerald-900"
          >
            Histamine Fighter
          </Link>
          <nav className="flex items-center gap-5 text-sm">
            <NavItem to="/" end>
              Home
            </NavItem>
            <NavItem to="/daily">Today's meals</NavItem>
            <NavItem to="/meals">Safe meals</NavItem>
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="text-stone-600 hover:text-stone-900 cursor-pointer"
            >
              Settings
            </button>
            <Link
              to="/admin"
              className="rounded border border-emerald-200 bg-emerald-50 px-3 py-1 text-emerald-800 hover:bg-emerald-100"
            >
              {isAdmin ? "Admin" : "Log in"}
            </Link>
          </nav>
        </div>
      </header>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </>
  );
}

function NavItem({ to, end, children }: { to: string; end?: boolean; children: React.ReactNode }) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        isActive ? "text-stone-900 font-medium" : "text-stone-600 hover:text-stone-900"
      }
    >
      {children}
    </NavLink>
  );
}
