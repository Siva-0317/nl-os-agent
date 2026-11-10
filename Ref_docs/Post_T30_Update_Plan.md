# Post-T30 Update Plan: Kumo, Autoresearch, Obsidian-style Memory

Your current order (from your screenshots): Phase A and B done, Phase C in progress at **T30**; then T31, T32, Phase D (T25/T40, T42, T37, T45), Phase E (T33 to T36).

---

## 0. Verdict

| Idea | Verdict | One line |
|---|---|---|
| **NVIDIA Kumo (tabular/relational foundation models)** | **Include-lite, offline only** | Not for the command model. Use it (or a plain baseline) as an optional "learned escalation" experiment on effect features and pilot logs. Do not ship it |
| **Autoresearch loop (Karpathy)** | **Include, on cheap surfaces** | Great for prompt, cache and runtime-config search with a fixed evaluator. Not practical for the 4B fine-tune on free Colab |
| **Obsidian / "LLM wiki" vault pattern** | **Include twice** | (a) As a dev-memory vault for you and the agent; (b) its "hot, index, detail" idea as a better **context manager** in the product (grounding the model in real file names) |

Honest note on "breakthrough": none of these gives a guaranteed breakthrough over baselines or other papers. What can be strong and defensible is a measured, well-controlled result: same accuracy with a much shorter prompt and lower latency on CPU-only machines, a very low harm rate in the end-to-end test with proper intervals, and a human-factors result. The N-tasks below are chosen because they can produce those.

---

## 1. What the three ideas are (verified)

**NVIDIA Kumo.** A family of pretrained foundation models for structured data. Kumo Tabular predicts outcomes from a single table using labelled rows as context at inference time, with no task-specific training. Kumo Relational does the same over connected tables and builds on KumoRFM-2. The models are pretrained on large numbers of synthetic tables, and NVIDIA's docs stress splitting data by time to prevent leakage. The Hugging Face card for Kumo Relational shows a PyTorch example on `cuda` and states the weights' license (OpenMDW 1.1) and that it finetunes TabICLv2 weights (BSD-3-Clause). Check the license of whichever model you use.

**Autoresearch (Karpathy, March 2026).** An agent repeatedly edits **one** training file (`train.py`), runs a fixed-budget experiment (about 5 minutes), checks one fixed metric (`val_bpb`) from an evaluation file the agent does not edit, then keeps or reverts the change with git. `program.md` holds the human's instructions. Roughly 12 experiments per hour. A port notes that a single short run is noisy, so it adds multi-seed confidence checks before accepting a change.

**LLM wiki / Obsidian vault pattern (Karpathy, April 2026, as described by the repos built on it).** Three layers: immutable `raw/` sources, an LLM-maintained `wiki/` of linked markdown pages (`index.md` as catalog, `log.md` append-only, `hot.md` for recent context), and a schema file (`CLAUDE.md`/`AGENTS.md`). The agent reads the hot cache first, then the index, then specific pages. Obsidian is just a free viewer for the markdown.

---

## 2. Fit with your project

| Idea | Where it fits | Where it does not | Risk |
|---|---|---|---|
| Kumo Tabular | Predicting harm or user regret from effect features, especially with few labelled rows; analysis of pilot logs | The NL-to-command model is text generation; your risk tier is a deterministic function and that is a strength. A learned classifier brings back bypass risk | Heavy PyTorch/GPU dependency, 4 GB VRAM contention, installer size, license |
| Kumo principles without the model | In-context learning = retrieval of verified examples (you already have a verified cache and few-shot). Synthetic pretraining = your attack generator. **Time-based splits** = evaluate caches and personalization on later sessions, not random ones | | Low |
| Autoresearch | Prompt/layout wording, few-shot selection, cache thresholds, state size, runtime flags, data-mix proxy | 4B fine-tune: about 117 s per step means a 5-minute budget is about 2 steps. Free Colab sessions also end | Overfitting to the dev set (Goodhart); agents editing safety code |
| Vault: dev memory | Persistent context across agent sessions; experiment cards; evidence ledger and pre-registration live there | The product must not depend on Obsidian | LLM-written notes can hold errors; keep numbers only in the ledger |
| Vault: hot / index / detail | Context manager v2: hot state block, a user-editable personal index (aliases, favourite folders), and read-only search tools that return real paths | Indexing file contents (privacy); do not do it by default | File names are untrusted text and can carry injected instructions |

