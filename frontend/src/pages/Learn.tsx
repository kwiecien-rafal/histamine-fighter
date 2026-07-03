import { useEffect, useState } from "react";

import { MAX_QUESTION_CHARS } from "../api/learn";
import { LLMProviderBadge } from "../components/LLMProviderBadge";
import { MedicalNote } from "../components/MedicalNote";
import { useLearnArticles, useLearnQuery } from "../hooks/useLearn";

// Show the counter only when the cap is actually in sight.
const COUNTER_THRESHOLD = 400;

export function Learn() {
  const [question, setQuestion] = useState("");
  const { articles } = useLearnArticles();
  const { response, asking, error, ask } = useLearnQuery();

  useEffect(() => {
    const previous = document.title;
    document.title = "Learn · Histamine Fighter";
    return () => {
      document.title = previous;
    };
  }, []);

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void ask(question);
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="font-serif text-3xl font-semibold text-forest-900 mb-1">
        Know your enemy
      </h1>
      <p className="text-stone-600 mb-8">
        Ask anything about histamine intolerance. Answers come only from our curated
        sources, with citations — and when the sources don't cover it, we say so instead
        of guessing.
      </p>

      <form onSubmit={onSubmit} className="mb-4">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={2}
          maxLength={MAX_QUESTION_CHARS}
          placeholder="e.g. Why do leftovers trigger symptoms?"
          aria-label="Your question"
          className="w-full rounded border border-stone-300 px-3 py-2 focus:outline-none focus:border-forest-700"
        />
        <div className="mt-2 flex items-center justify-between gap-3">
          <button
            type="submit"
            disabled={asking || !question.trim()}
            className="rounded bg-ember-600 hover:bg-ember-700 text-white px-4 py-2 disabled:opacity-50 enabled:cursor-pointer"
          >
            {asking ? "Searching…" : "Ask"}
          </button>
          {question.length >= COUNTER_THRESHOLD && (
            <p className="text-xs text-stone-500">
              {question.length} / {MAX_QUESTION_CHARS}
            </p>
          )}
        </div>
      </form>

      {articles && articles.length > 0 && (
        <div className="mb-8">
          <p className="text-sm text-stone-600 mb-2">Or start from a topic:</p>
          <div className="flex flex-wrap gap-2">
            {articles.map((article) => (
              <button
                key={article.slug}
                type="button"
                onClick={() => setQuestion(`Tell me about ${article.title.toLowerCase()}.`)}
                className="rounded-full border border-forest-200 bg-forest-50 px-3 py-1 text-sm text-forest-800 hover:bg-forest-100 cursor-pointer"
              >
                {article.title}
              </button>
            ))}
          </div>
        </div>
      )}

      {asking && (
        <p className="text-stone-600" aria-live="polite">
          Searching the sources…
        </p>
      )}

      {!asking && error && (
        <div role="alert" className="text-sm text-red-700">
          <span className="font-medium">Couldn't get an answer —</span> {error}{" "}
          <button
            type="button"
            onClick={() => void ask(question)}
            className="underline underline-offset-4 cursor-pointer"
          >
            Try again
          </button>
        </div>
      )}

      {!asking && !error && response && response.grounded && response.answer && (
        <article className="rounded border border-cream-200 bg-white p-5">
          <header className="flex items-start justify-between gap-3 mb-3">
            <p className="text-sm text-stone-500">{response.question}</p>
            <LLMProviderBadge model={response.model} />
          </header>
          <p className="text-stone-700 whitespace-pre-line">{response.answer}</p>
          {response.citations.length > 0 && (
            <footer className="mt-4 border-t border-cream-200 pt-3">
              <p className="text-sm font-medium text-stone-700 mb-1">Sources</p>
              <ul className="text-sm text-stone-600">
                {response.citations.map((citation) => (
                  <li key={citation.slug}>
                    {citation.title} — {citation.source}
                  </li>
                ))}
              </ul>
            </footer>
          )}
          <div className="mt-4">
            <MedicalNote />
          </div>
        </article>
      )}

      {!asking && !error && response && !response.grounded && (
        <div
          role="status"
          className="rounded border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900"
        >
          That one's outside our sources. We only answer what the curated library can
          back up — no improvising with your health. Try one of the topics above.
        </div>
      )}
    </div>
  );
}
