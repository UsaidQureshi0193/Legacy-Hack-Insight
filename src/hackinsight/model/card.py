"""Model card, written by code after every training run (never typed by hand).

artifacts/model_card_<disease>.md is shown on the dashboard, committed under docs/, and indexed by
the Insight Agent, so "how accurate is the forecast?" is answered from this document with a citation.
"""
from __future__ import annotations

from .. import config


def write(res: dict) -> str:
    d = res["disease"]
    t, v, s = res["test"], res["validation"], res["split"]
    groups = res["groups"]
    # weakest group across every grouping (lowest gain over the baseline, groups with >= 30 test weeks)
    cand = [r for k, rows in groups.items() if k != "district_worst10" for r in rows
            if r.get("skill") is not None and r["n"] >= 30]
    worst = min(cand, key=lambda r: r["skill"]) if cand else None
    worst_d = [r["value"] for r in groups.get("district_worst10", [])[:3]]
    served_baseline = str(res.get("served_method", "")).startswith("baseline")
    bcov = t.get("baseline_interval_coverage_80")
    lines = [
        f"# Model card: next-week suspected {d} cases per district",
        "",
        "## Intended use",
        f"Rank districts of Sindh, Balochistan and Khyber Pakhtunkhwa by expected suspected {d} cases next week, so a "
        "district health officer can decide which districts to prioritise for investigation, surveillance and resource "
        "planning. Not for diagnosis, "
        "not for individual patients, not for Punjab or Islamabad (no district data).",
        "",
        "## Data",
        f"Target: suspected {d} cases per district and ISO week from NIH Pakistan IDSR bulletins. Features: the last "
        "four weeks of cases, "
        + ("rainfall, humidity and temperature from Open-Meteo (lags 0-3 weeks), " if res.get("use_weather", True)
           else "(no weather features: they did not improve validation MAE in the optimization log, or the "
                "source is switched off), ")
        + "week of year, province, poverty index (OPHI MPI) and 2017 population.",
        "",
        "## Accuracy: how good is the forecast? (time-based split; the test set is scored once per training run and never used to choose)",
        f"- Train target weeks {s['train_target_weeks'][0]} to {s['train_target_weeks'][1]} ({s['n_train']:,} rows); "
        f"validation {s['val_target_weeks'][0]} to {s['val_target_weeks'][1]} ({s['n_val']:,}); "
        f"test {s['test_target_weeks'][0]} to {s['test_target_weeks'][1]} ({s['n_test']:,}).",
        f"- Declared baseline: {res['baseline']}.",
        f"- Validation MAE: baseline {v['mae_baseline']:.1f}, model {v['mae_model']:.1f}.",
        f"- Test MAE: baseline {t['mae_baseline']:.1f}, model {t['mae_model']:.1f}; change {-t['skill_vs_baseline']:+.1%} "
        f"(95% block-bootstrap interval of the gain {t['skill_ci95'][0]:.1%} to {t['skill_ci95'][1]:.1%}).",
        f"- 80% prediction interval covered {t['interval_coverage_80']:.0%} of test weeks.",
        f"- Served on the dashboard: {res.get('served_method', 'model')}.",
        (f"- Range shown with the baseline: the 10th-90th percentile of the baseline's own misses on validation weeks; "
         f"it covered {bcov:.0%} of test weeks." if served_baseline and bcov is not None else ""),
        "",
        "## Where it is weakest",
        (f"Weakest group: {worst['group'].replace('_', ' ')} = {worst['value']} (gain {worst['skill']:+.1%}; MAE model "
         f"{worst['mae_model']} vs baseline {worst['mae_baseline']}, {worst['n']} test weeks). "
         "Officers there should plan against the upper end of the range and check the district's own reports."
         if worst else ""),
        (f"Districts with the largest errors versus the baseline: {', '.join(worst_d)}." if worst_d else ""),
        "",
        "## Limits",
        "Suspected (not confirmed) cases; a disease missing from a bulletin's top-10 list is unknown, not zero; no "
        "age or sex breakdown, so errors by gender cannot be measured; bulletins arrive 1-2 weeks after the week; "
        "weather history may be incomplete while the Open-Meteo budget allows backfill. Tree models cannot predict "
        "beyond the counts seen in training, so for the very largest districts the range is compressed and its "
        "upper end is exceeded more often than its lower end; officers there should treat the upper end as a floor "
        "for planning, not a ceiling (see the 'Outbreak size' rows in the errors-by-group table). A next step is to "
        "predict the change from last week instead of the level, decided on validation only.",
        "",
        "## Top features (gain)",
        ", ".join(list(res.get("feature_importance_top10", {}))[:8]),
    ]
    text = "\n".join(lines) + "\n"
    (config.ARTIFACTS / f"model_card_{d}.md").write_text(text, encoding="utf-8")
    (config.ROOT / "docs").mkdir(exist_ok=True)
    (config.ROOT / "docs" / f"model_card_{d}.md").write_text(text, encoding="utf-8")
    return text
