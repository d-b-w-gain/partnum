"""partnum command line.

    partnum id <requirements.yaml>         print the part number
    partnum register <requirements.yaml>   write id/full_hash into the file
    partnum release <requirements.yaml> <rev>   tag HEAD as <id>/<rev>
    partnum verify                         check every part tag
    partnum list                           parts, nicknames, latest revisions
    partnum find [key=v|key<v|key>v|word ...]   search requirements
    partnum install-hook                   run `verify` before every push

Options: --specs DIR (default: specs) for list/find.
"""
import argparse
import re
import stat
import sys
from pathlib import Path

from .core import PartError, load_requirements, repo_root
from .release import part_tags, release, verify

HOOK = """#!/bin/sh
# Installed by `partnum install-hook`: refuse to push if any released part
# tag no longer matches its requirements, build record or files.
cd "$(git rev-parse --show-toplevel)" || exit 1
python -m partnum verify || {
    echo "pre-push: part release check failed - not pushing." >&2
    exit 1
}
"""


def _set_field(path, key, value):
    text = path.read_text(encoding="utf-8")
    line = "%s: %s" % (key, value)
    pat = re.compile(r"^%s:.*$" % re.escape(key), re.M)
    text = pat.sub(line, text, count=1) if pat.search(text) \
        else line + "\n" + text
    path.write_text(text, encoding="utf-8")


def _specs(root, folder):
    for p in sorted((root / folder).glob("*.yaml")):
        try:
            yield load_requirements(p)
        except Exception:
            continue


def _row(req, tags):
    revs = tags.get(req.id, [])
    return "%-15s %-4s %-28s %s" % (req.id, revs[-1] if revs else "-",
                                    req.get("nickname", ""),
                                    req.get("name", ""))


def _matches(req, term):
    m = re.fullmatch(r"([\w.-]+)\s*(=|<=|>=|<|>)\s*(.+)", term)
    if not m:
        hay = " ".join(str(req.get(k, "")) for k in
                       ("class", "name", "nickname", "description")).lower()
        return term.lower() in hay
    key, op, want = m[1].lower(), m[2], m[3]
    if key == "class":
        return want.lower() in str(req.get("class", "")).lower()
    caps = req.get("capabilities") or {}
    have = caps.get(key, req.params.get(key))
    if have is None:
        return False
    try:
        x = float(want)
    except ValueError:
        return op == "=" and want.lower() in str(have).lower()
    lo, hi = have if isinstance(have, list) else (have, have)
    lo, hi = float(lo), float(hi)
    return {"=": lo <= x <= hi, "<": lo < x, "<=": lo <= x,
            ">": hi > x, ">=": hi >= x}[op]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="partnum", description=__doc__,
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("command")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--specs", default="specs")
    a = ap.parse_args(argv)
    try:
        root = repo_root()
        if a.command == "id" and len(a.args) == 1:
            r = load_requirements(a.args[0])
            print(r.id)
            print("sha256:" + r.full_hash)
        elif a.command == "register" and len(a.args) == 1:
            r = load_requirements(a.args[0])
            old = r.get("id")
            if old and old != r.id:
                raise PartError(
                    "%s is registered as %s but its inputs now hash to %s. "
                    "Changed inputs make a new part - keep the old file for "
                    "the old part and register a new one." % (r.path.name,
                                                             old, r.id))
            _set_field(r.path, "full_hash", "sha256:" + r.full_hash)
            _set_field(r.path, "id", r.id)
            print("%s  %s" % (r.id, r.path.name))
            if not r.get("nickname"):
                print("  add a nickname: a backronym of %s" % r.id[:3])
        elif a.command == "release" and len(a.args) == 2:
            print("released", release(a.args[0], a.args[1], root))
        elif a.command == "verify" and not a.args:
            results = verify(root)
            for tag, problems in results:
                print(("FAIL %s: %s" % (tag, "; ".join(problems)))
                      if problems else "ok   " + tag)
            if not results:
                print("no part tags yet")
            return 1 if any(p for _, p in results) else 0
        elif a.command == "list" and not a.args:
            tags = part_tags(root)
            for r in _specs(root, a.specs):
                print(_row(r, tags))
        elif a.command == "find":
            tags = part_tags(root)
            hits = [r for r in _specs(root, a.specs)
                    if all(_matches(r, t) for t in a.args)]
            for r in hits:
                print(_row(r, tags))
            if not hits:
                print("nothing matches - this would be a new part")
        elif a.command == "install-hook" and not a.args:
            hooks = root / ".githooks"
            hooks.mkdir(exist_ok=True)
            hook = hooks / "pre-push"
            hook.write_text(HOOK, encoding="utf-8", newline="\n")
            hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
            from .release import git
            git(root, "config", "core.hooksPath", ".githooks")
            print("pre-push hook installed in", hooks)
        else:
            ap.print_help()
            return 2
    except PartError as e:
        print("error:", e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
