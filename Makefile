PYTHON ?= python3
VENV_DIR ?= .venv
PIP := $(VENV_DIR)/bin/pip
PY := $(VENV_DIR)/bin/python

.PHONY: init install run sandbox clean

init:
	$(PYTHON) -m venv $(VENV_DIR)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

install:
	$(PIP) install -r requirements.txt

run:
	$(PY) agent_loop.py

sandbox:
	$(PY) main.py

clean:
	rm -rf __pycache__ tools/__pycache__ sandbox/__pycache__

