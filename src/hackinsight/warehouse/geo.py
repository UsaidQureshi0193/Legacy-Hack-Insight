"""Match IDSR district names to OCHA ADM2 pcodes (the stable district_id used everywhere).

Bulletins spell districts freely ("Karachi East" vs OCHA "East Karachi", "Naseerabad" vs
"Nasirabad"). Order:
  1. reviewed alias file configs/district_aliases.csv (wins always)
  2. exact match on normalised name inside the same province
  3. fuzzy match inside the same province with token_sort_ratio (whole-string, word-order-free)
     >= AUTO_ACCEPT. Strings of more than 4 words are never fuzzy-matched: they are several
     district names glued together by the PDF and would otherwise "match" one of them.
Anything else is flagged unmatched, never guessed.
"""
from __future__ import annotations

import re

import pandas as pd
from rapidfuzz import fuzz

from .. import config

AUTO_ACCEPT = 86
MAX_WORDS = 4
PROVINCE_TO_ADM1 = {
    "Sindh": "Sindh", "Balochistan": "Balochistan", "Khyber Pakhtunkhwa": "Khyber Pakhtunkhwa",
    "Punjab": "Punjab", "Islamabad": "Islamabad", "Azad Kashmir": "Azad Kashmir",
    "Gilgit-Baltistan": "Gilgit Baltistan",
}


def norm(s: str) -> str:
    """lower case, letters and single spaces only, 'district' removed."""
    s = re.sub(r"[^a-z ]", " ", s.lower().replace("district", ""))
    return re.sub(r"\s+", " ", s).strip()


def score(a: str, b: str) -> float:
    """Whole-string similarity that ignores word order ("Karachi East" = "East Karachi") and
    word spacing ("Mirpurkhas" = "Mirpur Khas")."""
    return max(fuzz.token_sort_ratio(a, b), fuzz.ratio(a.replace(" ", ""), b.replace(" ", "")))


def _best(key: str, choices: list[str]) -> tuple[str, float, int] | None:
    if not choices:
        return None
    scores = [score(key, c) for c in choices]
    i = max(range(len(choices)), key=scores.__getitem__)
    return choices[i], scores[i], i


def load_aliases() -> pd.DataFrame:
    p = config.CONFIGS / "district_aliases.csv"
    if not p.exists():
        return pd.DataFrame(columns=["province", "idsr_name", "adm2_pcode", "reason"])
    return pd.read_csv(p, comment="#")


def match(places: pd.DataFrame, adm2: pd.DataFrame) -> pd.DataFrame:
    """places: unique (province, place) from IDSR district tables.
    Returns places + district_id, adm2_name, match_score, match_method."""
    adm2 = adm2.copy()
    adm2["key"] = adm2["adm2_name"].map(norm)
    adm2["adm1_norm"] = adm2["adm1_name"].map(norm)
    aliases = load_aliases()
    alias_map = {(r.province, norm(r.idsr_name)): r.adm2_pcode for r in aliases.itertuples()}
    out = []
    for r in places.itertuples(index=False):
        key = norm(r.place)
        prov_adm1 = norm(PROVINCE_TO_ADM1.get(r.province, r.province))
        cand = adm2[adm2["adm1_norm"] == prov_adm1]
        rec = dict(province=r.province, place=r.place, district_id=None, adm2_name=None,
                   match_score=0.0, match_method="unmatched")
        if (r.province, key) in alias_map:
            pc = alias_map[(r.province, key)]
            hit = adm2[adm2["adm2_pcode"] == pc]
            if len(hit):
                rec.update(district_id=pc, adm2_name=hit.iloc[0]["adm2_name"], match_score=100.0,
                           match_method="alias")
        elif len(cand):
            exact = cand[cand["key"] == key]
            if len(exact):
                rec.update(district_id=exact.iloc[0]["adm2_pcode"], adm2_name=exact.iloc[0]["adm2_name"],
                           match_score=100.0, match_method="exact")
            elif len(key.split()) > MAX_WORDS:
                rec.update(match_method="unmatched(several names glued together)")
            else:
                best = _best(key, cand["key"].tolist())
                if best and best[1] >= AUTO_ACCEPT:
                    row = cand.iloc[best[2]]
                    rec.update(district_id=row["adm2_pcode"], adm2_name=row["adm2_name"],
                               match_score=float(best[1]),
                               match_method="exact" if best[1] == 100 else "fuzzy")
                elif best:
                    rec.update(match_score=float(best[1]),
                               match_method=f"unmatched(best={cand.iloc[best[2]]['adm2_name']})")
        out.append(rec)
    return pd.DataFrame(out)
