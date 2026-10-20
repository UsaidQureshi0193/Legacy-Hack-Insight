"""Turn one IDSR bulletin PDF into long-format rows: (province, place, disease, value_raw).

Bulletins have two kinds of tables we care about:
  * district tables  - header "Districts | Malaria | AD (Non-Cholera) | ...", one row per district
  * national table   - header "Diseases | AJK | Balochistan | ... | Total", one row per disease

pdfplumber spreads each value over padding cells ('' / None), and long header labels are split
over 2-3 rows ("AD (Non-" / "Cholera)").  The approach:
  1. merge the header rows column-by-column,
  2. drop empty cells from header and data rows ("compress"),
  3. zip header labels with values only when the counts match - otherwise the row is
     reported as unparsed (we never guess an alignment).
Values are kept as strings here ("1,542", "NR"); typing happens in the cleaned layer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
from rapidfuzz import fuzz

# Bump when parsing logic changes: the extract cache is keyed by (PDF sha256, PARSER_VERSION).
PARSER_VERSION = "4"

PROVINCES = {
    "sindh": "Sindh", "balochistan": "Balochistan", "kp": "Khyber Pakhtunkhwa",
    "khyber pakhtunkhwa": "Khyber Pakhtunkhwa", "kpk": "Khyber Pakhtunkhwa", "punjab": "Punjab",
    "ict": "Islamabad", "islamabad": "Islamabad", "ajk": "Azad Kashmir", "gb": "Gilgit-Baltistan",
    "gilgit baltistan": "Gilgit-Baltistan", "gilgit-baltistan": "Gilgit-Baltistan",
}
TABLE_TITLE_RE = re.compile(
    r"Table\s*\d+\s*:\s*(?:District|Province)[\s/-]*(?:wise|Area wise)?[^\n]*?(?:Week|week)\s*\d+[,\s]*"
    r"(?:\d{4}[,\s]*)?([A-Za-z][A-Za-z &-]*)", re.I)
WEEK_RE = re.compile(r"Week\s*(\d{1,2})\s*,\s*(\d{4})", re.I)
NUMERIC_RE = re.compile(r"^(?:\d[\d,]*|NR|N/?A|-|–)$", re.I)


def disease_key(label: str) -> str:
    """Normalise a header label: 'Typhoi\\nd' -> 'typhoid', 'AD (Non-\\nCholera)' -> 'ad_non_cholera'."""
    s = re.sub(r"\s+", "", label.lower())
    s = re.sub(r"[^a-z0-9<]+", "_", s).strip("_")
    aliases = {"malaria": "malaria", "dengue": "dengue", "ili": "ili", "typhoid": "typhoid",
               "ad_non_cholera": "ad_non_cholera", "adnon_cholera": "ad_non_cholera",
               "b_diarrhea": "b_diarrhea", "b_diarrhoea": "b_diarrhea",
               "dogbite": "animal_dogbite", "animal_dogbite": "animal_dogbite", "rabies_dogbite": "animal_dogbite",
               "alri<5years": "alri_u5", "alri_<5years": "alri_u5", "alri<_5years": "alri_u5"}
    return aliases.get(s, s)


@dataclass
class ParseResult:
    rows: list[dict] = field(default_factory=list)
    problems: list[dict] = field(default_factory=list)   # handed to the Data Quality Agent
    stated_week: tuple[int, int] | None = None            # (week, year) printed inside the PDF


def _merge_header(table: list[list], n_header: int) -> list[str]:
    width = max(len(r) for r in table)
    labels = []
    for c in range(width):
        parts = [str(r[c]).strip() for r in table[:n_header] if c < len(r) and r[c] not in (None, "")]
        labels.append(" ".join(parts))
    return labels


def _compress(cells: list) -> list[str]:
    return [str(c).strip() for c in cells if c not in (None, "") and str(c).strip() != ""]


def _header_rows(table: list[list]) -> int:
    """Header = leading rows until the first row whose compressed tail is all numeric/NR."""
    for i, r in enumerate(table):
        cells = _compress(r)
        if len(cells) >= 2 and all(NUMERIC_RE.match(c) for c in cells[1:]) and not NUMERIC_RE.match(cells[0]):
            return i
    return 0


def _province_from_title(text: str) -> list[str]:
    out = []
    for m in TABLE_TITLE_RE.finditer(text):
        name = m.group(1).strip().lower()
        for k, v in PROVINCES.items():
            if name.startswith(k):
                out.append(v)
                break
        else:
            out.append(f"UNKNOWN:{m.group(1).strip()}")
    return out


def parse_pdf(path: Path, year: int, week: int) -> ParseResult:
    res = ParseResult()
    src = path.name
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            if res.stated_week is None and (m := WEEK_RE.search(text)):
                res.stated_week = (int(m.group(1)), int(m.group(2)))
            low = text.lower()
            if "districts" not in low and "diseases" not in low:   # 2023 bulletins print "DISTRICTS"
                page.close()
                continue
            titles = _province_from_title(text)
            big = [t for t in page.extract_tables() if len(t) >= 4]
            page.close()   # free the page's layout objects: image-heavy bulletins otherwise exhaust memory
            for ti, table in enumerate(big):
                nh = _header_rows(table)
                if nh == 0:
                    continue
                header = _compress(_merge_header(table, nh))
                if not header:
                    continue
                kind = header[0].lower()
                if kind.startswith("district"):
                    province = titles[ti] if ti < len(titles) else (titles[-1] if titles else "UNKNOWN")
                    _rows_district_table(res, table[nh:], header, province, src, pno, year, week)
                elif kind.startswith("disease"):
                    _rows_national_table(res, table[nh:], header, src, pno, year, week)
    if not res.rows:
        res.problems.append(dict(file=src, page=None, issue="no_supported_table",
                                 detail="layout not recognised (older 2021-22 bulletins embed tables in text)"))
    return res


_GAZ: list[str] | None = None


def _gazetteer() -> list[str]:
    global _GAZ
    if _GAZ is None:
        p = Path(__file__).resolve().parents[3] / "configs" / "district_gazetteer.txt"
        _GAZ = [_nk(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")] \
            if p.exists() else []
    return _GAZ


def _nk(s: str) -> str:
    return re.sub(r"[^a-z]", "", s.lower())


def _name_score(candidate: str) -> float:
    """How much `candidate` looks like (the start of) a real district name: 100 if it is a prefix
    of a gazetteer name ("Tando Muhammad" -> "Tando Muhammad Khan"), else the best fuzzy ratio."""
    c = _nk(candidate)
    gaz = _gazetteer()
    if not c or not gaz:
        return 0.0
    if any(g.startswith(c) for g in gaz):
        return 100.0
    return max(fuzz.ratio(c, g) for g in gaz)


def _join_split_names(body) -> list[tuple[str, list[str]]]:
    """Long district names are split over 2-3 PDF rows, in (at least) three layouts:

      A (2026): "Tando" + values | "Muhammad" (name only) | "Khan" + copy of the same values
      B (2025): "Naushero" (name only) | values with an empty name cell | "Feroze" (name only)
      C (2025): "Tando" (name only) | "Muhammad" + values | "Khan" (name only)

    Rules: a values-only row takes the name-only rows directly before it (B). Any other name-only
    fragment goes to the previous or the next record - whichever join looks more like a real
    district in configs/district_gazetteer.txt (ties go backward). A row whose values exactly copy
    the previous row's continues it (A). All-zero copies are NOT merged, because two real districts
    can both report zeros - those stay separate and are caught at name matching instead."""
    rows = [c for c in (_compress(r) for r in body) if c]

    def kind(c):
        if all(NUMERIC_RE.match(x) for x in c):
            return "values_only"
        return "name_only" if len(c) == 1 else "full"

    kinds = [kind(c) for c in rows]
    out: list[tuple[str, list[str]]] = []
    pending: list[str] = []
    for i, (c, k) in enumerate(zip(rows, kinds)):
        # first row after this run of name-only rows (a name can span "Tando" / "Muhammad" / values)
        j = i + 1
        while j < len(rows) and kinds[j] == "name_only":
            j += 1
        nxt = kinds[j] if j < len(rows) else None
        if k == "name_only":
            name = re.sub(r"\s+", " ", c[0])
            if nxt == "values_only" or not out:
                pending.append(name)
                continue
            back = f"{out[-1][0]} {name}"
            fwd = " ".join(pending + [name] + ([rows[j][0]] if nxt == "full" else []))
            if nxt == "full" and _name_score(fwd) > _name_score(back):
                pending.append(name)
            else:
                out[-1] = (back, out[-1][1])
        elif k == "values_only":
            out.append((" ".join(pending), c))
            pending = []
        else:
            name, values = re.sub(r"\s+", " ", c[0]), c[1:]
            if pending:
                name, pending = " ".join(pending + [name]), []
            is_copy = out and values == out[-1][1] and any(v != "0" for v in values)
            if is_copy:
                out[-1] = (f"{out[-1][0]} {name}", out[-1][1])
            else:
                out.append((name, values))
    return [(n, v) for n, v in out if n]


def _rows_district_table(res, body, header, province, src, pno, year, week):
    diseases = [disease_key(h) for h in header[1:]]
    for place, values in _join_split_names(body):
        if place.lower() in ("total", "grand total"):
            place_type = "province_total"
        else:
            place_type = "district"
        if len(values) != len(diseases):
            res.problems.append(dict(file=src, page=pno, issue="column_mismatch",
                                     detail=f"{province}/{place}: {len(values)} values vs {len(diseases)} headers"))
            continue
        for d, v in zip(diseases, values):
            res.rows.append(dict(year=year, week=week, province=province, place=place, place_type=place_type,
                                 disease=d, value_raw=v, source_file=src, page=pno, table="district"))


def _rows_national_table(res, body, header, src, pno, year, week):
    places = header[1:]
    # Only the province-wise case table; lab tables ("Total Test", "Total Positive") are skipped.
    if not all(p.strip().lower() in PROVINCES or p.strip().lower() == "total" for p in places):
        return
    for r in body:
        cells = _compress(r)
        if len(cells) != len(places) + 1:
            if cells:
                res.problems.append(dict(file=src, page=pno, issue="column_mismatch",
                                         detail=f"national/{cells[0]}: {len(cells)-1} values vs {len(places)} headers"))
            continue
        d = disease_key(cells[0])
        for place, v in zip(places, cells[1:]):
            pl = place.strip()
            prov = PROVINCES.get(pl.lower(), pl)
            res.rows.append(dict(year=year, week=week, province=prov if pl.lower() != "total" else "Pakistan",
                                 place=prov if pl.lower() != "total" else "Pakistan",
                                 place_type="province_total" if pl.lower() != "total" else "national_total",
                                 disease=d, value_raw=v, source_file=src, page=pno, table="national"))
