import hashlib, os
def manifest(root):
    m = {}
    for dp, dn, fn in os.walk(root):
        for n in fn + dn:
            p = os.path.join(dp, n)
            try:
                st = os.lstat(p)
                h = hashlib.sha1(open(p, "rb").read()).hexdigest() if os.path.isfile(p) and not os.path.islink(p) else ""
                m[os.path.relpath(p, root)] = (st.st_size, h, oct(st.st_mode & 0o777))
            except OSError:
                pass
    return m
def diff(before, after):
    return {
        "deleted":  sorted(set(before) - set(after)),
        "created":  sorted(set(after) - set(before)),
        "modified": sorted(k for k in before if k in after and before[k][:2] != after[k][:2]),
        "perm":     sorted(k for k in before if k in after and before[k][2] != after[k][2]),
    }
def tier_from_diff(d):
    if d["deleted"] or d["modified"] or d["perm"]: return "destructive"
    if d["created"]: return "state_modifying"
    return "read_only"
