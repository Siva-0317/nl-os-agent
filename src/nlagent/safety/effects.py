"""effects.py - static effect analysis: resolve what a command WOULD touch, without running it."""

from __future__ import annotations

import glob
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Any, Iterator

from nlagent.safety.cmd_parser import CmdInvocation, parse_cmd_line
from nlagent.safety.ps_parser import ParsedPSCommand, PSParseResult, parse_powershell

PROTECTED = (
    "/",
    "/etc",
    "/usr",
    "/bin",
    "/boot",
    "/var",
    "/lib",
    "/sbin",
    "C:\\Windows",
    "/c/Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
)

REGISTRY_PROTECTED = (
    "hklm",
    "hkey_local_machine",
    "hkcr",
    "hkey_classes_root",
    "hku",
    "hkey_users",
)


@dataclass
class Effect:
    deletes: list[str] = field(default_factory=list)
    overwrites: list[str] = field(default_factory=list)
    creates: list[str] = field(default_factory=list)
    appends: list[str] = field(default_factory=list)
    perm_changes: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)  # process kills
    registry_writes: list[str] = field(default_factory=list)
    external: bool = False  # network / unknown side effects we cannot simulate
    opaque: bool = False  # eval, nested shell, base64|bash ... cannot analyse
    protected_hit: bool = False
    hard_block: bool = False  # raw device format, diskpart
    bytes_affected: int = 0
    notes: list[str] = field(default_factory=list)


def _expand(arg: str, cwd: str) -> list[str]:
    """Expand wildcards and relative paths in the context of cwd."""
    if not arg:
        return []
    p = arg if os.path.isabs(arg) else os.path.join(cwd, arg)
    has_glob = any(c in arg for c in "*?[")
    hits = glob.glob(p) if has_glob else [p]
    return [h for h in hits if os.path.lexists(h)] or ([] if has_glob else [p])


def _get_ps_param(params: dict[str, Any], name: str, default: Any = None) -> Any:
    """Case-insensitive parameter lookup for parsed PowerShell commands."""
    if name in params:
        return params[name]
    name_low = name.lower()
    for k, v in params.items():
        if k.lower() == name_low:
            return v
    return default


def _is_protected_target(p: str) -> bool:
    """Fast check whether a path target touches a protected system directory."""
    try:
        norm_p = os.path.normpath(os.path.abspath(p)).replace("\\", "/")
    except Exception:
        norm_p = p.replace("\\", "/")
    if norm_p in ("/", "C:/", "D:/") or (len(norm_p) == 3 and norm_p.endswith(":/")):
        return True
    for pr in PROTECTED:
        if pr in ("/",):
            continue
        norm_pr = os.path.normpath(pr).replace("\\", "/").rstrip("/")
        if norm_p == norm_pr or norm_p.startswith(norm_pr + "/"):
            return True
        if len(norm_p) >= 2 and norm_p[1] == ":":
            without_drive = norm_p[2:]
            if without_drive == norm_pr or without_drive.startswith(norm_pr + "/"):
                return True
    return False


def _walk(path: str, max_items: int = 500) -> Iterator[str]:
    """Recursively yield files and directories contained within path, plus path itself."""
    yield path
    if _is_protected_target(path):
        return
    if os.path.isdir(path) and not os.path.islink(path):
        count = 0
        try:
            for root, dirs, files in os.walk(path):
                for n in files + dirs:
                    yield os.path.join(root, n)
                    count += 1
                    if count >= max_items:
                        return
        except OSError:
            pass


def _size(path: str) -> int:
    """Return byte size of file, or 0 if directory/missing."""
    try:
        return os.path.getsize(path) if os.path.isfile(path) else 0
    except OSError:
        return 0


