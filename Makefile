# Единая точка входа для операций проекта.

.DEFAULT_GOAL := help
SHELL := /bin/bash
COMPOSE ?= docker compose
DC := $(COMPOSE) --env-file .env -f docker/docker-compose.yml
MODELS ?= qwen3:14b
# Переменные из .env для запуска вне Docker: сервер модели на хосте виден как localhost.
HOST_ENV := set -a && . ../.env && set +a && \
  export LLM_BASE_URL=$${LLM_BASE_URL/host.docker.internal/localhost}

.PHONY: help install run run-frontend lint format test check \
        bench examples up-local down-local up-prod down-prod logs clean

help: ## Показать список целей
	@grep -hE '^[a-zA-Z0-9_-]+:.*## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Установка --------------------------------------------------------------

install: ## Установить зависимости бэкенда и фронтенда
	cd backend && uv sync
	cd frontend && npm ci

# --- Запуск -----------------------------------------------------------------

run: ## Запустить бэкенд локально (SQLite, переменные из .env)
	cd backend && $(HOST_ENV) && DATABASE_URL=sqlite:///./dev.db uv run uvicorn app.main:app --reload

run-frontend: ## Запустить фронтенд в режиме разработки (прокси /api на :8000)
	cd frontend && npm run dev

# --- Проверка ---------------------------------------------------------------

lint: ## Проверить код (ruff, tsc)
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run typecheck

format: ## Отформатировать код бэкенда
	cd backend && uv run ruff check --fix . && uv run ruff format .

test: ## Прогнать тесты бэкенда
	cd backend && uv run pytest -q

check: lint test ## Линт и тесты

bench: ## Сравнить модели на демо-сценариях: make bench MODELS="qwen3:14b gemma3:12b"
	cd backend && $(HOST_ENV) && uv run python scripts/bench.py $(MODELS)

examples: ## Пересобрать тексты образцов из документов в examples/docs
	cd backend && uv run python scripts/build_examples.py

# --- Docker -----------------------------------------------------------------

up-local: ## Собрать образы из исходников и запустить
	$(DC) up -d --build

down-local: ## Остановить локальный стенд
	$(DC) down

up-prod: ## Скачать образы из реестра и запустить
	$(DC) pull db backend frontend
	$(DC) up -d --no-build

down-prod: down-local ## Остановить продуктивный стенд

logs: ## Логи сервисов
	$(DC) logs -f --tail=200

# --- Обслуживание -----------------------------------------------------------

clean: ## Удалить кэши и артефакты сборки
	rm -rf backend/.pytest_cache backend/.ruff_cache backend/dev.db frontend/dist
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +
