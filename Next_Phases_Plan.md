# NL OS Agent: Next Phases Plan (after the handover review)

Based on `Claude_Context_Handover_and_Project_Status.md`. Phases are ordered by dependency. Durations assume 4 people working in parallel; shift them to your calendar.

---

## 1. Honest assessment

### What is strong
- Two-stage `propose()` / `commit()` with a re-check of the effects right before execution (this is the TOCTOU defence). Good design.
- The LLM only proposes; deterministic code decides. Caches cannot skip the effect engine.
- 60 passing tests, a clean Problems panel, a benchmark harness, and scripts that regenerate figures from `results/`.
- A real fine-tuning toolchain that works on a free T4 (30 steps, loss 3.21 to 0.81, no NaNs).

### What needs verifying before you rely on it

| # | Finding in the handover | Why it matters | Action |
|---|---|---|---|
| F1 | E0 reports uncached **45.0 ms**, cached **15.0 ms**, exactly **3.0x**, and 50/50 identical outputs | Means that come out as round numbers look like a mock or fake backend. A mock returns identical output by construction, so the equality gate proves nothing | Phase A1: check provenance; re-run on the real server if needed |
| F2 | Safety benchmark: 73 cases written by the team, G3 = 0/49 bypass | 0/49 only bounds the true bypass rate below about 7% (95% interval). The cases were written alongside the engine, so they may be tuned to it. FPR 2/24 has an interval of roughly 2% to 26% | Phase E: bigger blind set, intervals, pre-registered run |
| F3 | G2 "containment only" shows 100% bypass | It is defined as never intercepting, so the number is true by construction and a reviewer will call it a strawman. Containment tools do stop damage outside their scope | Phase E: report G2 on a different metric (see E2) |
| F4 | G0 is a hand-written verb regex | Weak denylists make the comparison look easy | Phase E: build G0 from published denylist patterns of open-source agents |
| F5 | Safety layer is **static analysis only**; the dynamic sandbox (Layer B) is not in the module list | Your paper and report say "sandbox simulation" | Implement a minimal Layer B for opaque commands, or reword all claims |
| F6 | Training time: 30 steps took 58:34 (about 117 s/step). The handover says 100 to 200 steps take 3 to 4 hours | 100 steps is about 3.3 h, 150 about 4.9 h, 200 about 6.5 h. Free sessions can end earlier | Phase B: chunked runs with checkpoints on Drive/HF |
| F7 | `checkpoint-30` location is not stated | If it is on the Colab VM disk it disappears with the session | Phase A2: confirm it is on Drive/HF |
| F8 | Starting loss 3.21 on a templated task | Often means the loss includes the prompt tokens or the chat template differs from inference | Phase B1: response-only loss, verify the template |
| F9 | Validation set is 800 examples from the same generator | Template leakage inflates accuracy; fine-tuned results will look better than real use | Phase B1/D: held-out template families plus human-written and public tests |
| F10 | LoRA targets are Llama-style names (`q/k/v/o/gate/up/down_proj`) on a hybrid that is mostly Mamba-2 | Mamba mixers may not be adapted at all; 10.1 M trainable parameters suggests only some modules matched | Phase B2: print the adapted module names; consider adding Mamba projections |
| F11 | "Thinking tag stripper" in the backend | Stripping hides tokens that were still generated and still cost decode time | Phase C: measure output tokens; turn reasoning off for this task |
| F12 | Context distillation (E5) only works if the training prompt is the **short** prompt | If the dataset used the full L0, the tuned model gives no prompt savings | Phase B1: build both prompt variants |
| F13 | No real-hardware numbers yet (`llama-bench`, KV/state buffer sizes, `cache_n`) | The latency story has no real data | Phase C |
| F14 | `prefix_cache.py` has tests but is not in the handover module list | Unclear whether it is integrated | Decide after Phase C gate |

---

## 2. Phase overview

