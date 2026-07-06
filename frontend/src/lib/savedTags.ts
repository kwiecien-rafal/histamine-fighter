// The closed tag vocabulary for saved meals, mirroring the backend's SavedMealTag
// enum: meal slots, a dish-check provenance tag, and six color labels. Free text
// is not accepted; the picker and the card visuals both key off this list.

export const COLOR_TAGS = ["pink", "red", "green", "blue", "yellow", "orange"] as const;
export type ColorTag = (typeof COLOR_TAGS)[number];

export const SAVED_MEAL_TAGS = [
  "breakfast",
  "lunch",
  "dinner",
  "snack",
  "dish_check",
  ...COLOR_TAGS,
] as const;
export type SavedMealTag = (typeof SAVED_MEAL_TAGS)[number];

export const SAVED_TAG_LABEL: Record<SavedMealTag, string> = {
  breakfast: "Breakfast",
  lunch: "Lunch",
  dinner: "Dinner",
  snack: "Snack",
  dish_check: "From dish check",
  pink: "Pink",
  red: "Red",
  green: "Green",
  blue: "Blue",
  yellow: "Yellow",
  orange: "Orange",
};

export const SAVED_TAG_COLORS: Record<ColorTag, string> = {
  pink: "bg-pink-400",
  red: "bg-red-500",
  green: "bg-green-500",
  blue: "bg-blue-500",
  yellow: "bg-yellow-400",
  orange: "bg-orange-500",
};

export function isColorTag(tag: string): tag is ColorTag {
  return (COLOR_TAGS as readonly string[]).includes(tag);
}

export function isSavedMealTag(tag: string): tag is SavedMealTag {
  return (SAVED_MEAL_TAGS as readonly string[]).includes(tag);
}
