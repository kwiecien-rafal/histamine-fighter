import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { deleteAccount } from "../api/auth";
import type { MealType } from "../api/domain";
import { errorMessage } from "../api/errors";
import { listSaves, type SavedMealCard as SavedMealCardData } from "../api/saves";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { SavedMealCard } from "../components/SavedMealCard";
import { MEAL_TYPE_LABEL, MEAL_TYPES } from "../lib/meal";
import { useSessionStore } from "../store/session";

// The grid's filter: the four meal slots plus the bucket lookup snapshots live in
// (an assessed dish has no slot, so a plain meal-type filter would strand them).
type SavedFilter = MealType | "lookup" | null;

// The signed-in identity surface: saved meals first, account controls last. Anon
// visitors get an in-page sign-in prompt (the Admin page pattern; there is no
// route-guard wrapper). The everyday Sign out stands alone; the rarer, sharper
// tools (sign out everywhere, delete account) live behind a closed disclosure so
// they cannot be hit by reflex.
export function Profile() {
  const user = useSessionStore((s) => s.user);
  const status = useSessionStore((s) => s.status);

  useEffect(() => {
    const previous = document.title;
    document.title = "Your profile · Histamine Fighter";
    return () => {
      document.title = previous;
    };
  }, []);

  if (status === "loading") {
    return (
      <p className="text-stone-600" aria-live="polite">
        Checking session…
      </p>
    );
  }

  if (user === null) {
    return (
      <div className="max-w-5xl mx-auto">
        <h1 className="font-serif text-3xl font-semibold text-forest-900">Your profile</h1>
        <p className="text-stone-600 mt-3">
          <Link to="/login" className="underline hover:text-stone-900">
            Sign in
          </Link>{" "}
          to save dishes for later and manage your account.
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      <header className="mb-8">
        <h1 className="font-serif text-3xl font-semibold text-forest-900">Your profile</h1>
        <p className="text-stone-600 mt-1">
          Signed in as <span className="font-medium text-stone-900">{user.email}</span>
        </p>
      </header>

      <SavedMealsSection />

      <AccountControls />
    </div>
  );
}

function SavedMealsSection() {
  const [saves, setSaves] = useState<SavedMealCardData[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<SavedFilter>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setSaves(await listSaves());
    } catch (err) {
      setError(errorMessage(err));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const shown = saves?.filter((meal) =>
    filter === null
      ? true
      : filter === "lookup"
        ? meal.source === "lookup"
        : meal.meal_type === filter,
  );

  return (
    <section aria-labelledby="saved-meals-heading" className="mb-12">
      <h2
        id="saved-meals-heading"
        className="font-serif text-xl font-semibold text-forest-900 mb-3"
      >
        Saved meals
      </h2>

      {saves === null && error === null && (
        <p className="text-stone-600 text-sm" aria-live="polite">
          Loading your meals…
        </p>
      )}

      {error && (
        <div role="alert" className="text-sm text-red-700">
          <span className="font-medium">Couldn't load your meals —</span> {error}{" "}
          <button
            type="button"
            onClick={() => void load()}
            className="underline underline-offset-4 cursor-pointer"
          >
            Try again
          </button>
        </div>
      )}

      {saves && saves.length === 0 && (
        <p className="text-stone-600 text-sm">
          Nothing saved yet. Hit "Save this dish" on any meal — on{" "}
          <Link to="/daily" className="underline hover:text-stone-900">
            today's board
          </Link>
          , in{" "}
          <Link to="/meals" className="underline hover:text-stone-900">
            safe meals
          </Link>
          , or after{" "}
          <Link to="/lookup" className="underline hover:text-stone-900">
            checking a dish
          </Link>
          .
        </p>
      )}

      {saves && saves.length > 0 && (
        <>
          <div className="flex flex-wrap gap-2 mb-6" role="group" aria-label="Filter saved meals">
            <FilterButton active={filter === null} onClick={() => setFilter(null)}>
              All
            </FilterButton>
            {MEAL_TYPES.map((type) => (
              <FilterButton key={type} active={filter === type} onClick={() => setFilter(type)}>
                {MEAL_TYPE_LABEL[type]}
              </FilterButton>
            ))}
            <FilterButton active={filter === "lookup"} onClick={() => setFilter("lookup")}>
              Dish checks
            </FilterButton>
          </div>

          {shown && shown.length === 0 ? (
            <p className="text-stone-600 text-sm">Nothing saved under this filter yet.</p>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2">
              {shown?.map((meal) => (
                <SavedMealCard key={meal.id} meal={meal} />
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? "rounded-full border border-forest-300 bg-forest-50 px-3 py-1 text-sm text-forest-800 cursor-pointer"
          : "rounded-full border border-stone-300 px-3 py-1 text-sm text-stone-600 hover:bg-stone-100 cursor-pointer"
      }
    >
      {children}
    </button>
  );
}

function AccountControls() {
  const user = useSessionStore((s) => s.user);
  const logout = useSessionStore((s) => s.logout);
  const logoutEverywhere = useSessionStore((s) => s.logoutEverywhere);
  const clear = useSessionStore((s) => s.clear);
  const navigate = useNavigate();
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  async function handleDelete() {
    await deleteAccount();
    clear();
    void navigate("/");
  }

  return (
    <section aria-labelledby="account-heading" className="border-t border-stone-200 pt-6">
      <h2 id="account-heading" className="font-serif text-xl font-semibold text-forest-900 mb-3">
        Account
      </h2>
      <button
        type="button"
        onClick={() => void logout()}
        className="rounded border border-stone-300 px-4 py-2 text-sm text-stone-700 hover:bg-stone-100 cursor-pointer"
      >
        Sign out
      </button>

      <details className="mt-6 text-sm">
        <summary className="text-stone-600 hover:text-stone-900 cursor-pointer select-none">
          Account &amp; security
        </summary>
        <div className="mt-3 flex flex-col items-start gap-3 pl-4 border-l border-stone-200">
          <div>
            <button
              type="button"
              onClick={() => void logoutEverywhere()}
              className="text-stone-600 underline hover:text-stone-900 cursor-pointer"
            >
              Sign out everywhere
            </button>
            <p className="text-xs text-stone-500 mt-0.5">
              Also signs out every other device and browser.
            </p>
          </div>
          {/* Admin accounts are operator-managed via the CLI; the backend answers 403. */}
          {user?.role === "user" && (
            <button
              type="button"
              onClick={() => setConfirmingDelete(true)}
              className="text-red-700 underline hover:text-red-800 cursor-pointer"
            >
              Delete account…
            </button>
          )}
        </div>
      </details>

      {confirmingDelete && (
        <ConfirmDialog
          title="Delete your account?"
          body={
            <p>
              This permanently removes your account, email, usage history, and saved meals.
              There is no undo.
            </p>
          }
          confirmLabel="Delete my account"
          onConfirm={handleDelete}
          onCancel={() => setConfirmingDelete(false)}
        />
      )}
    </section>
  );
}
