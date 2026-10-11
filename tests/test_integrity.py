import hashlib
import json
import shutil
from pathlib import Path

import pytest

from global_investment_research import data, integrity

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "data/snapshots/2026-10-11"


def test_manifest_records_sha256_and_bytes(tmp_path):
    (tmp_path / "a.json").write_bytes(b"abc")
    (tmp_path / "notes.txt").write_text("not covered")
    path = integrity.write_manifest(tmp_path)
    m = json.loads(path.read_text())
    # Worked example: SHA-256("abc") is the FIPS 180-2 test vector; 3 bytes.
    assert m["files"] == {"a.json": {"sha256": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
                                     "bytes": 3}}
    assert m["instruments"] == []                  # older *.json readers see an empty group
    assert integrity.verify_manifest(tmp_path) == []
    first = path.read_bytes()
    integrity.write_manifest(tmp_path)
    assert path.read_bytes() == first              # deterministic: no timestamp


def test_tampering_is_named(tmp_path):
    (tmp_path / "a.json").write_text('{"x": 1}')
    (tmp_path / "b.json").write_text('{"y": 2}')
    integrity.write_manifest(tmp_path)
    (tmp_path / "a.json").write_text('{"x": 2}')   # same size, different bytes
    (tmp_path / "b.json").unlink()
    (tmp_path / "c.json").write_text("{}")
    problems = integrity.verify_manifest(tmp_path)
    assert problems == ["changed: a.json (SHA-256 differs from manifest)", "missing: b.json",
                        "extra: c.json (not in manifest)"]
    (tmp_path / "a.json").write_text('{"x": 22}')
    assert integrity.verify_manifest(tmp_path)[0] == "changed: a.json (9 bytes, manifest records 8)"


def test_no_manifest_and_path_escape(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    assert integrity.verify_manifest(tmp_path) == ["no manifest"]
    assert data.snapshot_integrity(tmp_path)["status"] == "no manifest"
    (tmp_path / "manifest.json").write_text(json.dumps({"files": {"../a.json": {"sha256": "x", "bytes": 2}}}))
    problems = integrity.verify_manifest(tmp_path)
    assert "invalid entry: '../a.json'" in problems and "extra: a.json (not in manifest)" in problems
    (tmp_path / "manifest.json").write_text("not json")
    assert integrity.verify_manifest(tmp_path)[0].startswith("manifest unreadable")


def test_loader_skips_manifest_and_refuses_tampered_snapshot(tmp_path):
    uni = {"instruments": [{"symbol": "AAA", "name": "A", "market": "US", "kind": "stock", "sector": "x", "group": "g"}]}
    row = {"source": "test", "instruments": [{"symbol": "AAA", "status": "ok", "currency": "USD",
           "weekly": {"time": ["2026-10-02T13:30:00Z", "2026-10-09T13:30:00Z"], "close": [10, 11]}}]}
    (tmp_path / "g.json").write_text(json.dumps(row))
    px, _ = data.load_ibkr_snapshot(tmp_path, uni)        # no manifest: loads as before
    integrity.write_manifest(tmp_path)
    px2, _ = data.load_ibkr_snapshot(tmp_path, uni)       # manifest present and skipped as a group
    assert px2.equals(px)
    assert data.snapshot_integrity(tmp_path)["status"] == "verified"
    row["instruments"][0]["weekly"]["close"] = [10, 12]
    (tmp_path / "g.json").write_text(json.dumps(row))
    with pytest.raises(ValueError, match="integrity check failed: changed: g.json"):
        data.load_ibkr_snapshot(tmp_path, uni)
    assert data.snapshot_integrity(tmp_path)["status"] == "failed"


@pytest.mark.parametrize("rel", ["data/snapshots/2026-10-11", "data/history/weekly_long", "data/history"])
def test_saved_data_matches_its_manifest(rel):
    directory = ROOT / rel
    assert integrity.verify_manifest(directory) == []
    m = json.loads((directory / "manifest.json").read_text())
    assert set(m["files"]) == {f.name for f in integrity.covered_files(directory)}
    some = sorted(m["files"])[0]
    assert m["files"][some]["sha256"] == hashlib.sha256((directory / some).read_bytes()).hexdigest()


def test_real_snapshot_tamper_detected_in_copy(tmp_path):
    copy = tmp_path / "2026-10-11"
    shutil.copytree(SNAP, copy)
    u = data.load_universe(ROOT / "config/universe.json")
    data.load_ibkr_snapshot(copy, u)                      # verified copy loads
    payload = json.loads((copy / "sg.json").read_text())
    payload["instruments"][0]["weekly"]["close"][-1] *= 1.01
    (copy / "sg.json").write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="sg.json"):
        data.load_ibkr_snapshot(copy, u)
