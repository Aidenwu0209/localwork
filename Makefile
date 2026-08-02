SHELL := /bin/bash
COMPOSE_DATA := docker compose -f deploy/mac/compose.data.yml
COMPOSE_DEMO_DATA := docker compose -f deploy/mac/compose.demo-data.yml
COMPOSE_MONITORING := docker compose -f deploy/mac/compose.monitoring.yml

.PHONY: data-up data-down data-logs data-psql data-reset \
	demo-data-up demo-data-down demo-data-reset demo-data-logs \
	monitoring-up monitoring-down monitoring-logs help

help:
	@echo "data-up     start Mac data layer (postgres+pgvector on :5433, redis on :6380)"
	@echo "data-down   stop data layer (volumes preserved)"
	@echo "data-reset  stop and WIPE data layer volumes"
	@echo "data-logs   tail data layer logs"
	@echo "data-psql   open psql into the timeline database"
	@echo "demo-data-up    start isolated P3.4 data layer on :5433 / :6380"
	@echo "demo-data-down  stop isolated P3.4 data (volumes preserved)"
	@echo "demo-data-reset wipe ONLY isolated P3.4 volumes, then start them clean"
	@echo "monitoring-up    start local Prometheus + Grafana on :9090 / :3000"
	@echo "monitoring-down  stop monitoring (metrics volumes preserved)"
	@echo "monitoring-logs  tail local Prometheus + Grafana logs"

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

demo-data-up:
	$(COMPOSE_DEMO_DATA) up -d --wait

demo-data-down:
	$(COMPOSE_DEMO_DATA) down

demo-data-reset:
	$(COMPOSE_DEMO_DATA) down -v
	$(COMPOSE_DEMO_DATA) up -d --wait

demo-data-logs:
	$(COMPOSE_DEMO_DATA) logs -f --tail=100

monitoring-up:
	$(COMPOSE_MONITORING) up -d --wait

monitoring-down:
	$(COMPOSE_MONITORING) down

monitoring-logs:
	$(COMPOSE_MONITORING) logs -f --tail=100
