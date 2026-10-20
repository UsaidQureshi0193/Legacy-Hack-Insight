# Datasheet for the HackInsight malaria & dengue warehouse

Structure follows "Datasheets for Datasets" (Gebru et al.). Every count in this file is in `manifest.csv`,
`data/warehouse.duckdb` or `artifacts/*.json`.

## Motivation
District health officers in Sindh, Balochistan and Khyber Pakhtunkhwa need next week's likely malaria and dengue
burden per district, so they can pre-position kits, medicines and vector-control teams. No public, machine-readable
district series existed: the numbers are locked in weekly PDF bulletins.

## Composition
| Table | One row is | Key | Notes |
|---|---|---|---|
| `cleaned.idsr_cases` | one cell of an IDSR bulletin table (place × disease × week) | year, week, province, place, disease, table | ~204k rows, `value_raw` kept next to the typed `cases` |
| `cleaned.weather_hourly` | one district centroid × hour (UTC) | district_id, ts_utc | Open-Meteo archive + 7-day forecast; `is_filled`, `is_suspect`, `is_forecast` |
| `gold.district_week` | district × disease × ISO week | `row_id` (stable hash) | `cases_status` explains every missing value; PDF file + page for every number |
| `gold.province_week` | province × disease × week (national table) | province, week_start, disease | used for consistency checks and local validation |
| `quarantine.idsr_rows` | a row that could not enter gold | (as in cleaned) | with `quarantine_reason`; never silently dropped |
| `quality_log` | one Data Quality Agent decision | decision_id | gate, rule, FIX/FILL/FLAG/FAIL, rows affected, explanation |

No personal or patient-level data: the smallest unit is a district-week count.

## Collection
* **NIH IDSR bulletins** (nih.org.pk): the listing page is scraped, the year and week come from the link title, and
  each PDF is saved unedited as `data/raw/nih_idsr/<year>_W<week>.pdf`. robots.txt allows this. Weekly.
* **Open-Meteo** archive (ERA5) and forecast API: hourly temperature, humidity and rain at each OCHA district
  centroid. The API hosts' robots.txt says `Disallow: /` (aimed at crawlers), while the API terms grant free
  non-commercial programmatic use. We kept the source under the API terms, flagged it at Gate 0, and stay under 90%
  of the rate limits with a call ledger. *To confirm with the organisers.*
* **OpenDengue v1.3** (Figshare, CC BY 4.0) and **HDX** (OCHA boundaries CC BY-IGO, 2017 census population, OPHI MPI
  CC0): fetched once, then re-verified by SHA-256.
* All files were fetched during the event (see `fetched_at_utc` in the manifest). Each cleaned row carries
  `ingested_at_utc` (when we fetched it) next to its event time (`week_start` / `ts_utc`).

## Preprocessing (all in code, logged in `quality_log`)
Thousands separators removed (FIX). `NR` kept missing (FLAG). PDF ligature codes repaired (FIX). Split district names
re-joined with a reviewed gazetteer. Names matched to OCHA pcodes, with unmatched names quarantined. Sub-units summed
into their OCHA district (FIX). Weather gaps of up to 3 hours are interpolated and marked (FILL); longer gaps are
flagged. The top-10 trap is kept as `not_listed`, never zero.

## Known biases and gaps
* Cases are **suspected**, not confirmed. Reporting completeness varies by district (NIH compliance tables).
* Each bulletin prints only each province's most frequent diseases, so **dengue is often not listed**, especially in
  Sindh.
* Punjab and ICT districts are not in the district tables (`NR`).
* The 2021-22 bulletins have a different layout and are not parsed (flagged).
* There is no age or sex breakdown, so fairness by gender cannot be measured.
* Population comes from the 2017 census. Five districts created after 2022 have no OCHA pcode (quarantined).
* Weather history is backfilled across several hourly runs because of the API budget.

## Uses
Intended: district-level planning signals with citations. Not intended: diagnosis, individual decisions, Punjab/ICT,
or any commercial use of the Open-Meteo free tier.

## Maintenance
The hourly Prefect flow re-fetches, re-validates and republishes. The manifest's hashes detect edited or changed files.
The parser has a version number: when it changes, the bulletins are re-parsed.
