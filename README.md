# partnum

Part numbers made from a part's build inputs, and released as git tags.

A part's **requirements file** holds the static values its build script runs
from. The part number is a hash of those values. The same inputs always give
the same number, on any computer, without a central registry handing numbers
out:

```
SFD-T6P3-SXQ7K        part number
SFD-T6P3-SXQ7K/A      git tag: revision A of that part, released
```

Changing an input makes a new part number. A new **revision** is for
changes that don't touch the inputs: a fixed build script, drawing notes, a
re-export.

## Requirements file

```yaml
scheme: pn/1                  # hashing rules, frozen forever
class: track-roller           # hashed
uses:                         # hashed: parts this one is built from
  screw: QPT-8D2K-4MMXA
parameters:                   # hashed: the build inputs
  track_w_mm: 18.5
  track_depth_mm: 10
name: Track roller            # everything below is not hashed
nickname: Spinning Follower Disc
description: ...
appearance: {colour: [0.95, 0.55, 0.1]}
capabilities:                 # ranges it actually works over, for search
  track_w_mm: [17.9, 19.5]
files: [build_roller.py]      # extra files a release should pin
```

Hashing (`pn/1`): SHA-256 of the canonical JSON of `{scheme, class,
parameters, uses}`. Keys are sorted and lower-cased, text is NFC with single
spaces, numbers lose trailing zeros (`18.5 == 18.50`). The number is three
letters (A-Z without I L O U) and nine Crockford base32 characters,
`AAA-XXXX-XXXXX`, about 58 bits. The full hash is stored too, so IDs can be
lengthened later and every existing one stays a valid prefix.

`uses:` links parts like a software lockfile. If a part you use changes its
inputs, it gets a new number and your reference no longer resolves. Updating
the reference changes your inputs, so your part gets a new number too.

## In a build script

```python
from partnum import load_requirements, write_build_record

req = load_requirements("specs/track-roller.yaml")
width = req["track_w_mm"]
screw = req.use("screw")              # the screw's requirements
head = screw["head_d_mm"]
# ... build the CAD, export files ...
write_build_record(req, ["parts/Track_Roller.SLDPRT",
                         "stl/Track_Roller.STL"])
```

The build record (`track-roller.build.json`, next to the requirements file)
stores the number the build ran from and the git blob id of every file it
made.

## Releasing

```
partnum release specs/track-roller.yaml A
```

This refuses unless:

- the requirements, build record and outputs are committed;
- the build record matches the requirements;
- the committed outputs are the files the build made (not re-saved since);
- every `uses:` reference resolves;
- the revision is the next one in the sequence.

The annotated tag records the requirements file, full hash, build record and
the blob id of every released file. `partnum verify` re-checks every tag,
and `partnum install-hook` runs it before each push.

Tags only become truly fixed on a hosted remote with tag protection
(for example a GitHub ruleset forbidding updates and deletes of `*/*`).

## Finding parts

```
partnum find track_w_mm=18.7          # inside a capability range counts
partnum find class=spacer fastener=M5
partnum find roller                   # free text: class, name, description
partnum list
```

Nothing found means it would be a new part.

## Install

```
pip install -e path/to/partnum        # while developing
pip install git+https://github.com/<you>/partnum@v0.1.0   # pinned
```

Pin a version in projects that use it, so part numbers never depend on
which version of the tool happened to be installed.
