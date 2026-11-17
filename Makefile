PYTHON ?= python3

.DEFAULT_GOAL := check
.PHONY: check compile test clean

check: compile test

compile:
	$(PYTHON) -m compileall -q gitstats_card tests

test:
	$(PYTHON) -m unittest discover -s tests -v

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf build dist .pytest_cache .ruff_cache .coverage htmlcov
	rm -rf *.egg-info