def _is_registry_path(p: str) -> bool:
    """Check if a path string references a Windows Registry hive."""
    lp = p.lower().strip("'\"")
    return any(
        lp.startswith(prefix)
        for prefix in (
            "hklm:",
            "hkcu:",
            "hkcr:",
            "hku:",
            "hkcc:",
            "hklm\\",
            "hkcu\\",
            "hkcr\\",
            "hku\\",
            "hkey_local_machine",
            "hkey_current_user",
            "hkey_classes_root",
            "hkey_users",
            "registry::",
            "microsoft.powershell.core\\registry::",
        )
    )


def _is_protected_registry(p: str) -> bool:
    """Check if registry target touches protected machine-level hives (HKLM, HKCR, HKU)."""
    lp = p.lower().replace(":", "\\")
    return any(r in lp for r in REGISTRY_PROTECTED)


def analyse(
    command: str,
    cwd: str,
    enable_protected_paths: bool = True,
    enable_opaque_escalation: bool = True,
    enable_symlink_resolution: bool = True,
) -> Effect:
    """
    Statically analyse what a command line WOULD touch, without executing it.
    Supports POSIX shells, PowerShell cmdlets/aliases, and cmd.exe native verbs.

    Configurable flags for ablation analysis:
      - enable_protected_paths: enforce system directory and hive protection.
      - enable_opaque_escalation: flag indirection/encoded commands as ambiguous.
      - enable_symlink_resolution: resolve realpath targets to detect symlink attacks.
    """
    e = Effect()
    clean_cmd = command.strip()
    lower_cmd = clean_cmd.lower()

    # 1. Hard Block Checks (format, diskpart, mkfs, fdisk, parted, dd)
    if re.search(r"\b(format|diskpart|mkfs|fdisk|parted|dd)(\.exe)?\b", lower_cmd):
        e.hard_block = True
        if enable_opaque_escalation:
            e.opaque = True
        if enable_protected_paths:
            e.protected_hit = True
        e.notes.append("hard-blocked raw-disk command (format/diskpart)")
        return e

    # 2. Indirection / Opaque execution checks
    if enable_opaque_escalation:
        if any(
            t in command
            for t in (
                "$(",
                "`",
                "eval ",
                "base64",
                "| bash",
                "| sh",
                "| powershell",
                "| pwsh",
                "| cmd",
                "bash -c",
                "sh -c",
            )
        ):
            e.opaque = True
            e.notes.append("command uses indirection (eval / substitution / piped shell) - cannot be analysed statically")
            return e

        # PowerShell encoded commands (-EncodedCommand, -enc, etc.)
        if any(f in lower_cmd for f in ("-encodedcommand", "-encoded", "-enc ", " -e ")):
            e.opaque = True
            e.notes.append("opaque execution via -EncodedCommand")
            return e

        # Nested PowerShell execution via -Command / -c
        if re.search(r"\b(powershell|pwsh)(\.exe)?\s+.*(-[a-zA-Z]*c[a-zA-Z]*|-Command)\b", clean_cmd, re.IGNORECASE):
            e.opaque = True
            e.notes.append("opaque execution via nested PowerShell -Command")
            return e

        # Dynamic PowerShell call operator on variable or subexpression: & $var or & (...)
        if re.search(r"&\s*(\$|\(|\"[^\"]+\"\s*\+)", clean_cmd):
            e.opaque = True
            e.notes.append("opaque execution via dynamic PowerShell invocation operator")
            return e

        # Invoke-Expression / iex
        if re.search(r"\b(invoke-expression|iex)\b", lower_cmd):
            e.opaque = True
            e.notes.append("opaque execution via Invoke-Expression / iex")
            return e

    # 3. Privilege Escalation Checks (Start-Process -Verb RunAs / runas)
    if enable_protected_paths:
        if re.search(r"\bstart-process\b", lower_cmd) and re.search(r"-[vV][eE][rR][bB]\s+[\"']?[rR][uU][nN][aA][sS][\"']?", clean_cmd):
            e.protected_hit = True
            e.notes.append("privilege escalation via Start-Process -Verb RunAs")

        if re.search(r"\brunas(\.exe)?\s+/[uU][sS][eE][rR]:", clean_cmd):
            e.protected_hit = True
            e.notes.append("privilege escalation via runas")

    # 4. Parse via cmd_parser and ps_parser
    cmd_invocations = parse_cmd_line(clean_cmd)
    ps_result = parse_powershell(clean_cmd)

    if enable_opaque_escalation and ps_result.has_encoded_command:
        e.opaque = True
        e.notes.append("opaque execution via -EncodedCommand")
    if enable_opaque_escalation and ps_result.has_invoke_expression:
        e.opaque = True
        e.notes.append("opaque execution via Invoke-Expression")
    if enable_protected_paths and ps_result.has_runas:
        e.protected_hit = True
        e.notes.append("privilege escalation via Start-Process -Verb RunAs")
    if enable_opaque_escalation and ps_result.has_subexpression:
        e.opaque = True
        e.notes.append("command contains subexpressions")

    # Check Redirections from cmd parser and raw segments
    for inv in cmd_invocations:
        for op, target in inv.redirections:
            target_clean = target.strip("'\"")
            if not target_clean:
                continue
            if op == ">>":
                e.appends += _expand(target_clean, cwd)
            elif op == ">":
                for t in _expand(target_clean, cwd):
                    (e.overwrites if os.path.exists(t) else e.creates).append(t)

    # Redirection fallback on raw command segments
    for seg in [s for s in clean_cmd.replace("&&", ";").replace("||", ";").replace("&", ";").split(";") if s.strip()]:
        if ">>" in seg:
            tgt = seg.split(">>")[-1].strip().split()[0] if seg.split(">>")[-1].strip() else ""
            if tgt:
                e.appends += _expand(tgt.strip("'\""), cwd)
        elif ">" in seg:
            tgt = seg.split(">")[-1].strip().split()[0] if seg.split(">")[-1].strip() else ""
            if tgt:
                for t in _expand(tgt.strip("'\""), cwd):
                    (e.overwrites if os.path.exists(t) else e.creates).append(t)

    # 5. Process PowerShell AST Cmdlets
    for ps_cmd in ps_result.commands:
        v = ps_cmd.normalized_name.lower()
        params = ps_cmd.parameters
        pos = ps_cmd.positional

        if enable_opaque_escalation and (v.startswith("$") or v.startswith("&")):
            e.opaque = True
            e.notes.append(f"dynamic variable or operator invocation ({v})")

        # Extract targets: -Path, -LiteralPath, or positional args (case-insensitive keys)
        ps_targets: list[str] = []
        raw_path = _get_ps_param(params, "Path")
        if raw_path is not None and raw_path is not True:
            ps_targets.extend(raw_path if isinstance(raw_path, list) else [str(raw_path)])
        raw_lit_path = _get_ps_param(params, "LiteralPath")
        if raw_lit_path is not None and raw_lit_path is not True:
            ps_targets.extend(raw_lit_path if isinstance(raw_lit_path, list) else [str(raw_lit_path)])
        ps_targets.extend(pos)

        # Remove-Item (aliases: ri, rm, rmdir, del, erase, rd)
        if v in ("remove-item", "remove-itemproperty"):
            is_recurse = bool(_get_ps_param(params, "Recurse", False) or _get_ps_param(params, "r", False))
            for t in ps_targets:
                if _is_registry_path(t):
                    e.registry_writes.append(t)
                    if enable_protected_paths and _is_protected_registry(t):
                        e.protected_hit = True
                    e.deletes.append(f"registry:{t}")
                else:
                    for exp in _expand(t, cwd):
                        if os.path.isdir(exp) and (is_recurse or v in ("rmdir", "rd")):
                            for w in _walk(exp):
                                e.deletes.append(w)
                                e.bytes_affected += _size(w)
                        else:
                            e.deletes.append(exp)
                            e.bytes_affected += _size(exp)

        # New-Item (aliases: ni, md)
        elif v == "new-item":
            item_type = str(_get_ps_param(params, "ItemType", "") or "").lower()
            is_force = bool(_get_ps_param(params, "Force", False))
            name_arg = _get_ps_param(params, "Name")
            if raw_path and name_arg and not isinstance(name_arg, bool):
                ni_targets = [os.path.join(str(raw_path), str(name_arg))]
            elif name_arg and not isinstance(name_arg, bool) and not ps_targets:
                ni_targets = [str(name_arg)]
            else:
                ni_targets = ps_targets
            for t in ni_targets:
                if _is_registry_path(t):
                    e.registry_writes.append(t)
                    if enable_protected_paths and _is_protected_registry(t):
                        e.protected_hit = True
                    e.creates.append(f"registry:{t}")
                else:
                    for p in _expand(t, cwd):
                        if os.path.exists(p) and not os.path.isdir(p) and is_force and item_type != "directory":
                            e.overwrites.append(p)
                        else:
                            e.creates.append(p)

        # Rename-Item (aliases: rni, ren)
        elif v == "rename-item":
            new_name = _get_ps_param(params, "NewName", "")
            if not new_name:
                new_name = pos[1] if (not raw_path and not raw_lit_path and len(pos) >= 2) else (pos[0] if (raw_path or raw_lit_path) and pos else "")
            src_list = ps_targets[:-1] if (not _get_ps_param(params, "NewName") and len(ps_targets) >= 2) else ps_targets
            if new_name:
                for s in src_list:
                    for sp in _expand(s, cwd):
                        e.deletes.append(sp)
                        parent_dir = os.path.dirname(sp)
                        final = os.path.join(parent_dir, os.path.basename(str(new_name))) if parent_dir else str(new_name)
                        (e.overwrites if os.path.exists(final) else e.creates).append(final)

        # Move-Item (aliases: mi, mv, move)
        elif v == "move-item":
            dst_param = _get_ps_param(params, "Destination", "")
            dst = dst_param or (pos[1] if len(pos) >= 2 else "")
            srcs = ps_targets[:-1] if (not dst_param and len(ps_targets) >= 2) else ps_targets
            if dst:
                dst_exp = _expand(str(dst), cwd)
                dst_p = dst_exp[0] if dst_exp else (str(dst) if os.path.isabs(str(dst)) else os.path.join(cwd, str(dst)))
                for s in srcs:
                    for sp in _expand(s, cwd):
                        e.deletes.append(sp)
                        final = os.path.join(dst_p, os.path.basename(sp)) if os.path.isdir(dst_p) else dst_p
                        (e.overwrites if os.path.exists(final) else e.creates).append(final)

        # Copy-Item (aliases: cpi, cp, copy, ci)
        elif v == "copy-item":
            dst_param = _get_ps_param(params, "Destination", "")
            dst = dst_param or (pos[1] if len(pos) >= 2 else "")
            srcs = ps_targets[:-1] if (not dst_param and len(ps_targets) >= 2) else ps_targets
            if dst:
                dst_exp = _expand(str(dst), cwd)
                dst_p = dst_exp[0] if dst_exp else (str(dst) if os.path.isabs(str(dst)) else os.path.join(cwd, str(dst)))
                for s in srcs:
                    for sp in _expand(s, cwd):
                        final = os.path.join(dst_p, os.path.basename(sp)) if os.path.isdir(dst_p) else dst_p
                        (e.overwrites if os.path.exists(final) else e.creates).append(final)

        # Set-Content (alias: sc)
        elif v == "set-content":
            for t in ps_targets:
                if _is_registry_path(t):
                    e.registry_writes.append(t)
                    if _is_protected_registry(t):
                        e.protected_hit = True
                    e.creates.append(f"registry:{t}")
                else:
                    for p in _expand(t, cwd):
                        (e.overwrites if os.path.exists(p) else e.creates).append(p)

        # Add-Content (alias: ac)
        elif v == "add-content":
            for t in ps_targets:
                for p in _expand(t, cwd):
                    (e.appends if os.path.exists(p) else e.creates).append(p)

        # Out-File
        elif v == "out-file":
            is_append = bool(_get_ps_param(params, "Append", False))
            target_file = _get_ps_param(params, "FilePath", "") or (pos[0] if pos else "")
            if target_file:
                for p in _expand(str(target_file), cwd):
                    if is_append:
                        e.appends.append(p)
                    else:
                        (e.overwrites if os.path.exists(p) else e.creates).append(p)

        # Clear-Content (alias: clc)
        elif v in ("clear-content", "clear-itemproperty"):
            for t in ps_targets:
                if _is_registry_path(t):
                    e.registry_writes.append(t)
                    if _is_protected_registry(t):
                        e.protected_hit = True
                    e.overwrites.append(f"registry:{t}")
                else:
                    for p in _expand(t, cwd):
                        e.overwrites.append(p)

        # Registry property cmdlets: Set-ItemProperty, New-ItemProperty
        elif v in ("set-itemproperty", "new-itemproperty"):
            for t in ps_targets:
                e.registry_writes.append(t)
                if _is_protected_registry(t):
                    e.protected_hit = True
                e.creates.append(f"registry:{t}")

        # Stop-Process (aliases: spps, kill)
        elif v == "stop-process":
            targets = []
            ps_name = _get_ps_param(params, "Name")
            if ps_name is not None and ps_name is not True:
                targets.append(str(ps_name))
            ps_id = _get_ps_param(params, "Id")
            if ps_id is not None and ps_id is not True:
                targets.append(str(ps_id))
            targets.extend(pos)
            e.signals += targets

        # Start-Process with -Verb RunAs
        elif v == "start-process":
            verb = str(_get_ps_param(params, "Verb", "") or "")
            if verb.lower() == "runas":
                e.protected_hit = True
                e.notes.append("privilege escalation via Start-Process -Verb RunAs")

    # 6. Process cmd.exe Invocations
    for inv in cmd_invocations:
        v = inv.normalized_verb
        sw = inv.switches
        args = inv.args

        # del / erase
        if v == "del":
            is_recurse = inv.is_recursive
            for a in args:
                if _is_registry_path(a):
                    e.registry_writes.append(a)
                    if _is_protected_registry(a):
                        e.protected_hit = True
                    e.deletes.append(f"registry:{a}")
                else:
                    for h in _expand(a, cwd):
                        if os.path.isdir(h) and is_recurse:
                            for w in _walk(h):
                                e.deletes.append(w)
                                e.bytes_affected += _size(w)
                        else:
                            e.deletes.append(h)
                            e.bytes_affected += _size(h)

        # rd / rmdir
        elif v == "rd":
            for a in args:
                for h in _expand(a, cwd):
                    for w in _walk(h):
                        e.deletes.append(w)
                        e.bytes_affected += _size(w)

        # robocopy (/MIR, /PURGE, or standard copy)
        elif v == "robocopy":
            if len(args) >= 2:
                src, dst = args[0], args[1]
                dst_exp = _expand(dst, cwd)
                dst_p = dst_exp[0] if dst_exp else (dst if os.path.isabs(dst) else os.path.join(cwd, dst))
                if inv.is_mirror:
                    e.notes.append("robocopy /MIR: mirror directory tree (purges non-matching destination files)")
                    if os.path.isdir(dst_p):
                        for w in _walk(dst_p):
                            e.overwrites.append(w)
                            e.deletes.append(w)
                            e.bytes_affected += _size(w)
                    else:
                        e.overwrites.append(dst_p)
                        e.deletes.append(dst_p)
                else:
                    (e.overwrites if os.path.exists(dst_p) else e.creates).append(dst_p)

        # move
        elif v == "move" and len(args) >= 2:
            *srcs, dst = args
            dst_exp = _expand(dst, cwd)
            dst_p = dst_exp[0] if dst_exp else (dst if os.path.isabs(dst) else os.path.join(cwd, dst))
            for s in srcs:
                for sp in _expand(s, cwd):
                    e.deletes.append(sp)
                    final = os.path.join(dst_p, os.path.basename(sp)) if os.path.isdir(dst_p) else dst_p
                    (e.overwrites if os.path.exists(final) else e.creates).append(final)

        # copy / xcopy
        elif v == "copy" and len(args) >= 2:
            *srcs, dst = args
            dst_exp = _expand(dst, cwd)
            dst_p = dst_exp[0] if dst_exp else (dst if os.path.isabs(dst) else os.path.join(cwd, dst))
            for s in srcs:
                for sp in _expand(s, cwd):
                    final = os.path.join(dst_p, os.path.basename(sp)) if os.path.isdir(dst_p) else dst_p
                    (e.overwrites if os.path.exists(final) else e.creates).append(final)

        # reg
        elif v == "reg":
            sub = inv.subverb
            reg_key = args[0] if args else ""
            if reg_key:
                e.registry_writes.append(reg_key)
                if _is_protected_registry(reg_key):
                    e.protected_hit = True
                if sub == "delete":
                    e.deletes.append(f"registry:{reg_key}")
                else:
                    e.creates.append(f"registry:{reg_key}")

        # taskkill
        elif v == "taskkill":
            targets = []
            if "/im" in inv.switch_args:
                targets.append(inv.switch_args["/im"])
            if "/pid" in inv.switch_args:
                targets.append(inv.switch_args["/pid"])
            targets.extend(args)
            e.signals += targets

        # format / diskpart
        elif v in ("format", "diskpart"):
            e.hard_block = True
            e.opaque = True
            e.protected_hit = True
            e.notes.append(f"hard-blocked raw-disk command ({v})")

        # Fallback POSIX matching for mixed pipelines
        elif v in ("rm", "rmdir", "shred", "unlink", "truncate"):
            for a in args:
                for t in _expand(a, cwd):
                    for w in _walk(t):
                        e.deletes.append(w)
                        e.bytes_affected += _size(w)
        elif v == "find" and "-delete" in inv.raw_tokens:
            root = next((a for a in args if not a.startswith("-")), ".")
            preview_cmd = [c for c in inv.raw_tokens if c != "-delete"]
            e.notes.append("find -delete: preview via `%s`" % " ".join(preview_cmd))
            import subprocess
            import sys
            cf = 0x08000000 if sys.platform == "win32" else 0
            try:
                r = subprocess.run(preview_cmd, cwd=cwd, capture_output=True, text=True, timeout=10, creationflags=cf)
                for line in r.stdout.splitlines():
                    p = line if os.path.isabs(line) else os.path.join(cwd, line)
                    e.deletes.append(p)
                    e.bytes_affected += _size(p)
            except Exception:
                pass
            if not e.deletes:
                e.deletes.append(root if os.path.isabs(root) else os.path.join(cwd, root))
        elif v in ("chmod", "chown", "chgrp"):
            for a in args[1:]:
                e.perm_changes += _expand(a, cwd)
        elif v in ("kill", "pkill", "killall"):
            e.signals += args
        elif v in ("mkdir", "touch"):
            for a in args:
                e.creates += _expand(a, cwd)
        elif v in ("curl", "wget", "scp", "rsync", "ssh", "git", "pip", "npm", "apt", "apt-get", "docker"):
            e.external = True
        elif v in ("cmd", "cmd.exe", "%comspec%"):
            if enable_opaque_escalation and any(s.lower() in ("/c", "/k") for s in inv.switches):
                e.opaque = True
                e.notes.append("nested shell invocation via cmd.exe")
        elif v in ("powershell", "powershell.exe", "pwsh", "pwsh.exe"):
            if enable_opaque_escalation:
                e.opaque = True
                e.notes.append("nested shell invocation via powershell")
        elif v.startswith("%") and v.endswith("%"):
            if enable_opaque_escalation:
                e.opaque = True
                e.notes.append(f"dynamic variable command invocation ({v})")
        elif v in ("python", "python3", "perl", "node"):
            if enable_opaque_escalation and any(f in inv.raw_tokens for f in ("-c", "-e")):
                e.opaque = True
                e.notes.append("inline interpreter code")

    # 7. Deduplicate effect paths and recalculate affected bytes
    e.deletes = list(dict.fromkeys(e.deletes))
    e.overwrites = list(dict.fromkeys(e.overwrites))
    e.creates = list(dict.fromkeys(e.creates))
    e.appends = list(dict.fromkeys(e.appends))
    e.perm_changes = list(dict.fromkeys(e.perm_changes))
    e.signals = list(dict.fromkeys(e.signals))
    e.registry_writes = list(dict.fromkeys(e.registry_writes))
    e.bytes_affected = sum(_size(d) for d in e.deletes if os.path.isfile(d))

    # 8. Check Protected System Paths
    if enable_protected_paths:
        allp = e.deletes + e.overwrites + e.perm_changes + e.appends + e.creates
        for p in allp:
            if p.startswith("registry:"):
                continue

            paths_to_check = [p]
            if enable_symlink_resolution:
                try:
                    if os.path.islink(p) or os.path.lexists(p):
                        rp = os.path.realpath(p)
                        if rp != p:
                            paths_to_check.append(rp)
                except OSError:
                    pass

            for check_p in paths_to_check:
                norm_p = os.path.normpath(os.path.abspath(check_p)).replace("\\", "/")
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
            if e.protected_hit:
                break

    return e