| Phase | Goal | Weeks | Depends on | Gate to leave |
|---|---|---|---|---|
| A | Integrity audit and baseline freeze | 0.5 | none | F1, F7 resolved; provenance on all results |
| B | Fine-tuning round 2 (Colab) | 1 to 2 | A | Adapter + GGUF saved, loads locally |
| C | Real-hardware latency measurements | 1 to 3 | A | Real latency breakdown table |
| D | Model evaluation (E5) and strategy comparison (E9) | 1 | B, C | 2x2 results with intervals |
| E | Safety evaluation v2 and system-level harm test | 2 | A | Blind set run once; results frozen |
| F | Integration, undo, demo, small user pilot | 2 | C, E | Demo runs from a clean machine |
| G | Optional: voice, admin broker | 1 each, timeboxed | D, E done | Only if on schedule |
| H | Report and paper | 2 | D, E, F | Every number traced to a script |
| I | Freeze, reproducibility, viva prep | 1 | H | Tagged release |

Phases B, C and E can run in parallel (Colab, local GPU, benchmark writing).

---

## Phase A: Integrity audit and baseline freeze (days 1 to 3)

**A1. Verify E0.** Open `results/e0.jsonl` and `src/nlagent/bench/e0.py`.
- Look for the backend name, model id and server build in each row. Search the code for `mock`, `fake`, `stub`.
- If it ran against a mock: relabel those files as "dry run", do not put them in the report, and re-run against real `llama-server` (Phase C).
- If it ran against the real server: keep it, and also record `prompt_n`, `cache_n` and `prompt_ms` from the server, with the prompt token length.

**A2. Colab checkpoint safety.** Confirm `checkpoint-30` and the adapter are on Google Drive or Hugging Face. Record their sha256.

**A3. Dataset audit** (agent task T10): duplicates and near-duplicates between train and validation, template-family overlap, length distribution, balance across shells, tiers and clarification cases, and how many distinct commands exist.

**A4. Result provenance.** Every row written to `results/*.jsonl` must include `run_id, git_sha, model_file, model_sha256, backend, server_build, gpu, driver, timestamp, seed`. `make_figures.py` refuses rows without them (task T11).

**A5.** Commit and tag `v0.1-baseline`.

---

## Phase B: Fine-tuning round 2 (Colab)

**B1. Dataset v2**
- Build **two prompt variants** from the same examples: `full` (L0 with schema and rules) and `short` (about 100 tokens or less). Train on `short` for the distillation experiment.
- Response-only loss (Unsloth has a helper for this; check your version) and verify the chat template matches what `layout.py` sends at inference.
- Split **by template family** into train, validation and a sealed test set.
- Mix in: clarification examples (10 to 15%), Bash vs PowerShell vs cmd, quoting and spaces in paths, globs, and hard cases the base model gets wrong. Add a small human-written set (written by teammates who have not seen the templates). Check licences before adding any public dataset.

**B2. Training config**
- Print `model.print_trainable_parameters()` and the list of module names that received adapters. If Mamba projections (`in_proj`, `out_proj`) were not adapted, run a second short run that includes them and compare validation accuracy. Treat as an ablation, not a requirement.
- Save a checkpoint to Drive/HF every 20 steps. Evaluate every 20 to 40 steps on validation using actual generation (JSON validity and exact-command match), not just loss.
- Timebox 45 minutes to try compiled `mamba-ssm` and `causal-conv1d` on Colab; if it does not work, continue with the reference scan.

**B3. Run plan with corrected time.** About 117 s/step. Plan 100 steps (about 3.3 h) as chunks of 40 to 60 steps with resume. Go to 150 steps only if validation is still improving. Do not promise a loss target in advance; report what you measure.

**B4. Export**
- Save the LoRA adapter (small) and a merged Q4_K_M GGUF. Keep the original base GGUF untouched so you can A/B.
- Check the merged GGUF loads in `llama-server` and LM Studio; compare outputs for 50 prompts between the Colab model and the GGUF to see quantisation drift.
- Evaluate the **quantised artifact you ship**, not the 16-bit model.

---

## Phase C: Real-hardware latency measurements (the research core)

**C1. Environment.** WSL2 Ubuntu, llama.cpp built with CUDA, models on the Linux filesystem. Save `llama-bench` results for prompt sizes 128/512/1024/2048 and decode 64 tokens. Save the model load log lines for KV and recurrent-state buffer size.

**C2. Thermal protocol.** AC power, high-performance plan, log GPU clocks and temperature with each run, cool-down between runs, 5+ repeats, discard warm-up, randomise order, report median, p95 and bootstrap intervals.

**C3. Latency breakdown (RQ1).** Per request: prompt tokens, output tokens, prefill ms, decode ms, effect engine ms. Check how many tokens are spent on reasoning text. If the base model emits thinking text, switch it off via the system prompt flag in the model card and re-measure; this may be the largest single latency win, bigger than caching.

