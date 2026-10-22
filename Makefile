.PHONY: install check test train demo serve dashboard deploy destroy cost-check
install:
	uv sync --all-extras
check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy src tests dashboard scripts
	uv run pytest
	uv run pip-audit
	uv run bandit -q -r src scripts
test:
	uv run pytest
train:
	uv run sentinel train --output artifacts/demo
demo:
	uv run sentinel demo --output artifacts/demo
serve:
	uv run uvicorn sentinel.api:app --host 127.0.0.1 --port 8000
dashboard:
	uv run streamlit run dashboard/app.py
deploy:
	uv run python scripts/cloud.py deploy $(ARGS)
destroy:
	uv run python scripts/cloud.py destroy $(ARGS)
cost-check:
	uv run python scripts/cloud.py cost-check $(ARGS)