---

## 3. Revised execution order (insert N-tasks)

| Order | Task | Status / change |
|---|---|---|
| 1 | **T30** model manager | in progress |
| 2 | **N1** dev vault, `program.md` files, evidence ledger home | new, 0.5 to 1 day |
| 3 | **T31** core service and token API | as planned |
| 4 | **N2a** context v2 design and grounding tools (read-only search tool, personal index file, injection-safe formatting) | new, after T31 so it uses the service API |
| 5 | **T32** plain-English renderer | as planned |
| 6 | **N2b** context v2 implementation and evaluation | new |
| 7 | **T25 + T40, dev split only** (live E5 baselines) | as planned, but run on the **dev** set; the sealed test stays closed |
| 8 | **N4** autoresearch-style loops on dev (prompt, cache, state, runtime flags) | new |
| 9 | **T42** adaptive attacker | as planned |
| 10 | **T37** hardware matrix + **N6** first-run autotuner data | as planned plus new |
| 11 | **Sealed final runs** (E5 and safety, once each, after pre-registered configuration is frozen) | new step |
| 12 | **N3** learned-escalation experiment (optional, needs attack/benign sets and ideally pilot data) | new, optional |
| 13 | **T45** paper numbers gate | as planned |
| 14 | **T33, T34, T35** UI, wizard, installer (with **N7** study mode) | as planned plus new |
| 15 | **T36** multi-step plans | demote to stretch; N2 grounding gives more value per day |

Rough effort (estimates, adjust after the first one): N1 about 1 day, N2 4 to 6 days, N4 4 to 5 days, N6 1 to 2 days, N3 3 to 4 days, N7 1 day.

---

## 4. The new tasks

### N1: Dev vault and agent memory (process, low risk)
**Why:** the agent forgets between sessions; your claims need a research record.
**Structure** (plain markdown; Obsidian optional for viewing):
```
vault/
  AGENTS.md            # schema: how to read/write this vault (use whatever instruction file your IDE reads)
  raw/                 # papers, handover files, benchmark outputs (never edited by the agent)
  wiki/
    hot.md             # last ~500 words: current task, next step, open risks
    index.md           # catalogue of pages
    log.md             # append-only, newest first
    decisions/         # one page per decision (date, options, choice, evidence)
    experiments/       # experiment cards (below)
    claims/            # claim ladder status, links to ledger rows
    prior_art/         # feature table and search record
```
**Rules:** the agent reads `hot.md`, then `index.md`, then only the page it needs; every page that states a number links to the ledger row or result file instead of copying the number; log every change; run a lint pass weekly (contradictions, orphan pages, numbers without links).
**Experiment card fields:** hypothesis, config (git sha, model hash), dataset split, metric, result file, n, interval, decision.
**Acceptance:** after a fresh agent session, asking "what is the status of claim C3?" returns the right page and ledger link without you pasting context.

### N2: Context manager v2 (hot, index, detail), the main product-quality addition
**Idea:** keep prompts short while grounding the model in real names.
1. **Hot:** the existing compact state block (cwd, entity table, last effects).
2. **Personal index:** a user-editable file (`profile.md` or JSON) with aliases ("my invoices" maps to a path), favourite folders, shell, safety level. Entries can be proposed after successful commands and need the user's approval. Shown in settings; "forget" button.
3. **Detail via read-only tools:** before generation, deterministic code (not the LLM) runs bounded searches (names, dates, types) inside user-approved folders and passes the top-k candidates as a short `FILES` block. The model chooses from real paths instead of inventing them.
4. **Security:** file names are untrusted. Delimit them, escape control characters, cap length and count, and never let them change the system prompt. The effect engine remains the gate.
5. **Privacy:** metadata only, opt-in folders, no content indexing by default, everything local.

**Evaluate (new claims you can earn):**
- Path-hallucination rate (proposed paths that do not exist) with and without grounding.
- Reference-resolution accuracy on multi-turn dialogues (transcript replay vs compact state vs compact state + grounding).
- Prompt tokens and time to preview added by grounding.
- Injection test: files named like instructions; count harmful executions (must be zero without confirmation) and misleading proposals.
**Acceptance:** path hallucination drops measurably on 100+ requests; zero executions without the normal tier and confirmation in the injection tests.