**C4. Experiments (from the earlier plan)**
| ID | What | Real data needed |
|---|---|---|
| E0 | Cached vs uncached output equality (real server, greedy) | mismatch rate |
| E1 | Stock `llama-server` reuse: `cache_prompt` on/off; 1 vs 2 to 3 interleaved sessions | `prompt_n`, `cache_n`, `prompt_ms` |
| E2 | Naive vs cache-aware prompt layout | `cache_n/prompt_n`, TTFT |
| E4 | Transcript replay vs compact state | prompt tokens, TTFT, reference-resolution accuracy |
| E3 | Prefix-state cache vs stock reuse (only if E1 shows stock reuse fails under interleaving) | TTFT p50/p95, hit rate, snapshot size, restore time |
| E7 | KV quantisation (optional) | VRAM, speed, accuracy |

**Gate.** If stock reuse already works under interleaving, skip E3, report that as a result, and keep your contribution on layout, compact state, distillation and the safety coupling.

---

## Phase D: Model evaluation (E5) and strategy comparison (E9)

**D1. Execution-based evaluator.** For each test prompt, create a fixture directory, run the candidate command in a sandbox (WSL temp dir or container), and compare the resulting manifest diff or normalised stdout with the reference (reuse `manifest.py`). String match is not enough because many commands are equivalent.

**D2. Test sets (sealed until final run)**
| Set | Size | Source |
|---|---|---|
| T-a | 300+ | Held-out template families |
| T-b | 100+ | Human-written by teammates who did not see the templates |
| T-c | 200+ | Public benchmark sample (check licence) |

**D3. 2x2 design (answers F12).** Model {base, tuned} x prompt {full, short}, all Q4_K_M. Metrics: execution accuracy, JSON validity, clarification precision/recall, cross-shell correctness, prompt tokens, output tokens, TTFT, end-to-end latency, VRAM. Use McNemar for paired accuracy and bootstrap intervals for latency.

**D4. Strategy comparison (E9, a headline result).** Compare latency-reduction strategies on the same workloads: none, prefix cache, distillation, verified-command cache, and combinations. Do they add up or overlap? Plot a Pareto chart: p50 latency vs accuracy, marker size = VRAM. Hold safety constant (see E5/E6 in Phase E).

---

## Phase E: Safety evaluation v2 and system-level harm test

**E1. Bigger, blind benchmark.** Freeze `effects.py`. Two teammates who have not read it write at least 300 attack cases across the bypass families plus at least 1,000 benign commands (from public command corpora and real use). Run once, record the commit hash, and do not tune afterwards. Report all failures.

With 300 attacks and 0 misses, the 95% upper bound on the miss rate is about 1.2% (with 49 attacks it was about 7%). Use Clopper-Pearson or Wilson intervals everywhere.

**E2. Fair baselines**
- G0: build from denylist patterns published in open-source agent configs (cite each); keep your regex as G0-simple.
- G1: your old classifier, kept as is.
- G2: do not report "bypass 100%". Report that G2 gives no preview or tiering, and measure instead what it cannot prevent: damage inside its permitted scope and no information shown to the user. Say plainly that containment is complementary.
- G3: effect engine. Also report G3 + containment as the recommended combination.

**E3. Separate blocked vs escalated.** Report how many attacks were hard-blocked, escalated to confirmation, or auto-run. Report the confirmation burden (prompts per 100 benign commands).

**E4. Dynamic Layer B (resolves F5).** Implement a minimal mirror-run for opaque cases (inline interpreters, `find -exec`, nested shells) using a container or WSL temp mirror, then diff manifests. Measure what fraction of `ambiguous` cases it resolves and its latency. If you skip it, change every "sandbox simulation" claim to "static effect analysis" in the report and paper.

**E5. System-level harm test.** 500 natural-language requests (about 150 with risky intent, including vague ones like "clean this up"). The model generates commands, the pipeline runs in temp fixtures. Count harmful effects executed without confirmation and benign requests that were over-escalated. Compare pipelines: LLM + G0, LLM + G1, LLM + G3. This is the metric closest to your title.

**E6. Cache-safety regression.** Re-run E5 with the verified cache and prefix cache on/off. Tiers must match. Add the adversarial case: a destructive command previously approved and cached must still ask for confirmation.

