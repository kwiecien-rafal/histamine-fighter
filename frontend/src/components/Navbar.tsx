import { useState } from "react";
import { Link, NavLink } from "react-router-dom";

import { useSessionStore } from "../store/session";
import { SettingsDrawer } from "./SettingsDrawer";

// The shared public top bar. It lifts the LLM settings drawer in, so settings are
// reachable from every public page, and reads the shared session store for the
// account slot. The admin page keeps its own section nav and does not render this.
export function Navbar() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const user = useSessionStore((s) => s.user);
  const isAdmin = user?.role === "admin";

  return (
    <>
      <header className="sticky top-0 z-40 border-b border-cream-200 bg-cream-50/80 backdrop-blur">
        <div className="max-w-5xl mx-auto flex items-center justify-between gap-4 px-6 py-3">
          <Link
            to="/"
            className="font-serif text-lg font-semibold tracking-tight text-forest-900"
          >
            Histamine Fighter
          </Link>
          <nav className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm">
            <NavItem to="/" end>
              Home
            </NavItem>
            {/* The flagship flow gets the ember accent so it reads as the main action. */}
            <NavLink
              to="/lookup"
              className={({ isActive }) =>
                isActive
                  ? "text-ember-800 font-medium"
                  : "text-ember-700 font-medium hover:text-ember-800"
              }
            >
              Check a dish
            </NavLink>
            <NavItem to="/daily">Today's meals</NavItem>
            <NavItem to="/meals">Safe meals</NavItem>
            <NavItem to="/learn">Learn</NavItem>
            <button
              type="button"
              onClick={() => setSettingsOpen(true)}
              className="text-stone-600 hover:text-stone-900 cursor-pointer"
            >
              AI settings
            </button>
            {isAdmin && (
              <Link
                to="/admin"
                className="rounded border border-forest-200 bg-forest-50 px-3 py-1 text-forest-800 hover:bg-forest-100"
              >
                Admin
              </Link>
            )}
            {user ? (
              <Link
                to="/profile"
                title={user.email}
                className="rounded border border-forest-200 bg-forest-50 px-3 py-1 text-forest-800 hover:bg-forest-100 max-w-40 truncate"
              >
                {user.email}
              </Link>
            ) : (
              <Link
                to="/login"
                className="rounded border border-forest-200 bg-forest-50 px-3 py-1 text-forest-800 hover:bg-forest-100"
              >
                Log in
              </Link>
            )}
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
