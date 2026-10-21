# Datasheet for the HackInsight malaria & dengue warehouse

Structure follows "Datasheets for Datasets" (Gebru et al.). Every count in this file is in `manifest.csv`,
`data/warehouse.duckdb` or `artifacts/*.json`.

## Motivation
District health officers in Sindh, Balochistan and Khyber Pakhtunkhwa need next week's likely malaria and dengue
burden per district, so they can decide which districts to prioritise for investigation, surveillance and resource
planning. No public, machine-readable
district series existed: the numbers are locked in weekly PDF bulletins.

## Sources (publisher, licence, update frequency, Gate 0)

| # | Source | Original publisher / URL | Licence / terms | Update frequency | Role | Gate 0 | Independent of NIH IDSR? |
|---|---|---|---|---|---|---|---|
| 1 | IDSR weekly Public Health Bulletins (PDF) | National Institute of Health, Islamabad · nih.org.pk/phb/weekly-bulletin | Public government report, attribution; robots.txt allows the path | Weekly, published 1–2 weeks after the week | Target (district suspected cases) · **not on the track list** | ACCEPT | — |
| 2 | Historical (ERA5) + forecast hourly weather | Open-Meteo · archive-api / api.open-meteo.com | CC BY 4.0; free tier non-commercial | Forecast several times a day; archive daily with a few days' delay | Weather features | ACCEPT_API (robots.txt on the API host disallows crawlers; use under API terms — **to confirm with organisers**) | **Yes** (satellite/reanalysis, not health reports) |
| 3 | 2017 census population, OCHA admin boundaries, OPHI MPI | PBS / OCHA FISS / OPHI via HDX (data.humdata.org) | CC BY-IGO; CC0 | Static (census 2017, boundaries 2022, MPI 2017-18) | District ids, per-100k, poverty groups | ACCEPT | **Yes** |
| 4 | OpenDengue v1.3 temporal extract | Clarke et al. 2024, Figshare doi:10.6084/m9.figshare.24259573 | CC BY 4.0 | Versioned releases | Parser check and dengue history | ACCEPT | **No** — its Pakistan rows come from MoH Pakistan (to 2022) and WHO (2023–25) national reports, i.e. the same reporting system |
| 5 | Advisories, guidelines, fact sheets (RAG only) | NIH Pakistan; WHO | Public, attribution; WHO non-commercial | Ad hoc | Insight Agent library | — | n/a |

The two independent sources required by the rules are **NIH IDSR** and **Open-Meteo** (plus HDX). OpenDengue is not
counted as independent of NIH: because both rest on the same national reports, agreeing with it (15 of 20 months exact)
shows that **our PDF parsing reproduces the national totals correctly**; it does not validate the reports themselves.

### Open-Meteo access: evidence and fallback
* `archive-api.open-meteo.com/robots.txt` and `api.open-meteo.com/robots.txt` both say `User-agent: * / Disallow: /`
  (checked 21 Oct 2026). That is a rule for crawlers on the API hosts; `open-meteo.com/robots.txt` allows everything.
* The Terms of Use (open-meteo.com/en/terms) grant the **free API for non-commercial use**: "Less than 10'000 API calls
  per day, 5'000 per hour and 600 per minute", data under CC BY 4.0. Our call ledger keeps every run under 90% of those
  limits (`artifacts/open_meteo_ledger.json`), and our user agent states non-commercial student research.
* Open-Meteo is listed by the organisers themselves (guide chapter 5, "Where to look" and "Pakistani sources").
* **Organiser confirmation:** _requested on the official channel — add the date and a link/screenshot of the answer here._
* **Fallback if not approved:** set `HI_EXCLUDE_SOURCES=open_meteo` and run `make data` + `make train`. Gate 0 then
  rejects the source, no weather is fetched or read from disk, and the model trains without weather features
  (tested on 21 Oct: the pipeline and quality gate pass, training completes). Weather's measured effect on validation
  MAE was small (128.3 → 127.4, `docs/optimization_log.md`).

## Volume (all gathered during the event, see `fetched_at_utc` in `manifest.csv`)
* IDSR: 203,895 parsed table cells from 177 bulletins (weeks from 2021-07-19); gold: 34,920 district-disease-weeks for
  97 districts, 15,876 with a reported count.
* Weather: about 2.9 million hourly district rows (backfilled across hourly runs under the API budget).
* Manifest: 259 raw files with source, query, fetch time, SHA-256 and licence.

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
* Population comes from the 2017 census, joined by district **name** within the province (the census sheet uses an
  older pcode scheme than the OCHA boundaries). 84 of 97 districts get a population; districts split after 2017
  (Karachi's six, Chitral and Kohistan Upper/Lower, Duki, Chaman) stay missing, so no per-100k rate for them.
  Five districts created after 2022 have no OCHA pcode (quarantined).
* Weather history is backfilled across several hourly runs because of the API budget.

## Uses
Intended: district-level planning signals with citations. Not intended: diagnosis, individual decisions, Punjab/ICT,
or any commercial use of the Open-Meteo free tier.

## Maintenance
The hourly Prefect flow re-fetches, re-validates and republishes. The manifest's hashes detect edited or changed files.
The parser has a version number: when it changes, the bulletins are re-parsed.
