import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { QueuedDay } from "../api/admin";
import {
  AdminAuthError,
  type ApprovalStatus,
  approveDaily,
  approveMeal,
  deleteDaily,
  deleteMeal,
  errorMessage,
  listDailyQueue,
  listMeals,
  MAX_EMAIL_CHARS,
  MAX_PASSWORD_CHARS,
  rejectDaily,
  rejectMeal,
  updateDaily,
  updateMeal,
} from "../api/admin";
import { ComposePanel } from "../components/ComposePanel";
import { DailySuggestionCard } from "../components/DailySuggestionCard";
import { GenerationSettingsPanel } from "../components/GenerationSettingsPanel";
import { MealReviewCard } from "../components/MealReviewCard";
import { NewMealForm } from "../components/NewMealForm";
import { ReviewQueueShell } from "../components/ReviewQueueShell";
import { StatusTabs } from "../components/StatusTabs";
import { useAdminSession } from "../hooks/useAdminSession";
import { useReviewQueue } from "../hooks/useReviewQueue";
import { formatBoardDate } from "../lib/daily";
import { MEAL_TYPE_LABEL } from "../lib/meal";

const SECTIONS = [
  { id: "settings", label: "Generation settings" },
  { id: "compose", label: "Generate" },
  { id: "queue", label: "Daily queue" },
  { id: "curated", label: "Curated" },
];

// Per-status copy for the curated browse tabs: the tab label, the count wording, the
// pre-load placeholder, and the empty message. Pending keeps the review-queue voice;
// the others read as a browsable archive.
const CURATED_TABS: Record<
  ApprovalStatus,
  { label: string; countNoun: string; idleLabel: string; emptyMessage: string }
> = {
  pending: {
    label: "Pending",
    countNoun: "waiting for review",
    idleLabel: "Pending review",
    emptyMessage: "Nothing waiting for review. Compose some meals first.",
  },
  approved: {
    label: "Approved",
    countNoun: "approved",
    idleLabel: "Approved meals",
    emptyMessage: "No approved meals yet.",
  },
  rejected: {
    label: "Rejected",
    countNoun: "rejected",
    idleLabel: "Rejected meals",
    emptyMessage: "No rejected meals.",
  },
};
const CURATED_ORDER: ApprovalStatus[] = ["pending", "approved", "rejected"];

