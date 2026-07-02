# Histamine Fighter

Agentic meal assistant for histamine intolerance: check if a dish is safe and get a low-histamine swap.

Histamine Fighter is an AI-first web app for people struggling with histamine intolerance, and having to constantly come up with dishes light on histamine.
> Status: work in progress. The schema, API, and UI still change. Currently only available if you fork the code and run the app yourself locally.

Current main functionalities:
- **Dish lookup.** Input a dish of your choosing and get a verdict (safe / depends / avoid), with per-ingredient, low-histamine swap suggestions.
- **Daily board.** Every day an AI Composer Agent generates four dishes for the day (breakfast, lunch, dinner, snack) from scratch.
- **[WIP] Learn.** Retrieval-grounded answers over a curated histamine knowledge base.
- **Bring your own model.** OpenAI, Anthropic, Gemini, OpenRouter, or local Ollama (available only for local builds), switchable per request from the in-app settings.

## Table of Contents

- [Background](#background)
- [Install](#install)
- [Usage](#usage)
- [Configuration](#configuration)
- [Operations](#operations)
- [Maintainers](#maintainers)
- [Contributing](#contributing)
- [License](#license)

## Background

In my family we are struggling with histamine intolerance on a daily basis. Personally, I always wished for a tool that would do two things: (1) Suggest a light-on-histamine dish replacement that is close to my given dish by some degree, and (2) Recommend me a dish based on my current whim. This app aims to fulfill these two wishes. Combined with a knowledge base and per-ingredient lookup, I wish for people with histamine intolerance to be, at least a little more, free of the headaches it causes when it comes to meal planning.

The issue at hand is that even if we know a dish ingredient is either heavy on histamine, or acts as a liberator or the DAO enzyme blocker, it may be challenging to think of a replacement that would fit into said dish. Not everything blends together nicely, and you cannot possibly remember the hundreds of ingredients that might be harmful to you. Because of these problems, leveraging LLMs might be a perfect solution, using science-based knowledge and curated ingredient list. Of course, given it's a medically grounded issue, critical decisions cannot be made by a model.

## Stack

- **Backend.** FastAPI (async), SQLAlchemy 2, Pydantic v2. Business logic lives in a service layer with no HTTP awareness.
- **AI layer.** LangChain chat models behind one swappable seam; agents return typed Pydantic results. Prompts stored Markdown files.
- **Retrieval.** Postgres with pgvector and local fastembed embeddings back the Learn answers and meal composition.
- **Frontend.** React 19, Vite, TypeScript, Tailwind. The dev server proxies the API so the admin session cookie stays same-origin.

Using: Python 3.12, FastAPI, SQLAlchemy (async), Alembic, LangChain, pgvector, fastembed, React 19, Vite, TypeScript, Tailwind, Docker Compose, Postgres 16.

## Install

Requires Docker, [uv](https://docs.astral.sh/uv/), and Node 20+.

```bash
cp .env.example .env

# 1. Start Postgres (pgvector)
docker compose up -d db

# 2. Apply the schema and seed the factual data (run from the host)
uv run --directory backend alembic upgrade head
uv run --directory backend python -m app.scripts.seed_histamine_db
uv run --directory backend python -m app.scripts.seed_knowledge

# 3. Create an admin account (you can skip this if all you want is the dish replacement)
uv run --directory backend python -m app.scripts.create_admin --email you@example.com

# 4. Start the backend and frontend
docker compose up -d --build backend frontend
```

Migrations and seeds run on the host, not in the backend container: the image ships only the application code, while Alembic and the seed data live in the repo.

## Usage

Open http://localhost:5173. Pick a provider and paste an API key in the in-app settings, or run a local Ollama model. Server-side keys in `.env` are only needed for the daily-board generation script.

Run the services natively instead of in Docker:

```bash
uv run --directory backend uvicorn app.main:app --reload
cd frontend && npm install && npm run dev
```

Generate a day's meal board (needs a tool-calling model configured):

```bash
uv run --directory backend python -m app.scripts.generate_daily_meals
```

After changing a model, author a migration, review it, then apply it:

```bash
uv run --directory backend alembic revision --autogenerate -m "describe change"
uv run --directory backend alembic upgrade head
```

## Configuration

All settings are environment variables, documented inline in `.env.example`. The app reads `.env` from the repo root. User-supplied LLM keys travel as request headers and are never stored.

## Operations

Production runs on managed Postgres (Supabase). Most state is regenerable: meals can be recomposed by the agent and daily boards re-run from the cron. What cannot be regenerated is the human curation layer, the approvals and any hand-edits to a composed meal. That is the data worth protecting.

- **Backups.** Supabase takes automated daily backups, and supports point-in-time recovery (PITR) on its paid tiers. Enable PITR for the production project so a bad migration or an accidental delete can be rolled back to the minute. Restore from the Supabase dashboard (Database, then Backups), or restore into a fresh project and repoint `DATABASE_URL`.
- **Migrations.** `alembic upgrade head` runs on every deploy, before the new backend starts. Author and review migrations locally; never edit the schema by hand.
- **Least privilege.** The application connects with a role that can read and write the app tables but cannot alter the schema. Migrations run under a separate, higher-privilege role at deploy time only.
- **History retention.** The nightly board cron prunes `daily_suggestions` older than `DAILY_HISTORY_DAYS` (default 7), the same window the public past-board view reads, so the table stays bounded to what can be shown.

## Maintainers

[Rafał Kwiecień](https://github.com/kwiecien-rafal)

## Contributing

Issues and pull requests are welcome. Open an issue to ask a question or propose a change
before sending a large PR.

- Tests are required for any new feature.
- Run `ruff format` and `ruff check` before committing.
- Use Conventional Commits, for example `feat(agents): add streaming recipe support`.

## License

[MIT](LICENSE) (c) 2026 Rafał Kwiecień
