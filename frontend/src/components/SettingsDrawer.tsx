import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { getQuota, type QuotaStatus } from "../api/auth";
import { formatResetTime } from "../lib/format";
import { useDismissableOverlay } from "../hooks/useDismissableOverlay";
import { useLLMProviderStore, type Provider } from "../store/llmProvider";
import { useSessionStore } from "../store/session";

const PUBLIC_DEPLOYMENT = import.meta.env.VITE_PUBLIC_DEPLOYMENT === "true";

const SELF_HOST_GUIDE_URL = "/guides/self-host";

interface ProviderRow {
  id: Provider;
  label: string;
  note: string;
  ready: boolean;
  needsKey: boolean;
  defaultModel?: string;
  requiresModel?: boolean;
}

const PROVIDERS: ProviderRow[] = [
  { id: "shared", label: "Free tier (sign in)", note: "Our server-side model, free, with a daily limit. No key needed.", ready: true, needsKey: false },
  { id: "ollama", label: "Local Ollama", note: "Self-hosted, free, no API key.", ready: true, needsKey: false },
  { id: "openai", label: "OpenAI", note: "Use your own OpenAI API key.", ready: true, needsKey: true, defaultModel: "gpt-5.4-mini" },
  { id: "anthropic", label: "Anthropic", note: "Use your own Anthropic API key.", ready: true, needsKey: true, defaultModel: "claude-sonnet-4-6" },
  { id: "gemini", label: "Google Gemini", note: "Use your own Gemini API key.", ready: true, needsKey: true, defaultModel: "gemini-2.5-flash" },
  { id: "openrouter", label: "OpenRouter", note: "Use your own OpenRouter key.", ready: true, needsKey: true, requiresModel: true },
];

interface SettingsDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function SettingsDrawer({ open, onClose }: SettingsDrawerProps) {
  const provider = useLLMProviderStore((s) => s.provider);
  const apiKeys = useLLMProviderStore((s) => s.apiKeys);
  const models = useLLMProviderStore((s) => s.models);
  const ollamaBaseUrl = useLLMProviderStore((s) => s.ollamaBaseUrl);
  const setProvider = useLLMProviderStore((s) => s.setProvider);
  const setApiKey = useLLMProviderStore((s) => s.setApiKey);
  const setModel = useLLMProviderStore((s) => s.setModel);
  const setOllamaBaseUrl = useLLMProviderStore((s) => s.setOllamaBaseUrl);
  const [showKey, setShowKey] = useState(false);
  const drawerRef = useDismissableOverlay<HTMLElement>(open, onClose);
  const sessionStatus = useSessionStore((s) => s.status);
  const signedIn = sessionStatus === "authed";

