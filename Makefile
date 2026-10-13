.PHONY: check test lint type eval bench
check: lint type test
test:
	cd backend && uv run pytest -q
lint:
	cd backend && uv run ruff check .
type:
	cd backend && uv run mypy src
eval:
	cd backend && uv run python -m titan.evals.run
bench:
	cd backend && uv run python -m titan.bench.run
