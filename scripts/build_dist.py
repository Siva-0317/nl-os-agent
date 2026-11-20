"""
scripts/build_dist.py - Build and package standalone NL-OS-Agent distribution.
"""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def _remove_readonly(func, path, excinfo):
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass

def build():
    # Remove bin and models from dist/nlagent first so PyInstaller's rmtree doesn't fail on read-only DLLs
    dist_bin = ROOT / "dist" / "nlagent" / "bin"
    dist_models = ROOT / "dist" / "nlagent" / "models"
    if dist_bin.exists():
        shutil.rmtree(dist_bin, onerror=_remove_readonly)
    if dist_models.exists():
        shutil.rmtree(dist_models, onerror=_remove_readonly)

    print("[1/3] Running PyInstaller...")
    pyi = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
    spec = ROOT / "installer" / "nlagent.spec"
    res = subprocess.run([str(pyi), "--noconfirm", "--clean", str(spec)], cwd=str(ROOT))
    if res.returncode != 0:
        print("[ERROR] PyInstaller failed!")
        sys.exit(res.returncode)

    print("[2/3] Bundling bin/ into dist/nlagent/bin...")
    src_bin = ROOT / "bin"
    if dist_bin.exists():
        shutil.rmtree(dist_bin, onerror=_remove_readonly)
    shutil.copytree(src_bin, dist_bin)

    print("[3/3] Setting up models in dist/nlagent/models...")
    dist_models = ROOT / "dist" / "nlagent" / "models"
    dist_models.mkdir(exist_ok=True)
    manifest = ROOT / "models" / "manifest.json"
    if manifest.exists():
        shutil.copy2(manifest, dist_models / "manifest.json")
    
    model_src = ROOT / "models" / "NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf"
    model_dst = dist_models / "NVIDIA-Nemotron-3-Nano-4B-Dense-Q4_K_M.gguf"
    if model_src.exists() and not model_dst.exists():
        try:
            import os
            os.link(str(model_src), str(model_dst))
            print(f"  Hardlinked model: {model_dst.name}")
        except Exception:
            print("  Copying model (hardlink not supported)...")
            shutil.copy2(model_src, model_dst)

    print("\n[SUCCESS] Standalone distribution ready at dist/nlagent!")

if __name__ == "__main__":
    build()
