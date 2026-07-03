import { buildLLMHeaders } from "./client";
import { errorDetail } from "./errors";

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
  });
  if (response.status === 429) {
    throw new Error(
      "You're asking faster than the free tier allows. Catch your breath and try again in a minute.",
    );
  }
  if (!response.ok) {
    throw new Error(await errorDetail(response));
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
