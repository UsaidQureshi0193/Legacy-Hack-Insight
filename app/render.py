"""Turn the Insight Agent's structured answer into a card a non-technical reader understands.

Layout: one plain sentence first, then a small table with plain column names, then quoted official
guidance, then sources a person can click. Row ids (needed for audit) sit in a closed
"Audit details" section at the bottom instead of cluttering every line.
"""
from __future__ import annotations

import csv
import html
import re
from functools import lru_cache

import pandas as pd

from hackinsight import config

L = {
    "en": {"where": "Where to act first", "rank": "#", "district": "District", "province": "Province",
           "last": "Reported last week", "next": "Expected next week", "range": "Likely range (8 in 10 chance)",
           "source": "Source", "week": "Week", "cases": "Suspected cases", "note": "Note",
           "history": "Recent weeks", "guidance": "What official guidance says", "sources": "Documents quoted",
           "idk": "I don't know", "why": "Why not", "audit": "Audit details (ids to check every number)",
           "status": "Data status", "baseline": ("How this number is made: last week's reported count is used as the "
                                                 "estimate for next week, because our model was not more accurate "
                                                 "on past data. That is why there is no range."),
           "model": "How this number is made: the model's middle estimate; the range covered 8 in 10 past weeks.",
           "emergency": ("This is information, not medical advice. If someone has high fever with bleeding, severe "
                         "stomach pain, repeated vomiting or drowsiness, go to the nearest hospital now or call "
                         "Rescue 1122."),
           "reviewed": "Reviewed by", "not_reviewed": "Not yet reviewed by a person", "page": "page"},
    "ur": {"where": "پہلے کہاں کارروائی کریں", "rank": "#", "district": "ضلع", "province": "صوبہ",
           "last": "پچھلے ہفتے رپورٹ شدہ", "next": "اگلے ہفتے متوقع", "range": "ممکنہ حد (10 میں 8 امکان)",
           "source": "ماخذ", "week": "ہفتہ", "cases": "مشتبہ کیسز", "note": "نوٹ", "history": "حالیہ ہفتے",
           "guidance": "سرکاری رہنمائی کیا کہتی ہے", "sources": "حوالہ شدہ دستاویزات", "idk": "مجھے معلوم نہیں",
           "why": "وجہ", "audit": "آڈٹ تفصیلات (ہر عدد کی جانچ کے لیے آئی ڈیز)", "status": "ڈیٹا کی صورتحال",
           "baseline": ("یہ عدد کیسے بنا: پچھلے ہفتے کی رپورٹ کو اگلے ہفتے کا اندازہ مانا گیا ہے، کیونکہ ہمارا ماڈل "
                        "ماضی کے ڈیٹا پر زیادہ درست نہیں تھا۔ اسی لیے حد نہیں دی گئی۔"),
           "model": "یہ عدد کیسے بنا: ماڈل کا درمیانی اندازہ؛ حد ماضی کے 10 میں سے 8 ہفتوں کا احاطہ کرتی تھی۔",
           "emergency": ("یہ معلومات ہیں، طبی مشورہ نہیں۔ اگر کسی کو تیز بخار کے ساتھ خون آنا، پیٹ میں شدید درد، بار بار قے "
                         "یا غنودگی ہو تو فوراً قریبی ہسپتال جائیں یا ریسکیو 1122 پر کال کریں۔"),
           "reviewed": "جائزہ لیا", "not_reviewed": "ابھی کسی شخص نے جائزہ نہیں لیا", "page": "صفحہ"},
}


