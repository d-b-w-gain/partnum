"""
Part numbers from build inputs.

A part's REQUIREMENTS FILE holds the static values its build script runs
from. The part number is a hash of those values, so the same inputs always
give the same number, on any computer, forever.

    scheme: pn/1          # hashing rules - frozen
    class: track-roller   # hashed
    uses:                 # hashed: other parts this one is built from
      screw: QPT-8D2K-4MMXA
    parameters:           # hashed: the build inputs
      track_w_mm: 18.5
    name, nickname, description, appearance, capabilities, files, id,
    full_hash             # NOT hashed

Number format (pn/1): SHA-256 of the canonical JSON of
{scheme, class, parameters, uses}; three letters from A-Z without I L O U,
then 9 Crockford base32 characters: AAA-XXXX-XXXXX (~58 bits). The full hash
is kept so IDs can be lengthened later without changing any existing one.
"""
import hashlib
import json
import subprocess
import unicodedata
from decimal import Decimal
from pathlib import Path

import yaml

SCHEME = "pn/1"
LETTERS = "ABCDEFGHJKMNPQRSTVWXYZ"            # 22: no I L O U
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # 32: no I L O U
ID_PATTERN = r"[A-HJKMNP-TV-Z]{3}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{5}"


class PartError(Exception):
    """A requirements file, reference or build record is not consistent."""


# ----------------------------------------------------------------- hashing
def _canon(v):
    """Sorted keys, NFC text with single spaces, numbers without trailing
    zeros (18.5 == 18.50) and whole floats as ints. Lists keep order."""
    if isinstance(v, dict):
        return {_canon_key(k): _canon(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_canon(x) for x in v]
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float, Decimal)):
        d = Decimal(str(v)).normalize()
        return int(d) if d == d.to_integral_value() else float(d)
    if isinstance(v, str):
        return " ".join(unicodedata.normalize("NFC", v).split())
    raise PartError("unsupported value: %r" % (v,))


def _canon_key(k):
    return " ".join(unicodedata.normalize("NFC", str(k)).lower().split())


def canonical(data):
    """The exact text that is hashed."""
    if data.get("scheme") != SCHEME:
        raise PartError("scheme must be %r" % SCHEME)
    for key in ("class", "parameters"):
        if key not in data:
            raise PartError("requirements file is missing %r" % key)
    payload = {"scheme": data["scheme"], "class": data["class"],
               "parameters": data["parameters"],
               "uses": data.get("uses") or {}}
    return json.dumps(_canon(payload), sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)


def number_from_digest(hexdigest):
    n = int(hexdigest, 16)
    letters = ""
    for _ in range(3):
        n, r = divmod(n, len(LETTERS))
        letters += LETTERS[r]
    body = ""
    for _ in range(9):
        n, r = divmod(n, 32)
        body += CROCKFORD[r]
    return "%s-%s-%s" % (letters, body[:4], body[4:])


def part_number(data):
    """(part number, full sha256 hex) for a requirements mapping."""
    digest = hashlib.sha256(canonical(data).encode("utf-8")).hexdigest()
    return number_from_digest(digest), digest


# --------------------------------------------------------- requirements
class Requirements:
    """A loaded requirements file. Index it like a dict for parameters:
    req["track_w_mm"]. req.use("screw") gives the requirements of a part
    this one is built from."""

    def __init__(self, path, data):
        self.path = Path(path)
        self.data = data
        self.id, self.full_hash = part_number(data)

    def __getitem__(self, key):
        try:
            return self.data["parameters"][key]
        except KeyError:
            raise PartError("%s has no parameter %r"
                            % (self.path.name, key)) from None

    @property
    def params(self):
        return dict(self.data["parameters"])

    def get(self, key, default=None):
        return self.data.get(key, default)

    def use(self, name):
        """Load the part named in `uses:` - it must still exist with exactly
        that number, i.e. its inputs haven't changed since it was linked."""
        ref = (self.data.get("uses") or {}).get(name)
        if not ref:
            raise PartError("%s does not use %r" % (self.path.name, name))
        for p in sorted(self.path.parent.glob("*.yaml")):
            other = load_requirements(p)
            if other.id == ref:
                return other
        raise PartError(
            "%s uses %s = %s, but no requirements file in %s has that "
            "number any more. If that part's inputs changed on purpose, "
            "update the reference - which gives %s a new number too."
            % (self.path.name, name, ref, self.path.parent, self.path.name))

    def __repr__(self):
        return "<Requirements %s %s>" % (self.id, self.path.name)


def load_requirements(path):
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PartError("%s is not a requirements file" % path)
    return Requirements(path, data)


# --------------------------------------------------------- build records
def _git(root, *args):
    r = subprocess.run(["git", *args], cwd=root, capture_output=True,
                       text=True, encoding="utf-8")
    if r.returncode:
        raise PartError("git %s: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout.strip()


def repo_root(start="."):
    return Path(_git(start, "rev-parse", "--show-toplevel"))


def record_path(req):
    return req.path.with_name(req.path.stem + ".build.json")


def write_build_record(req, outputs):
    """Call at the end of a build: records the part number and inputs the
    build ran from, and the git blob id of every file it produced, next to
    the requirements file (<name>.build.json). Release checks the committed
    outputs against it, so released files are known to come from these
    inputs."""
    root = repo_root(req.path.parent)
    out = {}
    for f in outputs:
        f = Path(f).resolve()
        out[f.relative_to(root).as_posix()] = _git(root, "hash-object",
                                                   str(f))
    record = {
        "id": req.id,
        "full_hash": "sha256:" + req.full_hash,
        "requirements": req.path.resolve().relative_to(root).as_posix(),
        "canonical": canonical(req.data),
        "uses": dict(req.data.get("uses") or {}),
        "outputs": out,
    }
    p = record_path(req)
    p.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return p
