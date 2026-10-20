# HackInsight 2026 · Malaria & Dengue Early Warning for Pakistani Districts

**Track:** Public health. **Primary user:** district health officer (Sindh, Balochistan, Khyber Pakhtunkhwa).
**Decision:** where to send RDT kits, antimalarials, beds and fumigation teams next week.

```
NIH IDSR PDFs ─┐
Open-Meteo ────┼─> raw (+manifest.csv, SHA-256) ─[Gate 1]─> cleaned ─[Gate 2]─> gold ─┬─> quantile model vs baseline
OpenDengue ────┤        Data Quality Agent: FIX / FILL / FLAG / FAIL -> quality_log     ├─> Insight Agent (SQL + cited RAG, EN/UR)
HDX ───────────┘                                                                       └─> Gradio dashboard (HF Space)
                         Prefect flow "hackinsight-hourly", cron 5 * * * *
```

## Quick start
```bash
make setup     # Python 3.12 venv (uv) + dependencies       | Windows: .\make.ps1 setup
make data      # empty folders -> fetch/verify raw -> cleaned -> gold -> quality summary
make train     # declared baseline vs LightGBM quantile model, errors by group, next-week forecast
make index     # retrieval index for the Insight Agent
make app       # dashboard on http://127.0.0.1:7860
make flow      # hourly Prefect deployment (keep the machine on)
make test      # unit tests
```
Ask the agent from the terminal: `python -m hackinsight.cli ask "Which districts in Sindh have the highest malaria risk next week?"`
(`--lang ur` for Urdu).

## Data sources
| # | Source | Publisher | Licence / terms | Role |
|---|---|---|---|---|
| 1 | IDSR weekly Public Health Bulletins (PDF), 2021–2026 | National Institute of Health, Islamabad | Public government reports; robots.txt allows all | Target: suspected cases by district & week (**not on the track list**) |
| 2 | Historical (ERA5) + forecast hourly weather, from 2023 | Open-Meteo | CC BY 4.0; free tier non-commercial; a call ledger keeps every run under 90% of the minute/hour/day limits | Rain, humidity, temperature features |
| 3 | OpenDengue v1.3 | Clarke et al. 2024, Figshare | CC BY 4.0 | Benchmark history, local validation |
| 4 | Admin boundaries + centroids, 2017 census population, MPI | OCHA / PBS / OPHI via HDX | CC BY-IGO, CC0 | District ids, per-100k, poverty groups |
| 5 | Advisories & guidelines, fact sheets | NIH Pakistan, WHO | Public / attribution | Insight Agent library |

Every raw file is listed in `manifest.csv` (source, query, fetch time, SHA-256, licence).

## Repository map
| Path | What it does |
|---|---|
| `src/hackinsight/ingest/` | Fetchers (NIH listing + PDFs, Open-Meteo, OpenDengue/HDX, RAG docs) and the IDSR PDF parser |
| `src/hackinsight/manifest.py` | manifest.csv write/verify |
| `src/hackinsight/quality/agent.py` | Data Quality Agent: pandera tools, decision policy, human approval, quality_log |
| `src/hackinsight/warehouse/` | Extract cache, district matching, cleaned + gold layers (DuckDB) |
| `src/hackinsight/model/` | Features, training/evaluation, measured optimization experiments |
| `src/hackinsight/rag/` | Index (BM25 + multilingual embeddings), Insight Agent, briefs, golden eval |
| `src/hackinsight/pipeline/` | Shared pipeline steps, Prefect hourly flow, publish, run-history export |
| `app/` | Gradio dashboard + Space files · `scripts/deploy_space.py` |
| `docs/` | Dataset proposal, summary, business model, optimization log, **SAMJHO_GUIDE.md** (team explainer) |

## Results (from `artifacts/*.json`, run of 20 Oct 2026)
**Data:** 203,895 IDSR cells from 177 bulletins; gold = 34,920 district-disease-weeks for 97 districts
(15,876 with a reported count); 250 raw files in the manifest. Quality gate: PASS (FIX 6, FILL 1, FLAG 14).

**Malaria, next-week suspected cases** (test target weeks 2025-10-06 → 2026-09-21, 4,274 district-weeks, scored once):

| | Declared baseline (last value) | LightGBM quantile median |
|---|---|---|
| MAE | 117.0 | 110.1 |
| RMSE | 273.9 | 254.0 |
| MAE improvement | | 6.0% (95% block bootstrap −0.3% to +12.0%) |
| 80% interval coverage | | 78.0% |

On the validation period the model only tied the baseline (MAE 127.42 vs 127.33), so under the rule fixed
before the test (serve the model only if it beats the baseline on validation) the dashboard currently serves the
**baseline** and says so. The hourly flow re-decides with champion/challenger once the weather history is
complete. **Dengue:** the model is worse than the baseline (test −40%); Sindh bulletins never list dengue in their
top-10 columns, so the system answers "I don't know" there instead of forecasting.

**Insight Agent golden set** (17 questions, EN + UR): refusal accuracy 100%, citation accuracy 100%, number
faithfulness 100% (22 numbers re-read by row id). Bugs found by this set were fixed, so it is no longer an unseen test.

**Optimization log:** `docs/optimization_log.md` (measured: weather features kept, smaller tuning grid rejected,
parallel parsing 85 s → 39 s, embedding cache 42 s → 9 s).

## Responsible AI
* No number from the LLM: numbers come from gold tables with row ids; optional LLM wording is rejected if it
  contains any digit or an uncited line (`validate_llm_text`, unit-tested).
* Every recommendation cites a document + page; unsupported questions get "I don't know" plus what is missing.
* Missing stays missing (`NR`, `not_listed`), never zero. Fills are marked (`is_filled`, `lag_source_filled`).
* Outputs are information, not medical advice; every answer points to hospitals / Rescue 1122.
* No personal data: district-week aggregates only.

## Limits
Suspected (not confirmed) cases; Punjab/ICT districts not in district tables; 2021-22 bulletin layout not
parsed (flagged); no sex/age breakdown, so no gender fairness check is possible; bulletins arrive 1-2 weeks
after the week; population is from the 2017 census.

Note: results move a little as the hourly flow backfills weather history (API budget) and retrains; the same raw
files always give the same numbers. The current values are always in `docs/model_card_malaria.md`.

## More docs
`docs/HOW_IT_WORKS.md` (the methodology image, stage by stage) · `docs/datasheet.md` · `docs/problem_analysis.md` ·
`docs/business_model.md` · `docs/optimization_log.md` · `docs/model_card_*.md` · `docs/SAMJHO_GUIDE.md` (team explainer)
