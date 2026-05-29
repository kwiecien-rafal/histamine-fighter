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
  apiKey: string;
  model: string;
  ollamaBaseUrl: string;
  setProvider: (p: Provider) => void;
  setApiKey: (k: string) => void;
  setModel: (m: string) => void;
  setOllamaBaseUrl: (u: string) => void;
}

export const useLLMProviderStore = create<LLMProviderState>()(
  persist(
    (set) => ({
      provider: "ollama",
      apiKey: "",
      model: "",
      ollamaBaseUrl: "",
      setProvider: (provider) => set({ provider }),
      setApiKey: (apiKey) => set({ apiKey }),
      setModel: (model) => set({ model }),
      setOllamaBaseUrl: (ollamaBaseUrl) => set({ ollamaBaseUrl }),
    }),
    { name: "histamine-fighter:llm" },
  ),
);
