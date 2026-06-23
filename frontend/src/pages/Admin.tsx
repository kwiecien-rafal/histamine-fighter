import { useState } from "react";
import { Link } from "react-router-dom";

import type { AdminDailySuggestion, AdminMeal, ComposedMeal, MealType } from "../api/admin";
import {
  approveDaily,
  approveMeal,
  listPendingDaily,
  listPendingMeals,
  MAX_EMAIL_CHARS,
  MAX_PASSWORD_CHARS,
  rejectDaily,
  rejectMeal,
} from "../api/admin";
import { ComposeCost } from "../components/ComposeCost";
import { DailySuggestionCard } from "../components/DailySuggestionCard";
import { LLMProviderBadge } from "../components/LLMProviderBadge";
import { MealReviewCard } from "../components/MealReviewCard";
import { ReasoningReplay } from "../components/ReasoningReplay";
import { ReviewQueueShell } from "../components/ReviewQueueShell";
import { UnverifiedNote } from "../components/UnverifiedNote";
import { useAdminSession } from "../hooks/useAdminSession";
import { useReasoningStream } from "../hooks/useReasoningStream";
import { useReviewQueue, type ReviewAction } from "../hooks/useReviewQueue";
import { MEAL_TYPE_LABEL, MEAL_TYPES } from "../lib/meal";

