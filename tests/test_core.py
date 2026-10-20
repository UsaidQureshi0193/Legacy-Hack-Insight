"""Unit tests for the rules the jury will ask about. Run: make test"""
from __future__ import annotations

import numpy as np
import pandas as pd
import duckdb
import pytest

from hackinsight.ingest.parse_idsr import _join_split_names, disease_key
from hackinsight.ingest.nih_idsr import parse_listing
from hackinsight.quality.agent import DataQualityAgent
from hackinsight.model import features
from hackinsight.warehouse.build import stable_id, week_start


# ------------------------------------------------------------------ parsing
def test_split_district_names_are_joined():
    body = [["", "Tando", "", "1,728", "1,334"], [None, "Muhammad", None, None, None],
            ["", "Khan", "", "1,728", "1,334"], ["", "Thatta", "", "1,637", "1,626"]]
    out = _join_split_names(body)
    assert out[0] == ("Tando Muhammad Khan", ["1,728", "1,334"])
    assert out[1][0] == "Thatta"


def test_split_names_layout_2025():
    body = [["Mirpurkhas", "2,188", "3,210"], ["Naushero"], ["1,538", "1,123"], ["Feroze"], [],
            ["Tando Allahyar", "5", "6"], ["Tando"], ["Muhammad"], ["7", "8"], ["Khan"], ["Thatta", "1", "2"]]
    names = [n for n, _ in _join_split_names(body)]
    assert names == ["Mirpurkhas", "Naushero Feroze", "Tando Allahyar", "Tando Muhammad Khan", "Thatta"]
    body_c = [["Tando Allahyar", "5", "6"], ["Tando"], ["Muhammad", "7", "8"], ["Khan"], ["Tharparkar", "1", "2"],
              ["Thatta", "3", "4"]]
    assert [n for n, _ in _join_split_names(body_c)] == ["Tando Allahyar", "Tando Muhammad Khan", "Tharparkar", "Thatta"]
    body_a = [["Karachi", "21", "585"], ["Keamari"], ["Karachi Korangi", "70", "433"], ["Karachi Malir", "1", "2"]]
    assert [n for n, _ in _join_split_names(body_a)] == ["Karachi Keamari", "Karachi Korangi", "Karachi Malir"]


def test_all_zero_rows_are_not_merged():
    body = [["Awaran", "0", "0"], ["Barkhan", "0", "0"]]
    assert [n for n, _ in _join_split_names(body)] == ["Awaran", "Barkhan"]


def test_disease_keys_normalise_pdf_line_breaks():
    assert disease_key("Typhoi\nd") == "typhoid"
    assert disease_key("AD (Non-\nCholera)") == "ad_non_cholera"
    assert disease_key("Malaria") == "malaria"


def test_listing_parser_uses_title_not_filename():
    html = ("<a href='/wp-content/uploads/2026/10/IDSR weekly report 38-NG (1).pdf?v=14' "
            "title='Download IDSRS Week 38 Bulletin (2026)'>x</a>")
    b = parse_listing(html)
    assert (b[0].year, b[0].week) == (2026, 38)
    assert "%20" in b[0].url and b[0].dest.name == "2026_W38.pdf"


# ------------------------------------------------------------------ quality agent
@pytest.fixture
def agent():
    return DataQualityAgent(duckdb.connect(), "test-run")


def _ext(values):
    n = len(values)
    return pd.DataFrame({"year": [2026] * n, "week": [39] * n, "province": ["Sindh"] * n,
                         "place": [f"D{i}" for i in range(n)], "place_type": ["district"] * n,
                         "disease": ["malaria"] * n, "value_raw": values, "source_file": ["x.pdf"] * n,
                         "page": [7] * n, "table": ["district"] * n, "listing_year": [2026] * n,
                         "listing_week": [39] * n, "stated_week": [39] * n, "stated_year": [2026] * n})


def test_nr_is_missing_not_zero(agent):
    out = agent.gate1_idsr(_ext(["5,133", "NR", "0"]), pd.DataFrame())
    assert out["cases"].tolist()[0] == 5133
    assert pd.isna(out["cases"].tolist()[1])               # NR -> missing
    assert out["value_status"].tolist()[1] == "not_reported"
    assert out["cases"].tolist()[2] == 0                    # a printed zero stays zero
    acts = {d.rule: d.action for d in agent.decisions}
    assert acts["IDSR-R1"] == "FIX" and acts["IDSR-R2"] == "FLAG"


