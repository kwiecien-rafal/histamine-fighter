import { useState } from "react";
import { Link } from "react-router-dom";

import type { AdminMeal } from "../api/admin";
import { MealReviewCard } from "../components/MealReviewCard";
import { useAdminSession } from "../hooks/useAdminSession";
import { useMealReview } from "../hooks/useMealReview";

export function Admin() {
  const { token, login, logout, loggingIn, error: loginError } = useAdminSession();
  const { meals, loading, error, decidingId, reload, decide } = useMealReview(token, logout);

  return (
    <main className="min-h-screen bg-stone-50 text-stone-900 px-6 pt-12 pb-24">
      <div className="max-w-2xl mx-auto">
        <header className="flex items-baseline justify-between mb-8">
          <div>
            <Link to="/" className="text-sm text-stone-500 hover:text-stone-800">
              Histamine Fighter
            </Link>
            <h1 className="text-3xl font-semibold">Meal review</h1>
          </div>
          {token && (
            <button
              type="button"
              onClick={logout}
              className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4 cursor-pointer"
            >
              Log out
            </button>
          )}
        </header>

        {token ? (
          <ReviewQueue
            meals={meals}
            loading={loading}
            error={error}
            decidingId={decidingId}
            onReload={() => void reload()}
            onDecide={(id, action) => void decide(id, action)}
          />
        ) : (
          <LoginForm onSubmit={login} busy={loggingIn} error={loginError} />
        )}
      </div>
    </main>
  );
}

interface LoginFormProps {
  onSubmit: (email: string, password: string) => Promise<void>;
  busy: boolean;
  error: string | null;
}

function LoginForm({ onSubmit, busy, error }: LoginFormProps) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void onSubmit(email.trim(), password);
  }

  return (
    <form onSubmit={submit} className="max-w-sm flex flex-col gap-3">
      <p className="text-stone-600 mb-1">Sign in to review composed meals.</p>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-stone-600">Email</span>
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          autoComplete="username"
          required
          className="rounded border border-stone-300 px-3 py-2 focus:outline-none focus:border-emerald-700"
        />
      </label>
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-stone-600">Password</span>
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
          required
          className="rounded border border-stone-300 px-3 py-2 focus:outline-none focus:border-emerald-700"
        />
      </label>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          <span className="font-medium">Couldn't sign in —</span> {error}
        </p>
      )}
      <button
        type="submit"
        disabled={busy || !email.trim() || !password}
        className="rounded bg-emerald-800 text-white px-4 py-2 disabled:opacity-50 enabled:cursor-pointer"
      >
        {busy ? "Signing in…" : "Log in"}
      </button>
    </form>
  );
}

interface ReviewQueueProps {
  meals: AdminMeal[] | null;
  loading: boolean;
  error: string | null;
  decidingId: string | null;
  onReload: () => void;
  onDecide: (mealId: string, action: "approve" | "reject") => void;
}

function ReviewQueue({
  meals,
  loading,
  error,
  decidingId,
  onReload,
  onDecide,
}: ReviewQueueProps) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-stone-600">
          {meals ? `${meals.length} waiting for review` : "Pending review"}
        </p>
        <button
          type="button"
          onClick={onReload}
          disabled={loading}
          className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4 disabled:opacity-50 enabled:cursor-pointer"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-700">
          <span className="font-medium">Something went wrong —</span> {error}
        </p>
      )}

      {meals && meals.length === 0 && !loading && (
        <p className="text-stone-600">Nothing waiting for review. Compose some meals first.</p>
      )}

      {meals?.map((meal) => (
        <MealReviewCard
          key={meal.id}
          meal={meal}
          busy={decidingId === meal.id}
          onApprove={() => onDecide(meal.id, "approve")}
          onReject={() => onDecide(meal.id, "reject")}
        />
      ))}
    </section>
  );
}
