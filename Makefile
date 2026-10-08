.PHONY: setup up down logs test reseed inspector

setup:  ## create .env with random secrets (then add the Google OAuth values)
	@test -f .env || (cp .env.example .env && for v in AUTH_SECRET APP_JWT_SECRET POSTGRES_PASSWORD RO_PASSWORD RW_PASSWORD APP_PASSWORD; do \
	  sed -i.bak "s|^$$v=.*|$$v=$$(openssl rand -hex 24)|" .env; done && \
	  sed -i.bak "s|^BOOTSTRAP_API_KEY=.*|BOOTSTRAP_API_KEY=sk_shopops_$$(openssl rand -hex 16)|" .env && rm -f .env.bak && \
	  echo "Created .env - now add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET")

up:
	docker compose up --build -d
	@echo "Console: http://localhost:3001   MCP: http://localhost:8001/mcp   API docs: http://localhost:8001/docs"

down:
	docker compose down

logs:
	docker compose logs -f api

test:  ## run the test suite inside the api container against a throwaway shopops_test database
	docker compose run --rm --no-deps -e TEST_DB_NAME=shopops_test api python -m pytest -q

reseed:
	docker compose run --rm setup python -m shopops.setup_db --reseed

inspector:  ## open MCP Inspector against the HTTP endpoint (uses BOOTSTRAP_API_KEY from .env)
	npx -y @modelcontextprotocol/inspector
