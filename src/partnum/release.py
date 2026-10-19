"""
Releases are annotated git tags named <part number>/<revision>:

    SFD-T6P3-SXQ7K/A

The tag message records the requirements file, full hash, build record and
the git blob of every released file. `verify` re-checks all of it at the
tagged commit, so a revision can only exist against inputs that hash to its
number, and released files can't be swapped afterwards.

Revision letters run A, B, ... Z, AA ... (I and O skipped, as on drawings).
A new revision is for changes that don't touch the inputs (builder fixes,
drawing notes, re-exports); changed inputs are a new part number.
"""
import json
import re
import subprocess
from pathlib import Path

import yaml

from .core import (ID_PATTERN, PartError, Requirements, load_requirements,
                   part_number, record_path, repo_root)

TAG_RE = re.compile(r"^(?P<id>%s)/(?P<rev>[A-Z]+)$" % ID_PATTERN)
REV_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def git(root, *args, check=True):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True,
                       text=True, encoding="utf-8")
    if check and r.returncode:
        raise PartError("git %s: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def next_rev(rev=None):
    if not rev:
        return "A"
    digits = [REV_ALPHABET.index(c) for c in rev]
    i = len(digits) - 1
    while i >= 0:
        if digits[i] + 1 < len(REV_ALPHABET):
            digits[i] += 1
            return "".join(REV_ALPHABET[d] for d in digits)
        digits[i] = 0
        i -= 1
    return "A" + "".join(REV_ALPHABET[d] for d in digits)


def part_tags(root):
    tags = {}
    for t in git(root, "tag", "-l").split():
        m = TAG_RE.match(t)
        if m:
            tags.setdefault(m["id"], []).append(m["rev"])
    for revs in tags.values():
        revs.sort(key=lambda r: (len(r), r))
    return tags


def _check_uses_at(root, ref, spec_rel, data):
    """Every `uses:` reference must resolve at `ref` (a commit or tag)."""
    problems = []
    uses = data.get("uses") or {}
    if not uses:
        return problems
    folder = str(Path(spec_rel).parent.as_posix())
    files = git(root, "ls-tree", "--name-only", "%s:%s" % (ref, folder),
                check=False).split()
    found = {}
    for name in files:
        if name.endswith(".yaml"):
            text = git(root, "show", "%s:%s/%s" % (ref, folder, name),
                       check=False)
            try:
                found[part_number(yaml.safe_load(text))[0]] = name
            except Exception:
                pass
    for key, pid in uses.items():
        if pid not in found:
            problems.append("uses %s = %s, not found" % (key, pid))
    return problems


def release(req_path, rev, root=None):
    req = load_requirements(req_path)
    root = Path(root or repo_root(req.path.parent))
    spec_rel = req.path.resolve().relative_to(root).as_posix()
    if req.data.get("id") and req.data["id"] != req.id:
        raise PartError("%s says id %s but its inputs hash to %s"
                        % (spec_rel, req.data["id"], req.id))
    revs = part_tags(root).get(req.id, [])
    want = next_rev(revs[-1] if revs else None)
    if rev != want:
        raise PartError("next revision of %s is %s (have: %s)"
                        % (req.id, want, ", ".join(revs) or "none"))
    rec_p = record_path(req)
    if not rec_p.exists():
        raise PartError("no build record %s - run the build first"
                        % rec_p.name)
    rec = json.loads(rec_p.read_text(encoding="utf-8"))
    if rec.get("id") != req.id:
        raise PartError("build record is for %s, requirements are %s - "
                        "rebuild from the current requirements"
                        % (rec.get("id"), req.id))
    rec_rel = rec_p.resolve().relative_to(root).as_posix()
    files = [spec_rel, rec_rel] + list(rec["outputs"]) + \
        list(req.data.get("files") or [])
    dirty = git(root, "status", "--porcelain", "--", *files).strip()
    if dirty:
        raise PartError("commit these first - a release is exactly what is "
                        "in HEAD:\n" + dirty)
    blobs = {}
    for f in files:
        sha = git(root, "rev-parse", "HEAD:" + f, check=False).strip()
        if not sha:
            raise PartError("%s is not committed at HEAD" % f)
        blobs[f] = sha
    for f, sha in rec["outputs"].items():
        if blobs[f] != sha:
            raise PartError("%s is not the file the build produced (it has "
                            "been changed or re-saved since) - rebuild" % f)
    problems = _check_uses_at(root, "HEAD", spec_rel, req.data)
    if problems:
        raise PartError("; ".join(problems))
    msg = "\n".join([
        "%s rev %s - %s" % (req.id, rev, req.get("nickname", "")),
        req.get("name", ""),
        "",
        "requirements: " + spec_rel,
        "build-record: " + rec_rel,
        "full-hash: sha256:" + req.full_hash,
        "files:",
        *("  %s  %s" % (sha, f) for f, sha in blobs.items()),
    ])
    tag = "%s/%s" % (req.id, rev)
    git(root, "tag", "-a", tag, "-m", msg)
    return tag


def verify(root):
    """[(tag, [problems])] for every part tag in the repository."""
    results = []
    tags = part_tags(root)
    for pid, revs in sorted(tags.items()):
        for i, rev in enumerate(revs):
            tag = "%s/%s" % (pid, rev)
            msg = git(root, "tag", "-l", "--format=%(contents)", tag)
            problems = []
            m = re.search(r"^requirements: (.+)$", msg, re.M)
            if not m:
                problems.append("tag message names no requirements file")
            else:
                spec_rel = m[1].strip()
                text = git(root, "show", "%s:%s" % (tag, spec_rel),
                           check=False)
                if not text:
                    problems.append("%s missing at tag" % spec_rel)
                else:
                    data = yaml.safe_load(text)
                    got = part_number(data)[0]
                    if got != pid:
                        problems.append("inputs hash to %s, not %s"
                                        % (got, pid))
                    problems += _check_uses_at(root, tag, spec_rel, data)
            r = re.search(r"^build-record: (.+)$", msg, re.M)
            if r:
                text = git(root, "show", "%s:%s" % (tag, r[1].strip()),
                           check=False)
                rec = json.loads(text) if text else {}
                if rec.get("id") != pid:
                    problems.append("build record is not for this part")
                for f, sha in (rec.get("outputs") or {}).items():
                    now = git(root, "rev-parse", "%s:%s" % (tag, f),
                              check=False).strip()
                    if now != sha:
                        problems.append("%s is not the built file" % f)
            for sha, f in re.findall(r"^  ([0-9a-f]{40})  (.+)$", msg, re.M):
                now = git(root, "rev-parse", "%s:%s" % (tag, f),
                          check=False).strip()
                if now != sha:
                    problems.append("%s differs from the release" % f)
            if i and next_rev(revs[i - 1]) != rev:
                problems.append("revision gap before %s" % rev)
            results.append((tag, problems))
    return results
