SHELL := /bin/bash
COMPOSE_DATA := docker compose -f deploy/mac/compose.data.yml

.PHONY: data-up data-down data-logs data-psql data-reset help

help:
	@echo "data-up     start Mac data layer (postgres+pgvector on :5433, redis on :6380)"
	@echo "data-down   stop data layer (volumes preserved)"
	@echo "data-reset  stop and WIPE data layer volumes"
	@echo "data-logs   tail data layer logs"
	@echo "data-psql   open psql into the timeline database"

data-up:
	$(COMPOSE_DATA) up -d --wait

data-down:
	$(COMPOSE_DATA) down

data-reset:
	$(COMPOSE_DATA) down -v

data-logs:
	$(COMPOSE_DATA) logs -f --tail=100

data-psql:
	PGPASSWORD=dejaview psql -h 127.0.0.1 -p 5433 -U dejaview -d dejaview
