"""Feature table for "suspected cases next week" per district.

Row = (district, week t). Target = cases in week t+1. Every feature uses information up to and
including week t only, so nothing from the future leaks into training.

Why these features:
  * lags / rolling means of cases  - outbreaks are persistent; last week's level is the strongest signal
  * rain, humidity, temperature 1-4 weeks back - mosquitoes need standing water and warmth, and
    cases follow rain with a delay of a few weeks (breeding + incubation)
  * week of year (sin/cos)          - seasonality (post-monsoon peak) without a cliff at week 52 -> 1
  * province, poverty, population   - structural differences in reporting and risk
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WEATHER = ["precip_sum_mm", "temp_mean_c", "rh_mean_pct", "rain_hours"]
PROVINCE_CODES = {"Sindh": 0, "Balochistan": 1, "Khyber Pakhtunkhwa": 2, "Punjab": 3, "Islamabad": 4,
                  "Azad Kashmir": 5, "Gilgit-Baltistan": 6}


def build(gold: pd.DataFrame, disease: str, use_weather: bool = True, use_lags: bool = True) -> pd.DataFrame:
    d = gold[gold["disease"] == disease].sort_values(["district_id", "week_start"]).copy()
    g = d.groupby("district_id", sort=False)
    y_feat = np.log1p(d["cases_for_features"].astype(float))
    d["_ylog"] = y_feat

    # target: next week's reported count (NOT the filled series)
    d["target"] = g["cases"].shift(-1).astype("Float64")
    d["target_row_id"] = g["row_id"].shift(-1)
    d["target_week"] = g["week_start"].shift(-1)
    d["target_status"] = g["cases_status"].shift(-1)

    # baseline input: this week's value (naive "next week = this week")
    d["last_value"] = d["cases_for_features"].astype(float)

    if use_lags:
        gl = d.groupby("district_id", sort=False)["_ylog"]
        for k in (0, 1, 2, 3):
            d[f"lag{k}_log"] = gl.shift(k)
        d["roll4_log"] = gl.transform(lambda s: s.rolling(4, min_periods=2).mean())
        d["roll8_log"] = gl.transform(lambda s: s.rolling(8, min_periods=4).mean())
        d["diff1_log"] = d["lag0_log"] - d["lag1_log"]
        d["same_week_last_year_log"] = gl.shift(51)  # target week t+1 one year earlier = t-51
    else:
        d["lag0_log"] = d["_ylog"]

    if use_weather:
        for c in WEATHER:
            if c not in d:
                continue
            gw = d.groupby("district_id", sort=False)[c]
            for k in (0, 1, 2, 3):
                d[f"{c}_lag{k}"] = gw.shift(k)
            d[f"{c}_roll4"] = gw.transform(lambda s: s.rolling(4, min_periods=2).mean())

    woy = (d["week_start"] + pd.Timedelta(days=7)).dt.isocalendar().week.astype(int)
    d["woy_sin"] = np.sin(2 * np.pi * woy / 52.18)
    d["woy_cos"] = np.cos(2 * np.pi * woy / 52.18)
    d["province_code"] = d["province"].map(PROVINCE_CODES).astype("Int64")
    d["log_pop"] = np.log(d["population_2017"].astype(float))
    return d.drop(columns=["_ylog"])


def feature_columns(df: pd.DataFrame) -> list[str]:
    base = [c for c in df.columns if c.endswith("_log") or "_lag" in c or c.endswith("_roll4")]
    return base + ["woy_sin", "woy_cos", "province_code", "mpi", "log_pop"]


def trainable(df: pd.DataFrame) -> pd.Series:
    """Rows we can score honestly: a real reported target and a real (or 1-week-filled) last value."""
    return df["target"].notna() & (df["target_status"] == "reported") & df["last_value"].notna()
