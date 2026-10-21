"""Insight Agent: answers a district health officer's question with cited evidence.

    question -> parse (disease, place, intent) -> tools:
        sql_forecast / sql_history / sql_status   (numbers, each with its gold row_id)
        retrieve                                  (official guidance passages, each with doc + page)
    -> evidence bundle -> wording (template, or LLM with placeholders) -> checks -> answer

Hard rules, enforced in code (not just asked of the model):
  1. No number from the LLM: the LLM only sees placeholders like {N1}; code fills in the values.
     Any digit in LLM text that is not a placeholder -> wording rejected, template used instead.
  2. No claim without a source: advice text is either a verbatim quote of a retrieved passage or an
     LLM sentence that must carry a [S#] marker pointing at a retrieved passage.
  3. Unsupported -> "I don't know", plus what is missing.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

import duckdb
import pandas as pd


from .. import config
from .index import Retriever

LLM_MODEL = os.environ.get("HI_LLM_MODEL", "claude-opus-5-5")
PROMPT_VERSION = "insight_wording_v1"     # prompts/<version>.txt; recorded on every answer
MIN_DENSE = 0.40      # below this cosine similarity a passage is "not about the question"

DISEASE_WORDS = {"dengue": ["dengue", "ڈینگی", "ڈینگو"], "malaria": ["malaria", "ملیریا"]}
INTENTS = {
    "forecast": ["next week", "forecast", "expect", "predict", "risk", "which district", "highest", "top",
                 "where should", "send", "اگلے ہفتے", "خطرہ", "پیشگوئی", "کس ضلع", "زیادہ"],
    "history": ["last week", "trend", "how many", "cases in", "reported", "past", "increase", "rising",
                "tripled", "doubled", "کتنے", "کیسز", "رجحان", "پچھلے"],
    "advice": ["prevent", "what should", "advice", "recommend", "control", "do about", "measure", "fumigat",
               "protect", "symptom", "treat", "احتیاط", "کیا کرنا", "بچاؤ", "علامات", "سفارش"],
    "status": ["fresh", "latest data", "updated", "quality", "stale", "last run", "تازہ"],
    "model": ["accurate", "accuracy", "how good", "how reliable", "reliable", "error", "baseline", "model card",
              "درست", "ماڈل"],
}

EMERGENCY = {
    "en": "This is information, not medical advice. If someone has high fever with bleeding, severe stomach pain, "
          "repeated vomiting or drowsiness, go to the nearest hospital now or call Rescue 1122.",
    "ur": "یہ معلومات ہیں، طبی مشورہ نہیں۔ اگر کسی کو تیز بخار کے ساتھ خون آنا، پیٹ میں شدید درد، بار بار قے یا "
          "غنودگی ہو تو فوراً قریبی ہسپتال جائیں یا ریسکیو 1122 پر کال کریں۔",
}
T = {
    "en": {"dont_know": "I don't know.", "missing": "What is missing", "sources": "Sources", "rows": "Data rows used",
           "fc_head": "Expected suspected {d} cases for the week starting {w}:",
           "hist_head": "Reported suspected {d} cases in {p} (NIH IDSR bulletins):",
           "advice_head": "What official guidance says:", "status_head": "Data status:",
           "assume_d": "No disease named, so I used {d}.", "missing_val": "no value ({s})"},
    "ur": {"dont_know": "مجھے معلوم نہیں۔", "missing": "کیا موجود نہیں", "sources": "حوالہ جات",
           "rows": "استعمال شدہ ڈیٹا قطاریں",
           "fc_head": "ہفتہ {w} کے لیے {d} کے متوقع مشتبہ کیسز:",
           "hist_head": "{p} میں {d} کے رپورٹ شدہ مشتبہ کیسز (این آئی ایچ بلیٹن):",
           "advice_head": "سرکاری رہنمائی کیا کہتی ہے (انگریزی اقتباس):", "status_head": "ڈیٹا کی صورتحال:",
           "assume_d": "بیماری کا نام نہیں دیا گیا، اس لیے {d} استعمال کیا۔", "missing_val": "کوئی قدر نہیں ({s})"},
}
DISEASE_UR = {"malaria": "ملیریا", "dengue": "ڈینگی"}

# Questions about things this system has no data on. Answering them would invent claims about
# hospital capacity, medicine stocks or fumigation needs, so they get "I don't know" + where to ask.
OUT_OF_SCOPE = re.compile(
    r"\bbeds?\b|hospital capacity|\bicu\b|ventilator|occupancy|shortage|\bin stock\b|\bstocks?\b|\bsupplies\b|"
    r"supply of|(?:medicine|drug)s? (?:available|availability)|availability of (?:medicine|drug)|"
    r"(?:fumigat\w*|spray\w*|fogging)\b.*\b(?:need|needed|required|requirement|how many|how much)\b|"
    r"\b(?:how many|how much)\b.*\b(?:fumigat\w*|spray\w*|fogging)|بستر")
OUTBREAK = re.compile(r"outbreak|epidemic|وبا")
NOTE = {
    "en": {"outbreak": ("This system does not declare outbreaks: there is no validated outbreak threshold for these "
                        "suspected-case counts. Whether there is an outbreak is decided by the provincial health "
                        "department or NIH after investigation. The Priority screen lists districts that are rising or "
                        "above their usual level for the season, which means 'look closer', not 'outbreak'."),
           "scope": ("hospital beds, medicine stocks and fumigation needs are not in this system's data (NIH IDSR "
                     "bulletins count suspected cases only); ask the district health office or the provincial health "
                     "department")},
    "ur": {"outbreak": ("یہ نظام وبا کا اعلان نہیں کرتا: ان مشتبہ کیسز کے لیے وبا کی کوئی تصدیق شدہ حد موجود نہیں۔ وبا ہے یا "
                        "نہیں، یہ صوبائی محکمہ صحت یا این آئی ایچ تحقیق کے بعد طے کرتے ہیں۔ ترجیحی اضلاع کی فہرست کا مطلب "
                        "'مزید دیکھیں' ہے، 'وبا' نہیں۔"),
           "scope": ("ہسپتال کے بستر، ادویات کا ذخیرہ اور اسپرے کی ضرورت اس نظام کے ڈیٹا میں نہیں (این آئی ایچ بلیٹن صرف "
                     "مشتبہ کیسز گنتے ہیں)؛ ضلعی محکمہ صحت یا صوبائی محکمہ صحت سے رابطہ کریں")},
}
STOP = {"the", "a", "an", "and", "or", "of", "in", "on", "for", "to", "what", "which", "how", "is", "are", "do", "does",
        "should", "can", "i", "we", "my", "our", "this", "that", "with", "about", "there", "be", "it", "next", "week"}


@dataclass
class Answer:
    question: str
    lang: str
    status: str = "answered"                 # answered | dont_know
    lines: list[str] = field(default_factory=list)
    numbers: list[dict] = field(default_factory=list)    # {key, value, row_id, table}
    citations: list[dict] = field(default_factory=list)  # {sid, doc_id, title, page, url, quote}
    missing: list[str] = field(default_factory=list)
    wording: str = "template"
    prompt_version: str = PROMPT_VERSION
    pii_masked: list[str] = field(default_factory=list)
    headline: str = ""                                    # one plain-language sentence for a layperson
    blocks: list[dict] = field(default_factory=list)     # structured sections the dashboard renders

    def render(self) -> str:
        t = T[self.lang]
        out = list(self.lines)
        if self.status == "dont_know":
            out.insert(0, t["dont_know"])
            if self.missing:
                out.append(f"{t['missing']}: " + "; ".join(self.missing))
        if self.citations:
            out.append(f"\n{t['sources']}:")
            out += [f"  [{c['sid']}] {c['title']} - {c.get('publisher', '')} - p.{c['page']} ({c['url']})"
                    for c in self.citations]
        if self.numbers:
            out.append(f"{t['rows']}: " + ", ".join(sorted({n['row_id'] for n in self.numbers})))
        out.append("\n" + EMERGENCY[self.lang])
        return "\n".join(out)

    def to_dict(self) -> dict:
        return {"question": self.question, "lang": self.lang, "status": self.status, "text": self.render(),
                "numbers": self.numbers, "citations": self.citations, "missing": self.missing,
                "wording": self.wording, "headline": self.headline, "blocks": self.blocks,
                "prompt_version": self.prompt_version, "pii_masked": self.pii_masked}


def _tok(s: str) -> str:
    return " ".join(re.findall(r"[a-z]+", str(s).lower()))


# Urdu question words -> English search terms, so retrieval over English guidance works (the
# multilingual embedding alone scores Urdu-to-English passages too low to pass the threshold).
UR_EXPAND = {"ڈینگی": "dengue", "ڈینگو": "dengue", "ملیریا": "malaria", "مچھر": "mosquito", "بچاؤ": "prevention prevent",
             "احتیاط": "precautions prevention", "علامات": "symptoms warning signs", "علاج": "treatment",
             "سیلاب": "floods", "بارش": "rain monsoon", "پانی": "water", "بخار": "fever", "اسپرین": "aspirin"}
HEALTH_WORDS = {"dengue", "malaria", "mosquito", "fever", "vector", "larva", "larvae", "breeding", "fumigation",
                "spray", "net", "nets", "repellent", "aspirin", "paracetamol", "symptom", "symptoms", "treatment",
                "hospital", "case", "cases", "outbreak", "monsoon", "flood", "floods", "prevent", "prevention",
                "control", "district", "rdt", "test", "bleeding", "platelet", "chikungunya", "health", "surveillance"}


STATUS_LABEL = {
    "en": {"not_listed": "not in that week's top-disease list", "not_reported": "district did not report (NR)",
           "no_bulletin": "no bulletin that week", "district_not_in_table": "district missing from the table",
           "unparseable": "value unreadable in the PDF"},
    "ur": {"not_listed": "اس ہفتے کی فہرست میں شامل نہیں", "not_reported": "ضلع نے رپورٹ نہیں کیا",
           "no_bulletin": "اس ہفتے بلیٹن نہیں", "district_not_in_table": "ضلع جدول میں نہیں",
           "unparseable": "پی ڈی ایف میں پڑھا نہیں جا سکا"},
}


def source_label(source_file, page) -> str:
    """'2026_W39.pdf', 7 -> 'NIH bulletin · Week 39, 2026 · page 7' (what a person can open and check)."""
    m = re.match(r"(\d{4})_W(\d{2})", str(source_file or ""))
    if not m:
        return str(source_file or "")
    pg = f" · page {int(page)}" if page is not None and not pd.isna(page) else ""
    return f"NIH bulletin · Week {int(m.group(2))}, {m.group(1)}{pg}"


def fmt(x) -> str:
    return f"{int(round(float(x))):,}"


class _Result:
    def __init__(self, df: pd.DataFrame):
        self._df = df

    def df(self) -> pd.DataFrame:
        return self._df

    def fetchall(self) -> list[tuple]:
        return list(self._df.itertuples(index=False, name=None))

    def fetchone(self):
        rows = self.fetchall()
        return rows[0] if rows else None


class ShortConnection:
    """DuckDB allows one writing process at a time. The hourly pipeline writes the warehouse, so the
    agent (and the dashboard) never keep a connection open: each query opens read-only, reads, closes."""

    def __init__(self, path: str):
        self.path = path

    def execute(self, sql: str, args: list | None = None, retries: int = 5) -> _Result:
        import time
        for i in range(retries):
            try:
                with duckdb.connect(self.path, read_only=True) as c:
                    return _Result(c.execute(sql, args or []).df())
            except duckdb.IOException:          # the pipeline is writing right now
                if i == retries - 1:
                    raise WarehouseBusy("the hourly pipeline is updating the data; try again in a minute")
                time.sleep(2)

    def close(self) -> None:   # connections are already closed after every query
        pass


class WarehouseBusy(RuntimeError):
    pass


class InsightAgent:
    def __init__(self, use_llm: bool | None = None):
        self.con = ShortConnection(str(config.WAREHOUSE))
        self.retriever = Retriever()
        names = self.con.execute("SELECT DISTINCT district, province FROM gold.district_week").df()
        self.districts = dict(zip(names["district"], names["province"]))
        # bulletin spellings ("Karachi East", "Mirpurkhas") also resolve to the OCHA district name
        sp = self.con.execute("""SELECT DISTINCT place, district FROM gold.district_week
                                 WHERE place IS NOT NULL AND place NOT LIKE '%+%'""").df()
        self.name_lookup = {_tok(d): d for d in self.districts}
        self.name_lookup.update({_tok(p): d for p, d in zip(sp["place"], sp["district"])})
        self.provinces = sorted(set(self.districts.values()))
        self.use_llm = (os.environ.get("ANTHROPIC_API_KEY") is not None) if use_llm is None else use_llm

    # ------------------------------------------------------------------ parsing
    def parse(self, q: str) -> dict:
        ql = q.lower()
        disease = next((d for d, ws in DISEASE_WORDS.items() if any(w in ql for w in ws)), None)
        intents = [k for k, ws in INTENTS.items() if any(w in ql for w in ws)]
        # a district is mentioned when all of its words appear in the question (any order, any case)
        qwords = set(re.findall(r"[a-z]+", ql))
        hits = [(len(k), d) for k, d in self.name_lookup.items() if k and set(k.split()) <= qwords]
        district = max(hits)[1] if hits else None
        province = next((p for p in self.provinces if p.lower() in ql or (p == "Khyber Pakhtunkhwa" and " kp" in f" {ql}")), None)
        # a place-like capitalised word we do not know (e.g. "Lahore") -> remember it to say we lack data
        unknown_place = None
        if not district:
            for w in re.findall(r"\b(?:in|for|at|of)\s+([A-Z][a-z]{3,}(?:\s[A-Z][a-z]+)?)", q):
                if w not in self.provinces and w.lower() not in ("pakistan", "malaria", "dengue"):
                    unknown_place = w
        return {"disease": disease, "intents": intents, "district": district, "province": province,
                "unknown_place": unknown_place}

    # ------------------------------------------------------------------ tools
    def sql_forecast(self, disease, district=None, province=None, top=5) -> pd.DataFrame:
        sql = (f"SELECT f.*, d.source_file, d.page FROM gold.forecast_{disease} f "
               f"LEFT JOIN gold.district_week d ON d.row_id = f.based_on_row_id WHERE 1=1")
        args = []
        if district:
            sql += " AND f.district = ?"
            args.append(district)
        if province:
            sql += " AND f.province = ?"
            args.append(province)
        sql += " ORDER BY f.q50 DESC LIMIT ?"
        args.append(top)
        return self.con.execute(sql, args).df()

    def sql_history(self, disease, district, weeks=6) -> pd.DataFrame:
        return self.con.execute("""SELECT row_id, district, province, week_start, cases, cases_status, source_file, page
                                   FROM gold.district_week WHERE disease = ? AND district = ?
                                   ORDER BY week_start DESC LIMIT ?""", [disease, district, weeks]).df()

    def sql_status(self) -> dict:
        p = config.ARTIFACTS / "status.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    # ------------------------------------------------------------------ main
    def answer(self, question: str, lang: str = "en", guidance_only: bool = False,
               guidance_query: str | None = None) -> Answer:
        from .pii import mask
        question, pii_found = mask(question)      # names/phones/CNICs never reach a log or an LLM
        lang = "ur" if lang == "ur" or re.search(r"[؀-ۿ]", question) else "en"
        t = T[lang]
        a = Answer(question, lang)
        a.pii_masked = pii_found
        p = self.parse(question)
        disease = p["disease"] or config.PRIMARY_DISEASE
        dname = DISEASE_UR[disease] if lang == "ur" else disease
        intents = p["intents"] or []
        if "model" in intents and "next week" not in question.lower():
            intents = [i for i in intents if i != "forecast"]   # "how accurate is the forecast" is about the model

        if re.search(r"\bdeath|\bdied|mortality|fatalit|اموات|ہلاک", question.lower()):
            a.status = "dont_know"
            a.missing.append("deaths: the IDSR district tables count suspected cases only, not deaths; "
                             "ask the provincial health department for mortality figures")
            return a
        if OUT_OF_SCOPE.search(question.lower()):
            a.status = "dont_know"
            a.missing.append(NOTE[lang]["scope"])
            return a
        if p["unknown_place"] and not p["district"]:
            a.status = "dont_know"
            a.missing.append(self._unknown_place_reason(p["unknown_place"]))
            return a
        outbreak_q = bool(OUTBREAK.search(question.lower()))
        if outbreak_q:
            a.lines.append(NOTE[lang]["outbreak"])
            a.blocks.append(dict(type="note", text=NOTE[lang]["outbreak"]))
            if p["district"] and "history" not in intents:
                intents = intents + ["history"]
        if not p["disease"] and ({"forecast", "history"} & set(intents)):
            a.lines.append(t["assume_d"].format(d=dname))

        if "forecast" in intents:
            fc = self.sql_forecast(disease, p["district"], p["province"] if not p["district"] else None)
            if len(fc):
                a.lines.append(t["fc_head"].format(d=dname, w=fc["forecast_week"].iloc[0].date()))
                if fc["method"].iloc[0] != "model_q50":
                    a.lines.append("  (the model did not beat the baseline for this disease on validation, so this is "
                                   "the baseline: last reported week; the range is how far that rule missed before)")
                rows_out = []
                for r in fc.itertuples():
                    tab = f"gold.forecast_{disease}"
                    rows_out.append(dict(district=r.district, province=r.province, last_week=r.last_value,
                                         expected=r.q50, low=r.q10 if pd.notna(r.q10) else None,
                                         high=r.q90 if pd.notna(r.q90) else None,
                                         source=source_label(r.source_file, r.page), forecast_id=r.forecast_id,
                                         row_id=r.based_on_row_id))
                    a.numbers.append(dict(key=f"N{len(a.numbers)+1}", value=r.q50, row_id=r.forecast_id, table=tab))
                    if pd.notna(r.q10):
                        a.numbers += [dict(key=f"N{len(a.numbers)+1}", value=r.q10, row_id=r.forecast_id, table=tab),
                                      dict(key=f"N{len(a.numbers)+2}", value=r.q90, row_id=r.forecast_id, table=tab)]
                        rng = f" ({fmt(r.q10)}-{fmt(r.q90)})"
                    else:
                        rng = ""
                    a.lines.append(f"  - {r.district} ({r.province}): {fmt(r.q50)}{rng}"
                                   f"  [{r.forecast_id}; based on {r.based_on_row_id}]")
                week = fc["forecast_week"].iloc[0]
                a.blocks.append(dict(type="forecast", disease=disease, week=str(week.date()),
                                     method=fc["method"].iloc[0], rows=rows_out))
                top = rows_out[0]
                a.headline = (f"{top['district']} is expected to have the most suspected {disease} cases in the week of "
                              f"{week:%d %b %Y}: about {fmt(top['expected'])}." if lang == "en" else
                              f"ہفتہ {week:%d-%m-%Y} میں سب سے زیادہ مشتبہ {dname} کیسز {top['district']} میں متوقع ہیں: "
                              f"تقریباً {fmt(top['expected'])}۔")
            else:
                where = p["district"] or p["province"] or "that place"
                a.missing.append(f"no {disease} forecast for {where}: recent bulletins do not list {disease} among "
                                 f"its most frequent diseases, so there is no reliable recent count to forecast from")

        if "history" in intents or ("forecast" not in intents and p["district"] and "advice" not in intents):
            if p["district"]:
                h = self.sql_history(disease, p["district"])
                if len(h):
                    a.lines.append(t["hist_head"].format(d=dname, p=p["district"]))
                    hb = dict(type="history", disease=disease, district=p["district"], rows=[])
                    a.blocks.append(hb)
                    for r in h.itertuples():
                        hb["rows"].append(dict(week=str(r.week_start.date()),
                                               cases=None if pd.isna(r.cases) else int(r.cases),
                                               status=STATUS_LABEL[lang].get(r.cases_status, r.cases_status),
                                               source=source_label(r.source_file, r.page), row_id=r.row_id))
                        if pd.notna(r.cases):
                            a.numbers.append(dict(key=f"N{len(a.numbers)+1}", value=r.cases, row_id=r.row_id,
                                                  table="gold.district_week"))
                            val = fmt(r.cases)
                        else:
                            val = t["missing_val"].format(s=r.cases_status)
                        pg = f" p.{int(r.page)}" if pd.notna(r.page) else ""
                        a.lines.append(f"  - {r.week_start.date()}: {val}  [{r.row_id}; {r.source_file}{pg}]")
                    rep = [x for x in hb["rows"] if x["cases"] is not None]
                    if rep and not a.headline:
                        wk = pd.Timestamp(rep[0]["week"])
                        a.headline = (f"{p['district']} reported {rep[0]['cases']:,} suspected {disease} cases in the "
                                      f"week of {wk:%d %b %Y}." if lang == "en" else
                                      f"{p['district']} میں ہفتہ {wk:%d-%m-%Y} کو {rep[0]['cases']:,} مشتبہ {dname} "
                                      f"کیسز رپورٹ ہوئے۔")
            else:
                a.missing.append("which district (history is shown per district)")

        if "status" in intents:
            st = self.sql_status()
            a.lines.append(t["status_head"])
            a.blocks.append(dict(type="status", freshness=st.get("freshness", {}),
                                 last_good=(st.get("last_good") or {}).get("finished_utc")))
            for src, f in st.get("freshness", {}).items():
                a.lines.append(f"  - {src}: newest {f['newest']} ({'late' if f['late'] else 'on time'})")
            lr = st.get("last_good") or {}
            a.lines.append(f"  - last successful run: {lr.get('finished_utc', 'unknown')} UTC")

        # search query: the question + English terms for Urdu words
        expansion = " ".join(v for k, v in UR_EXPAND.items() if k in question)
        query = guidance_query or f"{question} {expansion}".strip()
        on_topic = bool(set(re.findall(r"[a-z]+", query.lower())) & HEALTH_WORDS)
        need_guidance = ("advice" in intents or "model" in intents or (not intents and not outbreak_q)) and on_topic
        if not intents and not on_topic and not outbreak_q:
            a.missing.append("the question is outside this system's scope (malaria/dengue surveillance and "
                             "official prevention guidance)")
        if need_guidance:
            hits = self.retriever.search(query, k=8 if guidance_only else 4)
            if guidance_only:   # briefs quote official guidance only, not weekly bulletin narrative
                hits = hits[hits["kind"] == "guidance"]
            elif "model" in intents:   # accuracy questions are answered from the model card
                # the document is known (this disease's model card): take it directly, accuracy part first
                ch = self.retriever.chunks
                hits = ch[(ch["kind"] == "model_card") & (ch["doc_id"] == f"MODEL-CARD-{disease}")].copy()
                hits = hits.assign(dense_score=1.0, bm25_score=10.0,
                                   _acc=hits["text"].str.contains("Test MAE")).sort_values("_acc", ascending=False)
            # relevant = semantically close, or a strong keyword hit that is also semantically related
            hits = hits[(hits["dense_score"] >= MIN_DENSE) | ((hits["bm25_score"] > 4) & (hits["dense_score"] >= 0.30))]
            if p["disease"] and len(hits):   # a dengue question must not be answered from a malaria-only document
                other = [d for d in DISEASE_WORDS if d != p["disease"]]
                title = hits["title"].str.lower()
                hits = hits[~(title.str.contains("|".join(other)) & ~title.str.contains(p["disease"]))]
            terms = {w for w in re.findall(r"[a-z]+", query.lower()) if w not in STOP and len(w) > 2}
            if len(hits):
                a.lines.append(t["advice_head"])
                gb = dict(type="guidance", items=[])
                a.blocks.append(gb)
                for i, r in enumerate(hits.head(3).itertuples(), start=1):
                    acc = re.findall(r"- (?:Validation|Test|80%)[^\n]*", r.text) if r.kind == "model_card" else []
                    quote = " ".join(x[2:] for x in acc) if acc else _best_sentences(r.text, terms, 2)
                    sid = f"S{i}"
                    a.citations.append(dict(sid=sid, doc_id=r.doc_id, title=r.title, publisher=r.publisher,
                                            page=int(r.page), url=r.url, quote=quote, chunk_id=r.chunk_id))
                    a.lines.append(f'  - "{quote}" [{sid}]')
                    gb["items"].append(dict(sid=sid, quote=quote, title=r.title, publisher=r.publisher,
                                            page=int(r.page), url=r.url))
            else:
                a.missing.append("no passage in the trusted library (NIH advisories/guidelines, WHO fact sheets, "
                                 "IDSR bulletins) supports an answer to this question")

        if not a.numbers and not a.citations and "status" not in intents:
            a.status = "dont_know"
            return a
        if self.use_llm and a.citations:
            self._llm_rewrite(a)
        return a

    def _unknown_place_reason(self, place: str) -> str:
        """Explain precisely why a named place has no numbers, instead of one generic sentence."""
        key = place.lower()
        parts = sorted(d for d in self.districts if key in d.lower())
        if parts:   # e.g. "Karachi" is split into several districts in the tables
            return (f"'{place}' is not one district in the NIH IDSR tables; it is reported as {', '.join(parts)}. "
                    "Please ask about one of these")
        return (f"'{place}' is not among the {len(self.districts)} districts of Sindh, Balochistan and Khyber "
                "Pakhtunkhwa in the NIH IDSR district tables (Punjab and Islamabad districts are reported as NR), "
                "so there are no counts for it")

    # ------------------------------------------------------------------ optional LLM wording
    def _llm_rewrite(self, a: Answer) -> None:
        """Ask the LLM for 2-3 plain-language action points from the cited passages only.
        It never sees real numbers. Output is accepted only if every sentence has a [S#] marker
        that exists and the text contains no digits at all."""
        try:
            import anthropic
        except ImportError:
            return
        passages = "\n\n".join(f"[{c['sid']}] ({c['title']}, p.{c['page']}): {c['quote']}" for c in a.citations)
        lang = "Urdu (Nastaliq script)" if a.lang == "ur" else "plain English"
        system = (config.ROOT / "prompts" / f"{PROMPT_VERSION}.txt").read_text(encoding="utf-8").strip()
        try:
            client = anthropic.Anthropic()
            resp = client.beta.messages.create(
                model=LLM_MODEL, max_tokens=1024, system=system,
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                messages=[{"role": "user", "content": f"Write in {lang}.\n\nQuestion: {a.question}\n\n{passages}"}],
            )
        except Exception as e:  # network/auth problems must never break an answer
            a.wording = f"template (llm unavailable: {type(e).__name__})"
            return
        if resp.stop_reason == "refusal":
            a.wording = "template (llm refused)"
            return
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        if not text or text == "NONE":
            return
        points = [s.strip() for s in text.splitlines() if s.strip()]
        if validate_llm_text(text, {c["sid"] for c in a.citations}):
            a.lines.append("Summary:" if a.lang == "en" else "خلاصہ:")
            a.lines += [f"  {s}" for s in points]
            a.wording = f"llm:{LLM_MODEL} (validated: no digits, every point cited)"
        else:
            a.wording = "template (llm output rejected by number/citation check)"


def validate_llm_text(text: str, sids: set[str]) -> bool:
    """Accept LLM wording only if it has no digits (Latin, Urdu or Arabic-Indic), every line carries
    a [S#] marker, and every marker points at a passage we actually retrieved."""
    points = [s.strip() for s in text.splitlines() if s.strip()]
    unmarked = re.sub(r"\[S\d+\]", "", text)
    return bool(points) and (not re.search(r"[0-9۰-۹٠-٩]", unmarked)
                             and all(re.search(r"\[S\d\]", s) for s in points)
                             and set(re.findall(r"\[(S\d)\]", text)) <= sids)


def _best_sentences(text: str, terms: set[str], n: int) -> str:
    """Quote the n sentences of a passage that share the most words with the question (kept in their
    original order), so a passage is not quoted by an unrelated opening line."""
    s = re.sub(r"[-]", " ", text)
    s = re.sub(r"\s+", " ", s).strip()
    parts = [x for x in re.split(r"(?<=[.!?])\s+", s) if len(x) > 25]
    # a chunk window can start mid-word ("d control The mosquitoes ..."): drop the fragment
    parts = [re.sub(r"^[a-z][^A-Z]{0,40}?(?=[A-Z])", "", x) if x[:1].islower() else x for x in parts]
    if not terms or len(parts) <= n:
        return _first_sentences(text, n)
    score = [len(terms & set(re.findall(r"[a-z]+", x.lower()))) for x in parts]
    if max(score) == 0:
        return _first_sentences(text, n)
    keep = sorted(sorted(range(len(parts)), key=lambda i: -score[i])[:n])
    out = " ".join(parts[i] for i in keep)
    return out[:400] + ("..." if len(out) > 400 else "")


def _first_sentences(text: str, n: int) -> str:
    # PDF bullet glyphs arrive as private-use characters (e.g. U+F0B7) and render as boxes
    s = re.sub(r"[-]", " ", text)
    s = re.sub(r"\s+", " ", s).strip()
    parts = re.split(r"(?<=[.!?])\s+", s)
    # a chunk window can start mid-sentence ("agement of dengue ..."): start at the first full sentence
    if len(parts) > n and (parts[0][:1].islower() or re.match(r"^[^\s(]*\)", parts[0])):
        parts = parts[1:]
    out = " ".join(parts[:n])
    return out[:400] + ("..." if len(out) > 400 else "")
