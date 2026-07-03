import { useCallback, useEffect, useRef, useState } from "react";

import { errorMessage } from "../api/errors";
import { askLearn, listLearnArticles, type LearnArticle, type LearnResponse } from "../api/learn";

// The topic chips are a nicety: a failed listing just means no chips, never an error.
export function useLearnArticles(): { articles: LearnArticle[] | null } {
  const [articles, setArticles] = useState<LearnArticle[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listLearnArticles()
      .then((items) => {
        if (!cancelled) setArticles(items);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  return { articles };
}

interface LearnQueryState {
  response: LearnResponse | null;
  asking: boolean;
  error: string | null;
  ask: (question: string) => Promise<void>;
}

// One in-flight question at a time; a stale resolution (the user already asked again)
// checks its token and bows out so the answer shown always matches the latest ask.
export function useLearnQuery(): LearnQueryState {
  const [response, setResponse] = useState<LearnResponse | null>(null);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestAsk = useRef(0);

  const ask = useCallback(async (question: string) => {
    const token = ++latestAsk.current;
    setAsking(true);
    setError(null);
    try {
      const result = await askLearn(question);
      if (token !== latestAsk.current) return;
      setResponse(result);
    } catch (err) {
      if (token !== latestAsk.current) return;
      setError(errorMessage(err));
    } finally {
      if (token === latestAsk.current) setAsking(false);
    }
  }, []);

  return { response, asking, error, ask };
}
