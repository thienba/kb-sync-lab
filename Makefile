.PHONY: test run demo

test:
	uv run pytest -q

run:
	uv run main.py

demo:
	bash scripts/demo.sh
