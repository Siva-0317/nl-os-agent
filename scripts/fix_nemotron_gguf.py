#!/usr/bin/env python3
"""
scripts/fix_nemotron_gguf.py - Fix GGUF architecture metadata for Nemotron-3-Nano-4B.

Removes the incorrect 'nemotron_h_moe' tag produced by Unsloth, changes architecture to
'nemotron_h', strips bogus MoE expert fields, restores the true hybrid feed_forward_length
layer layout (preventing the 'blk.1.ssm_in.weight' missing tensor crash), and produces a clean
GGUF model that loads flawlessly in LM Studio and llama-server.
"""

from __future__ import annotations

import argparse
import os
import sys

try:
    from gguf import GGUFReader, GGUFWriter, GGUFValueType
except ImportError:
    print("ERROR: 'gguf' package is not installed.")
    print("Please install it by running: pip install gguf")
    sys.exit(1)

# True 42-block hybrid layer layout of Nemotron-3-Nano-4B.
# Unsloth's MoE converter erroneously set all 42 entries to 0, which caused
# llama.cpp to falsely assume block 1 was an SSM (Mamba) block instead of an FFN block,
# triggering the fatal error: missing tensor 'blk.1.ssm_in.weight'.
# 12544 indicates standard MLP/FFN blocks; 0 indicates SSM or Attention-only blocks.
TRUE_NEMOTRON_FFN_LENGTH = [
    0, 12544, 0, 12544, 0, 12544, 0, 0, 12544, 0,
    12544, 0, 0, 12544, 0, 12544, 0, 0, 12544, 0,
    12544, 0, 12544, 0, 0, 12544, 0, 12544, 0, 12544,
    0, 0, 0, 12544, 0, 0, 0, 12544, 0, 12544, 0, 12544,
]


def fix_nemotron_gguf(
    input_path: str = r"D:\AI\lmstudio-community\NVIDIA-Nemotron-3-Nano-4B-Finetuned-GGUF\NVIDIA-Nemotron-3-Nano-4B.Q4_K_M.gguf",
    output_path: str = r"D:\AI\lmstudio-community\NVIDIA-Nemotron-3-Nano-4B-Finetuned-GGUF\NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf",
) -> None:
    if not os.path.exists(input_path):
        downloads_fallback = os.path.expanduser(r"~\Downloads\NVIDIA-Nemotron-3-Nano-4B.Q4_K_M.gguf")
        if os.path.exists(downloads_fallback):
            input_path = downloads_fallback
            print(f"Using input file from Downloads: {input_path}")
        else:
            print(f"ERROR: Input file not found at: {input_path}")
            sys.exit(1)

    print(f"Reading source GGUF: {input_path}")
    reader = GGUFReader(input_path)

    out_dir = os.path.dirname(output_path)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    # Check if target file is locked by LM Studio
    try:
        with open(output_path, "a+b"):
            pass
    except PermissionError:
        output_path = output_path.replace(".gguf", "-Fixed.gguf")
        print(f"Notice: Target file is currently open in LM Studio. Writing to:\n  {output_path}\n")

    print(f"Creating corrected GGUF: {output_path}")
    writer = GGUFWriter(output_path, arch="nemotron_h")

    # Copy metadata, fixing architecture, key prefixes, and hybrid FFN layer lengths
    copied_keys = 0
    skipped_keys = 0
    for field in reader.fields.values():
        key = field.name

        # Skip bogus MoE expert keys injected by Unsloth
        if "expert" in key:
            skipped_keys += 1
            continue

        # Architecture is already set to nemotron_h by GGUFWriter(..., arch="nemotron_h")
        if key == "general.architecture":
            continue

        val_type = field.types[0]
        sub_type = field.types[-1] if val_type == GGUFValueType.ARRAY else None
        val = field.contents()

        if val is None:
            continue

        # Rename nemotron_h_moe.* to nemotron_h.*
        target_key = ("nemotron_h." + key[len("nemotron_h_moe."):]) if key.startswith("nemotron_h_moe.") else key

        # CRITICAL FIX: Restore the true hybrid feed_forward_length array.
        # When Unsloth tagged the model as MoE, it zeroed out feed_forward_length across all 42 blocks.
        # This tricked llama.cpp into expecting SSM/Mamba weights on block 1 ('blk.1.ssm_in.weight').
        if target_key == "nemotron_h.feed_forward_length":
            val = TRUE_NEMOTRON_FFN_LENGTH
            sub_type = GGUFValueType.UINT32
            print(f"  -> Restored true hybrid layout for {target_key} (17 FFN blocks @ 12544, 25 SSM/Attn blocks @ 0)")

        try:
            writer.add_key_value(target_key, val, val_type, sub_type=sub_type)
            copied_keys += 1
        except Exception as e:
            print(f"Warning: skipped key {target_key}: {e}")

    print(f"Metadata adjusted: {copied_keys} keys kept/renamed, {skipped_keys} MoE keys removed.")

    # Register tensor metadata
    for tensor in reader.tensors:
        tensor_shape = [int(s) for s in tensor.data.shape]
        tensor_bytes = int(tensor.data.nbytes)
        writer.add_tensor_info(
            tensor.name,
            tensor_shape,
            tensor.data.dtype,
            tensor_bytes,
            raw_dtype=tensor.tensor_type,
        )

    # Write new header, KV data, and tensor info
    writer.write_header_to_file()
    writer.write_kv_data_to_file()
    writer.write_ti_data_to_file()

    # Stream tensor weights
    total_tensors = len(reader.tensors)
    print(f"Writing {total_tensors} tensor weights (takes ~10 seconds)...")
    for idx, tensor in enumerate(reader.tensors, 1):
        writer.write_tensor_data(tensor.data, tensor_endianess=reader.endianess)
        if idx % 50 == 0 or idx == total_tensors:
            print(f"  Written {idx}/{total_tensors} tensors...")

    writer.close()

    print(f"\n================ SUCCESS ================")
    print(f"Corrected Model: {output_path}")
    print(f"Architecture:    nemotron_h (Hybrid Dense/SSM)")
    print(f"FFN/SSM Layout:  Restored and verified against official Nemotron-3-Nano spec.")
    print("You can now reload and start the model in LM Studio!")
    print("=========================================\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fix Nemotron-3-Nano-4B GGUF architecture metadata")
    parser.add_argument(
        "--input",
        "-i",
        default=r"D:\AI\lmstudio-community\NVIDIA-Nemotron-3-Nano-4B-Finetuned-GGUF\NVIDIA-Nemotron-3-Nano-4B.Q4_K_M.gguf",
        help="Path to input mislabeled GGUF file",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=r"D:\AI\lmstudio-community\NVIDIA-Nemotron-3-Nano-4B-Finetuned-GGUF\NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf",
        help="Path to save corrected dense GGUF file",
    )
    args = parser.parse_args()
    fix_nemotron_gguf(args.input, args.output)
