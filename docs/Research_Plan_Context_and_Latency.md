# Research Plan: Cache-Aware Context Management and Low-Latency Inference for a Safety-Gated NL Shell Agent

Target hardware: RTX 2050 (4 GB VRAM), local. Model: NVIDIA-Nemotron-3-Nano-4B (hybrid Mamba-2 + Transformer).

---

## 0. Short answers

| Your question | Answer |
|---|---|
| Download the 4 files into the Antigravity repo and tell the agent to implement? | Yes for the files, but do **not** say "implement everything". Put the plan in `docs/`, the three tested Python files in `src/`, and give the agent **one small task at a time with a pytest acceptance test** (§7). Review the safety code yourself. |
| Is KV-cache / context latency a good research direction? | Yes, and it is a better fit for your model than it first looks (§1). But it must be framed as a measured systems study plus an application-aware design, not as "we invented prefix caching". |
| Does it run on an RTX 2050 4 GB? | Inference: probably, but tight (§5). Fine-tuning locally: no, plan on free cloud GPUs. |
| Is Unsloth free? | The open-source library is free. The limit is GPU memory, not the licence. Its docs say the 4B model fits a free Colab GPU. A community run on this model used an 8 GB card, so 4 GB is below the only data point I found (§6). |

**Scope warning.** You now have: effect engine, undo, admin broker, voice, fine-tuning, and a caching study. That is too much for one report. Pick a thesis (§8) and treat the rest as optional modules.

---

## 1. Why this direction is real (verified facts)

