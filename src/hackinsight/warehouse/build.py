"""Cleaned and processed (gold) layers in DuckDB, with the Data Quality Agent at both gates.

    raw files --(extract)--> [GATE 1] --> cleaned.*  --(join, aggregate)--> [GATE 2] --> gold.*
                                                                                 |
                                                                  fail -> keep last good gold

Gold tables (every number on the dashboard or in an insight comes from these):
  gold.district_week   one row per district x disease x ISO week, with a stable row_id,
                       the PDF file + page it came from, weather of that week, population,
                       poverty tier, season and a cases_status that says WHY a value is missing
  gold.weather_daily   district x day (UTC) weather, incl. 7-day forecast (is_forecast)
  gold.province_week   the national table of each bulletin (province totals)
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime, timezone

import duckdb
import numpy as np
import pandas as pd

from .. import config
from ..manifest import Manifest
from ..quality.agent import DataQualityAgent, Decision
from . import geo

STATUS_FILE = config.ARTIFACTS / "status.json"


def week_start(year: int, week: int):
    try:
        return pd.Timestamp(date.fromisocalendar(int(year), int(week), 1))
    except ValueError:
        return pd.NaT


def season_of(ts: pd.Series) -> pd.Series:
    m = ts.dt.month
    return pd.Series(np.select([m.isin([6, 7, 8, 9]), m.isin([10, 11]), m.isin([12, 1, 2])],
                               ["monsoon", "post_monsoon", "winter"], "pre_monsoon"), index=ts.index)


def stable_id(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def poverty_tier(mpi: float) -> str:
    if pd.isna(mpi):
        return "unknown"
    return "low" if mpi < 0.10 else ("medium" if mpi < 0.25 else "high")


MPI_PROVINCE = {"Sindh": "Sindh", "Balochistan": "Balochistan", "Khyber Pakhtunkhwa": "Khyber Pakhtunkhwa",
                "Punjab": "Punjab", "Islamabad (ICT)": "Islamabad", "Azad Jammu and Kashmir": "Azad Kashmir",
                "Gilgit Baltistan": "Gilgit-Baltistan"}


# ====================================================================== CLEANED
def build_cleaned(con, agent: DataQualityAgent, ext, probs, weather_raw, adm2, mpi, pop, od) -> dict:
    con.execute("CREATE SCHEMA IF NOT EXISTS cleaned")

    # ---- IDSR cases (Gate 1)
    idsr = agent.gate1_idsr(ext, probs)
    idsr["week_start"] = [week_start(y, w) for y, w in zip(idsr["year"], idsr["week"])]
    bad_w = idsr["week_start"].isna()
    if bad_w.sum():
        agent.log(Decision("gate1", "invalid_iso_week", "IDSR-R9", "FLAG", "cleaned.idsr_cases", int(bad_w.sum()),
                           "Week number does not exist in that ISO year; rows excluded.",
                           idsr.loc[bad_w, "source_file"].unique().tolist()[:5]))
        agent.quarantine.append(idsr[bad_w].assign(quarantine_reason="IDSR-R9 week not in ISO calendar"))
        idsr = idsr[~bad_w]

    # ---- district matching
    places = idsr.loc[idsr["place_type"] == "district", ["province", "place"]].drop_duplicates()
    m = geo.match(places, adm2)
    un = m[m["district_id"].isna()]
    if len(un):
        agent.log(Decision("gate1", "district_unmatched", "GEO-M1", "FLAG", "cleaned.district_match", len(un),
                           f"{len(un)} district names from the bulletins could not be matched to an OCHA pcode with "
                           f"score >= {geo.AUTO_ACCEPT}. They stay in cleaned but are excluded from gold until an "
                           "alias is reviewed and added to configs/district_aliases.csv.",
                           un[["province", "place", "match_method"]].head(10).to_dict("records")))
    fz = m[m["match_method"] == "fuzzy"]
    if len(fz):
        agent.log(Decision("gate1", "district_fuzzy", "GEO-M2", "FIX", "cleaned.district_match", len(fz),
                           f"{len(fz)} spellings matched by fuzzy score >= {geo.AUTO_ACCEPT} within the same province.",
                           fz[["place", "adm2_name", "match_score"]].head(10).to_dict("records")))
    idsr = idsr.merge(m[["province", "place", "district_id", "match_score", "match_method"]],
                      on=["province", "place"], how="left")
    # event time (week_start, from the bulletin) vs ingest time (when we fetched the file, from manifest.csv)
    fetched = {e.path.rsplit("/", 1)[-1]: e.fetched_at_utc for e in Manifest().entries("nih_idsr")}
    idsr["ingested_at_utc"] = pd.to_datetime(idsr["source_file"].map(fetched), utc=True).dt.tz_localize(None)
    # quarantine: district rows that cannot reach gold (no OCHA pcode) are kept, with the reason
    unm = (idsr["place_type"] == "district") & (idsr["table"] == "district") & idsr["district_id"].isna()
    agent.quarantine.append(idsr[unm].assign(quarantine_reason="GEO-M1 district name not matched to an OCHA pcode"))
    q = pd.concat([x for x in agent.quarantine if len(x)], ignore_index=True) if agent.quarantine else pd.DataFrame()
    con.execute("CREATE SCHEMA IF NOT EXISTS quarantine")
    if len(q):
        keep = [c for c in ["year", "week", "province", "place", "disease", "value_raw", "source_file", "page",
                            "table", "quarantine_reason"] if c in q.columns]
        con.register("_q", q[keep].astype(str))
        con.execute("CREATE OR REPLACE TABLE quarantine.idsr_rows AS SELECT *, now() AS quarantined_at FROM _q")
        con.unregister("_q")
        agent.log(Decision("gate1", "quarantine", "QUARANTINE", "FLAG", "quarantine.idsr_rows", len(q),
                           f"{len(q)} rows could not enter gold and were moved to quarantine.idsr_rows with their "
                           f"reason (never dropped silently): {q['quarantine_reason'].value_counts().to_dict()}."))
    con.register("_idsr", idsr.drop(columns=["value_status"]).assign(value_status=idsr["value_status"]))
    con.execute("CREATE OR REPLACE TABLE cleaned.idsr_cases AS SELECT * FROM _idsr")
    con.register("_m", m)
    con.execute("CREATE OR REPLACE TABLE cleaned.district_match AS SELECT * FROM _m")

    # ---- districts dimension (+ population 2017 where it can be matched)
    dist = adm2.rename(columns={"adm2_pcode": "district_id", "adm2_name": "district", "adm1_name": "province_adm1",
                                "center_lat": "lat", "center_lon": "lon"})
    dist["in_idsr"] = dist["district_id"].isin(m["district_id"].dropna())
    dist = dist.merge(_population_by_pcode(pop, adm2, agent), on="district_id", how="left")
    con.register("_d", dist)
    con.execute("CREATE OR REPLACE TABLE cleaned.districts AS SELECT * FROM _d")

    # ---- poverty (province MPI, OPHI/DHS 2017-18)
    mp = mpi[mpi["Admin 1 Name"].notna()].copy()
    mp["province"] = mp["Admin 1 Name"].map(MPI_PROVINCE)
    mp = mp[mp["province"].notna()][["province", "MPI", "Headcount Ratio", "Survey", "source_file"]]
    mp.columns = ["province", "mpi", "headcount_ratio", "survey", "source_file"]
    con.register("_mp", mp)
    con.execute("CREATE OR REPLACE TABLE cleaned.province_poverty AS SELECT * FROM _mp")

    # ---- OpenDengue Pakistan (benchmark history, kept as published)
    od = od.copy()
    od["calendar_start_date"] = pd.to_datetime(od["calendar_start_date"])
    con.register("_od", od)
    con.execute("CREATE OR REPLACE TABLE cleaned.opendengue_pak AS SELECT * FROM _od")

    # ---- weather (Gate 1)
    if len(weather_raw):
        w = agent.gate1_weather(weather_raw)
        wf = {e.path: e.fetched_at_utc for e in Manifest().entries("open_meteo")}
        w["ingested_at_utc"] = pd.to_datetime(w["source_file"].map(wf), utc=True).dt.tz_localize(None)
        keep = ["district_id", "ts_utc", "temperature_c", "rh_pct", "precip_mm", "kind", "is_forecast",
                "is_filled", "is_suspect", "source_file", "ingested_at_utc"]
        w = w[[c for c in keep if c in w.columns]]
        con.register("_w", w)
        con.execute("CREATE OR REPLACE TABLE cleaned.weather_hourly AS SELECT * FROM _w")
        con.unregister("_w")
    return {"idsr_rows": len(idsr), "weather_rows": len(weather_raw), "districts_matched": int(m["district_id"].notna().sum()),
            "districts_unmatched": len(un)}


def _population_by_pcode(pop: pd.DataFrame, adm2: pd.DataFrame, agent) -> pd.DataFrame:
    """Population 2017 (COD-PS) by district pcode. Districts created after 2017 have no row ->
    population stays missing and per-100k rates are not computed for them (flagged)."""
    cols = {c.lower(): c for c in pop.columns}
    pc = next((cols[c] for c in cols if "adm2" in c and "pcode" in c), None)
    tot = next((cols[c] for c in cols if c in ("t_tl", "total", "pop_total", "both_sexes", "total_population")), None)
    if tot is None:
        num = [c for c in pop.columns if pd.api.types.is_numeric_dtype(pop[c])]
        tot = max(num, key=lambda c: pop[c].fillna(0).sum()) if num else None
    if pc is None or tot is None:
        agent.log(Decision("gate1", "population_schema", "POP-P1", "FLAG", "cleaned.districts", 0,
                           "Population sheet has no recognisable pcode/total column; per-100k rates disabled."))
        return pd.DataFrame(columns=["district_id", "population_2017"])
    p = pop[[pc, tot]].rename(columns={pc: "district_id", tot: "population_2017"})
    p = p.groupby("district_id", as_index=False)["population_2017"].sum()
    missing = adm2.loc[~adm2["adm2_pcode"].isin(p["district_id"]), "adm2_name"]
    if len(missing):
        agent.log(Decision("gate1", "population_missing", "POP-P2", "FLAG", "cleaned.districts", len(missing),
                           f"{len(missing)} districts (mostly created after the 2017 census) have no 2017 population; "
                           "their per-100k rate is left missing rather than estimated.", missing.head(10).tolist()))
    return p


# ====================================================================== GOLD
def build_gold(con, agent: DataQualityAgent) -> tuple[bool, dict]:
    con.execute("CREATE SCHEMA IF NOT EXISTS gold")
    con.execute("CREATE SCHEMA IF NOT EXISTS staging")
    idsr = con.execute("SELECT * FROM cleaned.idsr_cases").df()
    dist = con.execute("SELECT * FROM cleaned.districts").df()
    pov = con.execute("SELECT * FROM cleaned.province_poverty").df()

    dt = idsr[(idsr["table"] == "district")].copy()
    drows = dt[(dt["place_type"] == "district") & dt["district_id"].notna()]

    # universe: matched districts x target diseases x every week from first to last bulletin
    weeks = pd.date_range(dt["week_start"].min(), dt["week_start"].max(), freq="7D")
    dd = drows[["district_id", "province"]].drop_duplicates("district_id")
    grid = (dd.merge(pd.DataFrame({"week_start": weeks}), how="cross")
              .merge(pd.DataFrame({"disease": config.TARGET_DISEASES}), how="cross"))

    vals = drows[drows["disease"].isin(config.TARGET_DISEASES)]
    key = ["district_id", "week_start", "disease"]
    dup = vals.duplicated(key, keep=False)
    if dup.sum():
        # Several bulletin units belong to one OCHA district (L&C Kurram + Upper Kurram = Kurram;
        # "SD Peshawar" + Peshawar). Their reported counts are summed; the provenance keeps all names.
        parts = vals[dup]
        agg = (parts.groupby(key, as_index=False)
                    .agg(cases=("cases", lambda s: s.sum(min_count=1)),
                         value_status=("value_status", lambda s: "reported" if (s == "reported").any() else s.iloc[0]),
                         value_raw=("value_raw", lambda s: "+".join(map(str, s))),
                         source_file=("source_file", "first"), page=("page", "first"),
                         place=("place", lambda s: "+".join(sorted(set(s))))))
        agent.log(Decision("gate2", "units_summed_to_district", "GOLD-G0", "FIX", "gold.district_week", len(agg),
                           f"{len(agg)} district-weeks combine several bulletin units that are one OCHA district "
                           "(reviewed in configs/district_aliases.csv). Counts summed; `place` lists the parts.",
                           agg[["place", "district_id"]].drop_duplicates().head(8).astype(str).to_dict("records")))
        vals = pd.concat([vals[~dup], agg], ignore_index=True)
    g = grid.merge(vals[["district_id", "week_start", "disease", "cases", "value_status", "value_raw",
                         "source_file", "page", "place"]],
                   on=["district_id", "week_start", "disease"], how="left")

    present = drows[["district_id", "week_start"]].drop_duplicates().assign(_present=True)
    prov_has = dt[["province", "week_start"]].drop_duplicates().assign(_prov_table=True)
    g = g.merge(present, on=["district_id", "week_start"], how="left").merge(prov_has, on=["province", "week_start"],
                                                                              how="left")
    g["cases_status"] = np.select(
        [g["value_status"].notna(), g["_present"].fillna(False).astype(bool), g["_prov_table"].fillna(False).astype(bool)],
        [g["value_status"], "not_listed", "district_not_in_table"], "no_bulletin")
    g = g.drop(columns=["_present", "_prov_table", "value_status"])
    g["cases"] = g["cases"].astype("Int64")

    # names, population, poverty, season
    g = g.merge(dist[["district_id", "district", "lat", "lon", "population_2017"]], on="district_id", how="left")
    g = g.merge(pov[["province", "mpi"]], on="province", how="left")
    g["poverty_tier"] = g["mpi"].map(poverty_tier)
    g["season"] = season_of(g["week_start"])
    g["cases_per_100k"] = (g["cases"].astype(float) / g["population_2017"] * 1e5).round(2)

    # outliers (flag only)
    g = agent.flag_outliers(g)

    # feature-only fill of single-week gaps (target stays missing)
    g = g.sort_values(["district_id", "disease", "week_start"])
    g["cases_for_features"] = (g.groupby(["district_id", "disease"])["cases"]
                                 .transform(lambda s: s.astype(float).interpolate(limit=1, limit_area="inside")))
    g["lag_source_filled"] = g["cases"].isna() & g["cases_for_features"].notna()

    # weekly weather (UTC weeks, Monday start)
    has_w = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='cleaned' "
                        "AND table_name='weather_hourly'").fetchone()[0]
    if has_w:
        ww = con.execute("""
            SELECT district_id, date_trunc('week', ts_utc) AS week_start,
                   avg(temperature_c) AS temp_mean_c, max(temperature_c) AS temp_max_c,
                   min(temperature_c) AS temp_min_c, avg(rh_pct) AS rh_mean_pct,
                   sum(precip_mm) AS precip_sum_mm, sum(CASE WHEN precip_mm > 0.1 THEN 1 ELSE 0 END) AS rain_hours,
                   count(temperature_c) AS weather_hours, sum(CASE WHEN is_filled THEN 1 ELSE 0 END) AS weather_filled_hours
            FROM cleaned.weather_hourly WHERE NOT is_forecast GROUP BY 1, 2""").df()
        ww["week_start"] = pd.to_datetime(ww["week_start"])
        g = g.merge(ww, on=["district_id", "week_start"], how="left")
        wd = con.execute("""
            SELECT district_id, CAST(ts_utc AS DATE) AS day_utc, avg(temperature_c) AS temp_mean_c,
                   max(temperature_c) AS temp_max_c, avg(rh_pct) AS rh_mean_pct, sum(precip_mm) AS precip_mm,
                   bool_or(is_forecast) AS is_forecast, max(ts_utc) AS last_hour_utc
            FROM cleaned.weather_hourly GROUP BY 1, 2""").df()
    else:
        wd = pd.DataFrame()

    g["row_id"] = [stable_id(a, b, c.date()) for a, b, c in zip(g["district_id"], g["disease"], g["week_start"])]
    g["week_start"] = pd.to_datetime(g["week_start"]).astype("datetime64[ns]")
    g = g.reset_index(drop=True)

    # consistency: province Total row vs sum of its districts (all diseases, same table)
    tot = dt[dt["place_type"] == "province_total"].groupby(["province", "week_start", "disease"])["cases"].sum()
    sm = dt[dt["place_type"] == "district"].groupby(["province", "week_start", "disease"])["cases"].sum()
    cons = pd.concat([tot.rename("reported_total"), sm.rename("sum_districts")], axis=1).dropna().reset_index()
    cons["abs_diff"] = (cons["reported_total"] - cons["sum_districts"]).abs()
    cons["rel_diff"] = cons["abs_diff"] / cons["reported_total"].replace(0, np.nan)
    cons = cons.dropna(subset=["rel_diff"])
    # (passed to the agent explicitly; DataFrame.attrs would leak into every derived Series)

    # province totals from the national table
    pw = idsr[(idsr["table"] == "national")][["province", "week_start", "disease", "cases", "value_raw",
                                               "source_file", "page"]].drop_duplicates(["province", "week_start", "disease"])

    fresh = freshness(con, g)
    prev = _last_good_rows()
    ok = agent.gate2(g, prev, fresh, cons)

    # drift: last 8 reported weeks vs training period (primary disease)
    prim = g[(g["disease"] == config.PRIMARY_DISEASE) & g["cases"].notna()]
    ref = np.log1p(prim.loc[prim["week_start"] < config.VAL_START, "cases"].astype(float))
    last8 = prim["week_start"].drop_duplicates().nlargest(8)
    cur = np.log1p(prim.loc[prim["week_start"].isin(last8), "cases"].astype(float))
    psi = agent.drift(ref, cur, f"log_{config.PRIMARY_DISEASE}_cases")
    if "precip_sum_mm" in g:
        agent.drift(g.loc[g["week_start"] < config.VAL_START, "precip_sum_mm"],
                    g.loc[g["week_start"].isin(last8), "precip_sum_mm"], "weekly_precip")

    info = {"gold_rows": len(g), "weeks": int(g["week_start"].nunique()), "districts": int(g["district_id"].nunique()),
            "freshness": fresh, "psi_primary": psi, "gate2_pass": ok,
            "reported_rows": int(g["cases_status"].eq("reported").sum()),
            "weather_rows": int((g.get("weather_hours", pd.Series(0, index=g.index)).fillna(0) > 0).sum())}
    if ok:
        cols_order = ["row_id", "district_id", "district", "province", "disease", "week_start", "cases",
                      "cases_status", "value_raw", "source_file", "page", "place", "cases_for_features",
                      "lag_source_filled", "is_outlier", "population_2017", "cases_per_100k", "mpi", "poverty_tier",
                      "season", "lat", "lon"]
        wcols = [c for c in ["temp_mean_c", "temp_max_c", "temp_min_c", "rh_mean_pct", "precip_sum_mm", "rain_hours",
                             "weather_hours", "weather_filled_hours"] if c in g.columns]
        out = g[cols_order + wcols]
        for name, df in (("district_week", out), ("weather_daily", wd), ("province_week", pw),
                         ("consistency", cons)):
            if df is None or not len(df):
                continue
            con.register("_g", df)
            con.execute(f"CREATE OR REPLACE TABLE gold.{name} AS SELECT * FROM _g")
            con.unregister("_g")
            config.PROCESSED.mkdir(parents=True, exist_ok=True)
            df.to_parquet(config.PROCESSED / f"{name}.parquet", index=False)
    return ok, info


def freshness(con, g: pd.DataFrame) -> dict:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    out = {}
    newest_week = g.loc[g["cases_status"] == "reported", "week_start"].max()
    if pd.notna(newest_week):
        end = pd.Timestamp(newest_week) + pd.Timedelta(days=7)
        age = (now - end.to_pydatetime()).total_seconds() / 3600
        out["nih_idsr"] = {"newest": str(end.date()), "age_h": age, "sla_h": config.FRESHNESS_SLA_H["nih_idsr"],
                           "late": age > config.FRESHNESS_SLA_H["nih_idsr"]}
    try:
        last = con.execute("SELECT max(ts_utc) FROM cleaned.weather_hourly WHERE NOT is_forecast").fetchone()[0]
        if last is not None:
            age = (now - pd.Timestamp(last).to_pydatetime()).total_seconds() / 3600
            out["open_meteo"] = {"newest": str(last), "age_h": age, "sla_h": config.FRESHNESS_SLA_H["open_meteo"],
                                 "late": age > config.FRESHNESS_SLA_H["open_meteo"]}
    except duckdb.CatalogException:
        pass
    return out


def _last_good_rows() -> int | None:
    import json
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8")).get("last_good", {}).get("gold_rows")
        except json.JSONDecodeError:
            return None
    return None