@lru_cache(maxsize=1)
def _pdf_urls() -> dict[str, str]:
    """'2026_W39.pdf' -> the NIH URL it was downloaded from (manifest.csv)."""
    out = {}
    try:
        with open(config.MANIFEST, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["source"] == "nih_idsr" and r["path"].endswith(".pdf"):
                    out[r["path"].rsplit("/", 1)[-1]] = r["query"]
    except FileNotFoundError:
        pass
    return out


def source_link(label: str) -> str:
    """'NIH bulletin · Week 39, 2026 · page 7' -> clickable link to that PDF (page anchor)."""
    m = re.search(r"Week (\d+), (\d{4})(?: · page (\d+))?", label or "")
    if not m:
        return html.escape(label or "")
    url = _pdf_urls().get(f"{m.group(2)}_W{int(m.group(1)):02d}.pdf")
    if not url:
        return html.escape(label)
    anchor = f"#page={m.group(3)}" if m.group(3) else ""
    return f'<a href="{html.escape(url)}{anchor}" target="_blank" rel="noopener">{html.escape(label)} ↗</a>'


def n(x) -> str:
    return "—" if x is None or (isinstance(x, float) and pd.isna(x)) else f"{int(round(float(x))):,}"


def _table(head: list[str], rows: list[list[str]], num_cols: set[int]) -> str:
    th = "".join(f'<th class="{"num" if i in num_cols else ""}">{h}</th>' for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f'<td class="{"num" if i in num_cols else ""}">{c}</td>'
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tbl-wrap"><table class="nice"><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def answer_card(d: dict, title: str | None = None, review: str | None = None) -> str:
    lang = d.get("lang", "en")
    t = L[lang]
    ur = lang == "ur"
    parts = []
    if title:
        parts.append(f'<div class="brief-title">{html.escape(title)}</div>')
    if review is not None:
        parts.append(f'<div class="review {"ok" if review else ""}">{"✔ " + t["reviewed"] + ": " + html.escape(review) if review else "○ " + t["not_reviewed"]}</div>')
    if d.get("status") == "dont_know":
        reasons = "".join(f"<li>{html.escape(m)}</li>" for m in d.get("missing", []))
        parts.append(f'<div class="idk-card"><div class="idk-h">{t["idk"]}</div>'
                     + (f'<div class="idk-why">{t["why"]}:</div><ul class="lat">{reasons}</ul>' if reasons else "")
                     + "</div>")
    if d.get("headline"):
        parts.append(f'<div class="headline">{html.escape(d["headline"])}</div>')
    audit = []
    for b in d.get("blocks", []):
        if b["type"] == "forecast":
            rows, has_range = [], any(r["low"] is not None for r in b["rows"])
            for i, r in enumerate(b["rows"], 1):
                rng = f"{n(r['low'])} – {n(r['high'])}" if r["low"] is not None else "—"
                row = [str(i), html.escape(r["district"]), html.escape(r["province"]), n(r["last_week"]),
                       f"<b>{n(r['expected'])}</b>"] + ([rng] if has_range else []) + [source_link(r["source"])]
                rows.append(row)
                audit.append(f"{r['district']}: forecast {r['forecast_id']} · source row {r['row_id']}")
            head = [t["rank"], t["district"], t["province"], t["last"], t["next"]] + ([t["range"]] if has_range else []) + [t["source"]]
            parts.append(f'<h4>{t["where"]}</h4>' + _table(head, rows, {0, 3, 4, 5} if has_range else {0, 3, 4}))
            parts.append(f'<div class="how">{t["model"] if b["method"] == "model_q50" else t["baseline"]}</div>')
        elif b["type"] == "history":
            rows = []
            for r in b["rows"]:
                rows.append([html.escape(pd.Timestamp(r["week"]).strftime("%d %b %Y")),
                             f"<b>{n(r['cases'])}</b>" if r["cases"] is not None else "—",
                             "" if r["status"] == "reported" else html.escape(r["status"]), source_link(r["source"])])
                audit.append(f"{r['week']}: row {r['row_id']}")
            parts.append(f'<h4>{t["history"]} · {html.escape(b["district"])}</h4>'
                         + _table([t["week"], t["cases"], t["note"], t["source"]], rows, {1}))
        elif b["type"] == "status":
            items = "".join(f"<li>{html.escape(k)}: {html.escape(str(v['newest'])[:16])} "
                            f"({'late' if v['late'] else 'on time'})</li>" for k, v in b["freshness"].items())
            parts.append(f'<h4>{t["status"]}</h4><ul class="lat">{items}<li>last successful run: '
                         f'{html.escape(str(b.get("last_good") or "")[:16].replace("T", " "))} UTC</li></ul>')
        elif b["type"] == "guidance":
            cards = "".join(
                f'<div class="quote"><div class="q-text">“{html.escape(g["quote"])}”</div>'
                f'<div class="q-src">— <a href="{html.escape(g["url"])}" target="_blank" rel="noopener">'
                f'{html.escape(g["title"])}</a>, {t["page"]} {g["page"]}</div></div>' for g in b["items"])
            parts.append(f'<h4>{t["guidance"]}</h4><div class="lat">{cards}</div>')
    if audit:
        lis = "".join(f"<li>{html.escape(x)}</li>" for x in audit)
        parts.append(f'<details class="audit"><summary>{t["audit"]}</summary><ul>{lis}</ul></details>')
    parts.append(f'<div class="emergency">⚕ {t["emergency"]}</div>')
    return f'<div class="answer{" urdu" if ur else ""}" dir="{"rtl" if ur else "ltr"}">{"".join(parts)}</div>'
