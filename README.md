# NL OS Agent — Final Year Project

This project implements the full pipeline for the **Natural Language Operating System Agent (NL OS Agent)**.

Powered by **NVIDIA Nemotron 3 Nano 4B** accessed via the **LM Studio Developer Endpoint** (`http://localhost:1234`).

## Directory Structure
- [`nl-os-agent/`](file:///d:/Final%20yr%20Proj/nl-os-agent): Main project directory containing all source code, tests, dataset generators, and configuration.
  - [`agent.py`](file:///d:/Final%20yr%20Proj/nl-os-agent/agent.py): Main interactive CLI loop.
  - [`nl_translator.py`](file:///d:/Final%20yr%20Proj/nl-os-agent/nl_translator.py): LM Studio Nemotron 3 Nano 4B translation interface.
  - [`safety_verifier.py`](file:///d:/Final%20yr%20Proj/nl-os-agent/safety_verifier.py): Risk tier classification, Docker sandbox simulation, and confirmation prompt.
  - [`session_context.py`](file:///d:/Final%20yr%20Proj/nl-os-agent/session_context.py): Multi-turn context resolution, CWD tracking, and pronoun resolution using SQLite.
  - [`dataset/`](file:///d:/Final%20yr%20Proj/nl-os-agent/dataset): Fine-tuning preparation (`generate_dataset.py`, `pairs.jsonl`).
  - [`tests/`](file:///d:/Final%20yr%20Proj/nl-os-agent/tests): Unit tests (`test_safety.py`, `test_translator.py`, `test_context.py`).
  - [`demo.ps1`](file:///d:/Final%20yr%20Proj/nl-os-agent/demo.ps1) / [`demo.bat`](file:///d:/Final%20yr%20Proj/nl-os-agent/demo.bat) / [`demo.sh`](file:///d:/Final%20yr%20Proj/nl-os-agent/demo.sh): Scripts to set up a clean demo environment and run the agent for review.

## Running the Agent
```bash
# From workspace root:
python agent.py

# Or from inside nl-os-agent:
cd nl-os-agent
python agent.py
```

## Running Tests
```bash
pytest
```
All 30 unit tests pass across safety classification, live translation via LM Studio Nemotron, and multi-turn context resolution.
