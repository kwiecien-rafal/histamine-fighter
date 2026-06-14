# Agent prompts

One folder per agent, plus shared `_partials/`. Loaded and rendered by `app/agents/prompting.py`.

Conventions:

- **Never hard-wrap prose.** One paragraph per line; soft-wrap in your editor. Mid-sentence newlines change what the model sees and make diffs reflow on a one-word edit.
- `{{> name}}` includes `_partials/name.md`. Includes are one level deep — a partial cannot include another partial.
- `{{name}}` is a placeholder filled by `render_prompt`. Rendering is strict: a missing value, an unused value, or a malformed tag raises.
- Static instruction belongs in `*system*.md` (stable across calls, prompt-cache friendly). `*user*.md` carries only the delimited variable input plus a one-line restatement of the output contract.
- Call sites pass user input through `strip_region_tags` before rendering, against the prompt's full set of region tags, so the input can neither close its own delimiter nor forge a sibling region's to break out of the data region.
- When editing a partial, every prompt that includes it changes — review the assembly tests in `backend/tests/test_prompting.py`.
- Prompts and responses are logged chronologically at the invocation boundary, debug level only. Each model call emits a `*_request` event with the messages exactly as sent (via `loggable_messages`), and its reply is logged next to it (`dish_lookup.propose_reply`, `dish_lookup.synthesis_reply`, `dish_lookup.alternatives_reply`, `learn.reply`). Rendering itself logs nothing.
