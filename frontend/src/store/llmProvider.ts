import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Provider =
  | "shared"
  | "ollama"
  | "openai"
  | "anthropic"
  | "gemini"
  | "openrouter";

const PUBLIC_DEPLOYMENT = import.meta.env.VITE_PUBLIC_DEPLOYMENT === "true";

// Hosted visitors start on the shared tier (it works after one sign-in, no key);
// self-hosters start on their own Ollama.
const DEFAULT_PROVIDER: Provider = PUBLIC_DEPLOYMENT ? "shared" : "ollama";

const KNOWN_PROVIDERS: readonly Provider[] = [
  "shared",
  "ollama",
  "openai",
  "anthropic",
  "gemini",
  "openrouter",
];

interface LLMProviderState {
  provider: Provider;
  apiKeys: Partial<Record<Provider, string>>;
  models: Partial<Record<Provider, string>>;
  ollamaBaseUrl: string;
  setProvider: (p: Provider) => void;
  setApiKey: (p: Provider, key: string) => void;
  setModel: (p: Provider, model: string) => void;
  setOllamaBaseUrl: (u: string) => void;
}

export const useLLMProviderStore = create<LLMProviderState>()(
  persist(
    (set) => ({
      provider: DEFAULT_PROVIDER,
      apiKeys: {},
      models: {},
      ollamaBaseUrl: "",
      setProvider: (provider) => set({ provider }),
      setApiKey: (p, key) =>
        set((s) => ({ apiKeys: { ...s.apiKeys, [p]: key } })),
      setModel: (p, model) =>
        set((s) => ({ models: { ...s.models, [p]: model } })),
      setOllamaBaseUrl: (ollamaBaseUrl) => set({ ollamaBaseUrl }),
    }),
    {
      name: "histamine-fighter:llm",
      version: 2,
      // v1 could persist "modal" (a never-released placeholder); any provider this
      // version doesn't know falls back to the deployment default.
      migrate: (persisted) => {
        const state = persisted as Partial<LLMProviderState> | undefined;
        if (
          state?.provider !== undefined &&
          !KNOWN_PROVIDERS.includes(state.provider)
        ) {
          state.provider = DEFAULT_PROVIDER;
        }
        return state as LLMProviderState;
      },
    },
  ),
);
