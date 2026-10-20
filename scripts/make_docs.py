"""Generate docs/datasetproposal.pdf and docs/Summary.pdf from the real pipeline outputs.

Every number printed here is read from manifest.csv, the DuckDB warehouse or artifacts/*.json,
so the documents can never disagree with the code.   python scripts/make_docs.py
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import duckdb  # noqa: E402
from reportlab.lib import colors  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # noqa: E402
from reportlab.lib.units import cm  # noqa: E402
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle  # noqa: E402

from hackinsight import config  # noqa: E402

ss = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, spaceAfter=4, textColor=colors.HexColor("#1f3a5f"))
H2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11, spaceBefore=6, spaceAfter=2, textColor=colors.HexColor("#1f3a5f"))
P = ParagraphStyle("p", parent=ss["BodyText"], fontSize=8.6, leading=11)
S = ParagraphStyle("s", parent=P, fontSize=7.4, leading=9)


def table(rows, widths, small=True):
    st = S if small else P
    hdr = ParagraphStyle("hdr", parent=st, textColor=colors.white)
    t = Table([[Paragraph(str(c), hdr if i == 0 else st) for c in r] for i, r in enumerate(rows)], colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
                           ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                           ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c9c8c2")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f4f6")])]))
    return t


def facts() -> dict:
    man = list(csv.DictReader(open(ROOT / "manifest.csv", encoding="utf-8")))
    by_src = Counter(r["source"] for r in man)
    size = Counter()
    for r in man:
        size[r["source"]] += int(r["bytes"])
    con = duckdb.connect(str(config.WAREHOUSE), read_only=True)
    q = lambda s: con.execute(s).fetchone()
    f = {
        "manifest_files": len(man), "files_by_source": dict(by_src),
        "mb_by_source": {k: round(v / 2**20, 1) for k, v in size.items()},
        "idsr_rows": q("SELECT count(*) FROM cleaned.idsr_cases")[0],
        "idsr_bulletins": q("SELECT count(DISTINCT source_file) FROM cleaned.idsr_cases")[0],
        "idsr_weeks": q("SELECT min(week_start), max(week_start) FROM cleaned.idsr_cases"),
        "weather_rows": q("SELECT count(*) FROM cleaned.weather_hourly WHERE NOT is_forecast")[0],
        "weather_span": q("SELECT min(ts_utc), max(ts_utc) FROM cleaned.weather_hourly WHERE NOT is_forecast"),
        "gold_rows": q("SELECT count(*) FROM gold.district_week")[0],
        "gold_districts": q("SELECT count(DISTINCT district_id) FROM gold.district_week")[0],
        "reported": q("SELECT count(*) FROM gold.district_week WHERE cases_status='reported'")[0],
        "status_counts": dict(con.execute("SELECT cases_status, count(*) FROM gold.district_week GROUP BY 1").fetchall()),
        "od_rows": q("SELECT count(*) FROM cleaned.opendengue_pak")[0],
        "qlog": con.execute("SELECT action, count(*) FROM quality_log WHERE run_id = (SELECT run_id FROM quality_log "
                            "ORDER BY ts_utc DESC LIMIT 1) GROUP BY 1").fetchall(),
    }
    con.close()
    return f


def proposal(f: dict) -> None:
    doc = SimpleDocTemplate(str(ROOT / "docs" / "datasetproposal.pdf"), pagesize=A4, leftMargin=1.6 * cm,
                            rightMargin=1.6 * cm, topMargin=1.4 * cm, bottomMargin=1.4 * cm)
    w = A4[0] - 3.2 * cm
    el = [Paragraph("Dataset Proposal - Malaria &amp; Dengue Early Warning (Public health track)", H1),
          Paragraph("HackInsight 2026 · Data-to-Decision · generated from manifest.csv and the warehouse", S),
          Paragraph("Problem and primary user", H2),
          Paragraph("District health officers in Sindh, Balochistan and Khyber Pakhtunkhwa decide each week where to "
                    "send RDT kits, antimalarials, beds and fumigation teams. We forecast next week's suspected malaria "
                    "and dengue cases per district, with an 80% range, and give cited NIH/WHO guidance in English and Urdu.", P),
          Paragraph("Credible sources used for model training and evaluation", H2),
          table([["Source", "Publisher / URL", "Licence / terms", "Update", "Files / MB in manifest"],
                 ["IDSR weekly Public Health Bulletins (PDF)", "National Institute of Health, Islamabad - nih.org.pk/phb/weekly-bulletin",
                  "Public government report; robots.txt 'Disallow:' empty (all allowed); attribution", "Weekly",
                  f"{f['files_by_source'].get('nih_idsr', 0)} / {f['mb_by_source'].get('nih_idsr', 0)}"],
                 ["Historical (ERA5) + forecast hourly weather", "Open-Meteo - archive-api.open-meteo.com, api.open-meteo.com",
                  "CC BY 4.0; free tier non-commercial (<10k calls/day)", "Hourly",
                  f"{f['files_by_source'].get('open_meteo', 0)} / {f['mb_by_source'].get('open_meteo', 0)}"],
                 ["OpenDengue v1.3 temporal extract", "Clarke et al. 2024 - doi:10.6084/m9.figshare.24259573",
                  "CC BY 4.0", "Versioned releases", f"{f['files_by_source'].get('opendengue', 0)} / {f['mb_by_source'].get('opendengue', 0)}"],
                 ["Admin boundaries (ADM2 pcodes, centroids), 2017 census population, MPI", "OCHA FISS / PBS / OPHI via data.humdata.org",
                  "CC BY-IGO; CC0", "Rare", f"{f['files_by_source'].get('hdx', 0)} / {f['mb_by_source'].get('hdx', 0)}"],
                 ["Advisories, guidelines, fact sheets (RAG only)", "NIH Pakistan; WHO", "Public; attribution",
                  "Ad hoc", f"{f['files_by_source'].get('rag_docs', 0)} / {f['mb_by_source'].get('rag_docs', 0)}"]],
                [3.2 * cm, 4.6 * cm, 4.2 * cm, 1.6 * cm, w - 13.6 * cm]),
          Paragraph("Source not on the track list, and why it is credible", H2),
          Paragraph("The NIH IDSR bulletins are the national Integrated Disease Surveillance and Response system of "
                    "Pakistan's federal public-health institute, compiled from district reports. The original publisher "
                    "is named on every page, files are fetched from nih.org.pk directly, and each file's SHA-256 is "
                    "recorded. They are independent of Open-Meteo (weather) and of OpenDengue (literature compilation).", P),
          Paragraph("Volume (gathered during the event)", H2),
          Paragraph(f"IDSR: {f['idsr_rows']:,} parsed cells from {f['idsr_bulletins']} bulletins "
                    f"(weeks {f['idsr_weeks'][0]:%Y-%m-%d} to {f['idsr_weeks'][1]:%Y-%m-%d}). "
                    f"Weather: {f['weather_rows']:,} hourly district rows ({f['weather_span'][0]:%Y-%m-%d} to "
                    f"{f['weather_span'][1]:%Y-%m-%d}). OpenDengue Pakistan: {f['od_rows']} rows. "
                    f"Gold table: {f['gold_rows']:,} district-disease-weeks for {f['gold_districts']} districts, "
                    f"{f['reported']:,} with a reported count. Manifest: {f['manifest_files']} raw files.", P),
          Paragraph("Why this data fits the problem and the user", H2),
          Paragraph("It is the same surveillance stream the health departments act on, at the district level where the "
                    "allocation decision is made, combined with the weather drivers of mosquito breeding.", P),
          Paragraph("Declared baseline (fixed before results)", H2),
          Paragraph("Next week's suspected cases in a district = this week's reported cases (naive persistence). "
                    "Metric: mean absolute error on a time-based test period (target weeks from "
                    f"{config.TEST_START}), scored once. 'Better' = lower MAE than the baseline, with a block-bootstrap 95% interval.", P),
          Paragraph("Known limits and risks", H2),
          Paragraph("Suspected not confirmed cases; each bulletin lists only each province's most frequent diseases "
                    "('not listed' kept missing, not zero); Punjab/ICT districts are 'NR'; 2021-22 bulletins use a "
                    "different layout and are flagged, not guessed; no sex/age breakdown; 1-2 week publication lag; "
                    "2017 population.", P)]
    doc.build(el)


def summary(f: dict) -> None:
    m = json.loads((config.ARTIFACTS / f"model_{config.PRIMARY_DISEASE}" / "metrics.json").read_text(encoding="utf-8"))
    ev = json.loads((config.ARTIFACTS / "eval_report.json").read_text(encoding="utf-8"))["summary"]
    lv = json.loads((config.ARTIFACTS / "local_validation.json").read_text(encoding="utf-8"))
    t = m["test"]
    worst = m["groups"]["province"][0]
    doc = SimpleDocTemplate(str(ROOT / "docs" / "Summary.pdf"), pagesize=A4, leftMargin=1.4 * cm, rightMargin=1.4 * cm,
                            topMargin=1.1 * cm, bottomMargin=1.0 * cm)
    w = A4[0] - 2.8 * cm
    el = [Paragraph("Malaria &amp; Dengue Early Warning for Pakistani Districts - Summary", H1),
          Paragraph("<b>User &amp; decision:</b> district health officers (Sindh, Balochistan, KP) deciding where to send "
                    "RDT kits, antimalarials, beds and fumigation teams next week. <b>Output:</b> next-week suspected cases "
                    "per district with an 80% range + cited NIH/WHO guidance in English and Urdu.", P),
          Paragraph("Data &amp; quality", H2),
          Paragraph(f"NIH IDSR bulletins ({f['idsr_bulletins']} PDFs, {f['idsr_rows']:,} cells), Open-Meteo hourly weather "
                    f"({f['weather_rows']:,} rows), OpenDengue, HDX. Raw -&gt; cleaned -&gt; gold in DuckDB; `make data` rebuilds "
                    f"from empty folders and verifies SHA-256 for {f['manifest_files']} raw files. The Data Quality Agent logged "
                    f"{', '.join(f'{a} {n}' for a, n in sorted(f['qlog']))} decisions in the last run (e.g. 'NR' and "
                    f"'not in top-10' kept missing, not zero; short weather gaps filled and marked; high-risk fixes need human approval).", P),
          Paragraph("Model vs declared baseline (test period scored once)", H2),
          table([["", "Baseline: last value", "LightGBM quantile (median)"],
                 ["MAE (suspected cases / district-week)", f"{t['mae_baseline']:.1f}", f"{t['mae_model']:.1f}"],
                 ["RMSE", f"{t['rmse_baseline']:.1f}", f"{t['rmse_model']:.1f}"],
                 ["MAE improvement (95% block bootstrap)", "", f"{t['skill_vs_baseline']:.1%} ({t['skill_ci95'][0]:.1%} to {t['skill_ci95'][1]:.1%})"],
                 ["80% interval coverage", "", f"{t['interval_coverage_80']:.1%}"]], [7 * cm, 4.5 * cm, w - 11.5 * cm], small=False),
          Paragraph(f"Test: {t['n']:,} district-weeks, {t['districts']} districts, target weeks {m['split']['test_target_weeks'][0]} to "
                    f"{m['split']['test_target_weeks'][1]}. Smallest gain over the baseline: {worst["value"]} (MAE {worst['mae_model']} vs baseline "
                    f"{worst['mae_baseline']}); full tables by province, poverty tier, season and district on the dashboard.", S),
          Paragraph("Insight Agent (RAG)", H2),
          Paragraph(f"Hybrid retrieval over NIH advisories/guidelines, WHO fact sheets and the latest bulletins; numbers only from "
                    f"gold SQL with row ids. Golden set ({ev['n']} questions, EN+UR): refusal accuracy {ev['refusal_accuracy']:.0%}, "
                    f"citation accuracy {ev['citation_accuracy']:.0%}, number faithfulness {ev['number_faithfulness']:.0%} "
                    f"({ev['numbers_checked']} numbers re-read by id).", P),
          Paragraph("Automation", H2),
          Paragraph("Prefect flow every hour (cron 5 * * * *): fetch -&gt; Gate 1 -&gt; Gate 2 (freshness, drift) -&gt; retrain only on new "
                    "weeks/drift (keep if better) -&gt; re-index -&gt; golden eval -&gt; publish to a Hugging Face Space showing freshness, "
                    "quality status and last successful run.", P),
          Paragraph("Business model &amp; Pakistan impact", H2),
          Paragraph("Free to districts, funded by provincial health departments or malaria/dengue programme partners "
                    "(contract/grant); small running cost (one small VM, free Space tier for a pilot, optional LLM wording at about "
                    "$0.01 per answer). Built entirely on Pakistani surveillance data, with Urdu outputs. Local validation: our "
                    f"parsed NIH national dengue totals equal OpenDengue's monthly Pakistan series in {lv['exact_match_months']} of "
                    f"{lv['months']} months (r = {lv['correlation']}); {lv['mismatch_months_with_missing_bulletin_week']} other months "
                    f"miss a bulletin week, {lv['mismatch_months_unexplained']} differs unexplained. District sums are checked "
                    "against each bulletin's province total.", P),
          Paragraph("Limits: suspected cases; Punjab/ICT districts not in tables; no gender breakdown; 1-2 week publication lag. "
                    "Information, not medical advice - emergencies: nearest hospital / Rescue 1122.", S)]
    doc.build(el)


if __name__ == "__main__":
    f = facts()
    proposal(f)
    print("docs/datasetproposal.pdf written")
    if (config.ARTIFACTS / "eval_report.json").exists():
        summary(f)
        print("docs/Summary.pdf written")
