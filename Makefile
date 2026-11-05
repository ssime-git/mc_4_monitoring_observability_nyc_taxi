.PHONY: up down reset test lint demo-normal demo-latency demo-drift demo-outage demo-performance demo-business demo-all

up:            ## Start everything (one command)
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example - edit PORTAL_PASSWORD if you want to change it")
	docker compose up --build -d

down:          ## Stop everything (data volumes are kept)
	docker compose down

reset:         ## Stop everything and erase all data
	docker compose down -v

test:          ## Unit tests + promtool rule tests
	uv run --no-sync pytest -q

demo-normal:      ## Scenario 1: normal traffic, no alert
	uv run --no-sync python scripts/demo_scenarios.py normal
demo-latency:     ## Scenario 2: degraded latency -> HighLatencyP99
	uv run --no-sync python scripts/demo_scenarios.py latency
demo-drift:       ## Scenario 3: drifting data -> DataDriftDetected
	uv run --no-sync python scripts/demo_scenarios.py drift
demo-outage:      ## Scenario 4: service stopped -> ServiceDown
	uv run --no-sync python scripts/demo_scenarios.py outage
demo-performance: ## Bonus: real fares diverge -> OnlineModelErrorHigh
	uv run --no-sync python scripts/demo_scenarios.py performance
demo-business:    ## Bonus: traffic collapses -> BusinessThroughputLow
	uv run --no-sync python scripts/demo_scenarios.py business
demo-all: demo-normal demo-latency demo-drift demo-outage demo-performance demo-business
