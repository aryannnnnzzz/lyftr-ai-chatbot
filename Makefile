.PHONY: up down logs test clean

up: .env
	docker compose up -d --build
	@echo "Frontend: http://localhost:3000   Backend: http://localhost:8000/health"

down:
	docker compose down

logs:
	docker compose logs -f

# Run backend tests locally (no Docker, no real API keys needed).
test:
	cd backend && python -m pip install -q -r requirements-dev.txt && python -m pytest -q

# Stop and also delete the SQLite volume.
clean:
	docker compose down -v

.env:
	cp .env.example .env
	@echo "Created .env from .env.example - put your real LLM_API_KEYS in it."
