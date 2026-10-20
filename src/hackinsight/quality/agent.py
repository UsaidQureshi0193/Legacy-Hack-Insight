"""Data Quality Agent: run validation tools -> decide FIX / FILL / FLAG -> explain -> log.

Decision policy (the same table the jury sees in the README):
  FIX   a safe, certain, reversible correction   (type cast, unit conversion, exact duplicate)
  FILL  an estimate for a short gap, always marked with an is_filled column
  FLAG  leave the value as it is (or missing) and warn - used whenever we cannot be sure
  FAIL  a gate-level stop: the run is aborted and gold keeps the last good version

Human-in-the-loop: a FIX that would touch more than HIGH_RISK_SHARE of a table is "high risk".
It is applied only if a person approved that rule in configs/approvals.json; otherwise the
agent falls back to FLAG and the decision is logged as pending_approval.

Every decision lands in the DuckDB table `quality_log` and in artifacts/quality_report.json.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone

import duckdb
import numpy as np
import pandas as pd

try:                                   # pandera >= 0.24
    import pandera.pandas as pa
except ImportError:                    # older pandera
    import pandera as pa

from .. import config

HIGH_RISK_SHARE = 0.05
MISSING_TOKENS = {"NR", "N/A", "NA", "-", "–"}
VALID_PROVINCES = ["Sindh", "Balochistan", "Khyber Pakhtunkhwa", "Punjab", "Islamabad",
                   "Azad Kashmir", "Gilgit-Baltistan", "Pakistan"]


@dataclass
class Decision:
    gate: str
    check: str
    rule: str
    action: str            # FIX | FILL | FLAG | PASS | FAIL
    table: str
    rows_affected: int
    explanation: str
    sample: list = field(default_factory=list)
    risk: str = "low"
    status: str = "applied"   # applied | pending_approval | info
    decision_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts_utc: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))


# ------------------------------------------------------------------ pandera schemas (the "tools")
IDSR_SCHEMA = pa.DataFrameSchema(
    {
        "year": pa.Column(int, pa.Check.in_range(2020, 2030)),
        "week": pa.Column(int, pa.Check.in_range(1, 53)),
        "province": pa.Column(str, pa.Check.isin(VALID_PROVINCES)),
        "place": pa.Column(str, pa.Check.str_length(min_value=2)),
        "disease": pa.Column(str),
        "cases": pa.Column("Int64", pa.Check.ge(0), nullable=True),
    },
    strict=False, coerce=False,
)

WEATHER_SCHEMA = pa.DataFrameSchema(
    {
        "temperature_c": pa.Column(float, pa.Check.in_range(-45, 60), nullable=True),
        "rh_pct": pa.Column(float, pa.Check.in_range(0, 100), nullable=True),
        "precip_mm": pa.Column(float, pa.Check.in_range(0, 400), nullable=True),
    },
    strict=False,
)

GOLD_SCHEMA = pa.DataFrameSchema(
    {
        "row_id": pa.Column(str, unique=True),
        "district_id": pa.Column(str),
        "disease": pa.Column(str),
        "week_start": pa.Column("datetime64[ns]"),
        "cases": pa.Column("Int64", pa.Check.ge(0), nullable=True),
    },
    strict=False,
)


def failures(schema: pa.DataFrameSchema, df: pd.DataFrame) -> pd.DataFrame:
    """Run a pandera schema lazily and return its failure cases (empty frame when valid)."""
    try:
        schema.validate(df, lazy=True)
        return pd.DataFrame(columns=["column", "check", "index", "failure_case"])
    except pa.errors.SchemaErrors as e:
        return e.failure_cases


def _approvals() -> set[str]:
    p = config.CONFIGS / "approvals.json"
    if not p.exists():
        return set()
    return {a["rule"] for a in json.loads(p.read_text(encoding="utf-8")).get("approved", [])}


class DataQualityAgent:
    def __init__(self, con: duckdb.DuckDBPyConnection, run_id: str):
        self.con = con
        self.run_id = run_id
        self.decisions: list[Decision] = []
        self.quarantine: list[pd.DataFrame] = []   # rows kept out of gold, with the reason
        self.approved = _approvals()
        con.execute("""CREATE TABLE IF NOT EXISTS quality_log (
            decision_id VARCHAR, run_id VARCHAR, ts_utc VARCHAR, gate VARCHAR, check_name VARCHAR,
            rule VARCHAR, action VARCHAR, table_name VARCHAR, rows_affected BIGINT, risk VARCHAR,
            status VARCHAR, explanation VARCHAR, sample VARCHAR)""")

    # ------------------------------------------------------------------ bookkeeping
    def log(self, d: Decision) -> Decision:
        self.decisions.append(d)
        return d

    def fix_allowed(self, rule: str, rows: int, total: int) -> tuple[bool, str]:
        """Human-in-the-loop guard for large automatic changes."""
        share = rows / max(total, 1)
        if share <= HIGH_RISK_SHARE:
            return True, "low"
        return (rule in self.approved), "high"

    def _fix(self, gate, check, rule, table, rows, total, explanation, sample=None) -> bool:
        if rows == 0:
            return False
        ok, risk = self.fix_allowed(rule, rows, total)
        if ok:
            self.log(Decision(gate, check, rule, "FIX", table, rows, explanation, sample or [], risk))
        else:
            self.log(Decision(gate, check, rule, "FLAG", table, rows,
                              explanation + f" NOT applied: touches {rows/total:.0%} of rows, waiting for "
                              f"human approval of rule {rule} in configs/approvals.json.",
                              sample or [], risk, "pending_approval"))
        return ok

    def persist(self) -> None:
        if not self.decisions:
            return
        df = pd.DataFrame([asdict(d) for d in self.decisions])
        df["sample"] = df["sample"].map(lambda s: json.dumps(s, default=str)[:2000])
        df["run_id"] = self.run_id
        df = df.rename(columns={"check": "check_name", "table": "table_name"})
        cols = ["decision_id", "run_id", "ts_utc", "gate", "check_name", "rule", "action", "table_name",
                "rows_affected", "risk", "status", "explanation", "sample"]
        self.con.register("_qlog", df[cols])
        self.con.execute("INSERT INTO quality_log SELECT * FROM _qlog")
        self.con.unregister("_qlog")

    def summary(self) -> dict:
        acts = pd.Series([d.action for d in self.decisions]).value_counts().to_dict()
        failed = [d for d in self.decisions if d.action == "FAIL"]
        return {"run_id": self.run_id, "status": "fail" if failed else "pass", "counts": acts,
                "decisions": [asdict(d) for d in self.decisions]}

    # ================================================================== GATE 1: raw extract -> cleaned
    def gate1_idsr(self, ext: pd.DataFrame, parse_problems: pd.DataFrame) -> pd.DataFrame:
        g, t = "gate1", "cleaned.idsr_cases"
        df = ext.copy()
        n = len(df)

        # R0 - PDF font ligatures leak as "(cid:NNN)": "Abbo(cid:425)abad" = Abbottabad. Only the two
        # codes verified against bulletin pages are replaced; any other cid code is left and flagged.
        lig = {"(cid:425)": "tt", "(cid:415)": "ti"}
        for col in ("place", "disease"):
            s = df[col].astype(str)
            hit = s.str.contains("(cid:", regex=False)
            if hit.sum():
                before = s[hit].unique()[:3].tolist()
                for k, v in lig.items():
                    s = s.str.replace(k, v, regex=False)
                df[col] = s
                self.log(Decision(g, f"ligature_{col}", "IDSR-R0", "FIX", t, int(hit.sum()),
                                  f"{int(hit.sum())} '{col}' values contained PDF ligature codes; replaced (cid:425)->'tt' "
                                  "and (cid:415)->'ti' (checked on the PDF page, e.g. Abbo(cid:425)abad = Abbottabad).",
                                  before))
                left = df[col].astype(str).str.contains("(cid:", regex=False)
                if left.sum():
                    self.log(Decision(g, f"ligature_unknown_{col}", "IDSR-R0", "FLAG", t, int(left.sum()),
                                      "Unknown (cid:NNN) codes left as printed.", df.loc[left, col].unique()[:5].tolist()))

        # R1 - type: "5,133" -> 5133 (certain, reversible: value_raw is kept)
        raw = df["value_raw"].astype(str).str.strip()
        has_sep = raw.str.contains(",", regex=False)
        numeric = raw.str.replace(",", "", regex=False).str.fullmatch(r"\d+")
        df["cases"] = pd.array(np.where(numeric, raw.str.replace(",", "", regex=False), None), dtype="object")
        df["cases"] = pd.to_numeric(df["cases"], errors="coerce").astype("Int64")
        if has_sep.sum():
            # casting is applied regardless of share: it is lossless and value_raw stays in the table
            self.log(Decision(g, "type_cast", "IDSR-R1", "FIX", t, int(has_sep.sum()),
                              f"{int(has_sep.sum())} counts used thousands separators (e.g. '5,133'). Removed the "
                              "separator and cast to integer. Lossless; the original string stays in value_raw.",
                              raw[has_sep].head(3).tolist()))

        # R2 - "NR" = not reported. Missing stays missing (never zero).
        nr = raw.str.upper().isin(MISSING_TOKENS)
        df["value_status"] = np.where(numeric, "reported", np.where(nr, "not_reported", "unparseable"))
        if nr.sum():
            by = df[nr].groupby(["province"]).size().sort_values(ascending=False).head(5).to_dict()
            self.log(Decision(g, "not_reported", "IDSR-R2", "FLAG", t, int(nr.sum()),
                              f"{int(nr.sum())} cells say 'NR' (area did not report that week), mostly {by}. Kept as "
                              "missing, NOT zero - a zero would tell the model that no one fell ill.", []))
        bad = ~numeric & ~nr
        if bad.sum():
            self.log(Decision(g, "unparseable_value", "IDSR-R3", "FLAG", t, int(bad.sum()),
                              f"{int(bad.sum())} cells are neither a count nor 'NR'; kept as missing for review.",
                              raw[bad].head(5).tolist()))

        # R4 - the week printed inside the PDF beats the listing title (the PDF is the primary record)
        mism = df["stated_week"].notna() & ((df["stated_week"] != df["listing_week"]) |
                                            (df["stated_year"] != df["listing_year"]))
        if mism.sum():
            files = df.loc[mism, "source_file"].unique().tolist()
            if self._fix(g, "week_label", "IDSR-R4", t, int(mism.sum()), n,
                         f"{len(files)} bulletins print a different week/year inside the PDF than in the NIH "
                         "listing title. Used the week printed in the PDF.", files[:5]):
                df.loc[mism, "week"] = df.loc[mism, "stated_week"].astype(int)
                df.loc[mism, "year"] = df.loc[mism, "stated_year"].astype(int)

        # R5 - province could not be read from the table title
        unk = df["province"].astype(str).str.startswith("UNKNOWN")
        if unk.sum():
            self.log(Decision(g, "province_unknown", "IDSR-R5", "FLAG", t, int(unk.sum()),
                              "Table title did not name a known province; rows excluded from gold.",
                              df.loc[unk, "province"].unique().tolist()[:5]))
            self.quarantine.append(df[unk].assign(quarantine_reason="IDSR-R5 province not readable"))
            df = df[~unk]

        # R6 - exact duplicate keys (same district listed twice, or a re-uploaded bulletin)
        key = ["year", "week", "province", "place", "disease", "table"]
        dup = df.duplicated(key, keep="first")
        if dup.sum():
            same = df[dup].merge(df[~dup][key + ["cases"]], on=key, suffixes=("", "_kept"))
            conflicting = int((same["cases"] != same["cases_kept"]).fillna(True).sum())
            if self._fix(g, "duplicates", "IDSR-R6", t, int(dup.sum()), n,
                         f"{int(dup.sum())} rows repeat a (week, province, place, disease) key; kept the first "
                         f"occurrence. {conflicting} of them had a different count - those are listed in sample.",
                         same[same["cases"] != same["cases_kept"]].head(5)[key + ["cases", "cases_kept"]]
                         .to_dict("records")):
                df = df[~dup]

        # R7 - parser problems become visible decisions, not silent drops
        if len(parse_problems):
            for issue, grp in parse_problems.groupby("issue"):
                self.log(Decision(g, f"parse_{issue}", "IDSR-R7", "FLAG", "raw.nih_idsr", len(grp),
                                  f"{len(grp)} parser findings of type '{issue}'. "
                                  + ("Whole bulletins skipped: their layout embeds tables in text (2021-22)."
                                     if issue == "no_supported_table" else
                                     "Rows skipped because header and value counts disagree; no guessing."),
                                  grp["detail"].head(5).tolist()))

        # Tool run: pandera schema on the typed frame
        fc = failures(IDSR_SCHEMA, df)
        if len(fc):
            self.log(Decision(g, "schema_idsr", "IDSR-SCHEMA", "FLAG", t, len(fc),
                              f"pandera found {len(fc)} schema violations (column/check pairs: "
                              f"{fc.groupby(['column','check']).size().head(5).to_dict()}).",
                              fc.head(5).astype(str).to_dict("records")))
        else:
            self.log(Decision(g, "schema_idsr", "IDSR-SCHEMA", "PASS", t, 0,
                              f"pandera schema passed on {len(df)} rows.", status="info"))
        return df

    def flag_outliers(self, dw: pd.DataFrame) -> pd.DataFrame:
        """Robust outlier test per district+disease on log counts (median / MAD). Values are kept:
        a huge week can be a real outbreak, so we flag and let the user see it."""
        g, t = "gate1", "cleaned.idsr_cases"
        x = np.log1p(dw["cases"].astype("float"))
        grp = dw.groupby(["district_id", "disease"])["cases"]
        med = grp.transform(lambda s: np.log1p(s.astype(float)).median())
        mad = grp.transform(lambda s: (np.log1p(s.astype(float)) - np.log1p(s.astype(float)).median()).abs().median())
        z = (x - med) / (1.4826 * mad.replace(0, np.nan))
        dw["is_outlier"] = (z.abs() > 6) & (dw["cases"] > 50)
        k = int(dw["is_outlier"].sum())
        if k:
            s = dw[dw["is_outlier"]].nlargest(5, "cases")[["district", "disease", "week_start", "cases"]]
            self.log(Decision(g, "outlier_robust_z", "IDSR-R8", "FLAG", t, k,
                              f"{k} district-weeks are > 6 robust SD from that district's usual level. Could be a "
                              "real outbreak or a typing error in the bulletin, so values are kept and marked "
                              "is_outlier for the user to check against the PDF page.",
                              s.astype(str).to_dict("records")))
        return dw

    def gate1_weather(self, w: pd.DataFrame) -> pd.DataFrame:
        g, t = "gate1", "cleaned.weather_hourly"
        df = w.rename(columns={"temperature_2m": "temperature_c", "relative_humidity_2m": "rh_pct",
                               "precipitation": "precip_mm"}).copy()
        df["ts_utc"] = pd.to_datetime(df["ts_utc"], utc=True).dt.tz_localize(None)
        n = len(df)

        # W1 - unit check: Open-Meteo says the unit in hourly_units; Kelvin would be > 200
        kelvin = (df["units_temp"].fillna("").str.upper().str.contains("K")) | (df["temperature_c"] > 200)
        if kelvin.sum() and self._fix(g, "unit_temperature", "WX-W1", t, int(kelvin.sum()), n,
                                      f"{int(kelvin.sum())} temperatures were in Kelvin; converted with C = K - 273.15."):
            df.loc[kelvin, "temperature_c"] -= 273.15
        else:
            self.log(Decision(g, "unit_temperature", "WX-W1", "PASS", t, 0,
                              "All temperatures declared in deg C by the API (hourly_units) and none above 200.",
                              status="info"))

        # W2 - archive and forecast overlap: one value per (district, hour). Archive (reanalysis) wins,
        # then the newest forecast run.
        df["_prio"] = np.where(df["kind"] == "archive", 0, 1)
        df = df.sort_values(["district_id", "ts_utc", "_prio", "fetch_stamp"], ascending=[True, True, True, False])
        dup = df.duplicated(["district_id", "ts_utc"], keep="first")
        if dup.sum():
            self.log(Decision(g, "overlap_duplicates", "WX-W2", "FIX", t, int(dup.sum()),
                              f"{int(dup.sum())} hourly rows exist in both archive and forecast (or several forecast "
                              "runs). Kept archive first, otherwise the newest forecast run. Certain: same hour, "
                              "same place, better source.", risk="low"))
            df = df[~dup]
        df = df.drop(columns="_prio")
        # forecast hours in the future are context only, never used as observations
        now = pd.Timestamp.now(tz="UTC").tz_localize(None)
        df["is_forecast"] = df["ts_utc"] > now

        # Tool run: pandera ranges
        obs = df[~df["is_forecast"]]
        fc = failures(WEATHER_SCHEMA, obs)
        if len(fc):
            idx = fc["index"].dropna().astype(int).unique()
            df.loc[df.index.isin(idx), "is_suspect"] = True
            self.log(Decision(g, "schema_weather", "WX-SCHEMA", "FLAG", t, len(idx),
                              f"{len(idx)} hourly rows outside physical ranges; kept and marked is_suspect.",
                              fc.head(5).astype(str).to_dict("records")))
        df["is_suspect"] = df.get("is_suspect", False)
        df["is_suspect"] = df["is_suspect"].fillna(False).astype(bool)

        # W3 - gaps: <= 3 missing hours -> FILL (linear, marked); longer -> FLAG and stay missing
        df = self._fill_hourly_gaps(df[~df["is_forecast"]], t).pipe(
            lambda o: pd.concat([o, df[df["is_forecast"]].assign(is_filled=False)], ignore_index=True))
        return df

    def _fill_hourly_gaps(self, obs: pd.DataFrame, t: str) -> pd.DataFrame:
        out, short, long_ = [], 0, 0
        long_examples = []
        for did, d in obs.groupby("district_id", sort=False):
            d = d.set_index("ts_utc").sort_index()
            full = pd.date_range(d.index.min(), d.index.max(), freq="h")
            d = d.reindex(full)
            d["district_id"] = did
            miss = d["temperature_c"].isna()
            # run-length of each missing block
            block = (miss != miss.shift()).cumsum()
            runlen = miss.groupby(block).transform("sum")
            fillable = miss & (runlen <= 3)
            for c in ("temperature_c", "rh_pct", "precip_mm"):
                interp = d[c].interpolate(limit=3, limit_area="inside")
                d[c] = d[c].where(~fillable, interp)
            d["is_filled"] = fillable & d["temperature_c"].notna()
            short += int(d["is_filled"].sum())
            lg = int((miss & ~fillable).sum())
            if lg:
                long_ += lg
                long_examples.append({"district_id": did, "missing_hours": lg})
            out.append(d.rename_axis("ts_utc").reset_index())
        res = pd.concat(out, ignore_index=True) if out else obs.assign(is_filled=False)
        if short:
            self.log(Decision("gate1", "hourly_gaps_short", "WX-W3", "FILL", t, short,
                              f"{short} hourly values missing in gaps of <= 3 hours were linearly interpolated "
                              "(temperature, humidity and rain change smoothly over such spans). Marked is_filled."))
        if long_:
            self.log(Decision("gate1", "hourly_gaps_long", "WX-W3", "FLAG", t, long_,
                              f"{long_} hourly values are in gaps longer than 3 hours; left missing, weekly "
                              "aggregates report how many hours they are based on.", long_examples[:5]))
        if not short and not long_:
            self.log(Decision("gate1", "hourly_gaps", "WX-W3", "PASS", t, 0, "No hourly gaps found.", status="info"))
        return res

    # ================================================================== GATE 2: cleaned -> gold
    def gate2(self, gold: pd.DataFrame, prev_rows: int | None, freshness: dict,
              cons: pd.DataFrame | None = None) -> bool:
        g, t = "gate2", "gold.district_week"
        ok = True

        fc = failures(GOLD_SCHEMA, gold)
        if len(fc):
            ok = False
            self.log(Decision(g, "schema_gold", "GOLD-SCHEMA", "FAIL", t, len(fc),
                              "Gold table breaks its contract (unique row_id, non-null keys, cases >= 0). Run "
                              "stopped; dashboard keeps the last good gold.", fc.head(5).astype(str).to_dict("records")))
        else:
            self.log(Decision(g, "schema_gold", "GOLD-SCHEMA", "PASS", t, 0,
                              f"Gold contract holds for {len(gold)} rows.", status="info"))

        # G1 - the "top-10 trap": district reported that week but the disease was not among the
        # columns printed for its province -> not_listed, NOT zero.
        nl = gold["cases_status"].eq("not_listed")
        if nl.sum():
            by = gold[nl].groupby("disease").size().to_dict()
            self.log(Decision(g, "top10_not_listed", "GOLD-G1", "FLAG", t, int(nl.sum()),
                              f"{int(nl.sum())} district-weeks have no value because the bulletin only prints each "
                              f"province's most frequent diseases that week ({by}). Kept missing: 'not in the "
                              "top list' does not mean zero cases."))

        # G2 - weeks with no bulletin at all
        miss_w = gold["cases_status"].eq("no_bulletin")
        if miss_w.sum():
            self.log(Decision(g, "missing_bulletin_week", "GOLD-G2", "FLAG", t, int(miss_w.sum()),
                              f"{gold.loc[miss_w, 'week_start'].nunique()} weeks have no usable bulletin "
                              "(not published, or 2021-22 layout). Left missing; never interpolated for the target."))

        # G3 - feature fill: a single missing week between two reported weeks
        filled = int(gold["lag_source_filled"].sum())
        if filled:
            self.log(Decision(g, "single_week_gap", "GOLD-G3", "FILL", t, filled,
                              f"{filled} single-week gaps were linearly interpolated ONLY in cases_for_features "
                              "(used to build lag features). The target column `cases` stays missing and those "
                              "weeks are never scored."))

        # G4 - consistency: sum of districts vs the province 'Total' row in the same table

        if cons is not None and len(cons):
            badc = cons[cons["rel_diff"].abs() > 0.02]
            act = "FLAG" if len(badc) else "PASS"
            self.log(Decision(g, "district_sum_vs_total", "GOLD-G4", act, t, len(badc),
                              f"Checked {len(cons)} province-week totals against the sum of their districts; "
                              f"{len(badc)} differ by more than 2% (often a district missing from the table).",
                              badc.nlargest(5, "abs_diff").astype(str).to_dict("records"),
                              status="applied" if len(badc) else "info"))

        # G5 - freshness per source
        for src, info in freshness.items():
            if info.get("late"):
                self.log(Decision(g, "freshness", f"FRESH-{src}", "FLAG", src, 0,
                                  f"Source {src} is late: newest data {info['newest']} is {info['age_h']:.0f} h old "
                                  f"(allowed {info['sla_h']} h). Dashboard shows it as stale."))

        # G6 - volume regression (a broken parse that silently loses rows)
        if prev_rows:
            drop = 1 - len(gold) / prev_rows
            if drop > 0.10:
                ok = False
                self.log(Decision(g, "volume_regression", "GOLD-G6", "FAIL", t, prev_rows - len(gold),
                                  f"Gold would shrink by {drop:.0%} versus the last good run ({prev_rows} -> "
                                  f"{len(gold)} rows). Stopped; last good gold kept."))
        return ok

    def drift(self, ref: pd.Series, cur: pd.Series, name: str, bins: int = 10) -> float:
        """Population Stability Index between a reference window and the latest window."""
        ref, cur = ref.dropna().astype(float), cur.dropna().astype(float)
        if len(ref) < 50 or len(cur) < 20:
            return float("nan")
        edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
        r = np.histogram(ref, edges)[0] / len(ref) + 1e-4
        c = np.histogram(cur.clip(edges[0], edges[-1]), edges)[0] / len(cur) + 1e-4
        psi = float(np.sum((c - r) * np.log(c / r)))
        act = "FLAG" if psi > 0.25 else "PASS"
        self.log(Decision("gate2", f"drift_{name}", "DRIFT-PSI", act, "gold.district_week", 0,
                          f"PSI of {name} (last 8 weeks vs training period) = {psi:.3f}. "
                          + ("> 0.25: new data no longer looks like training data -> retraining triggered."
                             if psi > 0.25 else "<= 0.25: no meaningful drift."),
                          status="applied" if psi > 0.25 else "info"))
        return psi
