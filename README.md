# Histamine Fighter

Agentic meal assistant for histamine intolerance: **get a low-histamine swap of any dish**.

Histamine Fighter is an AI-first web app for people struggling with histamine intolerance, and having to constantly come up with dishes that are light on histamine.

> Status: work in progress. The schema, API, and UI still change rapidly. Currently only available if you fork the code and run the app yourself locally. I'm keeping this README minimal for now to reduce liability.

Current main functionalities:
- **Dish lookup:** Input a dish of your choosing and get a low-histamine version suggestion with detailed ingredients.
- **Daily board:** Every day an AI Composer Agent generates four dishes for the day (breakfast, lunch, dinner, snack) from scratch.
- **[WIP] Learn:** Retrieval-grounded answers over a curated histamine knowledge base.
- **Bring your own model:** OpenAI, Anthropic, Gemini, OpenRouter, or local Ollama (available only for local builds), switchable per request from the in-app settings.

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
- **AI layer.** LangChain chat models behind one swappable seam. Agents return typed Pydantic results. Prompts stored Markdown files.
- **Retrieval.** Postgres with pgvector and local fastembed embeddings back the Learn answers and meal composition.
- **Frontend.** Server-rendered Jinja2 pages with htmx for the interactive steps, served by FastAPI app.

Using: Python 3.12, FastAPI, SQLAlchemy (async), Alembic, LangChain, pgvector, fastembed, Jinja2, htmx, Docker Compose, Postgres 16.

## Install

Requires Docker and [uv](https://docs.astral.sh/uv/).

### Quick start

Install [`just`](https://github.com/casey/just), a small cross-platform command runner (`winget install Casey.Just`, `brew install just`, or `cargo install just`). Then, from a fresh clone:

```bash
just bootstrap
```

That copies `.env`, starts Postgres and waits for it to be healthy, applies migrations, seeds the factual data, then builds and starts the app. Open http://localhost:8000.

Run `just` to list every recipe.

### Manual setup

If you prefer not to install just, or want to run each step yourself:

```bash
cp .env.example .env

# 1. Start Postgres (pgvector)
docker compose up -d db

# 2. Apply the schema and seed the factual data (run from the host)
uv run alembic upgrade head
uv run python -m app.scripts.seed_histamine_db
uv run python -m app.scripts.seed_knowledge

# 3. Create an admin account (you can skip this if all you want is the dish replacement)
uv run python -m app.scripts.create_admin --email you@example.com

# 4. Start the app (one service, serving both the pages and the API)
docker compose up -d --build backend
```

## Usage

Open http://localhost:8000. Pick a provider and paste an API key in the in-app settings, or run a local Ollama model. Server-side keys in `.env` are only needed for the daily-board generation script.

Run the app natively instead of in Docker:

```bash
uv run uvicorn app.main:app --reload
```

Generate a day's meal board (needs a tool-calling model configured):

```bash
uv run python -m app.scripts.generate_daily_meals
```

After changing a model, author a migration, review it, then apply it:

```bash
uv run alembic revision --autogenerate -m "describe change"
uv run alembic upgrade head
```

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