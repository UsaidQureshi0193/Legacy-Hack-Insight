"""Sources 3 and 4 - OpenDengue (benchmark history) and HDX context tables.

These change rarely, so each file is fetched once and then only re-verified by hash.
"""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pandas as pd

from .. import config, http
from ..manifest import Manifest

FILES = {
    "opendengue": (config.OPENDENGUE_URL, config.RAW / "opendengue" / "Temporal_extract_V1_3.zip",
                   config.OPENDENGUE_LICENCE),
    "hdx_mpi": (config.HDX_MPI_URL, config.RAW / "hdx" / "pak_mpi.csv", config.HDX_MPI_LICENCE),
    "hdx_boundaries": (config.HDX_BOUNDARIES_URL, config.RAW / "hdx" / "pak_admin_boundaries.geojson.zip",
                       config.HDX_BOUNDARIES_LICENCE),
    "hdx_population": (config.HDX_POP_URL, config.RAW / "hdx" / "pakistan_population_statistics_2017.xlsx",
                       config.HDX_POP_LICENCE),
}


def fetch(manifest: Manifest) -> int:
    n = 0
    for key, (url, dest, licence) in FILES.items():
        if manifest.has_valid(dest):
            continue
        http.download(url, dest)
        manifest.record(dest, "opendengue" if key == "opendengue" else "hdx", url, licence)
        n += 1
        print(f"  {key:15s} {dest.name}")
    manifest.save()
    return n


def load_opendengue_pakistan() -> pd.DataFrame:
    with zipfile.ZipFile(FILES["opendengue"][1]) as z:
        name = next(n for n in z.namelist() if n.endswith(".csv"))
        df = pd.read_csv(z.open(name), low_memory=False)
    df = df[df["adm_0_name"].str.upper() == "PAKISTAN"].copy()
    df["source_file"] = Manifest.rel(FILES["opendengue"][1])
    return df


def load_mpi() -> pd.DataFrame:
    df = pd.read_csv(FILES["hdx_mpi"][1])
    df["source_file"] = Manifest.rel(FILES["hdx_mpi"][1])
    return df


def load_adm2() -> pd.DataFrame:
    """District (ADM2) attributes from the OCHA boundaries: pcode, names, area and the centroid
    that OCHA publishes (center_lat/center_lon) - so we never compute geometry ourselves."""
    with zipfile.ZipFile(FILES["hdx_boundaries"][1]) as z:
        g = json.load(z.open("pak_admin2.geojson"))
    df = pd.DataFrame([f["properties"] for f in g["features"]])
    df = df[["adm2_pcode", "adm2_name", "adm1_pcode", "adm1_name", "area_sqkm", "center_lat", "center_lon",
             "valid_on"]]
    df["source_file"] = Manifest.rel(FILES["hdx_boundaries"][1])
    return df


def load_population() -> pd.DataFrame:
    path = FILES["hdx_population"][1]
    xl = pd.ExcelFile(path)
    sheet = next((s for s in xl.sheet_names if "adm2" in s.lower()), xl.sheet_names[-1])
    df = xl.parse(sheet)
    df["source_file"] = Manifest.rel(path)
    return df
