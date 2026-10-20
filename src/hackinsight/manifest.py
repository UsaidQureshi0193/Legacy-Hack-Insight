"""manifest.csv: one row per raw file - source, query, fetch time, SHA-256, licence.

The manifest is the chain of evidence. `make data` re-hashes every raw file and refuses
to continue if a hash does not match (someone edited a raw file or a download broke).
"""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from pathlib import Path

from . import config

COLUMNS = ["path", "source", "query", "fetched_at_utc", "sha256", "bytes", "licence"]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class Entry:
    path: str          # relative to repo root, forward slashes
    source: str        # nih_idsr | open_meteo | opendengue | hdx
    query: str         # exact URL or API query used
    fetched_at_utc: str
    sha256: str
    bytes: int
    licence: str


class Manifest:
    def __init__(self, path: Path = config.MANIFEST):
        self.path = path
        self.rows: dict[str, Entry] = {}
        if path.exists():
            with open(path, newline="", encoding="utf-8") as f:
                for r in csv.DictReader(f):
                    r["bytes"] = int(r["bytes"])
                    self.rows[r["path"]] = Entry(**{k: r[k] for k in COLUMNS})

    @staticmethod
    def rel(p: Path) -> str:
        return p.resolve().relative_to(config.ROOT.resolve()).as_posix()

    def has_valid(self, p: Path) -> bool:
        """True when the file exists and matches its recorded hash (so we can skip re-fetching)."""
        e = self.rows.get(self.rel(p))
        return bool(e and p.exists() and sha256_of(p) == e.sha256)

    def record(self, p: Path, source: str, query: str, licence: str) -> Entry:
        e = Entry(self.rel(p), source, query, datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  sha256_of(p), p.stat().st_size, licence)
        self.rows[e.path] = e
        return e

    def save(self) -> None:
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            for e in sorted(self.rows.values(), key=lambda e: e.path):
                w.writerow(asdict(e))

    def verify(self) -> list[str]:
        """Return a list of problems (missing file or hash mismatch). Empty list = all good."""
        problems = []
        for e in self.rows.values():
            p = config.ROOT / e.path
            if not p.exists():
                problems.append(f"missing: {e.path}")
            elif sha256_of(p) != e.sha256:
                problems.append(f"sha256 mismatch: {e.path}")
        return problems

    def entries(self, source: str | None = None) -> list[Entry]:
        return [e for e in self.rows.values() if source is None or e.source == source]
