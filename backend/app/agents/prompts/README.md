# Agent prompts

One folder per agent, plus shared `_partials/`. Loaded and rendered by `app/agents/prompting.py`.

Conventions:

- **Never hard-wrap prose.** One paragraph per line; soft-wrap in your editor. Mid-sentence newlines change what the model sees and make diffs reflow on a one-word edit.
- `{{> name}}` includes `_partials/name.md`. Includes are one level deep — a partial cannot include another partial.
- `{{name}}` is a placeholder filled by `render_prompt`. Rendering is strict: a missing value, an unused value, or a malformed tag raises.
- Static instruction belongs in `*system*.md` (stable across calls, prompt-cache friendly). `*user*.md` carries only the delimited variable input plus a one-line restatement of the output contract.
- Call sites pass user input through `strip_closing_tag` before rendering, so the input cannot close its own delimiter and break out of the data region.
- When editing a partial, every prompt that includes it changes — review the assembly tests in `backend/tests/test_prompting.py`.
- Prompts and responses are logged chronologically at the invocation boundary, debug level only. Each model call emits a `<agent>.request` event with the messages exactly as sent (via `loggable_messages`), and its response is logged next to it (`dish_lookup.reply` per loop turn, `dish_lookup.synthesis_reply`, `learn.reply`). Inside the tool loop, later turns log deltas (tool results, replies) rather than re-dumping the growing transcript. Rendering itself logs nothing.
