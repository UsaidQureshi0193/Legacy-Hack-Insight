# Problem analysis and worksheet answers (guide chapter 4, plus answers for chapters 7, 8, 9, 10)

**Team:** Legacy · **Track:** Public health · **Project:** SehatSignal AI (malaria & dengue early warning)

Every number here was read from `data/warehouse.duckdb` (gold tables, `quality_log`) or `artifacts/*.json` on 20 Oct
2026, and each one cites the bulletin file and page it comes from.

## The seven steps

**1. Primary user and decision.** District health officer / district surveillance focal person in Sindh, Balochistan
and Khyber Pakhtunkhwa. Each week they decide **which districts to prioritise next week for further investigation,
surveillance and resource planning**. Supplies, beds and vector control are then planned through the official
programme channels; our output informs that planning but does not claim stock, capacity or fumigation needs.

**2. The problem, with evidence.** In the week of 21–27 Sep 2026 alone, NIH's IDSR bulletin reports **79,781 suspected
malaria cases** in Pakistan, **67,795 of them in Sindh** (`2026_W39.pdf`, p. 5, Table 1). Over 2024 the bulletins'
national tables add up to **3.54 million** suspected malaria cases. NIH's dengue advisories (Oct 2024, Mar 2025) and its
2025 flood advisory warn of rises after rain and floods (see `configs/rag_sources.csv`).

**3. Who is affected and where.** The places are the 97 districts of Sindh, Balochistan and KP that report in the
district tables. Poverty is higher in Balochistan (MPI 0.354) than in Sindh or KP (OPHI/DHS 2017-18). The bulletins have
**no age or sex breakdown**, so we cannot check errors by gender. That is a stated limit.

**4. Outcome.** Suspected malaria (and dengue) cases per district per ISO week, reported in NIH IDSR. Target = next
week's count.

**5. Baseline and success measure.** Declared baseline: next week = this week (last reported value). Metric: MAE on
a time-based test period, scored once, with a 95% block-bootstrap interval. To matter to the user, the model must beat
the baseline and give a calibrated range they can use when ranking districts for follow-up.

**6. Limits and risks.** Cases are suspected, not confirmed. Punjab and ICT districts are `NR`. Each bulletin prints only
each province's top diseases (dengue is often "not listed"). The 2021-22 bulletins use a different layout. Bulletins
arrive 1–2 weeks after the week ends. Population is from the 2017 census. Five newer districts are not in the 2022 OCHA
boundaries. Weather history is incomplete until the Open-Meteo budget allows the rest.

**7. Recommendation and what we do not claim.** We recommend using the ranking and forecast range to decide which
districts to investigate first (check reporting, talk to the district team) and reading the cited NIH/WHO guidance. A high
rank is not an outbreak declaration. We do not diagnose, do not count deaths, and give no
treatment advice beyond quoting official sources. In an emergency, people should go to the nearest hospital or call
Rescue 1122.

## Stakeholders, decisions and impact

| Stakeholder | Role | Decision they make | What changes with our output |
|---|---|---|---|
| District health officer / surveillance focal person | **Primary user** | Which districts and areas to investigate and watch first next week | A ranked, cited district list with a range a week ahead, instead of reading a 25-page PDF 1–2 weeks late |
| Provincial health department, vector-borne disease / malaria control programme | Customer / payer (see `docs/business_model.md`) | Where to plan supplies and teams across districts | One consistent, comparable view of all districts, with stated reporting gaps |
| NIH Islamabad (IDSR) | Data publisher | What to publish and when | Nothing changes for them; we only read their public bulletins and always cite file + page |
| WHO / NIH guidance authors | Source of advice | — | Their guidance is quoted with document + page, never rewritten |
| Residents of high-burden districts (often poorer, e.g. Balochistan MPI 0.354) | Beneficiaries | Whether to seek care | Indirect only; the public dashboard says "information, not medical advice" and points to hospitals / Rescue 1122 |

