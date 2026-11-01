"""effects.py - static effect analysis: resolve what a command WOULD touch, without running it."""
import glob, os, shlex
from dataclasses import dataclass, field

PROTECTED = ("/", "/etc", "/usr", "/bin", "/boot", "/var", "/lib", "/sbin",
             "C:\\Windows", "/c/Windows")

@dataclass
class Effect:
    deletes: list = field(default_factory=list)
    overwrites: list = field(default_factory=list)
    creates: list = field(default_factory=list)
    appends: list = field(default_factory=list)
    perm_changes: list = field(default_factory=list)
    signals: list = field(default_factory=list)      # process kills
    external: bool = False      # network / unknown side effects we cannot simulate
    opaque: bool = False        # eval, nested shell, base64|bash ... cannot analyse
    protected_hit: bool = False
    bytes_affected: int = 0
    notes: list = field(default_factory=list)

def _expand(arg, cwd):
    p = arg if os.path.isabs(arg) else os.path.join(cwd, arg)
    hits = glob.glob(p) if any(c in arg for c in "*?[") else [p]
    return [h for h in hits if os.path.lexists(h)] or ([] if any(c in arg for c in "*?[") else [p])

def _walk(path):
    if os.path.isdir(path) and not os.path.islink(path):
        for root, dirs, files in os.walk(path):
            for n in files + dirs:
                yield os.path.join(root, n)
    yield path

def _size(path):
    try:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        return 0

def analyse(command: str, cwd: str) -> Effect:
    e = Effect()
    if any(t in command for t in ("$(", "`", "eval ", "base64", "| bash", "| sh", "bash -c", "sh -c", "powershell -enc")):
        e.opaque = True
        e.notes.append("command uses indirection (eval / substitution / piped shell) - cannot be analysed statically")
        return e
    # split simple pipelines / lists; analyse each segment
    for seg in [s for s in command.replace("&&", ";").replace("||", ";").split(";") if s.strip()]:
        try:
            parts = shlex.split(seg.strip())
        except ValueError:
            e.opaque = True; continue
        if not parts:
            continue
        cmd, args = os.path.basename(parts[0]), parts[1:]
        targets = [a for a in args if not a.startswith("-")]
        # redirection truncation:  > file   (single >)
        if ">>" in seg:
            tgt = seg.split(">>")[-1].strip().split()[0] if seg.split(">>")[-1].strip() else ""
            if tgt:
                e.appends += _expand(tgt.strip("'\""), cwd)
        elif ">" in seg:
            tgt = seg.split(">")[-1].strip().split()[0] if seg.split(">")[-1].strip() else ""
            if tgt:
                for t in _expand(tgt.strip("'\""), cwd):
                    (e.overwrites if os.path.exists(t) else e.creates).append(t)
        if cmd in ("rm", "rmdir", "shred", "unlink"):
            for a in targets:
                for t in _expand(a, cwd):
                    for w in _walk(t):
                        e.deletes.append(w); e.bytes_affected += _size(w)
        elif cmd == "find" and "-delete" in parts:
            root = next((a for a in args if not a.startswith("-")), ".")
            preview = [c for c in parts if c != "-delete"]
            e.notes.append("find -delete: preview via `%s`" % " ".join(preview))
            import subprocess
            r = subprocess.run(preview, cwd=cwd, capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                p = line if os.path.isabs(line) else os.path.join(cwd, line)
                e.deletes.append(p); e.bytes_affected += _size(p)
        elif cmd in ("mv", "cp") and len(targets) >= 2:
            *srcs, dst = targets
            dst_p = _expand(dst, cwd)[0]
            for s in srcs:
                for sp in _expand(s, cwd):
                    final = os.path.join(dst_p, os.path.basename(sp)) if os.path.isdir(dst_p) else dst_p
                    if os.path.exists(final):
                        e.overwrites.append(final)
                    else:
                        e.creates.append(final)
                    if cmd == "mv":
                        e.deletes.append(sp)
        elif cmd in ("chmod", "chown", "chgrp"):
            for a in targets[1:]:
                e.perm_changes += _expand(a, cwd)
        elif cmd in ("kill", "pkill", "killall", "taskkill"):
            e.signals += targets
        elif cmd in ("mkdir", "touch"):
            for a in targets:
                e.creates += _expand(a, cwd)
        elif cmd in ("curl", "wget", "scp", "rsync", "ssh", "git", "pip", "npm", "apt", "apt-get", "docker"):
            e.external = True
        elif cmd in ("dd", "mkfs", "fdisk", "parted", "format"):
            e.opaque = True; e.notes.append("raw-device command")
        elif cmd == "python" or cmd == "python3" or cmd == "perl" or cmd == "node":
            if "-c" in parts or "-e" in parts:
                e.opaque = True; e.notes.append("inline interpreter code")
    allp = e.deletes + e.overwrites + e.perm_changes
    for p in allp:
        norm_p = os.path.normpath(os.path.abspath(p)).replace("\\", "/")
        if norm_p in ("/", "C:/", "D:/") or (len(norm_p) == 3 and norm_p.endswith(":/")):
            e.protected_hit = True
            break
        for pr in PROTECTED:
            if pr in ("/",):
                continue
            norm_pr = os.path.normpath(pr).replace("\\", "/").rstrip("/")
            if norm_p == norm_pr or norm_p.startswith(norm_pr + "/"):
                e.protected_hit = True
                break
            if len(norm_p) >= 2 and norm_p[1] == ":":
                without_drive = norm_p[2:]
                if without_drive == norm_pr or without_drive.startswith(norm_pr + "/"):
                    e.protected_hit = True
                    break
        if e.protected_hit:
            break
    return e

def tier_from_effect(e: Effect) -> str:
    if e.protected_hit:
        return "privileged"
    if e.opaque:
        return "ambiguous"
    if e.deletes or e.overwrites or e.perm_changes or e.signals:
        return "destructive"
    if e.external:
        return "ambiguous"
    if e.creates or e.appends:
        return "state_modifying"
    return "read_only"

def preview(e: Effect) -> str:
    out = []
    if e.deletes:    out.append(f"DELETE {len(e.deletes)} item(s) (~{e.bytes_affected/1e6:.1f} MB), e.g. {e.deletes[:3]}")
    if e.overwrites: out.append(f"OVERWRITE {len(e.overwrites)} item(s): {e.overwrites[:3]}")
    if e.appends:    out.append(f"APPEND to {len(e.appends)} item(s): {e.appends[:3]}")
    if e.creates:    out.append(f"CREATE {len(e.creates)} item(s): {e.creates[:3]}")
    if e.perm_changes: out.append(f"CHANGE PERMISSIONS on {len(e.perm_changes)} item(s)")
    if e.signals:    out.append(f"SIGNAL processes: {e.signals}")
    if e.external:   out.append("EXTERNAL side effects (network/package manager) - cannot be simulated")
    if e.opaque:     out.append("OPAQUE: " + "; ".join(e.notes))
    if e.protected_hit: out.append("TOUCHES PROTECTED SYSTEM PATH")
    return "\n".join(out) or "No filesystem effect detected."

