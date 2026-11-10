# NL OS Agent — Natural Language Operating System Shell

An intelligent, context-aware Natural Language Operating System (NL OS) Agent powered by **NVIDIA Nemotron 3 Nano 4B** via LM Studio developer endpoint access.

---

## Key Highlights

- **Nemotron 3 Nano 4B Integration**: Translates user natural language queries into exact shell commands via the local LM Studio developer endpoint (`http://localhost:1234/v1/chat/completions`) with automatic reasoning/thinking tag isolation.
- **Multi-Turn Session Context**: SQLite-backed session store tracking working directories (`cwd`), command history, and pronoun/entity reference resolution (e.g., resolving *"that folder"*, *"that script"*, or *"it"*).
- **Multi-Tier Safety Verifier**: Classifies commands into `READ_ONLY`, `STATE_MODIFYING`, `DESTRUCTIVE`, and `AMBIGUOUS`. Protects the system against destructive operations (like `rm -rf`, `find -delete`, piped forks) with sandbox simulation and interactive user confirmation.
- **Cross-Platform Compatibility**: Executes Linux/macOS shell commands seamlessly on Unix and Windows (leveraging Git Bash when present).
- **Synthetic Dataset Generator**: Generates and validates NL-to-command pairs (`pairs.jsonl`) for Phase 2 fine-tuning.

---

## Architecture & Project Structure

```
nl-os-agent/
├── agent.py                  # Main interactive CLI loop & rich interface
├── session_context.py        # Multi-turn context manager & SQLite entity tracker
├── safety_verifier.py        # Risk tier classifier, sandbox simulator & confirmation
├── nl_translator.py          # LM Studio Nemotron 3 Nano 4B wrapper with thinking parser
├── dataset/
│   ├── generate_dataset.py   # Synthetic data generator for fine-tuning
│   └── pairs.jsonl           # 45 validated training pairs
├── tests/
│   ├── test_translator.py    # Unit tests for translation & output sanitization
│   ├── test_safety.py        # Unit tests for safety classification & sandbox
│   └── test_context.py       # Unit tests for session history & pronoun resolution
├── demo.sh                   # Linux/macOS/Git Bash demo script
├── demo.bat                  # Windows CMD demo script
├── demo.ps1                  # Windows PowerShell demo script
├── session.db                # SQLite session database (auto-generated)
├── .env                      # LM Studio host & model configuration
└── requirements.txt          # Python dependencies
```

---

## Quick Start

### 1. Prerequisites
- Python 3.10+
- LM Studio running with developer endpoint enabled at `http://localhost:1234`
- Loaded model: `nvidia/nemotron-3-nano-4b`

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Configuration (.env)
The `.env` file is pre-configured to use your LM Studio developer endpoint:
```env
LM_STUDIO_HOST=http://localhost:1234
MODEL_NAME=nvidia/nemotron-3-nano-4b
LLM_PROVIDER=lm_studio
COMMAND_TIMEOUT=30
```

### 4. Run the Agent
```bash
python agent.py
```

---

## Running the Automated Test Suite

Run all unit tests across the safety verifier, translator, and context manager:

```bash
pytest tests/ -v
```

Expected result: 30 passed tests.

---

## Dataset Generation (Fine-Tuning Prep)

Generate synthetic training pairs for Phase 2 fine-tuning:

```bash
python dataset/generate_dataset.py
```
This produces `dataset/pairs.jsonl` with validated natural language variants, commands, and categories.

---

## Demo Walkthrough

Run the pre-configured demo environment:

```bash
# On Windows PowerShell:
.\demo.ps1

# On Windows CMD:
demo.bat

# On Linux / Git Bash:
./demo.sh
```

### Sample Demo Steps to Showcase:
1. `show me all files here`
   - *Auto-executes `ls -la` (Read-only tier)*
2. `find all python files`
   - *Auto-executes `find . -name "*.py"` (Read-only tier)*
3. `create a backup folder`
   - *Executes `mkdir backup` (State-modifying tier)*
4. `copy the python file into that folder`
   - *Demonstrates entity resolution: resolves "that folder" to `backup` and executes `cp script.py backup/`*
5. `delete the old_logs folder permanently`
   - *Demonstrates safety verification: classified as `DESTRUCTIVE`, shows sandbox impact analysis, and prompts user `(y/N)` for confirmation. Type `N` to showcase prevention.*
6. `history`
   - *Displays full SQLite audit log with risk tiers and outputs.*
