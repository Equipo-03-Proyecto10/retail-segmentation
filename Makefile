COMPOSE = docker compose -f infra/docker-compose.yml --env-file .env

.PHONY: up down migrate seed test logs reset

up:
	$(COMPOSE) up -d --build

down:
	$(COMPOSE) down

# Two steps, matching D-14 / R-18: the frozen schema (including audit_log)
# only exists after alembic runs, so the REVOKE that finishes locking down
# app_runtime has to happen after, not in docker-entrypoint-initdb.d.
migrate:
	$(COMPOSE) exec web bash -c "cd web && alembic upgrade head"
	cat infra/sql/post-init/audit-log-revoke.sql | \
		$(COMPOSE) exec -T postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

seed:
	@echo "make seed: seed dataset script lands in S0-08 (docs/data/seed-strategy.md); nothing to run yet."

test:
	pytest
	black --check .
	ruff check .

logs:
	$(COMPOSE) logs -f

reset:
	$(COMPOSE) down -v
	$(MAKE) up
