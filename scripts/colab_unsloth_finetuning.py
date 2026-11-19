# -*- coding: utf-8 -*-
"""
scripts/colab_unsloth_finetuning.py - Unsloth Colab Training Script (V2).

Run in Google Colab (T4 16GB or A100 GPU):
1. Upload `datasets/finetune_v2_train.jsonl` and `datasets/finetune_v2_val.jsonl`
   (or clone the repository and run from repo root).
2. Loads unsloth/NVIDIA-Nemotron-3-Nano-4B in 4-bit QLoRA (r=32, seq_len=1024).
3. Trains with T4-safe micro-batching (batch_size=1, grad_accum=16 => effective batch 16),
   expandable CUDA segments, cosine LR decay, and checkpoint resume support across
   PowerShell, cmd.exe, Bash, and Zsh (400 steps default; raise if on A100/L4).
4. Exports GGUF (Q4_K_M). After downloading from Colab, run locally:
     python scripts/fix_nemotron_gguf.py --input <downloaded.gguf> --output models/NVIDIA-Nemotron-3-Nano-4B-V2-Dense-Q4_K_M.gguf
"""

# Cell 1 (Colab only): Install Unsloth & set allocator BEFORE importing torch
# !pip install --no-deps bitsandbytes accelerate xformers peft trl triton cut_cross_entropy unsloth_zoo
# !pip install --no-deps unsloth

import gc
import inspect
import os

# Must be set before PyTorch initializes CUDA to prevent T4 fragmentation OOM
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")

import torch
from datasets import load_dataset
from transformers import TrainingArguments
from transformers.trainer_utils import get_last_checkpoint
from trl import SFTTrainer
from unsloth import FastLanguageModel

# Cell 2: Model Configuration (T4 14.5 GB VRAM safe)
# V2 max sample is ~2,795 chars (~850 tokens), so 1024 fits 100% of examples
# while avoiding the 12+ GiB reference Mamba-2 chunk-scan allocation at seq_len=2048.
max_seq_length = 1024
dtype = None  # Auto-detect Float16 (T4) / Bfloat16 (Ampere+)
load_in_4bit = True

model_name = "unsloth/NVIDIA-Nemotron-3-Nano-4B"

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=model_name,
    max_seq_length=max_seq_length,
    dtype=dtype,
    load_in_4bit=load_in_4bit,
)

# Cell 3: Add LoRA Adapters (r=32 for stronger 4-shell cross-ecosystem retention)
model = FastLanguageModel.get_peft_model(
    model,
    r=32,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha=32,
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

# Cell 4: Load V2 Dataset (supports running from Colab root or repo root)
train_file = "finetune_v2_train.jsonl" if os.path.exists("finetune_v2_train.jsonl") else "datasets/finetune_v2_train.jsonl"
val_file = "finetune_v2_val.jsonl" if os.path.exists("finetune_v2_val.jsonl") else "datasets/finetune_v2_val.jsonl"

dataset = load_dataset("json", data_files={"train": train_file, "validation": val_file})


def formatting_prompts_func(examples):
    convs = examples["messages"]
    texts = [tokenizer.apply_chat_template(conv, tokenize=False, add_generation_prompt=False) for conv in convs]
    return {"text": texts}


formatted_dataset = dataset.map(formatting_prompts_func, batched=True)
eval_subset = formatted_dataset["validation"].shuffle(seed=3407).select(
    range(min(200, len(formatted_dataset["validation"])))
)

# Cell 5: Train with SFTTrainer (T4-safe batch=1 x grad_accum=16 = effective batch 16)
# Note: On Colab T4 without compiled `mamba_ssm`, throughput is ~1.5-2 min/step;
# raise `max_steps` to 600-1100 if running on A100/L4 or resuming across sessions.
ta_kwargs = dict(
    per_device_train_batch_size=1,
    per_device_eval_batch_size=1,
    gradient_accumulation_steps=16,
    eval_accumulation_steps=4,
    warmup_steps=25,
    max_steps=400,
    learning_rate=2e-4,
    fp16=not torch.cuda.is_bf16_supported(),
    bf16=torch.cuda.is_bf16_supported(),
    logging_steps=10,
    eval_steps=50,
    save_strategy="steps",
    save_steps=50,
    save_total_limit=3,
    load_best_model_at_end=True,
    metric_for_best_model="eval_loss",
    greater_is_better=False,
    optim="adamw_8bit",
    weight_decay=0.01,
    lr_scheduler_type="cosine",
    seed=3407,
    output_dir="outputs_v2",
)
sig_params = inspect.signature(TrainingArguments.__init__).parameters
if "eval_strategy" in sig_params:
    ta_kwargs["eval_strategy"] = "steps"
else:
    ta_kwargs["evaluation_strategy"] = "steps"

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=formatted_dataset["train"],
    eval_dataset=eval_subset,
    dataset_text_field="text",
    max_seq_length=max_seq_length,
    dataset_num_proc=2,
    packing=False,
    args=TrainingArguments(**ta_kwargs),
)

last_ckpt = get_last_checkpoint("outputs_v2") if os.path.isdir("outputs_v2") else None
trainer_stats = trainer.train(resume_from_checkpoint=last_ckpt)
print("Training finished:", trainer_stats)

# Free training graph buffers before GGUF conversion/export
gc.collect()
if torch.cuda.is_available():
    torch.cuda.empty_cache()

# Cell 6: Save LoRA Adapter & Export Q4_K_M GGUF
model.save_pretrained("lora_adapter_v2")
tokenizer.save_pretrained("lora_adapter_v2")

model.save_pretrained_gguf("model_v2_q4_km", tokenizer, quantization_method="q4_k_m")
print("V2 LoRA adapter saved to 'lora_adapter_v2/' and GGUF exported to 'model_v2_q4_km/'!")
print("Next step locally: python scripts/fix_nemotron_gguf.py --input <gguf_file> --output models/NVIDIA-Nemotron-3-Nano-4B-V2-Dense-Q4_K_M.gguf")
