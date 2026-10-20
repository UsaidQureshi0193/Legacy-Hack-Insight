# Problem analysis and worksheet answers (guide chapters 3, 6, 7, 9, 11)

Every number here was read from `data/warehouse.duckdb` (gold tables, `quality_log`) or `artifacts/*.json` on 20 Oct
2026, and each one cites the bulletin file and page it comes from.

## The seven steps

**1. Primary user and decision.** District health officer / district surveillance focal person in Sindh, Balochistan
and Khyber Pakhtunkhwa. Each week they decide where to send RDT kits, antimalarials, beds and fumigation/larviciding
teams.

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
the baseline and give a calibrated range they can plan stock against.

**6. Limits and risks.** Cases are suspected, not confirmed. Punjab and ICT districts are `NR`. Each bulletin prints only
each province's top diseases (dengue is often "not listed"). The 2021-22 bulletins use a different layout. Bulletins
arrive 1–2 weeks after the week ends. Population is from the 2017 census. Five newer districts are not in the 2022 OCHA
boundaries. Weather history is incomplete until the Open-Meteo budget allows the rest.

**7. Recommendation and what we do not claim.** We recommend using the forecast range to pre-position supplies in the
top-ranked districts and reading the cited NIH/WHO guidance. We do not diagnose, do not count deaths, and give no
treatment advice beyond quoting official sources. In an emergency, people should go to the nearest hospital or call
Rescue 1122.

## Worksheet answers

| Field | Answer |
|---|---|
| Chosen track | Public health |
| Problem statement | District officers react to last week's malaria/dengue numbers from a PDF read 1–2 weeks late. We give them next week's expected suspected cases per district, with a range and cited official guidance, in English and Urdu |
| Declared baseline / what counts as better | Last value. Lower test MAE than the baseline, with the bootstrap interval reported |
| Example logged decision (`quality_log`) | `IDSR-R2 FLAG, 8,992 rows`: "cells say 'NR' (area did not report that week), mostly Balochistan 5,217, Punjab 3,108 … Kept as missing, NOT zero - a zero would tell the model that no one fell ill." |
| Baseline and model score | Malaria test MAE: baseline 117.0, model 110.1 (−6.0%, 95% CI −0.3% to +12.0%), 80% interval coverage 78.0%. On validation the model only tied the baseline, so the dashboard serves the baseline (see README) |
| Weakest group | Khyber Pakhtunkhwa had the smallest gain over the baseline. Officers there should plan against the upper end of the range |
| Optimization trade-off | A smaller tuning grid saved 35% training time but cost 1.6% validation MAE, so we rejected it. Weather features cost 2.7 s and +23 MB of memory but cut validation MAE from 128.3 to 127.4, so we kept them (`docs/optimization_log.md`) |
| What a good answer contains | Numbers from gold with their row id + PDF page, a quoted passage with document and page, the limits, and the emergency line |
| Local validation | Our parsed national dengue totals equal OpenDengue's monthly Pakistan series in 15 of 20 months (r = 0.997). 4 of the other months miss a bulletin week, and 1 (Aug 2023) differs unexplained (`artifacts/local_validation.json`) |
| Value proposition | See `docs/business_model.md` |
