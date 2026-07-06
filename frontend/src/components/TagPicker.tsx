import {
  SAVED_MEAL_TAGS,
  SAVED_TAG_COLORS,
  SAVED_TAG_LABEL,
  isColorTag,
} from "../lib/savedTags";

interface TagPickerProps {
  value: string[];
  onChange: (tags: string[]) => void;
}

// Multi-select over the closed saved-meal vocabulary: labelled chips for the
// meal slots and dish-check, colored circles for the color labels. Output keeps
// the vocabulary's order regardless of click order, so tag rows render stably.
export function TagPicker({ value, onChange }: TagPickerProps) {
  function toggle(tag: string) {
    const next = value.includes(tag)
      ? value.filter((item) => item !== tag)
      : [...value, tag];
    onChange(SAVED_MEAL_TAGS.filter((item) => next.includes(item)));
  }

  return (
    <div role="group" aria-label="Tags" className="flex flex-wrap items-center gap-1.5">
      {SAVED_MEAL_TAGS.map((tag) => {
        const selected = value.includes(tag);
        return isColorTag(tag) ? (
          <button
            key={tag}
            type="button"
            onClick={() => toggle(tag)}
            aria-pressed={selected}
            aria-label={SAVED_TAG_LABEL[tag]}
            title={SAVED_TAG_LABEL[tag]}
            className={`h-5 w-5 rounded-full cursor-pointer transition-shadow ${SAVED_TAG_COLORS[tag]} ${
              selected
                ? "ring-2 ring-offset-1 ring-stone-600"
                : "hover:ring-2 hover:ring-offset-1 hover:ring-stone-300"
            }`}
          />
        ) : (
          <button
            key={tag}
            type="button"
            onClick={() => toggle(tag)}
            aria-pressed={selected}
            className={`font-mono text-[10px] uppercase tracking-wide border rounded px-1.5 py-0.5 cursor-pointer transition-colors ${
              selected
                ? "text-forest-800 bg-forest-50 border-forest-300"
                : "text-stone-500 bg-white border-stone-200 hover:border-forest-300"
            }`}
          >
            {SAVED_TAG_LABEL[tag]}
          </button>
        );
      })}
    </div>
  );
}