  // Re-mask on a provider switch: showKey is drawer-wide, so a key revealed for one
  // provider must not stay in the clear when a different provider's field appears.
  function selectProvider(id: Provider) {
    setProvider(id);
    setShowKey(false);
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex" aria-modal="true" role="dialog">
      <button
        type="button"
        aria-label="Close AI settings"
        className="flex-1 bg-stone-900/30"
        onClick={onClose}
      />
      <aside
        ref={drawerRef}
        tabIndex={-1}
        className="w-full max-w-md h-full bg-white border-l border-stone-200 shadow-xl overflow-y-auto focus:outline-none"
      >
        <header className="flex items-center justify-between px-5 py-4 border-b border-stone-200">
          <h2 className="text-lg font-semibold">AI settings</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-stone-500 hover:text-stone-900 cursor-pointer"
          >
            ✕
          </button>
        </header>

        <ul className="divide-y divide-stone-100">
          {PROVIDERS.map((row) => {
            const isOllamaOnPublic = row.id === "ollama" && PUBLIC_DEPLOYMENT;
            const isSharedSignedOut = row.id === "shared" && !signedIn;
            const disabled = !row.ready || isOllamaOnPublic || isSharedSignedOut;
            const selected = provider === row.id;
            const expanded = selected && !disabled;

            return (
              <li key={row.id} className="px-5 py-4">
                <label
                  className={`flex items-start gap-3 ${disabled ? "opacity-60" : "cursor-pointer"
                    }`}
                >
                  <input
                    type="radio"
                    name="llm-provider"
                    className="mt-1 accent-forest-800"
                    checked={selected}
                    disabled={disabled}
                    onChange={() => selectProvider(row.id)}
                  />
                  <div className="flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="font-medium">{row.label}</span>
                      {disabled && (
                        <span className="text-stone-400" aria-hidden>
                          *
                        </span>
                      )}
                    </div>
                    <p className="text-sm text-stone-600 mt-0.5">
                      {isOllamaOnPublic
                        ? "Available only when you run the stack on your own machine."
                        : row.note}
                    </p>
                    {isSharedSignedOut && (
                      <p className="text-xs text-stone-500 mt-1">
                        *{" "}
                        <Link to="/login" onClick={onClose} className="underline hover:text-stone-900">
                          Sign in
                        </Link>{" "}
                        to unlock the free tier.
                      </p>
                    )}
                    {disabled && !isSharedSignedOut && (
                      <p className="text-xs text-stone-500 mt-1">
                        *{" "}
                        <a
                          href={SELF_HOST_GUIDE_URL}
                          className="underline hover:text-stone-900"
                        >
                          Lorem ipsum self-host guide — placeholder
                        </a>
                        .
                      </p>
                    )}
                  </div>
                </label>

                {expanded && row.id === "shared" && (
                  <div className="mt-3 pl-7">
                    <SharedQuota />
                  </div>
                )}

                {expanded && row.id === "ollama" && (
                  <div className="mt-3 pl-7 space-y-3">
                    <label className="block">
                      <span className="text-xs uppercase tracking-wide text-stone-500">
                        Base URL
                      </span>
                      <input
                        type="text"
                        value={ollamaBaseUrl}
                        onChange={(e) => setOllamaBaseUrl(e.target.value)}
                        placeholder="server default (e.g. http://localhost:11434)"
                        className="mt-1 w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-forest-700"
                      />
                    </label>
                    <label className="block">
                      <span className="text-xs uppercase tracking-wide text-stone-500">
                        Model
                      </span>
                      <input
                        type="text"
                        value={models[row.id] ?? ""}
                        onChange={(e) => setModel(row.id, e.target.value)}
                        placeholder="server default (e.g. gpt-oss:20b)"
                        className="mt-1 w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-forest-700"
                      />
                    </label>
                    <p className="text-xs text-stone-500">
                      Leave blank to use the server-configured Ollama endpoint.
                      When running the stack via Docker Compose, that's already
                      set to{" "}
                      <code className="font-mono text-[11px]">
                        http://host.docker.internal:11434
                      </code>
                      , which routes to Ollama on your host machine.
                    </p>
                  </div>
                )}

                {expanded && row.needsKey && (
                  <div className="mt-3 pl-7 space-y-3">
                    <label className="block">
                      <span className="text-xs uppercase tracking-wide text-stone-500">
                        API key
                      </span>
                      <div className="mt-1 flex gap-2">
                        <input
                          type={showKey ? "text" : "password"}
                          value={apiKeys[row.id] ?? ""}
                          onChange={(e) => setApiKey(row.id, e.target.value)}
                          placeholder="sk-…"
                          autoComplete="off"
                          spellCheck={false}
                          className="flex-1 rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-forest-700"
                        />
                        <button
                          type="button"
                          onClick={() => setShowKey((v) => !v)}
                          className="text-xs text-stone-500 hover:text-stone-900 px-2 cursor-pointer"
                        >
                          {showKey ? "Hide" : "Show"}
                        </button>
                      </div>
                    </label>
                    <label className="block">
                      <span className="text-xs uppercase tracking-wide text-stone-500">
                        Model{row.requiresModel ? " (required)" : ""}
                      </span>
                      <input
                        type="text"
                        value={models[row.id] ?? ""}
                        onChange={(e) => setModel(row.id, e.target.value)}
                        placeholder={
                          row.requiresModel
                            ? "e.g. anthropic/claude-sonnet-4"
                            : row.defaultModel
                              ? `provider default (e.g. ${row.defaultModel})`
                              : "provider default"
                        }
                        className="mt-1 w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-forest-700"
                      />
                    </label>
                    {row.requiresModel && (
                      <p className="text-xs text-stone-500">
                        Browse available model IDs at{" "}
                        <a
                          href="https://openrouter.ai/models"
                          target="_blank"
                          rel="noreferrer"
                          className="underline hover:text-stone-900"
                        >
                          openrouter.ai/models
                        </a>
                        .
                      </p>
                    )}
                    <p className="text-xs text-stone-500">
                      Stored only in this browser and sent with each request —
                      never saved on our servers.
                    </p>
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      </aside>
    </div>
  );
}

// Today's free-tier allowance, fetched when the shared provider panel expands
// (which implies a signed-in session).
function SharedQuota() {
  const [quota, setQuota] = useState<QuotaStatus | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    getQuota()
      .then((status) => {
        if (active) setQuota(status);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
    };
  }, []);

  if (failed) return null;
  if (quota === null) {
    return <p className="text-xs text-stone-500">Checking today's allowance…</p>;
  }
  const exhausted = quota.used >= quota.limit;
  return (
    <p className={`text-xs ${exhausted ? "text-amber-700" : "text-stone-500"}`}>
      {quota.used} of {quota.limit} free requests used today
      {exhausted && <> — resets at {formatResetTime(quota.resets_at)}</>}. Site-wide limits may
      also apply.
    </p>
  );
}