export function Admin() {
  const { user, status, login, logout, expire, loggingIn, error: loginError } = useAdminSession();
  const isAdmin = user?.role === "admin";
  const mealReview = useReviewQueue(isAdmin, expire, listPendingMeals, approveMeal, rejectMeal);
  const dailyReview = useReviewQueue(isAdmin, expire, listPendingDaily, approveDaily, rejectDaily);

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
          {status === "authed" && (
            <button
              type="button"
              onClick={() => void logout()}
              className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4 cursor-pointer"
            >
              Log out
            </button>
          )}
        </header>

        {status === "loading" ? (
          <p className="text-stone-600">Checking your session…</p>
        ) : isAdmin ? (
          <>
            <GeneratePanel onExpired={expire} />
            <section className="mb-8">
              <h2 className="text-lg font-medium mb-3">Daily board</h2>
              <DailyReviewQueue
                suggestions={dailyReview.items}
                loading={dailyReview.loading}
                error={dailyReview.error}
                decidingId={dailyReview.decidingId}
                onReload={() => void dailyReview.reload()}
                onDecide={(id, action) => void dailyReview.decide(id, action)}
              />
            </section>
            <section>
              <h2 className="text-lg font-medium mb-3">Curated meals</h2>
              <ReviewQueue
                meals={mealReview.items}
                loading={mealReview.loading}
                error={mealReview.error}
                decidingId={mealReview.decidingId}
                onReload={() => void mealReview.reload()}
                onDecide={(id, action) => void mealReview.decide(id, action)}
              />
            </section>
          </>
        ) : status === "authed" ? (
          <p className="text-stone-600">This account doesn't have admin access.</p>
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
          maxLength={MAX_EMAIL_CHARS}
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
          maxLength={MAX_PASSWORD_CHARS}
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

interface GeneratePanelProps {
  onExpired: () => void;
}

function GeneratePanel({ onExpired }: GeneratePanelProps) {
  const [mealType, setMealType] = useState<MealType>("breakfast");
  const { status, events, meal, error, start, cancel } = useReasoningStream(onExpired);
  const streaming = status === "streaming";

  return (
    <section className="rounded border border-stone-200 bg-white p-5 mb-6">
      <h2 className="text-lg font-medium">Watch the agent compose</h2>
      <p className="text-sm text-stone-600 mb-3">
        A live demo of the composer. The meal is not saved — the public board is
        generated by the nightly job.
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="generate-meal-type" className="sr-only">
          Meal type
        </label>
        <select
          id="generate-meal-type"
          value={mealType}
          onChange={(e) => setMealType(e.target.value as MealType)}
          disabled={streaming}
          className="rounded border border-stone-300 px-3 py-2 text-sm focus:outline-none focus:border-emerald-700 disabled:opacity-50"
        >
          {MEAL_TYPES.map((type) => (
            <option key={type} value={type}>
              {MEAL_TYPE_LABEL[type]}
            </option>
          ))}
        </select>
        <button
          type="button"
          onClick={() => void start(mealType)}
          disabled={streaming}
          className="rounded bg-emerald-800 text-white px-4 py-2 text-sm disabled:opacity-50 enabled:cursor-pointer"
        >
          {streaming ? "Composing…" : "Generate now"}
        </button>
        {streaming && (
          <button
            type="button"
            onClick={cancel}
            className="rounded border border-stone-300 px-4 py-2 text-sm text-stone-700 hover:bg-stone-50 cursor-pointer"
          >
            Stop
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-700 mt-3">
          <span className="font-medium">Couldn't compose —</span> {error}
        </p>
      )}

      {(streaming || events.length > 0) && (
        <div className="mt-4">
          <ReasoningReplay events={events} live pending={streaming} />
        </div>
      )}

      {status === "done" && meal && (
        <div className="mt-4">
          <ComposedMealView meal={meal} />
        </div>
      )}
    </section>
  );
}

function ComposedMealView({ meal }: { meal: ComposedMeal }) {
  return (
    <article className="rounded border border-stone-200 bg-stone-50 p-5">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-lg font-medium">{meal.name}</h3>
        <div className="flex items-center gap-2 shrink-0">
          <span className="font-mono text-[10px] uppercase tracking-wide text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
            {MEAL_TYPE_LABEL[meal.meal_type]}
          </span>
          <LLMProviderBadge model={meal.model} />
        </div>
      </div>
      <div className="mb-2">
        <ComposeCost usage={meal.usage} model={meal.model} />
      </div>
      <p className="text-sm text-stone-600 mb-3">{meal.description}</p>
      <ul className="flex flex-wrap gap-1.5 mb-3">
        {meal.ingredients.map((ingredient) => (
          <li
            key={ingredient.name}
            className="rounded border border-stone-200 bg-white px-2 py-0.5 text-sm"
          >
            {ingredient.name}
            {ingredient.category && (
              <span className="text-stone-400"> · {ingredient.category}</span>
            )}
          </li>
        ))}
      </ul>
      <UnverifiedNote ingredients={meal.unverified_ingredients} />
    </article>
  );
}

interface ReviewQueueProps {
  meals: AdminMeal[] | null;
  loading: boolean;
  error: string | null;
  decidingId: string | null;
  onReload: () => void;
  onDecide: (mealId: string, action: ReviewAction) => void;
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
    <ReviewQueueShell
      count={meals?.length ?? null}
      loading={loading}
      error={error}
      emptyMessage="Nothing waiting for review. Compose some meals first."
      onReload={onReload}
    >
      {meals?.map((meal) => (
        <MealReviewCard
          key={meal.id}
          meal={meal}
          busy={decidingId === meal.id}
          onApprove={() => onDecide(meal.id, "approve")}
          onReject={() => onDecide(meal.id, "reject")}
        />
      ))}
    </ReviewQueueShell>
  );
}

interface DailyReviewQueueProps {
  suggestions: AdminDailySuggestion[] | null;
  loading: boolean;
  error: string | null;
  decidingId: string | null;
  onReload: () => void;
  onDecide: (suggestionId: string, action: ReviewAction) => void;
}

function DailyReviewQueue({
  suggestions,
  loading,
  error,
  decidingId,
  onReload,
  onDecide,
}: DailyReviewQueueProps) {
  return (
    <ReviewQueueShell
      count={suggestions?.length ?? null}
      loading={loading}
      error={error}
      emptyMessage="Nothing waiting for review. Generate a daily board first."
      onReload={onReload}
    >
      {suggestions?.map((suggestion) => (
        <DailySuggestionCard
          key={suggestion.id}
          suggestion={suggestion}
          busy={decidingId === suggestion.id}
          onApprove={() => onDecide(suggestion.id, "approve")}
          onReject={() => onDecide(suggestion.id, "reject")}
        />
      ))}
    </ReviewQueueShell>
  );
}
