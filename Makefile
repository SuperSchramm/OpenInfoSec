.PHONY: dev stop test lint eval docker clean install discord

install:
	cd packages/core && uv sync
	cd packages/ui && npm install

# Both dev recipe lines source the repo-root .env into the process env:
# - the UI needs it because Next.js only auto-loads packages/ui/.env*, so
#   Auth.js never saw AUTH_SECRET etc. (issue #44);
# - the API needs it because BACKEND_SHARED_SECRET / BACKEND_ALLOWED_ORIGINS
#   are read from os.environ, not pydantic Settings — dotenv alone doesn't
#   surface them, silently leaving the API gate open.
# Note: exported values win over packages/ui/.env.local for duplicate keys.
# .env values must be shell-safe: quote anything containing spaces or `$`.
dev:
	@echo "Starting Open Executive..."
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; cd packages/core && uv run uvicorn openexecutive.api.main:app --reload --port 8000 &
	@if [ -f .env ]; then set -a; . ./.env; set +a; fi; cd packages/ui && npm run dev

# See scripts/stop-dev.sh for why this is a script, not an inline recipe
# (issue #7): a pkill pattern embedded directly in a Makefile recipe line
# matches that line's own invoking shell (`make` runs each line via
# `sh -c "<literal recipe text>"`) and kills it before later lines run.
# MAKEFILE_LIST-derived path (not a bare relative one): `make` doesn't cd
# to this file's directory, so `make -f /path/to/Makefile stop` run from
# elsewhere must not fall back to executing whatever ./scripts/stop-dev.sh
# happens to resolve to in the caller's cwd.
stop:
	@bash "$(dir $(lastword $(MAKEFILE_LIST)))scripts/stop-dev.sh"

test:
	cd packages/core && uv run pytest tests/ -v --tb=short

lint:
	cd packages/core && uv run ruff check openexecutive/ && uv run mypy openexecutive/
	cd packages/core && uv run python scripts/validate_facts_yaml.py

eval:
	cd packages/core && uv run python ../../evals/run_evals.py \
		--scenarios ../../evals/scenarios/ \
		--output ../../evals/results/

# --env-file makes ${VAR} interpolation in docker-compose.yml read the
# repo-root .env (compose only auto-reads docker/.env otherwise). The
# containers additionally load the full .env via each service's env_file.
COMPOSE_ENV_FILE := $(if $(wildcard .env),--env-file .env,)

docker:
	docker compose $(COMPOSE_ENV_FILE) -f docker/docker-compose.yml up --build

docker-down:
	docker compose $(COMPOSE_ENV_FILE) -f docker/docker-compose.yml down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -rf packages/core/.venv packages/core/.mypy_cache packages/core/.ruff_cache
	rm -rf packages/ui/node_modules packages/ui/.next

discord:
	cd packages/core && uv run python -m openexecutive.integrations.discord_bot

seed-knowledge:
	cd packages/core && uv run python -c "from openexecutive.knowledge.loader import seed_builtin_knowledge; import asyncio; asyncio.run(seed_builtin_knowledge())"
