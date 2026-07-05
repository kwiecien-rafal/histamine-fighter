import { beforeEach, describe, expect, it } from "vitest";

import { buildLLMHeaders } from "../api/client";
import { useLLMProviderStore } from "./llmProvider";

describe("llmProvider store", () => {
  beforeEach(() => {
    useLLMProviderStore.setState({
      provider: "ollama",
      apiKeys: {},
      models: {},
      ollamaBaseUrl: "",
    });
  });

  it("sends only the provider header on the shared tier", () => {
    useLLMProviderStore.setState({
      provider: "shared",
      // Poisoned state: even a stale key/model for another provider must not leak.
      apiKeys: { openai: "sk-user-own-key" },
      models: { openai: "gpt-5.4" },
    });

    expect(buildLLMHeaders()).toEqual({ "X-LLM-Provider": "shared" });
  });

  it("still sends the full override headers for BYO-key providers", () => {
    useLLMProviderStore.setState({
      provider: "openai",
      apiKeys: { openai: "sk-test" },
      models: { openai: "gpt-5.4-mini" },
    });

    expect(buildLLMHeaders()).toEqual({
      "X-LLM-Provider": "openai",
      "X-LLM-Model": "gpt-5.4-mini",
      "X-LLM-API-Key": "sk-test",
    });
  });

  it("migrates a persisted v1 'modal' provider to the deployment default", () => {
    const migrate = useLLMProviderStore.persist.getOptions().migrate;
    expect(migrate).toBeDefined();

    const migrated = migrate!(
      { provider: "modal", apiKeys: {}, models: {}, ollamaBaseUrl: "" },
      1,
    ) as { provider: string };

    // VITE_PUBLIC_DEPLOYMENT is unset in tests, so the default is ollama.
    expect(migrated.provider).toBe("ollama");
  });

  it("keeps a known persisted provider through migration", () => {
    const migrate = useLLMProviderStore.persist.getOptions().migrate;

    const migrated = migrate!(
      { provider: "anthropic", apiKeys: {}, models: {}, ollamaBaseUrl: "" },
      1,
    ) as { provider: string };

    expect(migrated.provider).toBe("anthropic");
  });
});
