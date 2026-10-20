# Model card: next-week suspected dengue cases per district

## Intended use
Rank districts of Sindh, Balochistan and Khyber Pakhtunkhwa by expected suspected dengue cases next week, so a district health officer can pre-position test kits, medicines and vector-control teams. Not for diagnosis, not for individual patients, not for Punjab or Islamabad (no district data).

## Data
Target: suspected dengue cases per district and ISO week from NIH Pakistan IDSR bulletins. Features: the last four weeks of cases, rainfall, humidity and temperature from Open-Meteo (lags 0-3 weeks), week of year, province, poverty index (OPHI MPI) and 2017 population.

## Accuracy: how good is the forecast? (time-based split; the test set is scored once per training run and never used to choose)
- Train target weeks 2023-06-26 to 2024-05-27 (355 rows); validation 2025-09-15 to 2025-09-29 (105); test 2025-10-06 to 2026-09-21 (576).
- Declared baseline: last value (next week = this week).
- Validation MAE: baseline 14.3, model 15.4.
- Test MAE: baseline 9.8, model 13.8; change +40.0% (95% block-bootstrap interval of the gain -59.1% to -24.3%).
- 80% prediction interval covered 80% of test weeks.
- Served on the dashboard: baseline (model did not beat baseline on validation).

## Where it is weakest
Smallest gain over the baseline: Sindh (MAE 16.51 vs 8.97). Plan against the upper end of the range there.

## Limits
Suspected (not confirmed) cases; a disease missing from a bulletin's top-10 list is unknown, not zero; no age or sex breakdown, so errors by gender cannot be measured; bulletins arrive 1-2 weeks after the week; weather history may be incomplete while the Open-Meteo budget allows backfill.

## Top features (gain)
roll4_log, lag0_log, roll8_log, woy_sin, lag1_log, rh_mean_pct_lag1, rh_mean_pct_roll4, rh_mean_pct_lag3
