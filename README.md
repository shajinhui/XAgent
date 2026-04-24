# Codex-like Agent (Stage 0 Bootstrap)

This repository is initialized for Stage 0 with:

- Python project scaffold
- Docker-based sandbox executor (local)
- Basic Git repository bootstrap

## Quick Start

1. Create and activate virtualenv:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Configure environment:

```bash
cp .env.example .env
```

4. Run sandbox smoke test:

```bash
python main.py
```

## Project Structure

- `sandbox/executor.py`: Docker sandbox command runner
- `main.py`: simple smoke test entrypoint

## Notes

- Docker Desktop must be installed and running.
- Commands are executed in isolated containers with timeout and basic deny-list checks.
