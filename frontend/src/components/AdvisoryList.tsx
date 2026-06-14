import type { Advisory } from "../api/client";

interface AdvisoryListProps {
  advisories: Advisory[];
}

// Depends-level ingredients are worth a note, never a swap — amber to match
// the "depends" tone of the verdict badge and safety chips.
export function AdvisoryList({ advisories }: AdvisoryListProps) {
  return (
    <section className="mb-4">
      <h3 className="text-xs uppercase tracking-wide text-stone-500 mb-2">
        Worth watching
      </h3>
      <ul className="flex flex-col gap-2">
        {advisories.map((advisory) => (
          <li
            key={advisory.ingredient}
            className="rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
          >
            <span className="font-medium">{advisory.ingredient}</span>
            <span className="mx-1.5 text-amber-700">—</span>
            {advisory.note}
          </li>
        ))}
      </ul>
    </section>
  );
}
