# Model card: next-week suspected dengue cases per district

## Intended use
Rank districts of Sindh, Balochistan and Khyber Pakhtunkhwa by expected suspected dengue cases next week, so a district health officer can decide which districts to prioritise for investigation, surveillance and resource planning. Not for diagnosis, not for individual patients, not for Punjab or Islamabad (no district data).

## Data
Target: suspected dengue cases per district and ISO week from NIH Pakistan IDSR bulletins. Features: the last four weeks of cases, (no weather features: they did not improve validation MAE in the optimization log, or the source is switched off), week of year, province, poverty index (OPHI MPI) and 2017 population.

## Accuracy: how good is the forecast? (time-based split; the test set is scored once per training run and never used to choose)
- Train target weeks 2023-06-26 to 2024-05-27 (355 rows); validation 2025-09-15 to 2025-09-29 (105); test 2025-10-06 to 2026-09-21 (576).
- Declared baseline: last value (next week = this week).
- Validation MAE: baseline 14.3, model 15.2.
- Test MAE: baseline 9.8, model 13.4; change +36.1% (95% block-bootstrap interval of the gain -53.4% to -22.2%).
- 80% prediction interval covered 69% of test weeks.
- Served on the dashboard: baseline (model did not beat baseline on validation).
- Range shown with the baseline: the 10th-90th percentile of the baseline's own misses on validation weeks; it covered 85% of test weeks.

## Where it is weakest
Weakest group: province = Balochistan (gain -63.2%; MAE model 11.19 vs baseline 6.86, 108 test weeks). Officers there should plan against the upper end of the range and check the district's own reports.


## Limits
Suspected (not confirmed) cases; a disease missing from a bulletin's top-10 list is unknown, not zero; no age or sex breakdown, so errors by gender cannot be measured; bulletins arrive 1-2 weeks after the week; weather history may be incomplete while the Open-Meteo budget allows backfill. Tree models cannot predict beyond the counts seen in training, so for the very largest districts the range is compressed and its upper end is exceeded more often than its lower end; officers there should treat the upper end as a floor for planning, not a ceiling (see the 'Outbreak size' rows in the errors-by-group table). A next step is to predict the change from last week instead of the level, decided on validation only.

## Top features (gain)
roll4_log, woy_sin, lag0_log, log_pop, woy_cos, roll8_log, diff1_log, lag1_log
