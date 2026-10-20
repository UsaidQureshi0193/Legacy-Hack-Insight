"""Golden evaluation of the Insight Agent on real data (no synthetic records).

For each question in tests/golden_qa.jsonl:
  * refusal correctness - answered when it should, "I don't know" when it should
  * citation accuracy   - for advice questions, a cited document is one of the expected ones
  * number faithfulness - every number in the answer is re-read from the gold table by its id
                          and must match exactly
"""
from __future__ import annotations

import json

import pandas as pd

from .. import config
from .agent import InsightAgent

GOLDEN = config.ROOT / "tests" / "golden_qa.jsonl"


def _check_numbers(agent: InsightAgent, numbers: list[dict]) -> tuple[int, int]:
    ok = 0
    for n in numbers:
        if n["table"] == "gold.district_week":
            v = agent.con.execute("SELECT cases FROM gold.district_week WHERE row_id = ?", [n["row_id"]]).fetchone()
            ok += int(v is not None and float(v[0]) == float(n["value"]))
        else:
            v = agent.con.execute(f"SELECT q10, q50, q90 FROM {n['table']} WHERE forecast_id = ?",
                                  [n["row_id"]]).fetchone()
            ok += int(v is not None and any(x is not None and abs(float(x) - float(n["value"])) < 1e-9 for x in v))
    return ok, len(numbers)


def run() -> dict:
    agent = InsightAgent(use_llm=False)
    rows = []
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        g = json.loads(line)
        a = agent.answer(g["question"], lang=g["lang"])
        got = "answer" if a.status == "answered" else "dont_know"
        cited = [c["doc_id"] for c in a.citations]
        cite_ok = None
        if g.get("cite_any"):
            cite_ok = any(any(d.startswith(p) for p in g["cite_any"]) for d in cited)
        n_ok, n_all = _check_numbers(agent, a.numbers)
        rows.append({"id": g["id"], "lang": g["lang"], "expect": g["expect"], "got": got,
                     "refusal_correct": got == g["expect"], "citation_ok": cite_ok,
                     "numbers_needed": bool(g.get("needs_numbers")), "numbers": n_all, "numbers_faithful": n_ok,
                     "cited": ";".join(cited[:3]), "missing": "; ".join(a.missing)})
    df = pd.DataFrame(rows)
    cit = df["citation_ok"].dropna()
    summary = {
        "n": len(df),
        "refusal_accuracy": round(float(df["refusal_correct"].mean()), 3),
        "citation_accuracy": round(float(cit.astype(bool).mean()), 3) if len(cit) else None,
        "number_faithfulness": round(float(df["numbers_faithful"].sum() / max(df["numbers"].sum(), 1)), 3),
        "numbers_checked": int(df["numbers"].sum()),
        "numbers_present_when_needed": round(float((df.loc[df["numbers_needed"], "numbers"] > 0).mean()), 3),
    }
    rep = {"summary": summary, "items": rows}
    (config.ARTIFACTS / "eval_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return rep
