"""Prompt loading and strict placeholder rendering (CLAUDE §8).

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
    """Load ``prompts/<name>.md`` with its ``{{> partial}}`` includes resolved.

    Variable placeholders are left in place for :func:`render_prompt`.

    Args:
        name: Path of the prompt relative to the prompts directory, without the
            ``.md`` suffix, e.g. ``"dish_lookup/system"``.

    Returns:
        The template text with every include replaced by its partial's content.
    """
    template = (_PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")

    def _include(match: re.Match[str]) -> str:
        return _read_partial(match.group(2)) if match.group(1) else match.group(0)

    return _TAG.sub(_include, template)


def render_prompt(template: str, name: str = "inline template", /, **values: str) -> str:
    """Fill every ``{{name}}`` placeholder in ``template`` from ``values``.

    Strict both ways: a placeholder without a value, a value without a
    placeholder, an unresolved include, or a malformed ``{{`` tag raises
    :class:`PromptRenderError`. Braces inside the substituted values themselves
    are user data and pass through untouched.

    Args:
        template: A template returned by :func:`load_prompt`.
        name: The template's prompt name, named in errors so a failure at agent
            startup points at the right file. Positional-only, so it can never
            collide with a ``{{name}}`` placeholder.
        **values: One string per ``{{name}}`` placeholder in the template.

    Returns:
        The rendered prompt.
    """
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
    """Remove any literal region delimiter tags from user-supplied input.

    Each user template wraps its variable inputs in named ``<tag>`` … ``</tag>``
    regions. Stripping only a value's own closing tag is not enough: an input
    could forge a *different* region's delimiter to make attacker text look like
    a trusted, code-owned section (e.g. a dish name emitting ``<verdict>``). So
    both the opening and closing form of *every* region tag in the prompt are
    removed, leaving the input unable to close its own region or fake another.

    Args:
        value: The user-supplied text about to fill a delimited placeholder.
        tags: The prompt's region tag names, e.g. ``("dish_text", "verdict")``.

    Returns:
        ``value`` with every spoofed region tag removed, case-insensitively.
    """
    alternation = "|".join(re.escape(tag) for tag in tags)
    if not alternation:
        return value
    return re.sub(rf"<\s*/?\s*(?:{alternation})\s*>", "", value, flags=re.IGNORECASE)
