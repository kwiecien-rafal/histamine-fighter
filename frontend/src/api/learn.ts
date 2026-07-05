import { useLLMProviderStore } from "../store/llmProvider";
import { notifySessionExpired } from "./auth";
import { buildLLMHeaders } from "./client";
import { QuotaError, errorDetail, errorFromResponse } from "./errors";

// Mirrors MAX_QUESTION_LENGTH in app/schemas/learn.py.
export const MAX_QUESTION_CHARS = 500;

export interface LearnCitation {
  title: string;
  source: string;
  slug: string;
}

// answer is null when the curated sources cannot back an answer (grounded=false);
// the page renders that as an honest decline, not an error.
export interface LearnResponse {
  question: string;
  answer: string | null;
  grounded: boolean;
  citations: LearnCitation[];
  model: string;
}

export interface LearnArticle {
  slug: string;
  title: string;
  topic: string;
}

// The query rides the user's LLM provider override headers, like the dish flow.
export async function askLearn(question: string): Promise<LearnResponse> {
  const response = await fetch("/api/v1/learn/query", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...buildLLMHeaders() },
    body: JSON.stringify({ question }),
    credentials: "include",
  });
  if (!response.ok) {
    // A 401 on the shared tier means the session cookie died mid-session; flip
    // the UI to signed-out now instead of letting the navbar lie until reload.
    if (response.status === 401 && useLLMProviderStore.getState().provider === "shared") {
      notifySessionExpired();
    }
    const error = await errorFromResponse(response);
    // A daily-quota 429 carries its own copy and reset time; only the plain
    // burst limit gets the friendlier per-minute wording.
    if (response.status === 429 && !(error instanceof QuotaError)) {
      throw new Error(
        "You're asking faster than the free tier allows. Catch your breath and try again in a minute.",
      );
    }
    throw error;
  }
  return (await response.json()) as LearnResponse;
}

export async function listLearnArticles(): Promise<LearnArticle[]> {
  const response = await fetch("/api/v1/learn/articles");
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const payload = (await response.json()) as { articles: LearnArticle[] };
  return payload.articles;
}
