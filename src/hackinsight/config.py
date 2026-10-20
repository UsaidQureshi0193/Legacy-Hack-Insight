"""Central configuration: every path and constant the pipeline uses lives here.

Keeping these in one place means `make data` always writes to the same layout,
and nobody hard-codes a path inside a module.
"""
from __future__ import annotations

import os
from pathlib import Path

def _load_dotenv(path: Path) -> None:
    """Read KEY=VALUE lines from the repo's .env (secrets stay out of code and out of git).
    Variables already set in the shell win over the file."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if value:
            os.environ.setdefault(key.strip(), value)


_load_dotenv(Path(__file__).resolve().parents[2] / ".env")
ROOT = Path(os.environ.get("HI_ROOT", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
RAW = DATA / "raw"            # files exactly as fetched (evidence, never edited)
CLEANED = DATA / "cleaned"    # typed, unit-fixed, de-duplicated, flagged
PROCESSED = DATA / "processed"  # gold tables with stable row ids
ARTIFACTS = ROOT / "artifacts"  # model files, metrics, rag index, status json
DOCS_LIB = ROOT / "rag_library"  # trusted documents for the Insight Agent

MANIFEST = ROOT / "manifest.csv"
WAREHOUSE = DATA / "warehouse.duckdb"
CONFIGS = ROOT / "configs"

SEED = 42

# Polite HTTP: identify ourselves and never hammer a public server.
USER_AGENT = "HackInsight2026-student-research/1.0 (non-commercial; contact via GitHub repo)"
REQUEST_PAUSE_S = 1.0

# --- Source 1: NIH Pakistan IDSR weekly bulletins -------------------------------------------
NIH_LISTING_URL = "https://www.nih.org.pk/phb/weekly-bulletin"
NIH_BASE = "https://www.nih.org.pk"
NIH_LICENCE = "Public government report, NIH Islamabad (robots.txt allows all; attribution given)"

# --- Source 2: Open-Meteo historical weather (ERA5-based archive) --------------------------
OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
OPEN_METEO_FORECAST = "https://api.open-meteo.com/v1/forecast"
OPEN_METEO_LICENCE = "CC BY 4.0, Open-Meteo.com (free tier: non-commercial use)"
WEATHER_START = "2023-01-01"  # IDSR district tables are parseable from mid-2023
WEATHER_HOURLY_VARS = ["temperature_2m", "relative_humidity_2m", "precipitation"]

# --- Source 3: OpenDengue v1.3 (benchmark history) -----------------------------------------
OPENDENGUE_URL = "https://ndownloader.figshare.com/files/54854156"  # Temporal_extract_V1_3.zip
OPENDENGUE_LICENCE = "CC BY 4.0, OpenDengue (Clarke et al. 2024, doi:10.6084/m9.figshare.24259573)"

# --- Source 4: HDX context tables ----------------------------------------------------------
HDX_MPI_URL = ("https://data.humdata.org/dataset/6b9b9c1c-e43c-45e2-a411-621dc1f5a641/resource/"
               "194b2580-bbd8-4a65-a72c-7afd879e1ad3/download/pak_mpi.csv")
HDX_MPI_LICENCE = "CC0, Oxford Poverty & Human Development Initiative via HDX"
HDX_BOUNDARIES_URL = ("https://data.humdata.org/dataset/a64d1ff2-7158-48c7-887d-6af69ce21906/resource/"
                      "c6521e04-75e1-41be-8e02-0554a424d9f4/download/pak_admin_boundaries.geojson.zip")
HDX_BOUNDARIES_LICENCE = "CC BY-IGO, OCHA FISS via HDX"
HDX_POP_URL = ("https://data.humdata.org/dataset/9d48b019-3dd3-4284-ac11-200a70370447/resource/"
               "52b7f314-80a9-4759-87aa-2d3a49c2436b/download/pakistan_population_statistics_2017_20190603.xlsx")
HDX_POP_LICENCE = "OCHA COD-PS, source PBS Census 2017, via HDX"

# --- Modelling ------------------------------------------------------------------------------
TARGET_DISEASES = ["malaria", "dengue"]
PRIMARY_DISEASE = "malaria"
# Time-based split by ISO week start date (train < VAL_START <= val < TEST_START <= test).
VAL_START = "2025-01-01"
TEST_START = "2025-10-01"
QUANTILES = (0.1, 0.5, 0.9)

# Freshness: a source is "late" when its newest record is older than this many hours.
FRESHNESS_SLA_H = {"nih_idsr": 24 * 14, "open_meteo": 6, "opendengue": 24 * 400, "hdx": 24 * 800}


def ensure_dirs() -> None:
    for p in (RAW, CLEANED, PROCESSED, ARTIFACTS, DOCS_LIB):
        p.mkdir(parents=True, exist_ok=True)