**Impact we claim:** faster, more consistent prioritisation of districts for follow-up. **Impact we do not claim:** fewer
cases, fewer deaths or better stock levels; we have no data to show that.

## KPIs

Written on 21 Oct 2026, **after** the test results were seen. Only KPI 1 (and its baseline) was declared in advance; the
others are monitoring indicators we track, not new success thresholds chosen after the fact.

| # | KPI | Declared in advance? | Where it is measured |
|---|---|---|---|
| 1 | Test MAE of the forecast vs the declared baseline (last value), time split, scored once | Yes | `artifacts/model_*/metrics.json`, model card |
| 2 | 80% range coverage (share of weeks where the real count fell inside the range) | No (monitoring) | same |
| 3 | Insight Agent: refusal accuracy, citation accuracy, number faithfulness on the golden set | No (monitoring) | `artifacts/eval_report.json` |
| 4 | Data freshness vs limit (NIH bulletins 14 days, weather 6 h) | No (monitoring) | dashboard status tiles, `artifacts/status.json` |
| 5 | Data Quality gate result and decisions per run (FIX / FILL / FLAG / FAIL) | No (monitoring) | `quality_log`, `artifacts/quality_report.json` |
| 6 | Hourly pipeline runs succeeded in the evidence window | No (monitoring) | `prefect_runs.csv` |

## Worksheet answers

| Field | Answer |
|---|---|
| Team name | Legacy |
| Chosen track | Public health |
| Problem statement | District officers react to last week's malaria/dengue numbers from a PDF read 1–2 weeks late. We give them next week's expected suspected cases per district, with a range and cited official guidance, in English and Urdu |
| Primary user and the decision they make | District health officer: which districts to prioritise next week for investigation, surveillance and resource planning |
| Why this matters in Pakistan (cited) | 79,781 suspected malaria cases in one week (21–27 Sep 2026), 67,795 of them in Sindh (`2026_W39.pdf`, p. 5, Table 1); 3.54 million in the 2024 national tables |
| Who is affected and where | 97 reporting districts of Sindh, Balochistan and KP; poorer provinces (Balochistan MPI 0.354, Sindh 0.274, KP 0.258); no age/sex breakdown in the data |
| Outcome (what, how often, unit) | Suspected malaria / dengue cases per district per ISO week (NIH IDSR); target = next week's count |
| Limits and risks | Suspected not confirmed; Punjab/ICT not reported by district; top-10 lists hide some diseases (missing, not zero); 1–2 week delay; 2017 census population |
| Declared baseline / what counts as better | Last value. Lower test MAE than the baseline, with the bootstrap interval reported |
| Example logged decision (`quality_log`) | `IDSR-R2 FLAG, 8,992 rows`: "cells say 'NR' (area did not report that week), mostly Balochistan 5,217, Punjab 3,108 … Kept as missing, NOT zero - a zero would tell the model that no one fell ill." |
| Baseline and model score | Malaria test MAE: baseline 117.0, model 110.1 (−6.0%, 95% CI −0.3% to +12.0%), 80% interval coverage 78.0%. On validation the model only tied the baseline, so the dashboard serves the baseline (see README) |
| Weakest group | Khyber Pakhtunkhwa had the smallest gain over the baseline. Officers there should plan against the upper end of the range |
| Optimization trade-off | A smaller tuning grid saved 35% training time but cost 1.6% validation MAE, so we rejected it. Weather features cost 2.7 s and +23 MB of memory but cut validation MAE from 128.3 to 127.4, so we kept them (`docs/optimization_log.md`) |
| What a good answer contains | Numbers from gold with their row id + PDF page, a quoted passage with document and page, the limits, and the emergency line |
| Local validation | Our parsed national dengue totals equal OpenDengue's monthly Pakistan series (WHO-compiled from the same national reports) in 15 of 20 months (r = 0.997); 4 of the other months miss a bulletin week, 1 (Aug 2023) differs unexplained (`artifacts/local_validation.json`). Because both rest on the same reporting system, this checks **our PDF extraction**, not the reports themselves |
| Value proposition | See `docs/business_model.md` |