export function Admin() {
  const { user, status, login, logout, expire, loggingIn, error: loginError } = useAdminSession();
  const isAdmin = user?.role === "admin";
  // The curated section browses one approval state at a time; switching tabs swaps the
  // list call, which re-runs the queue's load (the callback identity changes).
  const [curatedStatus, setCuratedStatus] = useState<ApprovalStatus>("pending");
  const listCurated = useCallback(() => listMeals(curatedStatus), [curatedStatus]);
  const curated = useReviewQueue(isAdmin, expire, listCurated, approveMeal, rejectMeal, deleteMeal);
  const queue = useDailyQueue(isAdmin, expire);

  // The backend serializes live compositions behind one lock, so only one panel may run at
  // a time. Track which panel is streaming so both can disable their triggers while busy.
  const [composing, setComposing] = useState<"daily" | "curated" | null>(null);
  const handleComposingChange = useCallback(
    (mode: "daily" | "curated", streaming: boolean) =>
      setComposing((current) => (streaming ? mode : current === mode ? null : current)),
    [],
  );

  return (
    <main className="min-h-screen bg-stone-50 text-stone-900 px-6 pt-10 pb-24">
      <div className="max-w-2xl mx-auto">
        <header className="flex items-baseline justify-between mb-6">
          <div>
            <Link to="/" className="text-sm text-stone-500 hover:text-stone-800">
              ← Back to site
            </Link>
            <h1 className="text-3xl font-semibold">Admin</h1>
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
            <nav className="flex flex-wrap gap-x-4 gap-y-1 text-sm mb-8 border-b border-stone-200 pb-3">
              {SECTIONS.map((section) => (
                <a
                  key={section.id}
                  href={`#${section.id}`}
                  className="text-stone-600 hover:text-stone-900"
                >
                  {section.label}
                </a>
              ))}
            </nav>

            <Section id="settings" title="Generation settings">
              <GenerationSettingsPanel onExpired={expire} />
            </Section>

            <Section id="compose" title="Generate">
              <p className="text-sm text-stone-600 mb-3">
                Watch the composer run. Each generation adds a pending row to the queue or curated
                pool, which you can review, edit, watch again, or remove below.
              </p>
              <h3 className="text-sm font-medium mb-2">Daily board</h3>
              <ComposePanel
                mode="daily"
                defaultDate={defaultComposeDate(queue.days)}
                busy={composing !== null}
                onComposingChange={handleComposingChange}
                onSaved={queue.reload}
                onExpired={expire}
              />
              <h3 className="text-sm font-medium mt-5 mb-2">Curated pool</h3>
              <ComposePanel
                mode="curated"
                busy={composing !== null}
                onComposingChange={handleComposingChange}
                onSaved={curated.reload}
                onExpired={expire}
              />
            </Section>

            <Section id="queue" title="Daily queue">
              <DailyQueueView
                days={queue.days}
                loading={queue.loading}
                error={queue.error}
                onReload={() => void queue.reload()}
                onExpired={expire}
              />
            </Section>

            <Section id="curated" title="Curated meals">
              <div className="mb-4">
                <NewMealForm
                  onCreated={() => {
                    // A manual meal lands pending; show that tab, reloading it directly when
                    // it is already the active one (a same-status set would not re-fetch).
                    if (curatedStatus === "pending") void curated.reload();
                    else setCuratedStatus("pending");
                  }}
                />
              </div>
              <StatusTabs
                tabs={CURATED_ORDER}
                active={curatedStatus}
                label={(tabStatus) => CURATED_TABS[tabStatus].label}
                onSelect={setCuratedStatus}
                ariaLabel="Curated meal status"
                panelId="curated-panel"
                idPrefix="curated-tab"
              />
              <div id="curated-panel" role="tabpanel" aria-labelledby={`curated-tab-${curatedStatus}`}>
                <ReviewQueueShell
                  count={curated.items?.length ?? null}
                  loading={curated.loading}
                  error={curated.error}
                  emptyMessage={CURATED_TABS[curatedStatus].emptyMessage}
                  countNoun={CURATED_TABS[curatedStatus].countNoun}
                  idleLabel={CURATED_TABS[curatedStatus].idleLabel}
                  onReload={() => void curated.reload()}
                >
                  {curated.items?.map((meal) => (
                    <MealReviewCard
                      key={meal.id}
                      meal={meal}
                      busy={curated.decidingId === meal.id}
                      onApprove={() => void curated.decide(meal.id, "approve")}
                      onReject={() => void curated.decide(meal.id, "reject")}
                      onRemove={() => void curated.decide(meal.id, "delete")}
                      onSaveEdit={
                        meal.approval_status === "pending"
                          ? (edit) => updateMeal(meal.id, edit).then(() => undefined)
                          : undefined
                      }
                      onEdited={() => void curated.reload()}
                    />
                  ))}
                </ReviewQueueShell>
              </div>
            </Section>
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

function Section({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section id={id} className="mb-10 scroll-mt-6">
      <h2 className="text-lg font-medium mb-3">{title}</h2>
      {children}
    </section>
  );
}

// The date a new daily composition most likely targets: the earliest upcoming day still
// missing a slot, else the day after the last queued date (so queueing further ahead does
// not default onto a full slot), else today when nothing is queued. A fully-empty future
// date is never in the payload, so the queue's own dates anchor the next target.
function defaultComposeDate(days: QueuedDay[] | null): string {
  const today = new Date().toISOString().slice(0, 10);
  if (!days || days.length === 0) return today;
  const incomplete = days.find((day) => day.missing_meal_types.length > 0);
  return incomplete ? incomplete.date : nextDay(days[days.length - 1].date);
}

// The calendar day after a YYYY-MM-DD date. Parsed and advanced in UTC so the arithmetic
// is timezone-stable; the queue speaks calendar dates, not instants.
function nextDay(iso: string): string {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

interface DailyQueueState {
  days: QueuedDay[] | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

// The upcoming daily board grouped by date. Like useReviewQueue it runs only for an authed
// admin and routes a 401 to onExpired, but it reads the whole queue rather than one status.
function useDailyQueue(enabled: boolean, onExpired: () => void): DailyQueueState {
  const [days, setDays] = useState<QueuedDay[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!enabled) return;
    setLoading(true);
    setError(null);
    try {
      setDays(await listDailyQueue());
    } catch (err) {
      if (err instanceof AdminAuthError) onExpired();
      else setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [enabled, onExpired]);

  useEffect(() => {
    if (!enabled) {
      setDays(null);
      return;
    }
    void reload();
  }, [enabled, reload]);

  return { days, loading, error, reload };
}

interface DailyQueueViewProps {
  days: QueuedDay[] | null;
  loading: boolean;
  error: string | null;
  onReload: () => void;
  onExpired: () => void;
}

function DailyQueueView({ days, loading, error, onReload, onExpired }: DailyQueueViewProps) {
  const [decidingId, setDecidingId] = useState<string | null>(null);
  const [decideError, setDecideError] = useState<string | null>(null);

  const decide = useCallback(
    async (id: string, action: "approve" | "reject" | "delete") => {
      setDecidingId(id);
      setDecideError(null);
      try {
        await (action === "approve"
          ? approveDaily(id)
          : action === "reject"
            ? rejectDaily(id)
            : deleteDaily(id));
        onReload();
      } catch (err) {
        if (err instanceof AdminAuthError) onExpired();
        else setDecideError(errorMessage(err));
      } finally {
        setDecidingId(null);
      }
    },
    [onReload, onExpired],
  );

  if (loading && days === null) return <p className="text-stone-600">Loading the queue…</p>;
  if (error && days === null) {
    return (
      <p role="alert" className="text-sm text-red-700">
        {error}{" "}
        <button type="button" onClick={onReload} className="underline cursor-pointer">
          Try again
        </button>
      </p>
    );
  }
  if (!days || days.length === 0) {
    return (
      <p className="text-stone-600">
        Nothing scheduled. Generate a board above, or wait for the nightly job.
      </p>
    );
  }

  return (
    <div className="space-y-6">
      {decideError && (
        <p role="alert" className="text-sm text-red-700">
          {decideError}
        </p>
      )}
      {days.map((day) => (
        <div key={day.date}>
          <div className="flex items-baseline justify-between mb-2">
            <h3 className="font-medium">{formatBoardDate(day.date)}</h3>
            <span className="text-xs text-stone-500">
              {day.approved_count} approved · {day.pending_count} pending
            </span>
          </div>
          {(day.pending_count > 0 || day.missing_meal_types.length > 0) && (
            <p className="text-xs text-amber-700 mb-2">
              {day.approved_count === 0
                ? "No slot is approved yet, so this day will stay locked."
                : "Not fully approved — the public board will show only the approved meals."}
              {day.missing_meal_types.length > 0 &&
                ` Missing: ${day.missing_meal_types.map((m) => MEAL_TYPE_LABEL[m]).join(", ")}.`}
            </p>
          )}
          <div className="space-y-3">
            {day.slots.map((slot) => (
              <DailySuggestionCard
                key={slot.id}
                suggestion={slot}
                busy={decidingId === slot.id}
                onApprove={() => void decide(slot.id, "approve")}
                onReject={() => void decide(slot.id, "reject")}
                onRemove={() => void decide(slot.id, "delete")}
                onSaveEdit={
                  slot.approval_status === "pending"
                    ? (edit) => updateDaily(slot.id, edit).then(() => undefined)
                    : undefined
                }
                onEdited={onReload}
              />
            ))}
          </div>
        </div>
      ))}
    </div>
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
      <p className="text-stone-600 mb-1">Sign in to review and compose meals.</p>
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
