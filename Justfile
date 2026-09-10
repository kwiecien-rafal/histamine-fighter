# Histamine Fighter task runner. Run `just` (or `just --list`) to see everything.
# requires Docker and uv on PATH.

set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

# all recipes
_default:
    @just --list

# run everything
dev: setup up
    @echo "Ready -> http://localhost:8000  (admin is optional: just admin you@example.com)"
    @just logs

# data layer only (env, database, schema, seed) without the app containers
setup: env db migrate seed

# copy .env.example to .env if it does not exist yet (never overwrites an existing one)
[unix]
env:
    @test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

# copy .env.example to .env if it does not exist yet (never overwrites an existing one)
[windows]
env:
    @if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env from .env.example" }

# start Postgres
db:
    docker compose up -d --wait db

# apply all database migrations
migrate:
    uv run alembic upgrade head

# seed the histamine index and the knowledge base
seed:
    uv run python -m app.scripts.seed_histamine_db
    uv run python -m app.scripts.seed_knowledge

# build and start the app container
up:
    docker compose up -d --build backend

# stop and remove the containers (keeps the database volume)
down:
    docker compose down

# tail logs for every service or one: just logs backend
logs service="":
    docker compose logs -f --tail=100 {{service}}

# create or reset an admin account (prompts for a password): just admin you@example.com
admin email:
    uv run python -m app.scripts.create_admin --email {{email}}

# generate the daily meal board (needs a tool-calling model configured)
daily:
    uv run python -m app.scripts.generate_daily_meals

# author a new migration from model changes: just migration "add reveal_at to daily"
migration message:
    uv run alembic revision --autogenerate -m "{{message}}"

# run tests
test:
    uv run pytest

# lint and format-check all files
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run djlint app/web/templates --check
    uv run dprint check

# auto-format: ruff for Python, djLint for the Jinja templates, dprint for everything else
fmt:
    uv run ruff format .
    -uv run djlint app/web/templates --reformat
    uv run dprint fmt

# DESTRUCTIVE: destroy the database volume and rebuild everything
reset:
    docker compose down -v
    just bootstrap
