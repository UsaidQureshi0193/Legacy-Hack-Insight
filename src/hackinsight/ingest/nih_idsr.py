"""Source 1 - NIH Pakistan IDSR weekly bulletins (PDF).

The listing page links every bulletin with a title like "IDSRS Week 39 Bulletin (2026)".
File names on the server are irregular ("IDSR weekly report 38-NG (1).pdf"), so we never
guess URLs: we read the listing, take year + week from the link title, and save each PDF
under a stable name  data/raw/nih_idsr/<year>_W<week>.pdf.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urljoin

from .. import config, http
from ..manifest import Manifest

RAW_DIR = config.RAW / "nih_idsr"
LINK_RE = re.compile(r"<a href='([^']+\.pdf)[^']*' title='Download ([^']+)'", re.I)
TITLE_RE = re.compile(r"Week\s*(\d{1,2}).*?\((\d{4})\)", re.I)


@dataclass(frozen=True)
class Bulletin:
    year: int
    week: int
    url: str
    title: str

    @property
    def dest(self) -> Path:
        return RAW_DIR / f"{self.year}_W{self.week:02d}.pdf"


def parse_listing(html: str) -> list[Bulletin]:
    """Extract (year, week, url) from the listing HTML. First link wins if a week is listed twice
    (the page lists newest uploads first, so the first one is the latest correction)."""
    seen: dict[tuple[int, int], Bulletin] = {}
    for href, title in LINK_RE.findall(html):
        m = TITLE_RE.search(title)
        if not m:
            continue
        week, year = int(m.group(1)), int(m.group(2))
        url = urljoin(config.NIH_BASE, quote(href, safe="/:()-_.~%"))
        seen.setdefault((year, week), Bulletin(year, week, url, title.strip()))
    return sorted(seen.values(), key=lambda b: (b.year, b.week))


def fetch(manifest: Manifest, refresh_listing: bool = True, limit: int | None = None) -> list[Path]:
    """Download the listing (always, it is how we detect a new week) and any bulletin we do not
    already hold with a valid hash. Returns paths of newly downloaded PDFs."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    listing = RAW_DIR / "listing.html"
    if refresh_listing or not listing.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M")
        # Keep each listing snapshot: it proves what NIH published at that moment.
        snap = RAW_DIR / "listings" / f"listing_{stamp}.html"
        http.download(config.NIH_LISTING_URL, snap)
        manifest.record(snap, "nih_idsr", config.NIH_LISTING_URL, config.NIH_LICENCE)
        listing.write_bytes(snap.read_bytes())
    bulletins = parse_listing(listing.read_text(encoding="utf-8", errors="replace"))
    new = []
    for b in bulletins[-limit:] if limit else bulletins:
        if manifest.has_valid(b.dest):
            continue
        http.download(b.url, b.dest)
        manifest.record(b.dest, "nih_idsr", b.url, config.NIH_LICENCE)
        new.append(b.dest)
        print(f"  nih_idsr  {b.dest.name}  <- {b.title}")
    manifest.save()
    return new
