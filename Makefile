.PHONY: test lint check update readme
test:
	python -m pytest -q
lint:
	ruff check . && ruff format --check .
readme:
	python scripts/generate_readme.py
update:
	python scripts/update.py
check: lint test
	python scripts/generate_readme.py --check
