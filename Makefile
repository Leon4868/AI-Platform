.PHONY: bootstrap dev-web dev-api test build compose-up compose-down db-upgrade

bootstrap:
	pnpm install
	cd apps/api && uv sync --extra dev

dev-web:
	set -a; [ ! -f .env ] || . ./.env; set +a; pnpm dev:web

dev-api:
	set -a; [ ! -f .env ] || . ./.env; set +a; cd apps/api && uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	./scripts/verify.sh all

build:
	pnpm build

db-upgrade:
	set -a; [ ! -f .env ] || . ./.env; set +a; cd apps/api && uv run alembic upgrade head

compose-up:
	docker compose up --build

compose-down:
	docker compose down
