import { useLLMProviderStore, type Provider } from "../store/llmProvider";

const PUBLIC_DEPLOYMENT = import.meta.env.VITE_PUBLIC_DEPLOYMENT === "true";

const SELF_HOST_GUIDE_URL = "/guides/self-host";

interface ProviderRow {
  id: Provider;
  label: string;
  note: string;
}

const PROVIDERS: ProviderRow[] = [
  { id: "ollama", label: "Local Ollama", note: "Self-hosted, free, no API key." },
  { id: "modal", label: "Modal (hosted default)", note: "Coming in a later release." },
  { id: "openai", label: "OpenAI", note: "Coming in a later release." },
  { id: "anthropic", label: "Anthropic", note: "Coming in a later release." },
  { id: "gemini", label: "Google Gemini", note: "Coming in a later release." },
  { id: "openrouter", label: "OpenRouter (many models)", note: "Coming in a later release." },
];

interface SettingsDrawerProps {
  open: boolean;
  onClose: () => void;
}

export function SettingsDrawer({ open, onClose }: SettingsDrawerProps) {
  const provider = useLLMProviderStore((s) => s.provider);
  const model = useLLMProviderStore((s) => s.model);
  const ollamaBaseUrl = useLLMProviderStore((s) => s.ollamaBaseUrl);
  const setProvider = useLLMProviderStore((s) => s.setProvider);
  const setModel = useLLMProviderStore((s) => s.setModel);
  const setOllamaBaseUrl = useLLMProviderStore((s) => s.setOllamaBaseUrl);

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
            const isPending = row.id !== "ollama";
            const disabled = isOllamaOnPublic || isPending;
            const selected = provider === row.id;

            return (
              <li key={row.id} className="px-5 py-4">
                <label
                  className={`flex items-start gap-3 ${
                    disabled ? "opacity-60" : "cursor-pointer"
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

                {row.id === "ollama" && !disabled && selected && (
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
                        value={model}
                        onChange={(e) => setModel(e.target.value)}
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
              </li>
            );
          })}
        </ul>
      </aside>
    </div>
  );
}
