"""What-if scenarios: "what if rain in the coming weeks is X% higher or lower?"

Takes the latest feature row of a district, scales every rainfall feature (sum, rain hours, lags,
rolling mean) by (1 + change), and re-predicts with the saved quantile models. The output is a
SCENARIO, not a forecast: it shows how sensitive the model is to rain, nothing more. Only offered when
the model (not the baseline) is served, because the baseline ignores weather.
"""
from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd

from .. import config
from . import features, train

RAIN_COLS = ("precip_sum_mm", "rain_hours")


def available(disease: str) -> bool:
    p = config.ARTIFACTS / f"model_{disease}" / "metrics.json"
    if not p.exists() or not (config.ARTIFACTS / f"model_{disease}" / "q50.txt").exists():
        return False
    m = json.loads(p.read_text(encoding="utf-8"))
    # a rain scenario only means something if the served model actually uses rainfall
    return m.get("served_method", "model") == "model" and any("precip" in f for f in m.get("features", []))


def scenario(disease: str, district: str, rain_change: float) -> pd.DataFrame:
    meta = json.loads((config.ARTIFACTS / f"model_{disease}" / "metrics.json").read_text(encoding="utf-8"))
    cols = meta["features"]
    models = {q: lgb.Booster(model_file=str(config.ARTIFACTS / f"model_{disease}" / f"q{int(q * 100)}.txt"))
              for q in config.QUANTILES}
    df = features.build(train.load_gold(), disease)
    row = df[(df["district"] == district) & df["last_value"].notna()].sort_values("week_start").tail(1)
    if row.empty:
        return pd.DataFrame()
    X = row[cols].copy()
    base = train.predict(models, X).iloc[0]
    rain = [c for c in cols if c.startswith(RAIN_COLS)]
    X[rain] = X[rain] * (1 + rain_change)
    scen = train.predict(models, X).iloc[0]
    week = (row["week_start"].iloc[0] + pd.Timedelta(days=7)).strftime("%d %b %Y")
    return pd.DataFrame({
        "": ["Model forecast (actual rain)", f"Scenario: rain {rain_change:+.0%}"],
        "Week of": [week, week],
        "Expected": [f"{base['q50']:,.0f}", f"{scen['q50']:,.0f}"],
        "Likely range (8 in 10)": [f"{base['q10']:,.0f} – {base['q90']:,.0f}", f"{scen['q10']:,.0f} – {scen['q90']:,.0f}"],
        "Change": ["", f"{(scen['q50'] - base['q50']) / max(base['q50'], 1):+.1%}"],
    })