**E7. TOCTOU test.** Change the file system between `propose()` and `commit()` (add files, swap a symlink) and show that the commit re-analysis catches it.

**E8. Error analysis.** A table of every false positive, near-miss and known limit (network effects, time-dependent behaviour, sandbox vs host differences). This reads as rigour.

---

## Phase F: Integration, undo, demo, pilot

1. **Shell hooks.** Test `Ctrl+G` on Bash (WSL) and PowerShell using `quick_propose.py`; confirm `cd` and `export` persist.
2. **Daemon.** Package as a user service; measure start-up time and idle memory.
3. **Undo/quarantine (Should).** Move deleted or overwritten files to a quarantine folder with a manifest; `undo` restores. Tests: restore correctness, name clashes, quota. Report undo success rate.
4. **Demo script (3 minutes):** read-only, state-modifying, destructive with preview and confirmation, undo, a bypass attempt that fails, a vague request that triggers a clarifying question, and a cache hit.
5. **Small pilot.** 6 to 8 peers perform 6 tasks with and without the agent; record time, errors and a short satisfaction rating. Informal, consented, anonymised, and report it as a pilot with its limits. Check your department's rules on human participants.

---

## Phase G: Optional modules (only if A to E are done by week 7)

| Module | Timebox | Key requirement |
|---|---|---|
| Voice | 1 week | Offline Whisper on CPU; show transcript; destructive commands need a spoken challenge phrase with the effect count; never confirm elevation by voice; report word error rate on command vocabulary |
| Admin broker | 1 week | Separate process, structured operations only (4 to 6), approval hash must match the preview, audit log |

If skipped, list both as future work with a short design sketch. Do not half-implement them.

---

## Phase H: Report and paper

| Chapter / section | Fill with |
|---|---|
| Ch. 2 | Updated related work including Marconi and the sandbox tools; correct Nemotron-3-Nano-4B facts (about 4B hybrid, four attention layers) |
| Ch. 5 | Real configs: model file, quantisation, LoRA settings, training time |
| Ch. 6 | Test results (unit, integration, TOCTOU, cache-safety) |
| Ch. 8 | Only measured data: latency breakdown, E1 to E9, safety v2, harm test, pilot |
| Ch. 9 | Limitations: static analysis limits, small hardware, template-built data, pilot size |

Checklist:
- Remove all placeholder numbers from earlier figure files.
- Reword "first" claims as "to the best of our knowledge, among the systems we reviewed", and keep a record of your search queries.
- Replace "kernel-level" wording with "OS-level user service" unless you wrote kernel code.
- Add a limitations and threats-to-validity section (benchmark authored by the team, small n, one GPU, one model).
- Disclose AI assistance in writing and coding according to IEEE and your college policy (IEEE asks for disclosure of AI-generated text in the acknowledgements; check the current author guidelines).
- If the conference paper is already submitted, check the AIEI dates for any revision window and update it with real numbers.

---

## Phase I: Freeze, reproducibility, viva

- `v1.0` tag; pinned `requirements.txt`; model file hashes; `make reproduce` (or one script) that re-creates tables and figures from `results/`.
- README with a clean-machine setup (WSL2, llama.cpp build, model download).
- Demo video as a backup in case live hardware fails.
- Panel Q&A prep (see end of this file).

---

## 3. Claims ledger

| Claim | Status today | Needed to claim it |
|---|---|---|
| Effect-based tiering beats name-based and denylist guards | Preliminary (n=49, team-written) | Phase E1 to E3 |
| 0% bypass | Not claimable; only "0/49 on our own set, upper bound about 7%" | Phase E1 |
| Sandbox simulation | Not implemented (static only) | Phase E4, or reword |
| Prefix caching gives X speedup | Unverified (E0 numbers suspicious) | Phase A1, C |
| Fine-tuning improves accuracy | Not yet measured (only training loss) | Phase D |
| Context distillation shortens the prompt | Depends on how the dataset was built | Phase B1, D |
| System lowers latency on a 4 GB GPU | No real measurement yet | Phase C, D |
| End-to-end harm reduced | Not measured | Phase E5 |

---

## 4. Decision gates and fallbacks

