.PHONY: help showcase up down restart build test test-unit logs clean

SHELL := /bin/bash

# Color helpers
CYAN  := \033[36m
GREEN := \033[32m
YELLOW:= \033[33m
RESET := \033[0m

help:
	@echo ""
	@echo "  $(CYAN)JackVerse Agent Runtime · Management, Verification & Demo Commands$(RESET)"
	@echo "  ──────────────────────────────────────────────────────────────────"
	@echo "  $(GREEN)make showcase$(RESET)    Start entire stack & launch Live Operator Console (TUI)"
	@echo "  $(GREEN)make up$(RESET)          Start Docker compose stack in background"
	@echo "  $(GREEN)make down$(RESET)        Stop Docker compose stack"
	@echo "  $(GREEN)make build$(RESET)       Rebuild Docker images cleanly from pyproject.toml"
	@echo "  $(GREEN)make test$(RESET)        Run the complete test suite inside the Docker container"
	@echo "  $(GREEN)make test-unit$(RESET)   Run fast deterministic unit tests inside Docker"
	@echo "  $(GREEN)make logs$(RESET)        Follow Docker container logs"
	@echo "  $(GREEN)make clean$(RESET)       Remove temporary files and caches"
	@echo ""

up:
	@echo "$(CYAN)Starting JackVerse Docker stack...$(RESET)"
	docker compose up -d

down:
	@echo "$(YELLOW)Stopping JackVerse Docker stack...$(RESET)"
	docker compose down

restart: down up

build:
	@echo "$(CYAN)Building Docker images cleanly from declared dependencies...$(RESET)"
	docker compose build

test:
	@echo "$(CYAN)Running full test suite in Docker container...$(RESET)"
	docker compose exec -T harness python -m unittest discover -s tests -p "test_*.py"

test-unit:
	@echo "$(CYAN)Running unit tests in Docker container...$(RESET)"
	docker compose exec -T harness python -m unittest discover -s tests/unit -p "test_*.py"

logs:
	docker compose logs -f

showcase: up
	@echo ""
	@echo "$(CYAN)Waiting for runtime readiness...$(RESET)"
	@for i in {1..20}; do \
		if docker compose exec -T transport-mcp python -c "import urllib.request, urllib.error, sys\ntry:\n    urllib.request.urlopen('http://127.0.0.1:8000/mcp', timeout=1)\n    sys.exit(0)\nexcept urllib.error.HTTPError as e:\n    sys.exit(0 if e.code in (400, 405, 406) else 1)\nexcept Exception:\n    sys.exit(1)" 2>/dev/null; then \
			break; \
		fi; \
		sleep 1; \
	done
	@echo "$(GREEN)✓ Transport MCP and dependencies are ready$(RESET)"
	@echo ""
	@echo "  $(CYAN)======================================================================$(RESET)"
	@echo "  $(GREEN)JACKVERSE AGENT RUNTIME OPERATOR CONSOLE READY$(RESET)"
	@echo "  $(CYAN)======================================================================$(RESET)"
	@echo "  Grafana Dashboard: $(YELLOW)http://localhost:3000/d/agent-harness-runtime/agent-harness-operations$(RESET)"
	@echo "  Credentials:       $(YELLOW)admin / admin$(RESET)"
	@echo "  Prometheus UI:     $(YELLOW)http://localhost:9090$(RESET)"
	@echo "  Tempo Explorer:    $(YELLOW)http://localhost:3000/explore$(RESET) (datasource: tempo)"
	@echo "  Transport MCP:     $(YELLOW)http://transport-mcp:8000/mcp (internal)$(RESET)"
	@echo "  $(CYAN)======================================================================$(RESET)"
	@echo ""
	@echo "$(CYAN)Attaching Operator Console... (Press Ctrl+C or Q to exit)$(RESET)"
	docker compose exec harness python -m harness --tui

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
