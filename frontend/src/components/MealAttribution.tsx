import { modelAttribution } from "../lib/meal";
import { LLMProviderBadge } from "./LLMProviderBadge";

interface MealAttributionProps {
  model: string;
}

// The provenance line on a public meal card: "Composed by {model}" for an AI meal, or the
// bare "Curated by admin" badge for a hand-authored one, where no model did the composing.
export function MealAttribution({ model }: MealAttributionProps) {
  return (
    <span className="flex items-center gap-2">
      {!modelAttribution(model).isManual && "Composed by"}
      <LLMProviderBadge model={model} />
    </span>
  );
}
