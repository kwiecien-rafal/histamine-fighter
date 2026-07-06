import { create } from "zustand";
import { persist } from "zustand/middleware";

import { useSessionStore } from "./session";

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
  // False until the user picks a provider themselves. A defaulted provider may be
  // switched to the shared tier on sign-in; an explicit choice never is.
  providerChosen: boolean;
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
      providerChosen: false,
      apiKeys: {},
      models: {},
      ollamaBaseUrl: "",
      setProvider: (provider) => set({ provider, providerChosen: true }),
      setApiKey: (p, key) =>
        set((s) => ({ apiKeys: { ...s.apiKeys, [p]: key } })),
      setModel: (p, model) =>
        set((s) => ({ models: { ...s.models, [p]: model } })),
      setOllamaBaseUrl: (ollamaBaseUrl) => set({ ollamaBaseUrl }),
    }),
    {
      name: "histamine-fighter:llm",
      version: 3,
      // v1 could persist "modal" (a never-released placeholder); any provider this
      // version doesn't know falls back to the deployment default. v2 predates
      // providerChosen: a persisted non-default provider reads as an explicit
      // choice, a default one as never chosen.
      migrate: (persisted) => {
        const state = persisted as Partial<LLMProviderState> | undefined;
        if (
          state?.provider !== undefined &&
          !KNOWN_PROVIDERS.includes(state.provider)
        ) {
          state.provider = DEFAULT_PROVIDER;
        }
        if (state !== undefined && state.providerChosen === undefined) {
          state.providerChosen =
            state.provider !== undefined && state.provider !== DEFAULT_PROVIDER;
        }
        return state as LLMProviderState;
      },
    },
  ),
);

// Signing in defaults a never-chosen provider to the shared tier (it now works,
// no key needed) without marking it chosen, so it stays a default the user can
// override. Sign-out deliberately leaves the provider alone.
useSessionStore.subscribe((session, previous) => {
  if (session.status === "authed" && previous.status !== "authed") {
    const { providerChosen } = useLLMProviderStore.getState();
    if (!providerChosen) useLLMProviderStore.setState({ provider: "shared" });
  }
});
