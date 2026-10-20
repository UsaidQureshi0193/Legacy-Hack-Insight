"""Chart captions in English and Urdu, generated from gold rows (every number has a row id).

Example: "Larkana: 3,914 suspected malaria cases in the week of 21 Sep 2026, down 5% from the week
before. Highest week since 2023: 19,612 (14 Aug 2023)."
"""
from __future__ import annotations

import pandas as pd

from .agent import ShortConnection
from .. import config

UR = {"malaria": "ملیریا", "dengue": "ڈینگی"}


def caption(disease: str, district: str, lang: str = "en") -> tuple[str, list[str]]:
    con = ShortConnection(str(config.WAREHOUSE))
    h = con.execute("""SELECT row_id, week_start, cases FROM gold.district_week
                       WHERE disease = ? AND district = ? AND cases IS NOT NULL ORDER BY week_start""",
                    [disease, district]).df()
    if len(h) < 2:
        return "", []
    last, prev = h.iloc[-1], h.iloc[-2]
    peak = h.loc[h["cases"].idxmax()]
    change = (last["cases"] - prev["cases"]) / max(prev["cases"], 1)
    wk = pd.Timestamp(last["week_start"])
    pk = pd.Timestamp(peak["week_start"])
    ids = [last["row_id"], prev["row_id"], peak["row_id"]]
    if lang == "ur":
        # dates and numbers are left-to-right islands inside right-to-left Urdu (Unicode isolates)
        L = lambda x: "⁦" + str(x) + "⁩"
        trend = "اضافہ" if change > 0 else "کمی"
        week_txt, peak_week_txt = L(f"{wk:%d %b %Y}"), L(f"{pk:%d %b %Y}")
        cases_txt, peak_txt = L(f"{int(last['cases']):,}"), L(f"{int(peak['cases']):,}")
        pct_txt, since_txt = L(f"{abs(change):.0%}"), L(f"{h['week_start'].min():%Y}")
        return (f"{L(district)}: ہفتہ {week_txt} میں {cases_txt} مشتبہ {UR[disease]} کیسز، پچھلے ہفتے سے {pct_txt} "
                f"{trend}۔ {since_txt} سے سب سے زیادہ ہفتہ: {peak_txt} ({peak_week_txt})۔", ids)
    trend = "up" if change > 0 else "down"
    return (f"{district}: {int(last['cases']):,} suspected {disease} cases in the week of {wk:%d %b %Y}, {trend} "
            f"{abs(change):.0%} from the week before. Highest week since {h['week_start'].min():%Y}: "
            f"{int(peak['cases']):,} ({pk:%d %b %Y}).", ids)