| Gate | If it fails |
|---|---|
| E0 numbers were from a mock | Re-run on the real server before any caching claim |
| Colab sessions end too early | Chunked runs with Drive checkpoints; consider Kaggle (check current limits) |
| Tuned model is not better than base + full prompt | Report honestly; the contribution shifts to prompt-length and latency, not accuracy |
| Stock reuse already handles interleaving | Skip E3; report the finding |
| 4 GB runs out of memory | IQ4_XS, smaller context, partial offload |
| Blind safety set reveals misses | Fix once, document the fix, report both before and after on the same set plus a fresh second blind set |
| Timeline slips | Cut Phase G, then undo, then the pilot |

---

## 5. Agent prompts (copy-paste)

**T10: dataset audit.**
"Create scripts/audit_dataset.py. For datasets/finetune_train.jsonl and finetune_val.jsonl: report exact and near-duplicate overlap (normalised lowercase, Jaccard >= 0.9 on word 3-grams), template-family overlap if a `family` field exists, token-length distribution, counts by shell, risk tier and clarification flag, and the number of distinct target commands. Write results/dataset_audit.md. Add tests with a tiny synthetic dataset."

**T11: provenance.**
"Add src/nlagent/bench/provenance.py with `collect() -> dict` returning run_id, git_sha, model_file, model_sha256, backend, server_build, gpu name, driver, timestamp, seed. Make every script under scripts/ attach it to each JSONL row. Make scripts/make_figures.py refuse rows without these fields and print which file is missing them."

**T12: E0 on the real server.**
"Update scripts/e0_equality.py to refuse to run when the backend reports a mock/fake name. Use 50 prompts from layout.build_prompt with an L0 of at least 400 tokens. Record prompt_n, cache_n, prompt_ms per request, and the exact-match rate under greedy decoding. Write results/e0_real.jsonl."

**T13: execution-based evaluator.**
"Create src/nlagent/eval/exec_eval.py: given test cases (fixture spec, request, reference command), run model-generated and reference commands in separate temp copies of the fixture and compare manifest diffs (or normalised stdout for read-only commands). Tests use only tempfile directories."

**T14: 2x2 evaluation runner.**
"Create scripts/run_e5.py that evaluates {base, tuned} x {full, short} prompts on a sealed test file, writes per-case results and per-model summaries with Wilson intervals and a McNemar test for paired comparisons."

**T15: safety bench v2.**
"Create scripts/run_safety_bench_v2.py that loads datasets/safety_bench_v2_attacks.jsonl and datasets/benign_commands.jsonl, scores G0-published, G0-simple, G1, G2-metrics, G3, reports hard-blocked / escalated / auto-run counts, confirmation burden per 100 benign, and Clopper-Pearson intervals. Record the engine git_sha in the output. All execution in tempfile directories."

**T16: system-level harm test.**
"Create scripts/run_harm_test.py: for each of 500 requests, call propose(), apply the policy (simulate 'user declines' for escalated commands), and execute the allowed ones inside a fixture temp directory. Count harmful effects (deletions, overwrites, truncations, permission changes beyond what the request asked for) that ran without escalation. Run with cache on/off and assert identical tiers."

**T17: TOCTOU test.**
"Add tests/test_toctou.py: create files, call propose() for a read-only-looking glob command, then change the directory contents (add files, replace a path with a symlink), then call commit(); assert that commit re-analysis changes the tier or blocks execution."

---

## 6. Likely panel questions (prepare honest answers)

1. How is your classifier different from a denylist? (Resolves targets and derives the tier from the effect; opaque forms are escalated; show the bypass families.)
2. Who wrote the benchmark? (State it; show the blind second set.)
3. What can your system not catch? (Network effects, time-dependent behaviour, sandbox/host differences, TOCTOU if you skip the re-check; show your limits table.)
4. Why not just use a sandbox like Codex or Claude Code? (Complementary: containment limits scope; you preview and tier effects and support undo. Show G3 + containment.)
5. Is caching safe? (Every hit still goes through the effect engine; E6 shows identical tiers.)
6. Why fine-tune a 4B model? (Local, private, and a measured prompt/latency trade-off; show the 2x2.)
7. What is new compared with Marconi and AIOS? (Marconi is a server-side caching system; AIOS is an application-layer agent OS. Yours is a safety-gated single-user agent with application-aware context on a small GPU; state your search record.)
8. How do you know the speedup is real? (Provenance, repeats, thermal protocol, intervals, scripts in the repo.)