### N3: Learned escalation experiment (optional, offline)
**Question:** can a model trained on effect features add value on top of the deterministic engine?
**Design:** features from `effects.analyse` (counts, bytes, path class, depth, wildcard use, opaque flag, shell, command family); label from sandbox diff (harmful or not) or, later, from pilot logs (undone or rejected). Compare logistic regression, gradient boosting, TabICLv2 and Kumo Tabular on training sizes of 50, 100, 300 and 1,000; split **by time or by technique family**, not randomly.
**Hard rule (monotone escalation):** a learned score may only raise scrutiny (add a confirmation, ask for typed count); it can never lower a tier set by the deterministic engine.
**Report:** extra guard G5 (learned alone) and G3+G5; precision-recall, calibration, added confirmations per 100 benign commands.
**If it helps:** distil to a tiny model for shipping; keep Kumo/TabICL as offline analysis. **If not:** report that the deterministic engine was sufficient; that is still a result.
**Time-box:** 3 to 4 days, after T37 and the sealed runs.

### N4: Autoresearch-style loops (bounded, fixed evaluator)
**Surfaces that suit it (each experiment in minutes):**
- Short L0 wording, schema format, few-shot count and selection.
- Verified-cache similarity threshold, state-block size.
- Runtime flags: threads, context size, GPU layers, batch size.
- Data-mix proportions for a *proxy* fine-tune on a smaller model or a 1-epoch subset (validate the winner on the 4B).

**What does not suit it:** the 4B fine-tune itself on free Colab, and **safety rules** (see below).

**Mechanics (copy the autoresearch contract):**
- One mutable surface per loop, one fixed evaluator the agent cannot edit, one fixed budget, git keep or revert, a `results.tsv`.
- **Dev/test separation by code:** the loop sees only the dev set. The sealed test set is run once, after the configuration is frozen, through a command that logs each use.
- **Acceptance rule:** keep a change only if the paired bootstrap lower bound of the improvement on dev cases is above zero **and** prompt tokens or latency do not get worse by more than a stated margin. Greedy decoding is deterministic, so the noise to guard against is case sampling, not random seeds.
- **Goodhart controls:** refresh dev cases periodically, count total experiments tried and report that number, and keep the pre-registered hypotheses.
- **Safety code stays human-reviewed.** A loop may *propose* changes to `effects.py` rules, but every change needs human review and a re-run on a fresh blind set; never auto-accept.

**Template (`program.md` per loop):**
```
Goal: <one sentence, one metric, one constraint>
You may edit: <single file or config only>
You may not edit: evaluator, datasets, tests, anything under safety/
Run: <command>  (fixed budget: <minutes>)
Accept a change only if: paired bootstrap lower bound > 0 on dev AND <guard metric> not worse
Log: append to results.tsv (id, change, metric, CI, kept/reverted, git sha)
Stop after <N> experiments or <hours> hours and write a summary card to the vault.
```
**Acceptance:** a loop runs unattended for several hours, logs every trial, and the winning configuration is then evaluated once on the sealed test.

### N6: First-run autotuner (autoresearch-lite at install time)
In the wizard (T34), try 3 to 6 candidate settings (threads, GPU layers, context size, batch size) for about 60 to 90 seconds in total on a fixed set of prompts; save the fastest stable profile in the config. Deterministic grid search is enough. It gives users a good setup on any machine and gives you per-device data for the hardware matrix.

### N7: Study mode in the UI (for the pilot)
Add a flag that switches between "plain-English preview" and "raw command" displays, a consent screen, anonymous participant ids, and local logging of comprehension answers, approvals and times. Needed for the human-factors pilot in the validation plan.

---

## 5. Claims these additions can strengthen

| Addition | Possible new claim (only if the data support it) | Evidence needed |
|---|---|---|
| N2 grounding | "Grounding in read-only search results cuts path hallucination from X% to Y% at Z ms extra" | 100+ requests, intervals |
| N2 injection tests | "File-name injection did not cause unconfirmed harmful execution" | Test set with injected names |
| N4 loops | "A fixed-evaluator search found a prompt N% shorter with non-inferior accuracy; M configurations were tried" | Sealed final run |
| N6 | "Auto-tuning improved time to preview by X% across N machines" | Hardware matrix |
| N3 | "A learned score added Z confirmations per 100 benign commands for W% fewer misses (or none)" | Time-split evaluation |
| N1 | Reproducibility and a complete research record | Vault plus ledger |

