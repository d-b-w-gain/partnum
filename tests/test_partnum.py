"""Run with: python -m unittest discover -s tests"""
import json
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from partnum import PartError, load_requirements, part_number, \
    write_build_record
from partnum.core import ID_PATTERN
from partnum.release import next_rev, release, verify

BASE = {"scheme": "pn/1", "class": "track-roller",
        "parameters": {"track_w_mm": 18.5, "depth_mm": 10}}


class Numbers(unittest.TestCase):
    def test_format(self):
        pid, digest = part_number(BASE)
        self.assertRegex(pid, "^%s$" % ID_PATTERN)
        self.assertEqual(len(digest), 64)

    def test_known_value(self):
        # Frozen: if this changes, pn/1 numbers everywhere would change.
        self.assertEqual(part_number(BASE)[0], "FZE-6TDH-NC5P4")

    def test_formatting_does_not_matter(self):
        other = {"parameters": {"Depth_mm": 10.0, "track_w_mm": 18.50},
                 "class": "track-roller", "scheme": "pn/1",
                 "name": "anything", "nickname": "x", "uses": {}}
        self.assertEqual(part_number(BASE), part_number(other))

    def test_inputs_matter(self):
        changed = json.loads(json.dumps(BASE))
        changed["parameters"]["track_w_mm"] = 18.6
        self.assertNotEqual(part_number(BASE)[0], part_number(changed)[0])
        used = dict(BASE, uses={"screw": "AAA-0000-00000"})
        self.assertNotEqual(part_number(BASE)[0], part_number(used)[0])

    def test_scheme_required(self):
        with self.assertRaises(PartError):
            part_number(dict(BASE, scheme="pn/0"))

    def test_revisions(self):
        self.assertEqual(next_rev(), "A")
        self.assertEqual(next_rev("H"), "J")      # no I
        self.assertEqual(next_rev("N"), "P")      # no O
        self.assertEqual(next_rev("Z"), "AA")


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class Repo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        sh(self.root, "git", "init", "-q")
        sh(self.root, "git", "config", "user.email", "t@t")
        sh(self.root, "git", "config", "user.name", "t")
        (self.root / "specs").mkdir()
        self.screw = self.root / "specs" / "screw.yaml"
        self.screw.write_text(textwrap.dedent("""\
            scheme: pn/1
            class: fastener
            parameters: {thread: M5, length_mm: 12}
            """))
        sid = load_requirements(self.screw).id
        self.roller = self.root / "specs" / "roller.yaml"
        self.roller.write_text(textwrap.dedent("""\
            scheme: pn/1
            class: track-roller
            uses: {screw: %s}
            parameters: {track_w_mm: 18.5}
            """ % sid))
        self.out = self.root / "roller.stl"

    def tearDown(self):
        self.tmp.cleanup()

    def build_and_commit(self):
        req = load_requirements(self.roller)
        self.out.write_bytes(b"solid made from %s" % req.id.encode())
        write_build_record(req, [self.out])
        sh(self.root, "git", "add", "-A")
        sh(self.root, "git", "commit", "-q", "-m", "build")
        return req

    def test_uses_resolves(self):
        req = load_requirements(self.roller)
        self.assertEqual(req.use("screw")["length_mm"], 12)

    def test_stale_reference(self):
        self.screw.write_text(self.screw.read_text().replace("12", "16"))
        with self.assertRaises(PartError):
            load_requirements(self.roller).use("screw")

    def test_release_and_verify(self):
        req = self.build_and_commit()
        tag = release(self.roller, "A", self.root)
        self.assertEqual(tag, req.id + "/A")
        self.assertEqual(verify(self.root), [(tag, [])])
        with self.assertRaises(PartError):      # no repeats or skips
            release(self.roller, "C", self.root)

    def test_release_refuses_resaved_output(self):
        self.build_and_commit()
        self.out.write_bytes(b"changed after the build")
        sh(self.root, "git", "commit", "-qam", "resave")
        with self.assertRaises(PartError):
            release(self.roller, "A", self.root)

    def test_verify_catches_fake_tag(self):
        req = self.build_and_commit()
        sh(self.root, "git", "tag", "-a", req.id + "/A", "-m", "fake")
        self.assertTrue(verify(self.root)[0][1])


if __name__ == "__main__":
    unittest.main()