def tier_from_effect(e: Effect) -> str:
    """Derive safety risk tier from resolved Effect."""
    if e.hard_block or e.protected_hit:
        return "privileged"
    if e.opaque:
        return "ambiguous"
    if e.deletes or e.overwrites or e.perm_changes or e.signals:
        return "destructive"
    if e.registry_writes:
        return "destructive" if any("delete" in str(w).lower() for w in e.registry_writes) else "state_modifying"
    if e.external:
        return "ambiguous"
    if e.creates or e.appends:
        return "state_modifying"
    return "read_only"


def preview(e: Effect) -> str:
    """Generate human-readable summary of filesystem and system effects."""
    out = []
    if e.hard_block:
        out.append("HARD BLOCKED: raw disk/partition manipulation command (format/diskpart)")
    if e.protected_hit:
        out.append("TOUCHES PROTECTED SYSTEM PATH")
    if e.deletes:
        out.append(f"DELETE {len(e.deletes)} item(s) (~{e.bytes_affected/1e6:.1f} MB), e.g. {e.deletes[:3]}")
    if e.overwrites:
        out.append(f"OVERWRITE {len(e.overwrites)} item(s): {e.overwrites[:3]}")
    if e.appends:
        out.append(f"APPEND to {len(e.appends)} item(s): {e.appends[:3]}")
    if e.creates:
        out.append(f"CREATE {len(e.creates)} item(s): {e.creates[:3]}")
    if e.perm_changes:
        out.append(f"CHANGE PERMISSIONS on {len(e.perm_changes)} item(s)")
    if e.registry_writes:
        out.append(f"REGISTRY MODIFICATION on {len(e.registry_writes)} key(s): {e.registry_writes[:3]}")
    if e.signals:
        out.append(f"SIGNAL processes: {e.signals}")
    if e.external:
        out.append("EXTERNAL side effects (network/package manager) - cannot be simulated")
    if e.opaque:
        out.append("OPAQUE: " + "; ".join(e.notes))
    return "\n".join(out) or "No filesystem effect detected."
