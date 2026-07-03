"""Python-owned variety for the composer: a sampled brief the model starts from.

Repetition is code's problem, not the sampling temperature's: an LLM given an
identical prompt collapses to its modal dish, so the entropy is drawn here with
``random`` and handed to the model as a brief. The anchor pools live in
``seed_data/culinary_anchors.json``, fork-editable like the ingredient seed. The
hero ingredient is drawn from the curated index's well-tolerated rows, so the
model is never inspired toward something it must immediately abandon, and recent
board names ride along as a do-not-repeat list.
"""

import random
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

from app.enums import MealType

_ANCHORS_FILE = Path(__file__).resolve().parents[2] / "seed_data" / "culinary_anchors.json"

# Enough recent names to steer away from without flooding the prompt.
_MAX_AVOID_NAMES = 60


class CulinaryAnchors(BaseModel):
    """The anchor pools a brief is drawn from, one format pool per meal type."""

    cuisines: list[str]
    techniques: list[str]
    flavor_profiles: list[str]
    formats: dict[MealType, list[str]]


class InspirationBrief(BaseModel):
    """One drawn direction for a composition, rendered into the compose prompt."""

    cuisine: str
    technique: str
    dish_format: str
    flavor_profile: str
    hero_ingredient: str | None = None
    avoid_names: list[str] = Field(default_factory=list)

    def prompt_lines(self) -> str:
        """The brief as the bullet block the compose prompt embeds."""
        lines = [
            "Compose to this drawn direction. It is a starting point, not a "
            "straitjacket: when the index fights a line, keep its spirit and adapt.",
            f"- Cuisine direction: {self.cuisine}",
            f"- Format: {self.dish_format}",
            f"- Technique: {self.technique}",
            f"- Flavour profile: {self.flavor_profile}",
        ]
        if self.hero_ingredient:
            lines.append(
                f"- Hero ingredient, already verified well tolerated: {self.hero_ingredient}"
            )
        if self.avoid_names:
            lines.append(
                "- Recent dishes. Do not propose these or near-duplicates: "
                + "; ".join(self.avoid_names)
            )
        return "\n".join(lines)

    def summary(self) -> str:
        """The draw as one trace-friendly line."""
        parts = [self.cuisine, self.dish_format, self.technique, self.flavor_profile]
        if self.hero_ingredient:
            parts.append(f"hero: {self.hero_ingredient}")
        return ", ".join(parts)


@lru_cache(maxsize=1)
def load_anchors() -> CulinaryAnchors:
    """Load and validate the anchor pools once per process."""
    return CulinaryAnchors.model_validate_json(_ANCHORS_FILE.read_text(encoding="utf-8"))


def sample_brief(
    meal_type: MealType,
    *,
    hero_pool: Sequence[str],
    avoid_names: Sequence[str] = (),
    rng: random.Random | None = None,
    anchors: CulinaryAnchors | None = None,
) -> InspirationBrief:
    """Draw one brief for a slot.

    Args:
        meal_type: The slot being composed.
        hero_pool: Well-tolerated index names to draw the hero from; empty skips it.
        avoid_names: Recent dish names the model must not repeat, newest first.
        rng: The entropy source. Inject a seeded one for a reproducible draw (the
            daily script keys it on date, slot, and attempt); ``None`` draws fresh.
        anchors: Anchor pool override for tests; defaults to the seed file.
    """
    pools = anchors or load_anchors()
    draw = rng if rng is not None else random.Random()
    return InspirationBrief(
        cuisine=draw.choice(pools.cuisines),
        technique=draw.choice(pools.techniques),
        dish_format=draw.choice(pools.formats[meal_type]),
        flavor_profile=draw.choice(pools.flavor_profiles),
        hero_ingredient=draw.choice(list(hero_pool)) if hero_pool else None,
        avoid_names=list(avoid_names)[:_MAX_AVOID_NAMES],
    )