1. **The model has very few attention layers.** The Nemotron-3-Nano-4B card describes a hybrid made mostly of Mamba-2 and MLP layers with just four attention layers. So the classic KV-cache memory problem is small for this model; check the "KV buffer size" line in the llama.cpp load log. The thing that matters is the **recurrent (SSM) state**, and how it is reused across requests.
2. **Prefix caching is harder on hybrids.** Marconi (MLSys 2025, arXiv:2411.19379) explains that recurrent layers update their state in place, so a cache entry cannot be rolled back to a shorter prefix; only exact-match hits work. They report, on server-class setups, up to 34.4x higher token hit rates and up to 71.1% (617 ms) lower P95 time-to-first-token versus prior prefix caching. Related: "Sparse Prefix Caching for Hybrid and Recurrent LLM Serving" (arXiv:2605.05219).
3. **llama.cpp support is still maturing.** Open issues report that on hybrid/recurrent models, prompt reuse needs "context checkpoints", that restoring a saved slot can lose all reuse because checkpoints are not persisted, and that `cache_reuse` is unsupported for hybrid models in some builds (issues #24055, #25913, #28194, #21383, mostly reported on other hybrid models). **Do not assume these hold for Nemotron-H on your build; measure them (Experiment E1).**
4. **Nobody has designed for your workload.** Those papers target multi-user GPU servers. Your workload is single-user, a few interleaved sessions, a small GPU, and a prompt with a large static part plus a tiny per-turn delta. That structure lets you place checkpoints *by design* instead of guessing.

**Honest novelty statement (use this wording).** "Prefix caching and hybrid-model caching are existing work. Our contribution is an application-aware context and checkpoint design for a safety-gated command agent on a constrained consumer GPU, plus a measured study of its latency/accuracy/safety trade-offs." Before claiming anything is new, run a systematic search (arXiv, IEEE Xplore, Scholar) and record the queries. The community is actively patching hybrid caching in llama.cpp forks, so the engineering alone will not be a durable differentiator.

---

## 2. Research questions and hypotheses

| RQ | Question | Hypothesis (to be tested, not assumed) |
|---|---|---|
| RQ1 | Where does end-to-end latency go (prefill, decode, effect engine, sandbox, ASR)? | Prefill of a long static prompt dominates TTFT on a 4 GB laptop GPU; decode is memory-bandwidth-bound. |
| RQ2 | Does stock llama.cpp reuse the static prefix for Nemotron-H, and does it survive interleaved sessions? | Reuse works for a single warm slot but degrades when 2+ sessions interleave. |
| RQ3 | Does an application-aware multi-level prefix-state cache beat stock reuse? | Yes under interleaving; no difference for a single session. |
| RQ4 | Compact semantic state vs transcript replay: effect on prompt length, latency, reference-resolution accuracy? | Compact state keeps prompt length roughly constant per turn and matches or beats transcript replay accuracy. |
| RQ5 | Can LoRA "context distillation" remove few-shot/schema text from the prompt without losing accuracy? | Prompt shrinks substantially with a small accuracy loss. |
| RQ6 | Does a verified-command cache cut LLM calls without safety regressions? | Meaningful hit rate on repetitive workloads; zero change in bypass rate because hits still pass the effect engine. |
| RQ7 (sanity) | Does KV-cache quantization help this model? | Little effect, because only four attention layers exist. A confirmed negative result is still a finding. |

---

## 3. System design

### 3.1 Cache-aware context layout (the "rules")
Hybrid models need **exact, append-only prefixes**, so the prompt is built in layers, stable first, volatile last:

```
L0  (static, cached forever)   system prompt + JSON schema + safety rules + (optional) fixed few-shot
L1  (per session, changes rarely) OS, shell, user, available tools, path style
L2  (per turn, never cached)   compact STATE block + user request
```

Rules:
1. No timestamps, random ids, or reordered fields inside L0/L1.
2. Anything that changes every turn (cwd, last effects, entity table) goes in L2 as a **compact state block**, never in L0/L1.
3. Cut prefixes at message or special-token boundaries; tokenize the prefix and suffix separately and concatenate token ids so a token merge across the boundary cannot change the prefix.
4. Pin one chat template and the reasoning on/off switch (see the model card); a different template string invalidates the cache.

### 3.2 Compact semantic state (replaces transcript replay)
Instead of replaying the last N commands, render a bounded state block:

```
STATE cwd=/home/u/proj
ENT f1=./report.pdf(last:t3) d1=./backup(last:t2)
LAST t4: mkdir backup -> created 1; t5: cp *.pdf backup/ -> created 3
```
Bounded size (for example at most 200 tokens). The entity table you already have supplies the `ENT` line. This gives O(1) prompt growth per turn versus O(n) for transcripts.

### 3.3 Hierarchical prefix-state cache (HPSC)
- Entries: `(hash(token_ids[0:k])) -> snapshot` where a snapshot is the full sequence state (attention KV for those tokens plus the recurrent state) at boundary `k`.
- Boundaries: B0 = end of L0, B1 = end of L1. Taken once, at admission time, because you know they will recur (this is the application-aware part; Marconi has to infer likely reuse).
- Lookup per request: restore the longest matching snapshot (B1, else B0, else none), then evaluate only the remaining tokens.
- Eviction: LRU with a RAM budget (and optional disk spill). On a 4 GB GPU, keep snapshots in system RAM and restore to the GPU.
- Correctness test (E0): with greedy decoding, outputs from cached and uncached runs must match; report any mismatch rate.

### 3.4 Verified-command cache (semantic)
Store `(normalised request embedding -> structured proposal)` only for proposals the user approved and that executed successfully. On a hit above a similarity threshold, skip the LLM but **still run the effect engine, tiering, and confirmation**. Never cache across different cwd/OS/shell keys.

### 3.5 Context distillation
Fine-tune with LoRA so the model internalises the schema and examples; then remove L0's few-shot block. Compare accuracy and TTFT before and after.

---

## 4. Experiments

Common setup: temperature 0, fixed seed, 5+ repeats, discard warm-up, report median and p95 with bootstrap confidence intervals, randomise run order. **Laptop GPUs throttle**: plug in AC, use a high-performance power plan, log `nvidia-smi` clocks and temperature, and let the GPU cool between runs.

| ID | Experiment | Measure | Notes |
|---|---|---|---|
| E0 | Cached vs uncached output equality | Mismatch rate (greedy) | Gate for everything else |
| E1 | Stock `llama-server`: `cache_prompt` on/off; single vs 2-3 interleaved sessions | `prompt_n`, `cache_n`, `prompt_ms`, TTFT | Checks the hybrid-reuse issues on your build |
| E2 | Prompt layout: naive vs cache-aware (§3.1) | `cache_n`/prompt_n ratio, TTFT | Cheapest win |
| E3 | HPSC vs stock reuse under interleaved sessions | TTFT p50/p95, hit rate, snapshot size, restore time, RAM | Core systems result |
| E4 | State representation: transcript vs compact state vs hybrid | Prompt tokens, TTFT, reference-resolution accuracy on multi-turn dialogues | Core context-management result |
| E5 | Context distillation (cloud LoRA) | Exec accuracy, JSON validity, prompt tokens, TTFT | Needs trained adapter |
| E6 | Verified-command cache | Hit rate, accuracy on hits, safety regressions | Hits must still pass the effect engine |
| E7 | KV quantization (`--cache-type-k/v`) | VRAM, speed, accuracy | Expect small effect |
| E8 | End-to-end | p50/p95 latency of propose (text), plus voice path | Final headline number |

Workloads: (a) synthetic session traces of 10 to 20 turns built from templates, (b) your own audit logs from real use, (c) adversarial "volatile" sessions where state changes every turn (worst case for caching), (d) interleaved traces from 2-3 sessions. Report a latency breakdown (RQ1) in every experiment.

Accuracy must always be reported next to latency; a faster agent that is less accurate or less safe is not a win. Re-run the safety benchmark (see the earlier plan, section 9) with caching enabled and report any change.

---

## 5. Running on an RTX 2050 (4 GB)

### 5.1 Memory budget (estimates, verify with the load log and `nvidia-smi`)
| Item | Notes |
|---|---|
| Weights | Q4_K_M is about 2.9 GB; IQ4_XS about 2.5 GB; Q3_K_M about 2.5 GB (file sizes from public GGUF listings) |
| CUDA context + compute buffers | Several hundred MB; grows with context and batch size |
| KV + recurrent state | Small for this model; read the log |
| Desktop use | On a hybrid-graphics laptop the iGPU usually drives the display; check that the dGPU is not also used by the compositor |

Start with Q4_K_M, `-ngl 99`, `-c 2048` to `4096`. If it OOMs: IQ4_XS, then smaller `-c`/`-b`/`-ub`, then fewer GPU layers. Decode speed is memory-bandwidth-bound, so measure tokens/s rather than trusting any theoretical number.

### 5.2 Keep the GPU for the LLM only
Run Whisper on CPU (int8), the sandbox on CPU, and the effect engine on CPU. Do not run LM Studio and a research harness at the same time; they will fight for VRAM.

### 5.3 Environment (use WSL2 Ubuntu for everything)
```bash
# inside WSL2 Ubuntu; use the NVIDIA WSL CUDA toolkit package, not a Linux GPU driver
sudo apt update && sudo apt install -y build-essential cmake git python3-venv
git clone https://github.com/ggml-org/llama.cpp && cd llama.cpp
cmake -B build -DGGML_CUDA=ON && cmake --build build -j
# baseline speed
./build/bin/llama-bench -m ~/models/NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf -p 512,1024,2048 -n 64 -ngl 99
# server for E1-E3
./build/bin/llama-server -m ~/models/NVIDIA-Nemotron-3-Nano-4B-Q4_K_M.gguf -ngl 99 -c 4096 --port 8080
```
Check `llama-server --help` for checkpoint-related flags in your build. Keep the repo and models on the Linux filesystem (`~/`), not `/mnt/c`, for speed.

**Bindings caveat.** `llama-cpp-python` may lag behind llama.cpp and might not load `nemotron_h` yet. Test that first. If it fails, drive `llama-server` over HTTP (it returns a `timings` object with `prompt_n`, `prompt_ms`, and `cache_n` in recent builds) or build the bindings against your own llama.cpp checkout.

### 5.4 Tiny measurement client (sketch, untested here)
```python
import requests, time, statistics as st
def ask(prompt, n_predict=64, cache=True, url="http://127.0.0.1:8080/completion"):
    t0 = time.perf_counter()
    r = requests.post(url, json={"prompt": prompt, "n_predict": n_predict,
                                 "temperature": 0, "cache_prompt": cache}).json()
    return {"wall_s": time.perf_counter() - t0, **r.get("timings", {})}
```
Log every call as JSONL (request id, session id, prompt tokens, cache hit tokens, times) so figures are regenerated by script.

---

## 6. Unsloth LoRA (free route)

| Where | Verdict |
|---|---|
| Local 4 GB | Not recommended. The only verified local run I found used an 8 GB RTX 2060 Super with 4-bit loading and sequence length 1024. Treat 4 GB as likely OOM. |
| **Free Colab GPU** | Use this first. Unsloth's docs state the 4B model fits a free Colab GPU. Session limits and GPU availability vary. |
| Kaggle notebooks | Free weekly GPU quota (check current limits) as a second option. |
| NeMo AutoModel | Official LoRA recipe for this exact model; needs a Linux GPU or NGC container. |

Practical notes: the architecture needs `mamba-ssm` and `causal-conv1d` builds and eager attention (reported by community guides); use Unsloth's notebook so versions are pinned. Save adapters to Google Drive or Hugging Face after every run, because free sessions end. Then merge, convert to GGUF with llama.cpp, quantise, and copy the file to your laptop. For research E5 you need two checkpoints: base and distilled.

---

## 7. Working with the Antigravity agent

### 7.1 Repo layout
```
nl-agent/
  docs/        Next_Steps_Fully_Functional_System.md, this file
  src/nlagent/
    safety/    effects.py, manifest.py          (from the delivered files)
    context/   layout.py, state.py, entity.py
    cache/     prefix_cache.py, verified_cache.py
    bench/     harness.py, workloads.py
    daemon/    api.py, propose.py
  tests/       test_effects.py, test_layout.py, ...
  results/     jsonl logs, figures (generated)
```

### 7.2 Rules to give the agent (paste into your IDE's rules/instructions file)
- Work on one task at a time; stop when the stated pytest command passes.
- Tests must use temp directories. Never run `rm`, `chmod`, `kill`, or `sudo` against real paths. No network calls in tests.
- Do not change the semantics of `effects.py` without adding or updating tests; show the diff and explain it.
- Type hints and docstrings on public functions; no new dependencies without asking.
- Log latency data to JSONL; never hard-code results.
- Run the agent's terminal inside WSL2. Export a backup first: `wsl --export Ubuntu ubuntu-backup.tar`.

### 7.3 Prompts (copy-paste, one per task)

**T0: orient.**
"Read docs/Next_Steps_Fully_Functional_System.md and docs/Research_Plan_Context_and_Latency.md. Do not change code. Run `pytest tests/test_effects.py` and report results. Then list the modules you propose to create for sections 3.1 to 3.4 of the research plan, with function signatures only."

**T1: prompt layout.**
"Create src/nlagent/context/layout.py with `build_prompt(l0: str, l1: dict, state: str, request: str) -> PromptParts`, where PromptParts holds `l0_text, l1_text, l2_text` and the full text. Rules: L0 and L1 must be deterministic (sorted keys, no timestamps). Add tests/test_layout.py proving that two calls with different state/request produce byte-identical L0+L1 prefixes, and that changing one L1 field changes only L1+L2. Stop when `pytest tests/test_layout.py` passes."

**T2: compact state.**
"Create src/nlagent/context/state.py: a `SessionState` class with cwd, an entity table (name, type, path, last_turn), and the last 3 effects. Add `render(max_tokens=200)` that returns the compact STATE block in section 3.2 of the research plan, truncating oldest items first. Add tests for 'delete that file' resolution across 3 turns and for the size bound (count tokens with a whitespace approximation)."

**T3: bench harness.**
"Create src/nlagent/bench/harness.py that sends requests to a llama-server /completion endpoint with cache_prompt on/off, records wall time and the server `timings` object to results/*.jsonl, and supports running N interleaved sessions. No model needed for unit tests: mock the HTTP layer and test the JSONL schema and interleaving order."

**T4: snapshot cache (after E0/E1 show it is needed).**
"Create src/nlagent/cache/prefix_cache.py implementing the HPSC in section 3.3: keys are SHA-256 of token-id tuples at boundaries B0/B1, values are opaque snapshot blobs, LRU eviction by byte budget. Keep the model backend behind an interface (`save_state()`, `load_state(blob)`, `eval(tokens)`), so tests use a fake backend. Do not call a real model in tests."

**T5: verified cache.**
"Create src/nlagent/cache/verified_cache.py: store (request embedding, env key, proposal) only after a success record; lookup returns a proposal when cosine similarity >= threshold and the env key (OS, shell, cwd) matches. Every hit must still be passed to `effects.analyse` by the caller; add a test that a cached destructive proposal is still classified destructive."

Review every agent diff in `safety/` and `cache/` yourself before merging.

---

## 8. Recommended scope (thesis)

**Core thesis:** "A safe, low-latency, local NL shell agent: (1) effect-based safety, and (2) cache-aware context management for edge hybrid models."

| Priority | Modules |
|---|---|
| **Must** | Effect engine + tiers, shell integration (propose/commit), compact state + cache-aware layout, stock-vs-HPSC measurement (E0-E4, E8), safety benchmark |
| **Should** | Fine-tuning with context distillation (E5), verified cache (E6), undo/quarantine |
| **Could** | Voice, admin broker, KV quantisation study (E7) |

Cut "Could" items from the report if time slips; mention them as future work.

---

## 9. Revised timeline (12 weeks)

| Weeks | Work | Done when |
|---|---|---|
| 1-2 | WSL2 + CUDA llama.cpp, baseline `llama-bench`, harness (T3), effects tests extended | Baseline latency breakdown (RQ1) in a table |
| 3-4 | Layout (T1), compact state (T2), E1, E2 | `cache_n` ratio before/after layout change |
| 5-6 | E0, snapshot cache (T4) if justified, E3, E4 | HPSC vs stock under interleaving |
| 7-8 | Dataset build; Colab LoRA run; E5; merge to GGUF | Distilled vs base results |
| 9 | Verified cache (T5), E6; safety benchmark re-run with caching on | No safety regression |
| 10 | E8 end-to-end; optional undo/voice | Headline latency figure |
| 11-12 | Writing, figures from scripts, demo | Report + paper draft |

---

## 10. Related work to cite (verify each before use)

- Marconi: Prefix Caching for the Era of Hybrid LLMs, MLSys 2025, arXiv:2411.19379 (base paper for the caching part).
- Sparse Prefix Caching for Hybrid and Recurrent LLM Serving, arXiv:2605.05219.
- Efficient Memory Management for LLM Serving with PagedAttention (vLLM), arXiv:2309.06180.
- SGLang / RadixAttention, arXiv:2312.07104.
- LMCache, arXiv:2510.09665.
- llama.cpp issues #24055, #25913, #28194, #21383 as engineering evidence (cite as technical issue reports).
- Your earlier references for safety (AIOS, denylist fragility, etc.).

---

## 11. Risks

| Risk | Mitigation |
|---|---|
| 4 GB is too tight | IQ4_XS, smaller context; fallback is a smaller model for the caching study, keeping Nemotron for the final demo |
| Bindings do not support Nemotron-H | Use `llama-server` over HTTP; build bindings from your own checkout |
| Cached output differs from uncached | E0 gate; use the snapshot only at exact prefix boundaries; report mismatch rate |
| Noisy laptop timings | AC power, fixed clocks where possible, repeats, randomised order, report CIs |
| Stock llama.cpp already solves reuse | That is a valid result for RQ2; your contribution shifts to the context-layout and interleaving results |
| Scope creep | Section 8 priorities; freeze features by week 8 |

---

## 12. Next 7 actions

1. Create the repo layout in §7.1 inside WSL2, copy in the three tested files and both docs.
2. Build llama.cpp with CUDA; run `llama-bench` on your Q4_K_M file and save the numbers.
3. Check whether the model loads and what the load log says about KV and recurrent state size.
4. Run E1 by hand: same long prompt twice, `cache_prompt` on, read `cache_n`.
5. Give the agent T0, then T1 and T2.
6. Start the Colab Unsloth notebook for the 4B model with a tiny dataset to confirm the toolchain works.
7. Write down your literature-search queries now, so your novelty claim in the paper is backed by a record.
