import type { CautionedIngredient } from "../api/domain";

interface CautionedNoteProps {
  ingredients: CautionedIngredient[];
}

// Moderately compatible ingredients the composer kept within its cap. The note is
// the curated index's own guidance, shown to the reviewer and the visitor alike so
// "in moderation" is never silent context.
export function CautionedNote({ ingredients }: CautionedNoteProps) {
  if (ingredients.length === 0) return null;
  return (
    <div className="mb-4 rounded border border-orange-300 bg-orange-50 px-3 py-2 text-sm text-orange-900">
      <span className="font-semibold">Enjoy in moderation:</span>
      <ul className="mt-1 flex flex-col gap-0.5">
        {ingredients.map((item) => (
          <li key={item.name}>
            <span className="font-medium">{item.name}</span>: {item.note}
          </li>
        ))}
      </ul>
    </div>
  );
}
