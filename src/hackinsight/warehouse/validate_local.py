"""Local validation: our parsed NIH national dengue totals vs OpenDengue v1.3 (monthly, Pakistan).

OpenDengue compiled its Pakistan series from published national reports, so this is a check that
our PDF parser reproduces an independent team's numbers - not an independent ground truth.
A month is compared only where both exist; months where we are missing a bulletin week are
reported as such (they explain most differences).
"""
from __future__ import annotations

import json

from .. import config

SQL = """
WITH od AS (
    SELECT date_trunc('month', calendar_start_date) AS month, sum(dengue_total) AS opendengue
    FROM cleaned.opendengue_pak WHERE T_res = 'Month' AND S_res = 'Admin0' GROUP BY 1),
nih AS (
    SELECT date_trunc('month', week_start + INTERVAL 3 DAY) AS month, sum(cases) AS nih_parsed,
           count(*) AS nih_weeks
    FROM gold.province_week WHERE disease = 'dengue' AND province = 'Pakistan' GROUP BY 1)
SELECT od.month, od.opendengue, nih.nih_parsed, nih.nih_weeks
FROM od JOIN nih USING (month) ORDER BY 1
"""


def run(con) -> dict:
    df = con.execute(SQL).df()
    if df.empty:
        return {"months": 0}
    df["exact"] = df["opendengue"] == df["nih_parsed"]
    # an ISO week is counted in the month that holds its Thursday; a month has 4 or 5 such weeks
    df["missing_week"] = df["nih_weeks"] < 4
    res = {
        "what": "NIH IDSR national dengue (parsed by us) vs OpenDengue v1.3 monthly Pakistan totals",
        "months": len(df),
        "exact_match_months": int(df["exact"].sum()),
        "mismatch_months_with_missing_bulletin_week": int((~df["exact"] & df["missing_week"]).sum()),
        "mismatch_months_unexplained": int((~df["exact"] & ~df["missing_week"]).sum()),
        "correlation": round(float(df["opendengue"].corr(df["nih_parsed"])), 4),
        "rows": [{**r, "month": str(r["month"])[:7]} for r in df.to_dict("records")],
    }
    (config.ARTIFACTS / "local_validation.json").write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    return res
