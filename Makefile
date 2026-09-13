.PHONY: run test lint

run:
	uvicorn app.main:app --reload

test:
	pytest

lint:
	python -m py_compile app/*.py app/**/*.py