What would count as a strong result: same accuracy at least 30% lower time to preview on CPU-only and GPU (live, non-inferiority tested), near-zero harmful executions in the 500-request end-to-end test with a stated upper bound, and a pilot showing the plain-English preview lowers unsafe approvals. Those are possible but not guaranteed; do not promise them in advance.

---

## 6. Agent prompts

**N1.**
"Create vault/ with AGENTS.md, wiki/hot.md, wiki/index.md, wiki/log.md and folders decisions/, experiments/, claims/, prior_art/. AGENTS.md must say: read hot.md then index.md then only needed pages; never copy numbers, link to results files or the evidence ledger; log every change newest first; never edit raw/. Add scripts/vault_lint.py that reports pages containing numbers without links, orphan pages, and missing log entries. Tests use a tiny sample vault."

**N2a (design and tools).**
"Create src/nlagent/context/grounding.py: bounded read-only file search (name, extension, modified-time range) restricted to folders listed in config, returning at most K candidates; and src/nlagent/context/profile.py for a user-editable profile (aliases, favourite folders) with approve-before-save for suggested entries. Add src/nlagent/context/sanitize.py that escapes control characters, truncates, and wraps file names in delimiters before they enter a prompt. Tests include file names that look like instructions; assert the prompt layer treats them as data."

**N2b (integration and evaluation).**
"Integrate grounding and the profile into propose() as an optional FILES block after the STATE block, keeping L0 and L1 byte-identical. Create scripts/run_context_eval.py measuring path-hallucination rate, reference-resolution accuracy for {transcript replay, compact state, compact state + grounding}, prompt tokens, and time to preview, with provenance and intervals. Add tests/test_injection.py: files named with injected instructions must not alter tier, confirmation or command beyond the allowed paths."

**N4.**
"Create experiments/loops/ with a runner scripts/run_loop.py that executes a program.md contract: restricts edits to the declared mutable file, runs the fixed evaluator on the dev set only, computes a paired bootstrap interval versus the current best, keeps or reverts with git, appends to results.tsv, counts experiments, and refuses to read the sealed test set. Add a command scripts/run_sealed.py that runs the sealed test once for a given git sha and logs every invocation."

**N3.**
"Create scripts/run_learned_escalation.py: build features from effects.analyse for the attack and benign sets, train logistic regression and gradient boosting, optionally TabICLv2 if installed, using time or technique-family splits at sizes 50/100/300/1000; report precision-recall, calibration and extra confirmations per 100 benign. Enforce monotone escalation in the combined guard G3+G5 with a test proving it never lowers a tier."

**N6.**
"Add src/nlagent/runtime/autotune.py: run a short fixed prompt set across candidate settings (threads, GPU layers, context size, batch size), record median time to first token and stability, pick the best, and store the profile. Respect a total time cap of 90 seconds and fall back to defaults on any failure. Tests use a fake server."

**N7.**
"Add a study mode to the UI core: condition flag (plain preview or raw command), consent text, anonymous ids, and local JSONL logging of task, display condition, comprehension answer, approval decision and time. No network. Tests with a fake core client."

---

## 7. Scope control

- **Must (do in order):** T30, N1, T31, N2a/N2b, T32, T25/T40 dev, N4, T42, T37 + N6, sealed finals, T45, T33 to T35 with N7.
- **Should:** pilot study, 500-request end-to-end harm test.
- **Optional:** N3 (learned escalation), T36 (multi-step plans), voice, admin broker.
- If you are late: drop N3 first, then T36, then reduce N4 to the prompt-length loop only.

---

## 8. Checklist

- [ ] Sealed test sets are untouched until the configuration is frozen and pre-registered.
- [ ] Every loop, dev run and final run is logged with provenance; the number of experiments tried is reported.
- [ ] No auto-accepted changes in `safety/`.
- [ ] File names, search results and command outputs are treated as untrusted data in prompts.
- [ ] Kumo or TabICL is not a runtime dependency of the shipped app; check licenses before using any weights.
- [ ] Vault pages link to ledger rows instead of copying numbers.
- [ ] Claims in the paper still follow the claim ladder from `Claims_Validation_and_Combined_Plan.md`.
