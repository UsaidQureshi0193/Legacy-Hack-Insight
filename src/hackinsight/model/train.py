"""Train and evaluate: declared baseline vs LightGBM quantile model, with a time-based split.

Declared baseline (fixed before results, see docs/dataset_proposal): next week's suspected cases
in a district = this week's cases ("last value", naive persistence).

Model: three LightGBM models on log1p(cases) predicting the 10th, 50th and 90th percentile of
next week's count. The median is the point forecast; 10-90 is an 80% prediction interval.
Quantiles survive the log transform, so expm1 of a predicted quantile is the count quantile.

Split (by the week being predicted):  train < VAL_START <= validation < TEST_START <= test
  * tuning and early stopping use validation only
  * the final model is refit on train+validation and the test set is scored once
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb
import lightgbm as lgb
import numpy as np
import pandas as pd
import psutil

from .. import config
from . import features

PARAM_GRID = [dict(num_leaves=nl, min_child_samples=mcs, learning_rate=0.05, feature_fraction=0.8,
                   bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0)
              for nl in (7, 15, 31) for mcs in (20, 50)]


def load_gold() -> pd.DataFrame:
    con = duckdb.connect(str(config.WAREHOUSE), read_only=True)
    g = con.execute("SELECT * FROM gold.district_week").df()
    con.close()
    g["week_start"] = pd.to_datetime(g["week_start"])
    return g


def split(df: pd.DataFrame):
    tw = df["target_week"]
    return df[tw < config.VAL_START], df[(tw >= config.VAL_START) & (tw < config.TEST_START)], df[tw >= config.TEST_START]


def _params(p: dict, alpha: float) -> dict:
    return {**p, "objective": "quantile", "alpha": alpha, "metric": "quantile", "verbosity": -1,
            "seed": config.SEED, "deterministic": True, "force_col_wise": True, "num_threads": 4}


def fit_quantiles(X, y, params: dict, rounds: int) -> dict[float, lgb.Booster]:
    return {q: lgb.train(_params(params, q), lgb.Dataset(X, y), num_boost_round=rounds) for q in config.QUANTILES}


def predict(models: dict, X) -> pd.DataFrame:
    out = pd.DataFrame({f"q{int(q*100)}": np.expm1(m.predict(X)).clip(0) for q, m in models.items()}, index=X.index)
    # quantile crossing can happen with separate models; sort each row so p10 <= p50 <= p90
    out[:] = np.sort(out.values, axis=1)
    return out


def mae(a, b) -> float:
    return float(np.mean(np.abs(np.asarray(a, float) - np.asarray(b, float))))


def rmse(a, b) -> float:
    return float(np.sqrt(np.mean((np.asarray(a, float) - np.asarray(b, float)) ** 2)))


def bootstrap_skill(df: pd.DataFrame, n: int = 500) -> dict:
    """Block bootstrap over weeks: resample whole weeks (errors inside a week are correlated)
    and recompute the MAE improvement. Gives a 95% interval for the headline number."""
    rng = np.random.default_rng(config.SEED)
    weeks = df["target_week"].unique()
    by_w = {w: g for w, g in df.groupby("target_week")}
    vals = []
    for _ in range(n):
        s = pd.concat([by_w[w] for w in rng.choice(weeks, len(weeks), replace=True)])
        mb, mm = mae(s["target"], s["baseline"]), mae(s["target"], s["q50"])
        vals.append(1 - mm / mb if mb else np.nan)
    v = np.array(vals, float)
    return {"skill_mean": float(np.nanmean(v)), "skill_ci95": [float(np.nanpercentile(v, 2.5)),
                                                                float(np.nanpercentile(v, 97.5))]}


def group_table(df: pd.DataFrame, col: str) -> list[dict]:
    rows = []
    for k, g in df.groupby(col, dropna=False):
        mb, mm = mae(g["target"], g["baseline"]), mae(g["target"], g["q50"])
        rows.append({"group": col, "value": str(k), "n": len(g), "mae_baseline": round(mb, 2), "mae_model": round(mm, 2),
                     "skill": round(1 - mm / mb, 3) if mb else None,
                     "interval_coverage": round(float(((g["target"] >= g["q10"]) & (g["target"] <= g["q90"])).mean()), 3),
                     "mean_target": round(float(g["target"].mean()), 1)})
    return sorted(rows, key=lambda r: (r["skill"] if r["skill"] is not None else 9))


def run_one(gold: pd.DataFrame, disease: str, use_weather=True, use_lags=True, grid=None, save=True) -> dict:
    t0 = time.perf_counter()
    proc = psutil.Process()
    mem0 = proc.memory_info().rss
    df = features.build(gold, disease, use_weather=use_weather, use_lags=use_lags)
    cols = features.feature_columns(df)
    ok = features.trainable(df)
    data = df[ok].copy()
    data["target"] = data["target"].astype(float)
    data["baseline"] = data["last_value"]
    tr, va, te = split(data)
    if len(tr) < 200 or len(va) < 50 or len(te) < 50:
        return {"disease": disease, "skipped": f"not enough rows (train={len(tr)}, val={len(va)}, test={len(te)})"}
    ylog = lambda s: np.log1p(s.astype(float))

    # ---- tuning on validation only (median model, early stopping)
    best = None
    for p in grid or PARAM_GRID:
        m = lgb.train(_params(p, 0.5), lgb.Dataset(tr[cols], ylog(tr["target"])), num_boost_round=2000,
                      valid_sets=[lgb.Dataset(va[cols], ylog(va["target"]))],
                      callbacks=[lgb.early_stopping(100, verbose=False)])
        v_mae = mae(va["target"], np.expm1(m.predict(va[cols], num_iteration=m.best_iteration)).clip(0))
        if best is None or v_mae < best[0]:
            best = (v_mae, p, m.best_iteration)
    val_mae, params, best_iter = best
    val_base = mae(va["target"], va["baseline"])

    # ---- refit on train + validation, score test ONCE
    trva = pd.concat([tr, va])
    rounds = max(50, int(best_iter * 1.1))
    models = fit_quantiles(trva[cols], ylog(trva["target"]), params, rounds)
    te = te.join(predict(models, te[cols]))
    test = {
        "n": len(te), "weeks": int(te["target_week"].nunique()), "districts": int(te["district_id"].nunique()),
        "mae_baseline": mae(te["target"], te["baseline"]), "mae_model": mae(te["target"], te["q50"]),
        "rmse_baseline": rmse(te["target"], te["baseline"]), "rmse_model": rmse(te["target"], te["q50"]),
        "interval_coverage_80": float(((te["target"] >= te["q10"]) & (te["target"] <= te["q90"])).mean()),
        "mean_interval_width": float((te["q90"] - te["q10"]).mean()),
    }
    test["skill_vs_baseline"] = 1 - test["mae_model"] / test["mae_baseline"]
    test.update(bootstrap_skill(te))

    # ---- errors by group
    q = tr["last_value"].quantile([0.5, 0.9]).values
    te["size_band"] = np.select([te["last_value"] <= q[0], te["last_value"] <= q[1]],
                                ["low (<= median)", "medium"], "high (top 10%)")
    groups = {c: group_table(te, c) for c in ("province", "poverty_tier", "season", "size_band")}
    dist_tab = group_table(te, "district")
    groups["district_worst10"] = [r for r in dist_tab if r["n"] >= 10][:10]

    imp = pd.Series(models[0.5].feature_importance("gain"), index=cols).sort_values(ascending=False)
    seconds = time.perf_counter() - t0
    res = {
        "disease": disease, "baseline": "last value (next week = this week)", "split": {
            "train_target_weeks": [str(tr["target_week"].min().date()), str(tr["target_week"].max().date())],
            "val_target_weeks": [str(va["target_week"].min().date()), str(va["target_week"].max().date())],
            "test_target_weeks": [str(te["target_week"].min().date()), str(te["target_week"].max().date())],
            "n_train": len(tr), "n_val": len(va), "n_test": len(te)},
        "validation": {"mae_baseline": val_base, "mae_model": val_mae, "params": params, "best_iter": best_iter},
        "test": test, "groups": groups, "feature_importance_top10": imp.head(10).round(1).to_dict(),
        "features": cols, "use_weather": use_weather, "use_lags": use_lags,
        "train_seconds": round(seconds, 1), "peak_rss_mb_delta": round((proc.memory_info().rss - mem0) / 2**20, 1),
        "weather_rows_at_train": int((gold["weather_hours"].fillna(0) > 0).sum()) if "weather_hours" in gold else 0,
    }
    if save:
        _save(disease, models, res, te, df, cols)
    return res


def _save(disease, models, res, te, df, cols) -> None:
    out = config.ARTIFACTS / f"model_{disease}"
    out.mkdir(parents=True, exist_ok=True)
    champion = out / "metrics.json"
    if champion.exists():
        old = json.loads(champion.read_text(encoding="utf-8"))
        # champion / challenger on VALIDATION (the test set is never used to choose)
        if old.get("validation", {}).get("mae_model", 1e18) < res["validation"]["mae_model"] * 0.98 \
                and old.get("features") == res["features"]:
            res["kept_previous_model"] = True
            print(f"  {disease}: new model not better on validation; keeping previous champion")
            return
    for q, m in models.items():
        m.save_model(str(out / f"q{int(q*100)}.txt"))
    champion.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    te_out = te[["target_row_id", "row_id", "district_id", "district", "province", "target_week", "target",
                 "baseline", "q10", "q50", "q90"]].rename(columns={"row_id": "input_row_id"})
    te_out.to_parquet(out / "test_predictions.parquet", index=False)

    # forecast: for each district, the latest week with a value -> predict the following week
    latest = df[df["last_value"].notna()].sort_values("week_start").groupby("district_id").tail(1)
    recent = latest[latest["week_start"] >= latest["week_start"].max() - pd.Timedelta(weeks=4)]
    fc = recent.join(predict(models, recent[cols]))
    # Serve the model only if it beat the declared baseline on VALIDATION (decided before the test
    # set is looked at). Otherwise the forecast is the baseline itself, with no fake interval.
    use_model = res["validation"]["mae_model"] < res["validation"]["mae_baseline"]
    res["served_method"] = "model" if use_model else "baseline (model did not beat baseline on validation)"
    champion.write_text(json.dumps(res, indent=2, default=str), encoding="utf-8")
    from . import card
    card.write(res)
    if not use_model:
        fc["q50"] = fc["last_value"]
        fc["q10"] = float("nan")
        fc["q90"] = float("nan")
    fc["method"] = "model_q50" if use_model else "baseline_last_value"
    fc["forecast_week"] = fc["week_start"] + pd.Timedelta(days=7)
    fc["forecast_id"] = [f"fc-{disease}-{a}-{b.date()}" for a, b in zip(fc["district_id"], fc["forecast_week"])]
    fc = fc[["forecast_id", "district_id", "district", "province", "disease", "week_start", "forecast_week",
             "row_id", "last_value", "q10", "q50", "q90", "method", "poverty_tier", "precip_sum_mm_roll4"
             if "precip_sum_mm_roll4" in fc else "season"]].rename(columns={"row_id": "based_on_row_id",
                                                                           "week_start": "based_on_week"})
    fc.to_parquet(out / "forecast.parquet", index=False)
    con = duckdb.connect(str(config.WAREHOUSE))
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    con.register("_fc", fc)
    con.execute(f"CREATE OR REPLACE TABLE gold.forecast_{disease} AS SELECT * FROM _fc")
    con.register("_te", te_out)
    con.execute(f"CREATE OR REPLACE TABLE gold.test_predictions_{disease} AS SELECT * FROM _te")
    con.close()
    fc.to_parquet(config.PROCESSED / f"forecast_{disease}.parquet", index=False)


def run() -> dict:
    gold = load_gold()
    out = {d: run_one(gold, d) for d in config.TARGET_DISEASES}
    p = out[config.PRIMARY_DISEASE]
    head = {"disease": config.PRIMARY_DISEASE}
    if "test" in p:
        t = p["test"]
        head.update({k: round(v, 3) if isinstance(v, float) else v for k, v in t.items()})
    out["headline"] = head
    return out
