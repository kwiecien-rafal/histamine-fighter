// Approximate USD list prices per million tokens, keyed by the `provider/model`
// string the backend reports on every response. Pricing is a presentation concern
// that changes far more often than the API contract (CLAUDE.md Section 19), so it
// lives here, never in the backend. Self-hosters can extend this table for their
// own models; an unlisted model simply shows no cost rather than a wrong one.

interface ModelPrice {
  inputPerMTok: number;
  outputPerMTok: number;
}

// List prices per 1M tokens (USD), grouped by provider. Approximate and editable;
// an unlisted model shows "—" rather than a wrong figure.
const PRICES: Record<string, ModelPrice> = {
  // OpenAI
  "openai/gpt-5.4-mini": { inputPerMTok: 0.75, outputPerMTok: 4.50 },
  // Anthropic
  "anthropic/claude-sonnet-4-6": { inputPerMTok: 3, outputPerMTok: 15 },
  "anthropic/claude-haiku-4-5": { inputPerMTok: 1, outputPerMTok: 5 },
  // Google
  "gemini/gemini-2.5-flash": { inputPerMTok: 0.3, outputPerMTok: 2.5 },
  "gemini/gemini-2.5-pro": { inputPerMTok: 1.25, outputPerMTok: 10 },
};

// Bump this whenever you edit the prices above; the panel shows it so users know
// how current the figures are.
export const PRICES_UPDATED = "2026-06-14";

// Providers whose models you run yourself, so there is no per-token bill.
const SELF_HOSTED_PREFIXES = ["ollama/", "modal/"];

export interface CostEstimate {
  // null when the model has no known price, so the UI shows "—" rather than $0.
  usd: number | null;
  selfHosted: boolean;
}

export function estimateCost(
  model: string,
  inputTokens: number,
  outputTokens: number,
): CostEstimate {
  if (SELF_HOSTED_PREFIXES.some((prefix) => model.startsWith(prefix))) {
    return { usd: 0, selfHosted: true };
  }
  const price = PRICES[model];
  if (!price) return { usd: null, selfHosted: false };
  const usd =
    (inputTokens * price.inputPerMTok + outputTokens * price.outputPerMTok) /
    1_000_000;
  return { usd, selfHosted: false };
}
