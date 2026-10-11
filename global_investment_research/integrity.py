"""SHA-256 manifests for saved data directories (IBKR snapshots and long history).

A manifest records the hash and size of every *.json file in one directory, so a later run can
prove it is reading exactly the bytes that were collected: a file edited by hand, truncated,
removed or added after collection is reported by name. Adapted from the reference app's
`source_snapshot.json` replay check and dashboard integrity gate, fitted to our JSON layout.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

MANIFEST = "manifest.json"


def sha256_file(path: str | Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def covered_files(directory: str | Path) -> list[Path]:
    """The files a manifest covers: every *.json directly in the directory except the manifest."""
    return sorted(f for f in Path(directory).glob("*.json") if f.name != MANIFEST and f.is_file())


def write_manifest(directory: str | Path) -> Path:
    """Write <directory>/manifest.json and return its path.

    Shape: {"schema_version", "algorithm", "files": {name: {"sha256", "bytes"}}, "instruments": []}.
    The empty "instruments" list is deliberate: readers that glob *.json in a snapshot directory
    and iterate each file's instruments then see the manifest as an empty group instead of failing.
    No timestamp is stored, so re-writing a manifest for unchanged files gives identical bytes.
    """
    directory = Path(directory)
    files = covered_files(directory)
    if not files:
        raise FileNotFoundError(f"no JSON files to record in {directory}")
    manifest = {
        "schema_version": 1,
        "algorithm": "sha256",
        "files": {f.name: {"sha256": sha256_file(f), "bytes": f.stat().st_size} for f in files},
        "instruments": [],
        "note": "Integrity manifest: verify with integrity.verify_manifest; not an instrument group.",
    }
    path = directory / MANIFEST
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return path


def verify_manifest(directory: str | Path) -> list[str]:
    """Problems found comparing the directory with its manifest; [] means every byte matches.

    Reports, by file name: 'missing' (recorded, now absent), 'extra' (present, not recorded) and
    'changed' (size or hash differs). A directory without a manifest returns ["no manifest"].
    """
    directory = Path(directory)
    path = directory / MANIFEST
    if not path.exists():
        return ["no manifest"]
    try:
        recorded = json.loads(path.read_text(encoding="utf-8"))["files"]
        if not isinstance(recorded, dict):
            raise TypeError("'files' is not an object")
    except (ValueError, KeyError, TypeError) as exc:
        return [f"manifest unreadable: {exc}"]
    problems = []
    for name in sorted(recorded):
        entry = recorded[name] or {}
        # Only bare file names in this directory, as in the reference app's replay check.
        if Path(name).name != name or name in ("", ".", "..") or name == MANIFEST:
            problems.append(f"invalid entry: {name!r}")
            continue
        f = directory / name
        if not f.is_file():
            problems.append(f"missing: {name}")
            continue
        size = f.stat().st_size
        if size != entry.get("bytes"):
            problems.append(f"changed: {name} ({size} bytes, manifest records {entry.get('bytes')})")
        elif sha256_file(f) != entry.get("sha256"):
            problems.append(f"changed: {name} (SHA-256 differs from manifest)")
    for f in covered_files(directory):
        if f.name not in recorded:
            problems.append(f"extra: {f.name} (not in manifest)")
    return problems
