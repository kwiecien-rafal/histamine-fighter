import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Provider =
  | "ollama"
  | "openai"
  | "anthropic"
  | "gemini"
  | "openrouter"
  | "modal";

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
      provider: "ollama",
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
    { name: "histamine-fighter:llm", version: 1 },
  ),
);