def test_short_gap_filled_long_gap_flagged(agent):
    ts = pd.date_range("2025-01-01", periods=30, freq="h")
    t = np.linspace(10, 20, 30)
    t[5:7] = np.nan          # 2-hour gap  -> FILL
    t[15:22] = np.nan        # 7-hour gap  -> FLAG, stays missing
    w = pd.DataFrame({"district_id": "PK1", "ts_utc": ts.astype(str), "temperature_2m": t,
                      "relative_humidity_2m": 50.0, "precipitation": 0.0, "units_temp": "°C",
                      "units_precip": "mm", "source_file": "f", "fetch_stamp": None, "kind": "archive"})
    w.loc[5:6, ["relative_humidity_2m", "precipitation"]] = np.nan
    w.loc[15:21, ["relative_humidity_2m", "precipitation"]] = np.nan
    out = agent.gate1_weather(w).sort_values("ts_utc").reset_index(drop=True)
    assert out.loc[5:6, "is_filled"].all() and out.loc[5:6, "temperature_c"].notna().all()
    assert out.loc[15:21, "temperature_c"].isna().all() and not out.loc[15:21, "is_filled"].any()
    rules = {(d.rule, d.action) for d in agent.decisions}
    assert ("WX-W3", "FILL") in rules and ("WX-W3", "FLAG") in rules


def test_high_risk_fix_needs_human_approval(agent):
    ok, risk = agent.fix_allowed("IDSR-R6", rows=50, total=100)
    assert not ok and risk == "high"
    ok, risk = agent.fix_allowed("IDSR-R6", rows=1, total=100)
    assert ok and risk == "low"


# ------------------------------------------------------------------ gold + features
def test_row_id_is_stable():
    a = stable_id("PK601", "malaria", week_start(2026, 39).date())
    b = stable_id("PK601", "malaria", week_start(2026, 39).date())
    assert a == b and len(a) == 16


def _gold(n=60):
    w = pd.date_range("2024-01-01", periods=n, freq="7D")
    return pd.DataFrame({"row_id": [f"r{i}" for i in range(n)], "district_id": "PK1", "district": "D",
                         "province": "Sindh", "disease": "malaria", "week_start": w,
                         "cases": pd.array(np.arange(n) * 10, dtype="Int64"), "cases_status": "reported",
                         "cases_for_features": np.arange(n) * 10.0, "population_2017": 1e6, "mpi": 0.2,
                         "precip_sum_mm": 1.0, "temp_mean_c": 30.0, "rh_mean_pct": 60.0, "rain_hours": 2})


def test_features_use_only_past_and_target_is_next_week():
    f = features.build(_gold(), "malaria")
    r = f.iloc[10]
    assert r["target"] == 110                   # week t+1
    assert np.isclose(r["lag0_log"], np.log1p(100))   # week t
    assert np.isclose(r["lag1_log"], np.log1p(90))    # week t-1
    assert r["last_value"] == 100               # baseline input = this week
    # no feature equals the target (leak check)
    cols = features.feature_columns(f)
    assert not any(np.isclose(f.iloc[10][c], np.log1p(110)) for c in cols if c.endswith("_log"))


# ------------------------------------------------------------------ manifest
def test_manifest_detects_edited_raw_file(tmp_path, monkeypatch):
    from hackinsight import config
    from hackinsight.manifest import Manifest
    monkeypatch.setattr(config, "ROOT", tmp_path)
    f = tmp_path / "data" / "raw" / "x.csv"
    f.parent.mkdir(parents=True)
    f.write_text("a,b\n1,2\n")
    m = Manifest(tmp_path / "manifest.csv")
    m.record(f, "test", "http://example", "CC0")
    assert m.verify() == []
    f.write_text("a,b\n1,3\n")                  # someone edits raw evidence
    assert any("mismatch" in p for p in m.verify())


# ------------------------------------------------------------------ agent number rule
def test_llm_wording_rules():
    from hackinsight.rag.agent import validate_llm_text
    sids = {"S1", "S2"}
    assert validate_llm_text("Remove standing water every week [S1]\nUse mosquito nets [S2]", sids)
    assert not validate_llm_text("Cases will rise 40% [S1]", sids)            # invented number
    assert not validate_llm_text("کیسز ۴۰ فیصد بڑھیں گے [S1]", sids)            # Urdu digits too
    assert not validate_llm_text("Use mosquito nets", sids)                   # no citation
    assert not validate_llm_text("Use mosquito nets [S3]", sids)              # citation to nothing


# ------------------------------------------------------------------ PII filter
def test_pii_is_masked_before_logging():
    from hackinsight.rag.pii import mask
    text, found = mask("Call me on 0300-1234567, CNIC 35202-1234567-1, mail ali@x.pk: which districts?")
    assert "0300" not in text and "35202" not in text and "ali@" not in text
    assert set(found) >= {"PHONE", "CNIC", "EMAIL"}
    assert mask("Which districts in Sindh?")[1] == []
