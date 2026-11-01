# -*- coding: utf-8 -*-
"""
scripts/colab_unsloth_finetuning.py - Unsloth Colab Notebook Script for Task E5.

Run this script or notebook in Google Colab (Free T4 or A100 GPU):
1. Install Unsloth and pinned dependencies.
2. Load base model in 4-bit (fits within 6-8 GB VRAM).
3. Attach LoRA PEFT adapters.
4. Train Base vs Context-Distilled adapter on datasets/finetune_train.jsonl.
5. Export adapter weights and convert to GGUF (Q4_K_M) for local llama-server.
"""

# Cell 1: Install Unsloth
"""
!pip install --no-deps "xformers<0.0.29" "trl<0.9.0" peft accelerate bitsandbytes
!pip install "unsloth[colab-new] @ git+https://github.com/unslothai/unsloth.git"
"""

import torch
from unsloth import FastLanguageModel
from datasets import load_dataset
from trl import SFTTrainer
from transformers import TrainingArguments

# Cell 2: Model Configuration
max_seq_length = 2048
dtype = None  # Auto-detection: Float16 for Tesla T4, Bfloat16 for Ampere+
load_in_4bit = True  # 4-bit quantization reduces VRAM under 4 GB

# Recommended base models for 4 GB target:
# 1. "unsloth/Qwen2.5-Coder-3B-Instruct-bnb-4bit"
# 2. "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"
# 3. "nvidia/Nemotron-Mini-4B-Instruct" (or community GGUF/HF mirror)
model_name = "unsloth/Qwen2.5-Coder-3B-Instruct-bnb-4bit"

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name=model_name,
    max_seq_length=max_seq_length,
    dtype=dtype,
    load_in_4bit=load_in_4bit,
)

# Cell 3: Add LoRA Adapters
model = FastLanguageModel.get_peft_model(
    model,
    r=16,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    lora_alpha=16,
    lora_dropout=0,
    bias="none",
    use_gradient_checkpointing="unsloth",
    random_state=3407,
)

# Cell 4: Load Dataset
# Upload finetune_train.jsonl to Colab workspace
dataset = load_dataset("json", data_files={"train": "finetune_train.jsonl", "validation": "finetune_val.jsonl"})

def formatting_prompts_func(examples):
    convs = examples["messages"]
    texts = [tokenizer.apply_chat_template(conv, tokenize=False, add_generation_prompt=False) for conv in convs]
    return {"text": texts}

formatted_dataset = dataset.map(formatting_prompts_func, batched=True)

# Cell 5: Train with SFTTrainer
trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=formatted_dataset["train"],
    eval_dataset=formatted_dataset["validation"],
    dataset_text_field="text",
    max_seq_length=max_seq_length,
    dataset_num_proc=2,
    packing=False,
    args=TrainingArguments(
        per_device_train_batch_size=4,
        gradient_accumulation_steps=4,
        warmup_steps=20,
        max_steps=250,  # Use ~250 for quick validation, ~1200 for full 8k epoch
        learning_rate=2e-4,
        fp16=not torch.cuda.is_bf16_supported(),
        bf16=torch.cuda.is_bf16_supported(),
        logging_steps=10,
        optim="adamw_8bit",
        weight_decay=0.01,
        lr_scheduler_type="linear",
        seed=3407,
        output_dir="outputs",
    ),
)

trainer_stats = trainer.train()

# Cell 6: Save LoRA Adapter & GGUF
# Save to Google Drive or local Colab storage
model.save_pretrained("lora_adapter")
tokenizer.save_pretrained("lora_adapter")

# Export directly to Q4_K_M GGUF for local llama-server execution
model.save_pretrained_gguf("model_q4_km", tokenizer, quantization_method="q4_k_m")
print("Model training and GGUF export complete!")
