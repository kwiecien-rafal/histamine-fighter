import { useState } from "react";

import { useLLMProviderStore, type Provider } from "../store/llmProvider";

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
  { id: "ollama", label: "Local Ollama", note: "Self-hosted, free, no API key.", ready: true, needsKey: false },
  { id: "openai", label: "OpenAI", note: "Use your own OpenAI API key.", ready: true, needsKey: true, defaultModel: "gpt-4o-mini" },
  { id: "modal", label: "Modal (hosted default)", note: "Coming in a later release.", ready: false, needsKey: false },
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

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex" aria-modal="true" role="dialog">
      <button
        type="button"
        aria-label="Close settings"
        className="flex-1 bg-stone-900/30"
        onClick={onClose}
      />
      <aside className="w-full max-w-md h-full bg-white border-l border-stone-200 shadow-xl overflow-y-auto">
        <header className="flex items-center justify-between px-5 py-4 border-b border-stone-200">
          <h2 className="text-lg font-semibold">LLM provider</h2>
          <button
            type="button"
            onClick={onClose}
            className="text-stone-500 hover:text-stone-900"
          >
            ✕
          </button>
        </header>

        <ul className="divide-y divide-stone-100">
          {PROVIDERS.map((row) => {
            const isOllamaOnPublic = row.id === "ollama" && PUBLIC_DEPLOYMENT;
            const disabled = !row.ready || isOllamaOnPublic;
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
                    className="mt-1 accent-emerald-800"
                    checked={selected}
                    disabled={disabled}
                    onChange={() => setProvider(row.id)}
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
                    {disabled && (
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
                        className="mt-1 w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
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
                        className="mt-1 w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
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
                          className="flex-1 rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
                        />
                        <button
                          type="button"
                          onClick={() => setShowKey((v) => !v)}
                          className="text-xs text-stone-500 hover:text-stone-900 px-2"
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
                        className="mt-1 w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
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
