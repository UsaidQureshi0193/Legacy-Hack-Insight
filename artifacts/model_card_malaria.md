# Model card: next-week suspected malaria cases per district

## Intended use
Rank districts of Sindh, Balochistan and Khyber Pakhtunkhwa by expected suspected malaria cases next week, so a district health officer can pre-position test kits, medicines and vector-control teams. Not for diagnosis, not for individual patients, not for Punjab or Islamabad (no district data).

## Data
Target: suspected malaria cases per district and ISO week from NIH Pakistan IDSR bulletins. Features: the last four weeks of cases, rainfall, humidity and temperature from Open-Meteo (lags 0-3 weeks), week of year, province, poverty index (OPHI MPI) and 2017 population.

## Accuracy: how good is the forecast? (time-based split; the test set is scored once per training run and never used to choose)
- Train target weeks 2023-06-05 to 2024-12-23 (6,518 rows); validation 2025-01-06 to 2025-09-29 (3,369); test 2025-10-06 to 2026-09-21 (4,274).
- Declared baseline: last value (next week = this week).
- Validation MAE: baseline 127.3, model 127.4.
- Test MAE: baseline 117.0, model 110.1; change -6.0% (95% block-bootstrap interval of the gain -0.3% to 12.0%).
- 80% prediction interval covered 78% of test weeks.
- Served on the dashboard: baseline (model did not beat baseline on validation).

## Where it is weakest
Smallest gain over the baseline: Khyber Pakhtunkhwa (MAE 30.44 vs 31.14). Plan against the upper end of the range there.

## Limits
Suspected (not confirmed) cases; a disease missing from a bulletin's top-10 list is unknown, not zero; no age or sex breakdown, so errors by gender cannot be measured; bulletins arrive 1-2 weeks after the week; weather history may be incomplete while the Open-Meteo budget allows backfill.

## Top features (gain)
lag0_log, roll4_log, roll8_log, lag1_log, woy_cos, lag2_log, diff1_log, woy_sin
