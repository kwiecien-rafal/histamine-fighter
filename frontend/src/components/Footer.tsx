import { Link } from "react-router-dom";

const GITHUB_REPO_URL = "https://github.com/kwiecien-rafal/histamine-fighter";

// The shared public footer. Carries the site-wide medical disclaimer, so it must
// render on every public page (via Layout); the admin page keeps its own chrome.
export function Footer() {
  return (
    <footer className="border-t border-cream-200 bg-cream-100">
      <div className="max-w-5xl mx-auto flex flex-col gap-3 px-6 py-8 text-sm text-stone-600">
        <p>
          <Link to="/" className="font-serif font-semibold text-forest-900">
            Histamine Fighter
          </Link>{" "}
          — in your corner against histamine intolerance.
        </p>
        <p>
          Histamine Fighter is an educational tool, not medical advice. Histamine tolerance is
          highly individual — verdicts and meal suggestions are general guidance built from a
          curated ingredient index and an AI assistant, and they can be wrong. Always confirm
          with your doctor or dietitian before changing your diet.
        </p>
        <p className="text-stone-500">
          An open-source portfolio project · MIT license ·{" "}
          <a
            href={GITHUB_REPO_URL}
            target="_blank"
            rel="noreferrer"
            className="underline hover:text-stone-700"
          >
            GitHub
          </a>{" "}
          ·{" "}
          <Link to="/privacy" className="underline hover:text-stone-700">
            Privacy
          </Link>{" "}
          ·{" "}
          <Link to="/terms" className="underline hover:text-stone-700">
            Terms
          </Link>
        </p>
      </div>
    </footer>
  );
}
