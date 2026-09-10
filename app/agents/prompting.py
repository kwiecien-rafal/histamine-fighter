"""Prompt loading and strict placeholder rendering.

Prompts live as markdown under ``app/agents/prompts``, one folder per agent plus
shared ``_partials``. ``load_prompt`` resolves ``{{> partial}}`` includes one
level deep; ``render_prompt`` fills ``{{name}}`` placeholders and fails loud on
any mismatch, so a broken template can never reach the model silently.
"""

import re
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_PARTIALS_DIR_NAME = "_partials"
_TAG = re.compile(r"\{\{\s*(>?)\s*([a-z_]+)\s*\}\}")


class PromptRenderError(ValueError):
    """A prompt template and its placeholders or includes do not line up."""


def _read_partial(name: str) -> str:
    path = _PROMPTS_DIR / _PARTIALS_DIR_NAME / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Unknown prompt partial '{name}' (expected {path}).")
    content = path.read_text(encoding="utf-8").strip()
    for match in _TAG.finditer(content):
        if match.group(1):
            raise PromptRenderError(
                f"Partial '{name}' includes '{match.group(2)}': includes are one level deep."
            )
    return content


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Load ``prompts/<name>.md`` with its ``{{> partial}}`` includes resolved."""
    template = (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")

    def _include(match: re.Match[str]) -> str:
        return _read_partial(match.group(2)) if match.group(1) else match.group(0)

    return _TAG.sub(_include, template)


def render_prompt(template: str, name: str = "inline template", /, **values: str) -> str:
    """Fill every ``{{name}}`` placeholder in ``template`` from ``values``."""
    if any(match.group(1) for match in _TAG.finditer(template)):
        raise PromptRenderError(
            f"Prompt '{name}' still contains a '{{{{> ...}}}}' include; load it first."
        )
    if "{{" in _TAG.sub("", template):
        raise PromptRenderError(f"Prompt '{name}' contains a malformed '{{{{' tag.")
    names = {match.group(2) for match in _TAG.finditer(template)}
    missing = names - values.keys()
    extra = values.keys() - names
    if missing or extra:
        raise PromptRenderError(
            f"Prompt '{name}': placeholders and values do not match: "
            f"missing={sorted(missing)}, unused={sorted(extra)}."
        )
    return _TAG.sub(lambda match: values[match.group(2)], template)


def strip_region_tags(value: str, tags: Iterable[str]) -> str:
    """Remove any literal region delimiter tags from user-supplied input."""
    alternation = "|".join(re.escape(tag) for tag in tags)
    if not alternation:
        return value
    return re.sub(rf"<\s*/?\s*(?:{alternation})\s*>", "", value, flags=re.IGNORECASE)
