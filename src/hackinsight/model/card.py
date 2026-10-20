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
    worst = groups["province"][0] if groups.get("province") else None
    lines = [
        f"# Model card: next-week suspected {d} cases per district",
        "",
        "## Intended use",
        f"Rank districts of Sindh, Balochistan and Khyber Pakhtunkhwa by expected suspected {d} cases next week, so a "
        "district health officer can pre-position test kits, medicines and vector-control teams. Not for diagnosis, "
        "not for individual patients, not for Punjab or Islamabad (no district data).",
        "",
        "## Data",
        f"Target: suspected {d} cases per district and ISO week from NIH Pakistan IDSR bulletins. Features: the last "
        "four weeks of cases, rainfall, humidity and temperature from Open-Meteo (lags 0-3 weeks), week of year, "
        "province, poverty index (OPHI MPI) and 2017 population.",
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
        "",
        "## Where it is weakest",
        (f"Smallest gain over the baseline: {worst['value']} (MAE {worst['mae_model']} vs {worst['mae_baseline']}). "
         "Plan against the upper end of the range there." if worst else ""),
        "",
        "## Limits",
        "Suspected (not confirmed) cases; a disease missing from a bulletin's top-10 list is unknown, not zero; no "
        "age or sex breakdown, so errors by gender cannot be measured; bulletins arrive 1-2 weeks after the week; "
        "weather history may be incomplete while the Open-Meteo budget allows backfill.",
        "",
        "## Top features (gain)",
        ", ".join(list(res.get("feature_importance_top10", {}))[:8]),
    ]
    text = "\n".join(lines) + "\n"
    (config.ARTIFACTS / f"model_card_{d}.md").write_text(text, encoding="utf-8")
    (config.ROOT / "docs").mkdir(exist_ok=True)
    (config.ROOT / "docs" / f"model_card_{d}.md").write_text(text, encoding="utf-8")
    return text
