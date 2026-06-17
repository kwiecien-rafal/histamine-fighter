interface UnverifiedNoteProps {
  ingredients: string[];
}

// Ingredients the index could not vouch for. The automated gate lets them through
// (a miss is unknown, not unsafe), so the reviewer eyeballs them before approving.
// Admin-only: the public board never shows this.
export function UnverifiedNote({ ingredients }: UnverifiedNoteProps) {
  if (ingredients.length === 0) return null;
  return (
    <div className="mb-4 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
      <span className="font-semibold">Not in the index — check before approving:</span>{" "}
      {ingredients.join(", ")}
    </div>
  );
}
