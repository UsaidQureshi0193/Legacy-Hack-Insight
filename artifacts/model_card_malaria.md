# Model card: next-week suspected malaria cases per district

## Intended use
Rank districts of Sindh, Balochistan and Khyber Pakhtunkhwa by expected suspected malaria cases next week, so a district health officer can decide which districts to prioritise for investigation, surveillance and resource planning. Not for diagnosis, not for individual patients, not for Punjab or Islamabad (no district data).

## Data
Target: suspected malaria cases per district and ISO week from NIH Pakistan IDSR bulletins. Features: the last four weeks of cases, (no weather features: they did not improve validation MAE in the optimization log, or the source is switched off), week of year, province, poverty index (OPHI MPI) and 2017 population.

## Accuracy: how good is the forecast? (time-based split; the test set is scored once per training run and never used to choose)
- Train target weeks 2023-06-05 to 2024-12-23 (6,518 rows); validation 2025-01-06 to 2025-09-29 (3,400); test 2025-10-06 to 2026-09-21 (4,274).
- Declared baseline: last value (next week = this week).
- Validation MAE: baseline 126.7, model 125.9.
- Test MAE: baseline 117.0, model 110.0; change -6.0% (95% block-bootstrap interval of the gain -0.2% to 11.9%).
- 80% prediction interval covered 80% of test weeks.
- Served on the dashboard: model.


## Where it is weakest
Weakest group: province = Khyber Pakhtunkhwa (gain +1.6%; MAE model 30.66 vs baseline 31.14, 1741 test weeks). Officers there should plan against the upper end of the range and check the district's own reports.
Districts with the largest errors versus the baseline: Zhob, Pishin, Khyber.

## Limits
Suspected (not confirmed) cases; a disease missing from a bulletin's top-10 list is unknown, not zero; no age or sex breakdown, so errors by gender cannot be measured; bulletins arrive 1-2 weeks after the week; weather history may be incomplete while the Open-Meteo budget allows backfill. Tree models cannot predict beyond the counts seen in training, so for the very largest districts the range is compressed and its upper end is exceeded more often than its lower end; officers there should treat the upper end as a floor for planning, not a ceiling (see the 'Outbreak size' rows in the errors-by-group table). A next step is to predict the change from last week instead of the level, decided on validation only.

## Top features (gain)
lag0_log, roll4_log, roll8_log, woy_cos, lag1_log, diff1_log, woy_sin, lag2_log
