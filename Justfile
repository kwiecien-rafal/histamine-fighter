# Histamine Fighter task runner. Run `just` (or `just --list`) to see everything.
# Requires Docker, uv, and Node 20+ on PATH. Works from PowerShell on Windows and
# from sh on macOS/Linux; `env` is the only recipe that differs per OS.

# Windows has no sh, so run recipes through PowerShell there instead.
set windows-shell := ["powershell.exe", "-NoLogo", "-NoProfile", "-Command"]

backend := "uv run --directory backend"

# Show all recipes
_default:
    @just --list

# From a fresh clone to a running app in one command
bootstrap: setup up
    @echo "Ready -> http://localhost:5173  (admin is optional: just admin you@example.com)"

# Bootstrap, then stay attached to the logs (Ctrl-C leaves the app running)
dev: bootstrap
    @just logs

# Prepare the data layer only (env, database, schema, seed) without the app containers
setup: env db migrate seed

# Copy .env.example to .env if it does not exist yet (never overwrites an existing one)
[unix]
env:
    @test -f .env || (cp .env.example .env && echo "Created .env from .env.example")

# Copy .env.example to .env if it does not exist yet (never overwrites an existing one)
[windows]
env:
    @if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env from .env.example" }

# Start Postgres and block until its healthcheck passes
db:
    docker compose up -d --wait db

# Apply all database migrations
migrate:
    {{backend}} alembic upgrade head

# Seed the factual histamine index and the knowledge base (safe to re-run)
seed:
    {{backend}} python -m app.scripts.seed_histamine_db
    {{backend}} python -m app.scripts.seed_knowledge

# Build and start the backend and frontend containers
up:
    docker compose up -d --build backend frontend

# Stop and remove the containers (keeps the database volume)
down:
    docker compose down

# Tail logs for every service, or one: just logs backend
logs service="":
    docker compose logs -f --tail=100 {{service}}

# Create or reset an admin account (prompts for a password): just admin you@example.com
admin email:
    {{backend}} python -m app.scripts.create_admin --email {{email}}

# Generate the daily meal board (needs a tool-calling model configured)
daily:
    {{backend}} python -m app.scripts.generate_daily_meals

# Author a new migration from model changes: just migration "add reveal_at to daily"
migration message:
    {{backend}} alembic revision --autogenerate -m "{{message}}"

# Run the backend test suite
test:
    {{backend}} pytest

# Lint and format-check the backend, matching CI
lint:
    {{backend}} ruff check .
    {{backend}} ruff format --check .

# Auto-format the backend
fmt:
    {{backend}} ruff format .

# Destroy the database volume and rebuild everything from scratch (DESTRUCTIVE)
reset:
    docker compose down -v
    just bootstrap
