"""HackInsight dashboard (Gradio) - runs locally or as a Hugging Face Space.

Shows, always from the latest pipeline run (never fixed numbers):
  * data freshness per source, quality gate status, last successful run
  * next-week forecast per district with 80% range, history chart, row ids
  * Insight Agent (English / Urdu) with citations and "I don't know"
  * policy briefs, model vs baseline + errors by group, quality decision log

On a Space: set HI_HF_DATASET (dataset repo the hourly flow publishes to). Locally it reads the
repo's own data/ and artifacts/.
"""
from __future__ import annotations

import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIVE_DIR = Path(os.environ.get("HI_LIVE_DIR", "/tmp/hackinsight_live" if os.name != "nt" else HERE.parent / "publish"))


def sync() -> str:
    """Download the newest published snapshot (Space) - no-op locally."""
    repo = os.environ.get("HI_HF_DATASET")
    if not repo:
        return "local files"
    from huggingface_hub import snapshot_download
    snapshot_download(repo_id=repo, repo_type="dataset", local_dir=str(LIVE_DIR))
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


if os.environ.get("HI_HF_DATASET"):
    sync()
    os.environ["HI_ROOT"] = str(LIVE_DIR)
for cand in (HERE / "src", HERE.parent / "src"):
    if cand.exists():
        sys.path.insert(0, str(cand))

import duckdb  # noqa: E402
import gradio as gr  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402

import hashlib  # noqa: E402

import render  # noqa: E402  (app/render.py)
from hackinsight import config  # noqa: E402
from hackinsight.rag.agent import ShortConnection, WarehouseBusy, source_label  # noqa: E402

# ------------------------------------------------------------------ palette (one system everywhere)
NAVY, BLUE, TEAL = "#1f3a5f", "#2a78d6", "#1baf7a"
# chart text/grid are mid-tone so they stay readable on both the light and the dark surface
INK, INK2, GRID = "#5b6b82", "#6f7d92", "rgba(125,138,160,0.22)"
SURFACE = "#ffffff"
STATUS = {  # reserved for state only; always shown with an icon + a word, never colour alone
    "good": ("✔", "OK"), "warning": ("▲", "Needs attention"), "critical": ("✖", "Problem"),
}
_agent = None


def con():
    """Short read-only connections with retry: never block the hourly pipeline's writes."""
    return ShortConnection(str(config.WAREHOUSE))


BUSY = ('<div class="note note-warn"><b>Updating.</b> The hourly pipeline is writing new data right now. '
        'Please try again in a minute.</div>')


def read_json(name: str) -> dict:
    p = config.ARTIFACTS / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def fmt_ts(s: str | None) -> str:
    if not s:
        return "never"
    return str(s)[:16].replace("T", " ") + " UTC"


# ------------------------------------------------------------------ status tiles
def tile(title: str, value: str, sub: str, state: str) -> str:
    icon, word = STATUS[state]
    return (f'<div class="tile tile-{state}"><div class="t-title">{html.escape(title)}</div>'
            f'<span class="pill pill-{state}">{icon} {word}</span>'
            f'<div class="t-value">{html.escape(value)}</div><div class="t-sub">{html.escape(sub)}</div></div>')


def alerts_html() -> str:
    a = read_json("alerts.json").get("active", [])
    if not a:
        return ""
    items = "".join(f'<li><b>{"✖" if x["level"] == "critical" else "▲"}</b> {html.escape(x["text"])}</li>' for x in a[:6])
    crit = any(x["level"] == "critical" for x in a)
    return (f'<div class="alerts {"crit" if crit else ""}"><div class="al-h">{len(a)} alert{"s" if len(a) > 1 else ""} '
            f'from the latest run</div><ul>{items}</ul></div>')


def status_html() -> str:
    st = read_json("status.json")
    if not st:
        return '<div class="empty">No pipeline run yet. Run <code>make data</code>.</div>'
    names = {"nih_idsr": "NIH bulletins", "open_meteo": "Weather"}
    tiles = []
    for src, f in st.get("freshness", {}).items():
        age = f["age_h"]
        age_txt = f"{age / 24:.0f} days old" if age >= 48 else f"{age:.0f} h old"
        tiles.append(tile(f"Freshness · {names.get(src, src)}", f["newest"][:10],
                          f"{age_txt} · limit {f['sla_h'] / 24:.0f} days" if f["sla_h"] >= 48 else
                          f"{age_txt} · limit {f['sla_h']} h", "warning" if f["late"] else "good"))
    q = st.get("quality", {})
    c = q.get("counts", {})
    tiles.append(tile("Data Quality Agent", "PASS" if q.get("status") == "pass" else "FAIL",
                      f"FIX {c.get('FIX', 0)} · FILL {c.get('FILL', 0)} · FLAG {c.get('FLAG', 0)}",
                      "good" if q.get("status") == "pass" else "critical"))
    lg = st.get("last_good") or {}
    tiles.append(tile("Last successful run", fmt_ts(lg.get("finished_utc")).replace(" UTC", ""),
                      f"{lg.get('gold_rows', 0):,} gold rows · {lg.get('mode', '').replace('_', ' ')}",
                      "good" if lg else "critical"))
    lr = st.get("last_run", {})
    ok = lr.get("status") == "success"
    tiles.append(tile("Latest run", "Success" if ok else lr.get("status", "?").replace("_", " ").title(),
                      f"{fmt_ts(lr.get('finished_utc') or lr.get('started_utc'))} · {lr.get('duration_s', '?')} s",
                      "good" if ok else "critical"))
    return alerts_html() + '<div class="tiles">' + "".join(tiles) + "</div>"


# ------------------------------------------------------------------ forecast + history
def forecast_tables(disease: str, province: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(table for people, table of ids for audit). Plain column names; the source is a link to the
    exact NIH PDF page the number was read from."""
    try:
        c = con()
        sql = (f"SELECT f.district, f.province, f.based_on_week, f.forecast_week, f.last_value, f.q50, f.q10, f.q90, "
               f"f.forecast_id, f.based_on_row_id, d.source_file, d.page FROM gold.forecast_{disease} f "
               f"LEFT JOIN gold.district_week d ON d.row_id = f.based_on_row_id")
        df = c.execute(sql + (" WHERE f.province = ?" if province != "All" else "") + " ORDER BY f.q50 DESC",
                       [province] if province != "All" else []).df()
        c.close()
    except WarehouseBusy:
        msg = pd.DataFrame({"Info": ["The hourly pipeline is updating the data - try again in a minute"]})
        return msg, msg
    except duckdb.Error:
        msg = pd.DataFrame({"Info": ["No forecast yet - run make train"]})
        return msg, msg
    if df.empty:
        msg = pd.DataFrame({"Info": [f"No {disease} forecast for {province}: recent NIH bulletins do not list "
                                     f"{disease} among this province's most frequent diseases"]})
        return msg, msg
    num = lambda x: f"{int(round(x)):,}" if pd.notna(x) else "—"
    last_w = pd.to_datetime(df["based_on_week"].iloc[0]).strftime("%d %b")
    next_w = pd.to_datetime(df["forecast_week"].iloc[0]).strftime("%d %b %Y")
    people = pd.DataFrame({
        "#": range(1, len(df) + 1),
        "District": df["district"],
        "Province": df["province"],
        f"Reported last week ({last_w})": df["last_value"].map(num),
        f"Expected next week ({next_w})": df["q50"].map(num),
        "Likely range (8 in 10 chance)": [f"{num(a)} – {num(b)}" if pd.notna(a) else "—"
                                          for a, b in zip(df["q10"], df["q90"])],
        "Source (opens the NIH PDF)": [render.source_link(source_label(f, p)) for f, p in zip(df["source_file"], df["page"])],
    })
    if people["Likely range (8 in 10 chance)"].eq("—").all():
        people = people.drop(columns="Likely range (8 in 10 chance)")
    audit = pd.DataFrame({"District": df["district"], "Forecast id": df["forecast_id"],
                          "Gold row id (input)": df["based_on_row_id"],
                          "File / page": [f"{f} p.{int(p)}" if pd.notna(p) else str(f)
                                          for f, p in zip(df["source_file"], df["page"])]})
    return people, audit


def forecast_note(disease: str) -> str:
    m = read_json(f"model_{disease}/metrics.json")
    if m.get("served_method", "").startswith("baseline"):
        return ('<div class="note note-warn"><b>How "Expected next week" is made:</b> it is last week\'s reported '
                'count. Our model was not more accurate than this simple rule on past data, so we show the simple '
                'rule and no range. (A rule fixed before testing decides this.)</div>')
    return ('<div class="note"><b>How "Expected next week" is made:</b> the model\'s middle estimate. '
            '"Likely range" covered the real number in about 8 of 10 past weeks.</div>')


def history_plot(disease: str, district: str):
    c = con()
    h = c.execute("""SELECT week_start, cases, cases_status, row_id FROM gold.district_week
                     WHERE disease = ? AND district = ? ORDER BY week_start""", [disease, district]).df()
    try:
        f = c.execute(f"SELECT * FROM gold.forecast_{disease} WHERE district = ?", [district]).df()
    except (duckdb.Error, WarehouseBusy):
        f = pd.DataFrame()
    c.close()
    fig = go.Figure()
    # missing weeks stay gaps (connectgaps=False): a gap is not a zero
    fig.add_trace(go.Scatter(
        x=h["week_start"], y=h["cases"].astype(float), mode="lines+markers", name="Reported",
        line=dict(color=BLUE, width=2), marker=dict(size=7, color=BLUE, line=dict(color="rgba(255,255,255,0.9)", width=1)),
        connectgaps=False, customdata=h[["cases_status", "row_id"]],
        hovertemplate="<b>%{x|%d %b %Y}</b><br>%{y:,.0f} suspected cases<br>%{customdata[0]} · row %{customdata[1]}"
                      "<extra></extra>"))
    if len(f):
        r = f.iloc[0]
        has_range = pd.notna(r["q10"])
        fig.add_trace(go.Scatter(
            x=[r["based_on_week"], r["forecast_week"]], y=[r["last_value"], r["q50"]], mode="lines+markers",
            name="Forecast" if has_range else "Baseline (next = last)",
            line=dict(color=TEAL, width=2, dash="dot"), marker=dict(size=[0, 12], symbol="diamond", color=TEAL),
            error_y=dict(type="data", symmetric=False, array=[0, (r["q90"] - r["q50"]) if has_range else 0],
                         arrayminus=[0, (r["q50"] - r["q10"]) if has_range else 0], color=TEAL, thickness=2, width=8),
            hovertemplate=(f"<b>%{{x|%d %b %Y}}</b><br>expected %{{y:,.0f}}"
                           + (f"<br>80% range {r['q10']:,.0f} – {r['q90']:,.0f}" if has_range else "")
                           + f"<extra>{r['forecast_id']}</extra>")))
    fig.update_layout(
        title=dict(text=f"<b>Weekly suspected {disease} cases · {district}</b>", font=dict(color=INK, size=18), x=0.01),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)", font=dict(color=INK2, size=14),
        height=420, margin=dict(l=60, r=24, t=64, b=48), hovermode="closest",
        hoverlabel=dict(font_size=14, bgcolor="white", bordercolor=GRID),
        legend=dict(orientation="h", y=1.02, x=1, xanchor="right", yanchor="bottom", font=dict(size=13)),
        yaxis=dict(title="suspected cases", rangemode="tozero", gridcolor=GRID, zeroline=False),
        xaxis=dict(gridcolor="rgba(0,0,0,0)", showline=True, linecolor=GRID, tickfont=dict(size=13)))
    return fig


def caption_html(disease: str, district: str) -> str:
    from hackinsight.rag.captions import caption
    try:
        en, ids = caption(disease, district, "en")
        ur, _ = caption(disease, district, "ur")
    except WarehouseBusy:
        return BUSY
    if not en:
        return ""
    return (f'<div class="caption"><div>{html.escape(en)}</div><div class="cap-ur" dir="rtl">{html.escape(ur)}</div>'
            f'<div class="cap-ids">gold rows: {", ".join(ids)}</div></div>')


def whatif_html(disease: str, district: str, change_pct: float):
    from hackinsight.model import whatif
    if not whatif.available(disease):
        return ('<div class="note">What-if needs the model, and for this disease the dashboard serves the simple '
                'baseline (which ignores weather), so there is nothing to vary.</div>'), gr.update(visible=False)
    try:
        df = whatif.scenario(disease, district, change_pct / 100)
    except WarehouseBusy:
        return BUSY, gr.update(visible=False)
    note = ('<div class="note note-warn"><b>Scenario, not a forecast.</b> Only the rainfall inputs of the model '
            'are changed; everything else stays as observed. Use it to see how sensitive the district is to rain.</div>')
    return note, gr.update(value=df, visible=not df.empty)


def districts_for(disease: str) -> list[str]:
    try:
        return con().execute("""SELECT district FROM gold.district_week WHERE disease = ? AND cases IS NOT NULL
                                GROUP BY district ORDER BY sum(cases) DESC""", [disease]).df()["district"].tolist()
    except (duckdb.Error, WarehouseBusy):
        return []


# ------------------------------------------------------------------ agent, briefs, model, quality
def ask(question: str, lang: str):
    global _agent
    if not question.strip():
        return '<div class="empty">Type a question above, or pick an example.</div>'
    from hackinsight.rag.agent import InsightAgent
    if _agent is None:
        _agent = InsightAgent()
    try:
        a = _agent.answer(question, lang="ur" if lang == "اردو" else "en")
    except WarehouseBusy:
        return BUSY
    try:   # question log for evaluation; the question is already PII-masked by the agent
        with open(config.ARTIFACTS / "agent_log.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "question": a.question, "status": a.status, "pii_masked": a.pii_masked,
                                "prompt_version": a.prompt_version, "wording": a.wording}, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return render.answer_card(a.to_dict())


def _reviews() -> dict:
    p = config.CONFIGS / "brief_reviews.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def brief(disease: str, province: str, lang: str) -> str:
    b = read_json("briefs.json") or []
    code = "ur" if lang == "اردو" else "en"
    for x in b if isinstance(b, list) else []:
        if x["disease"] == disease and x["province"] == province and x["lang"] == code:
            week = next((bl["week"] for bl in x.get("blocks", []) if bl["type"] == "forecast"), "")
            title = (f"Weekly brief · {disease.title()} · {province}" + (f" · week of {week}" if week else "")
                     if code == "en" else f"ہفتہ وار بریف · {province} · {week}")
            r = _reviews().get(x["brief_id"])
            # a review counts only for the exact text that was reviewed (a regenerated brief needs a new review)
            ok = r and r.get("text_sha1") == hashlib.sha1(x["text"].encode()).hexdigest()
            return render.answer_card(x, title=title, review=f"{r['by']} ({r['at_utc'][:10]})" if ok else "")
    return ('<div class="empty">No brief for this selection: recent NIH bulletins do not list this disease among '
            'the province\'s most frequent diseases, so there is nothing reliable to brief on.</div>')


def model_md(disease: str) -> tuple[str, pd.DataFrame]:
    m = read_json(f"model_{disease}/metrics.json")
    if not m or "test" not in m:
        return '<div class="empty">Model not trained yet.</div>', pd.DataFrame()
    t = m["test"]
    v = m["validation"]
    better = t["skill_vs_baseline"] > 0
    kpis = (
        f'<div class="kpis">'
        f'<div class="kpi"><div class="k-label">Test MAE · baseline</div><div class="k-value">{t["mae_baseline"]:.1f}</div>'
        f'<div class="k-sub">suspected cases per district-week</div></div>'
        f'<div class="kpi"><div class="k-label">Test MAE · model</div><div class="k-value">{t["mae_model"]:.1f}</div>'
        f'<div class="k-sub">lower is better</div></div>'
        f'<div class="kpi"><div class="k-label">Change vs baseline</div>'
        f'<div class="k-value {"pos" if better else "neg"}">{"−" if better else "+"}{abs(t["skill_vs_baseline"]):.1%}</div>'
        f'<div class="k-sub">95% CI {t["skill_ci95"][0]:.1%} to {t["skill_ci95"][1]:.1%}</div></div>'
        f'<div class="kpi"><div class="k-label">80% range coverage</div><div class="k-value">{t["interval_coverage_80"]:.0%}</div>'
        f'<div class="k-sub">target 80%</div></div></div>')
    info = (f'<div class="note"><b>Declared baseline:</b> {html.escape(m["baseline"])}. '
            f'<b>Test period:</b> {m["split"]["test_target_weeks"][0]} → {m["split"]["test_target_weeks"][1]} '
            f'({t["n"]:,} district-weeks, {t["districts"]} districts, scored once). '
            f'<b>Validation MAE:</b> baseline {v["mae_baseline"]:.1f} vs model {v["mae_model"]:.1f}. '
            f'<b>Served:</b> {html.escape(m.get("served_method", "model"))}.</div>')
    rows = [r for k, vv in m["groups"].items() for r in vv]
    gt = pd.DataFrame(rows)
    if len(gt):
        coded = gt["group"].isin(["poverty_tier", "season"])   # code-like values ("post_monsoon") only
        gt.loc[coded, "value"] = gt.loc[coded, "value"].str.replace("_", " ").str.capitalize()
        gt["group"] = gt["group"].map({"province": "Province", "poverty_tier": "Poverty tier", "season": "Season",
                                       "size_band": "Outbreak size", "district": "District (10 weakest)"}
                                      ).fillna(gt["group"])
        gt["n"] = gt["n"].map("{:,}".format)
        gt["skill"] = gt["skill"].map(lambda x: f"{x:+.1%}" if pd.notna(x) else "—")
        gt["interval_coverage"] = gt["interval_coverage"].map("{:.0%}".format)
        gt["mean_target"] = gt["mean_target"].map("{:,.0f}".format)
        gt = gt.rename(columns={"group": "Group", "value": "Value", "n": "Weeks", "mae_baseline": "MAE baseline",
                                "mae_model": "MAE model", "skill": "Gain vs baseline", "interval_coverage": "80% range coverage",
                                "mean_target": "Mean cases"})
    return kpis + info, gt


def quality_df() -> pd.DataFrame:
    c = con()
    try:
        q = c.execute("""SELECT ts_utc, gate, rule, action, rows_affected, status, explanation
                         FROM quality_log WHERE run_id = (SELECT run_id FROM quality_log ORDER BY ts_utc DESC LIMIT 1)
                         ORDER BY CASE action WHEN 'FAIL' THEN 0 WHEN 'FLAG' THEN 1 WHEN 'FIX' THEN 2
                                  WHEN 'FILL' THEN 3 ELSE 4 END, rows_affected DESC""").df()
    except (duckdb.Error, WarehouseBusy):
        q = pd.DataFrame()
    c.close()
    if len(q):
        q["ts_utc"] = q["ts_utc"].str[:16].str.replace("T", " ")
        q = q.rename(columns={"ts_utc": "Time (UTC)", "gate": "Gate", "rule": "Rule", "action": "Action",
                              "rows_affected": "Rows", "status": "Status", "explanation": "Why"})
    return q


def refresh():
    global _agent
    when = sync()
    _agent = None
    return status_html(), f'<span class="synced">Synced from {when} · {datetime.now(timezone.utc):%H:%M} UTC</span>'


# ------------------------------------------------------------------ look & feel
THEME = gr.themes.Soft(
    primary_hue=gr.themes.Color(c50="#eef5fd", c100="#d9e8fa", c200="#b5d1f4", c300="#86b4ec", c400="#5292e1",
                                c500="#2a78d6", c600="#1f63b8", c700="#1b4f92", c800="#1a4276", c900="#1f3a5f",
                                c950="#132541"),
    neutral_hue="slate", radius_size="lg", text_size="lg", spacing_size="lg",
    font=[gr.themes.GoogleFont("Inter"), "ui-sans-serif", "system-ui", "sans-serif"],
).set(
    body_background_fill="#f4f6fa", body_background_fill_dark="#0f1724",
    block_background_fill="#ffffff", block_background_fill_dark="#162133",
    block_border_width="1px", block_border_color="#e3e7ee", block_border_color_dark="#25324a",
    block_shadow="0 1px 2px rgba(16,24,40,.04)", block_label_text_size="15px", block_title_text_size="16px",
    body_text_color="#14202e", body_text_color_subdued="#4a5566",
    body_text_color_dark="#e8edf5", body_text_color_subdued_dark="#aeb9cc",
    button_primary_background_fill="#2a78d6", button_primary_background_fill_hover="#1f63b8",
    button_primary_text_color="#ffffff",
)

HEAD = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
        '<link href="https://fonts.googleapis.com/css2?family=Noto+Nastaliq+Urdu:wght@400;600&display=swap" rel="stylesheet">')

CSS = """
:root{--card:#ffffff;--ink:#14202e;--ink2:#4a5566;--line:#e3e7ee;--soft:#f4f6fa;
  --good-bg:#e6f6ea;--good-tx:#0b6b2b;--warn-bg:#fff3d6;--warn-tx:#7a4f00;--crit-bg:#fde8e8;--crit-tx:#a32020;
  --good:#0ca30c;--warn:#e19a00;--crit:#d03b3b;--accent:#2a78d6;--teal:#1baf7a}
.dark{--card:#162133;--ink:#e8edf5;--ink2:#aeb9cc;--line:#25324a;--soft:#0f1724;
  --good-bg:#12351f;--good-tx:#8fe0a6;--warn-bg:#3a2d0c;--warn-tx:#ffd27a;--crit-bg:#3d1717;--crit-tx:#ffaaaa}
.gradio-container{max-width:1280px !important;margin:0 auto !important;padding:12px 20px 40px !important}
.hero{background:linear-gradient(120deg,#1f3a5f 0%,#2a78d6 65%,#1baf7a 130%);border-radius:18px;
  padding:28px 32px;color:#fff;box-shadow:0 8px 24px rgba(31,58,95,.18);margin-bottom:8px}
.hero h1{font-size:28px;line-height:1.25;margin:0 0 6px;color:#fff;font-weight:700;letter-spacing:-.01em}
.hero .ur{font-family:'Noto Nastaliq Urdu',serif;font-size:20px;line-height:2;color:#e9f2ff;margin:0}
.hero p.sub{font-size:16px;line-height:1.6;color:#dce9fb;margin:8px 0 0;max-width:900px}
.hero .chips{margin-top:14px;display:flex;flex-wrap:wrap;gap:8px}
.hero .chip{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.25);border-radius:999px;
  padding:5px 12px;font-size:13.5px;color:#fff}
.tiles{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin:10px 0 4px}
@media (max-width:1100px){.tiles{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}}
.tile{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--good);border-radius:14px;
  padding:16px 18px;color:var(--ink);box-shadow:0 1px 3px rgba(16,24,40,.05)}
.tile-warning{border-left-color:var(--warn)}.tile-critical{border-left-color:var(--crit)}
.tile .pill{display:inline-block;margin-top:8px}
.t-title{font-size:13.5px;font-weight:600;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em;line-height:1.4}
.t-value{font-size:24px;font-weight:700;margin:10px 0 4px;color:var(--ink);line-height:1.2}
.t-sub{font-size:14.5px;color:var(--ink2);line-height:1.5}
.pill{font-size:12.5px;font-weight:600;border-radius:999px;padding:3px 10px;white-space:nowrap}
.pill-good{background:var(--good-bg);color:var(--good-tx)}.pill-warning{background:var(--warn-bg);color:var(--warn-tx)}
.pill-critical{background:var(--crit-bg);color:var(--crit-tx)}
.synced{font-size:14px;color:var(--ink2)}
.lead{font-size:16px;line-height:1.65;color:var(--ink2);margin:4px 2px 10px}
.note{background:var(--soft);border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:12px;
  padding:12px 16px;font-size:15px;line-height:1.6;color:var(--ink);margin:6px 0}
.note-warn{border-left-color:var(--warn);background:var(--warn-bg);color:var(--warn-tx)}
.answer{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px 22px;
  font-size:16px;line-height:1.7;color:var(--ink)}
.answer h4{font-size:15px;font-weight:700;color:var(--accent);margin:14px 0 6px;text-transform:none}
.answer h4:first-child{margin-top:0}
.answer ul{margin:4px 0 8px 20px;padding:0}.answer li{margin:6px 0}
.answer p{margin:6px 0}
.answer .src{font-size:14px;color:var(--ink2);padding:6px 10px;border-left:3px solid var(--teal);
  background:var(--soft);border-radius:6px;margin:6px 0;word-break:break-word}
.answer .idk{display:inline-block;background:var(--warn-bg);color:var(--warn-tx);font-weight:700;
  border-radius:8px;padding:6px 12px;margin-bottom:6px}
.answer .emergency{margin-top:14px;padding:10px 14px;border-radius:10px;background:var(--crit-bg);
  color:var(--crit-tx);font-size:14.5px}
.answer.urdu{font-family:'Noto Nastaliq Urdu',serif;font-size:19px;line-height:2.2}
.answer.urdu ul{margin:4px 20px 8px 0}
.answer.urdu .src,.answer.urdu .lat{font-family:Inter,system-ui,sans-serif;font-size:15px;line-height:1.7;direction:ltr;text-align:left;unicode-bidi:isolate}
.wording{font-size:13px;color:var(--ink2);margin:8px 4px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:12px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.k-label{font-size:13.5px;font-weight:600;color:var(--ink2);text-transform:uppercase;letter-spacing:.04em}
.k-value{font-size:30px;font-weight:700;color:var(--ink);margin:8px 0 2px}
.k-value.pos{color:var(--good-tx)}.k-value.neg{color:var(--crit-tx)}
.k-sub{font-size:14px;color:var(--ink2)}
.empty{padding:22px;text-align:center;color:var(--ink2);font-size:15.5px;background:var(--soft);
  border:1px dashed var(--line);border-radius:14px}
.limits li{font-size:16px;line-height:1.7;margin:6px 0;color:var(--ink)}
.limits .ur{font-family:'Noto Nastaliq Urdu',serif;font-size:19px;line-height:2.2;direction:rtl;margin-top:14px}
.tab-nav button,button[role=tab]{font-size:15.5px !important;font-weight:600 !important;padding:10px 16px !important}
.nicetable, .nicetable *{font-family:Inter,ui-sans-serif,system-ui,sans-serif !important}
.nicetable td,.nicetable th{font-size:14.5px !important;line-height:1.5 !important;padding:10px 12px !important}
.nicetable th{font-weight:700 !important;white-space:nowrap !important}

.headline{font-size:20px;font-weight:700;line-height:1.5;color:var(--ink);margin:0 0 12px}
.answer.urdu .headline{font-size:22px}
.brief-title{font-size:22px;font-weight:700;color:var(--accent);margin-bottom:6px}
.review{display:inline-block;font-size:13.5px;border-radius:999px;padding:3px 12px;margin-bottom:12px;
  background:var(--warn-bg);color:var(--warn-tx)}
.review.ok{background:var(--good-bg);color:var(--good-tx)}
.how{font-size:14.5px;color:var(--ink2);background:var(--soft);border-radius:10px;padding:10px 14px;margin:8px 0 4px}
.tbl-wrap{overflow-x:auto;margin:6px 0 4px;border:1px solid var(--line);border-radius:12px}
table.nice{width:100%;border-collapse:collapse;font-family:Inter,system-ui,sans-serif;font-size:15px;direction:ltr}
table.nice th{background:var(--soft);color:var(--ink2);font-weight:700;text-align:left;padding:10px 12px;
  font-size:13.5px;text-transform:uppercase;letter-spacing:.03em;border-bottom:1px solid var(--line)}
table.nice td{padding:10px 12px;border-bottom:1px solid var(--line);color:var(--ink);vertical-align:top}
table.nice tr:last-child td{border-bottom:none}
table.nice .num{text-align:right;font-variant-numeric:tabular-nums}
.answer a,.nicetable a{color:var(--accent);text-decoration:none;font-weight:600}
.answer a:hover,.nicetable a:hover{text-decoration:underline}
.quote{background:var(--soft);border-left:4px solid var(--teal);border-radius:10px;padding:12px 16px;margin:8px 0}
.q-text{font-size:15.5px;line-height:1.65;color:var(--ink)}
.q-src{font-size:13.5px;color:var(--ink2);margin-top:6px}
.idk-card{background:var(--warn-bg);color:var(--warn-tx);border-radius:12px;padding:14px 18px;margin-bottom:10px}
.idk-h{font-size:19px;font-weight:700}.idk-why{font-weight:600;margin-top:6px}
.idk-card ul{margin:6px 0 0 18px}
.audit{margin-top:14px;font-size:13.5px;color:var(--ink2)}
.audit summary{cursor:pointer;font-weight:600;color:var(--ink2)}
.audit ul{margin:8px 0 0 18px;font-family:ui-monospace,Consolas,monospace;font-size:12.5px;direction:ltr;text-align:left}
.answer.urdu h4,.answer.urdu .headline,.answer.urdu .idk-h{font-family:'Noto Nastaliq Urdu',serif}
.answer.urdu .audit,.answer.urdu .quote{direction:ltr;text-align:left}

.alerts{background:var(--warn-bg);color:var(--warn-tx);border-radius:14px;padding:12px 18px;margin:10px 0 4px;
  border:1px solid rgba(0,0,0,.04)}
.alerts.crit{background:var(--crit-bg);color:var(--crit-tx)}
.al-h{font-weight:700;font-size:15.5px}.alerts ul{margin:6px 0 0 18px;font-size:14.5px;line-height:1.6}
.caption{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;font-size:15px;
  line-height:1.6;color:var(--ink);margin-top:8px}
.cap-ur{font-family:'Noto Nastaliq Urdu',serif;font-size:17px;line-height:2.1;margin-top:4px}
.cap-ids{font-size:12px;color:var(--ink2);font-family:ui-monospace,Consolas,monospace;margin-top:4px}
footer{display:none !important}
@media (max-width:640px){.gradio-container{padding:8px 10px 32px !important}.hero{padding:20px;border-radius:14px}.hero h1{font-size:22px}.hero .ur{font-size:17px}.t-value{font-size:20px}.answer{padding:14px 16px}}
"""

HERO = """
<div class="hero">
  <h1>Malaria &amp; Dengue Early Warning for District Health Officers</h1>
  <p class="ur" dir="rtl">ضلعی ہیلتھ افسران کے لیے ملیریا اور ڈینگی کی پیشگی وارننگ</p>
  <p class="sub">Next-week suspected cases for 97 districts of Sindh, Balochistan and Khyber Pakhtunkhwa,
  built from NIH Pakistan IDSR bulletins and Open-Meteo weather, with cited official guidance in English and Urdu.</p>
  <div class="chips"><span class="chip">Updated every hour</span><span class="chip">Every number links to its PDF page</span>
  <span class="chip">Information, not medical advice</span></div>
</div>
"""

with gr.Blocks(title="HackInsight · Malaria & Dengue Early Warning") as demo:
    gr.HTML(HERO)
    status = gr.HTML(status_html())
    with gr.Row(equal_height=True):
        sync_msg = gr.HTML('<span class="synced">Data from the latest pipeline run</span>')
        sync_btn = gr.Button("↻ Refresh data", size="sm", variant="secondary", scale=0, min_width=150)
    sync_btn.click(refresh, outputs=[status, sync_msg])
    gr.Timer(600).tick(refresh, outputs=[status, sync_msg])

    with gr.Tab("📈 Forecast"):
        gr.HTML('<p class="lead">Which districts should get kits, medicines and fumigation teams first? '
                'Districts are ranked by next week\'s expected suspected cases.</p>')
        with gr.Row():
            dis = gr.Radio(config.TARGET_DISEASES, value=config.PRIMARY_DISEASE, label="Disease")
            prov = gr.Dropdown(["All", "Sindh", "Balochistan", "Khyber Pakhtunkhwa"], value="All", label="Province")
        note = gr.HTML(forecast_note(config.PRIMARY_DISEASE))
        ppl0, aud0 = forecast_tables(config.PRIMARY_DISEASE, "All")
        table = gr.Dataframe(ppl0, interactive=False, wrap=True, max_height=460, show_search="search",
                             elem_classes="nicetable", datatype="html")
        with gr.Accordion("Audit details: ids that link each number to its gold row and PDF page", open=False):
            audit = gr.Dataframe(aud0, interactive=False, wrap=True, max_height=320, elem_classes="nicetable")
        first = districts_for(config.PRIMARY_DISEASE)
        gr.HTML('<p class="lead" style="margin-top:18px">History of one district. Gaps are weeks with no '
                'reported value; they are not zeros.</p>')
        dist = gr.Dropdown(first, value=first[0] if first else None, label="District")
        plot = gr.Plot(history_plot(config.PRIMARY_DISEASE, first[0]) if first else None, show_label=False)
        cap = gr.HTML(caption_html(config.PRIMARY_DISEASE, first[0]) if first else "")
        gr.HTML('<p class="lead" style="margin-top:18px"><b>What if?</b> Change next weeks\' rainfall for the '
                'selected district and see how the model\'s expectation moves.</p>')
        with gr.Row(equal_height=True):
            rain = gr.Slider(-50, 100, value=30, step=10, label="Rain change (%)", scale=3)
            wi_btn = gr.Button("Run scenario", variant="secondary", scale=1)
        wi_note = gr.HTML()
        wi_tab = gr.Dataframe(interactive=False, elem_classes="nicetable", visible=False)
        wi_btn.click(whatif_html, [dis, dist, rain], [wi_note, wi_tab])
        dis.change(lambda d, p: (*forecast_tables(d, p), forecast_note(d)), [dis, prov], [table, audit, note])
        prov.change(lambda d, p: forecast_tables(d, p), [dis, prov], [table, audit])
        dis.change(lambda d: gr.update(choices=districts_for(d), value=(districts_for(d) or [None])[0]), dis, dist)
        dist.change(history_plot, [dis, dist], plot)
        dist.change(caption_html, [dis, dist], cap)

    with gr.Tab("💬 Ask the Insight Agent"):
        gr.HTML('<p class="lead">Ask in English or Urdu. Numbers come only from the data tables (with row ids); '
                'advice comes only from NIH and WHO documents (with page). If there is no support, the agent says '
                '"I don\'t know".</p>')
        with gr.Row(equal_height=True):
            q = gr.Textbox(label="Your question", placeholder="Which districts in Sindh have the highest malaria risk next week?",
                           scale=4, lines=1)
            lang = gr.Radio(["English", "اردو"], value="English", label="Language", scale=1)
        ask_btn = gr.Button("Ask", variant="primary")
        gr.Examples([["Which districts will have the highest malaria cases next week?", "English"],
                     ["What should households do to prevent dengue?", "English"],
                     ["How many people died of dengue in Quetta this month?", "English"],
                     ["ڈینگی سے بچاؤ کے لیے کیا کرنا چاہیے؟", "اردو"]], [q, lang], label="Try an example")
        out = gr.HTML()
        ask_btn.click(ask, [q, lang], out)
        q.submit(ask, [q, lang], out)

    with gr.Tab("📄 Policy briefs"):
        gr.HTML('<p class="lead">A <b>policy brief</b> is a one-page note a district or provincial health officer can '
                'forward on WhatsApp or print: <b>where</b> cases are expected to be highest next week, and <b>what</b> '
                'official NIH/WHO guidance says to do. It is regenerated automatically when a new NIH bulletin arrives, '
                'and a person marks it reviewed before it is shared.</p>')
        with gr.Row():
            bd = gr.Radio(config.TARGET_DISEASES, value=config.PRIMARY_DISEASE, label="Disease")
            bp = gr.Dropdown(["Sindh", "Balochistan", "Khyber Pakhtunkhwa"], value="Sindh", label="Province")
            bl = gr.Radio(["English", "اردو"], value="English", label="Language")
        bo = gr.HTML(brief(config.PRIMARY_DISEASE, "Sindh", "English"))
        for w in (bd, bp, bl):
            w.change(brief, [bd, bp, bl], bo)

    with gr.Tab("🎯 Model vs baseline"):
        gr.HTML('<p class="lead">How much better is the model than the simple rule "next week = this week", '
                'and where does it do worst? Groups are sorted weakest first.</p>')
        md0, gt0 = model_md(config.PRIMARY_DISEASE)
        md_dis = gr.Radio(config.TARGET_DISEASES, value=config.PRIMARY_DISEASE, label="Disease")
        mmd = gr.HTML(md0)
        gtab = gr.Dataframe(gt0, interactive=False, label="Errors by group", max_height=440, wrap=True,
                            elem_classes="nicetable",
                            column_widths=["15%", "19%", "9%", "11%", "10%", "12%", "13%", "11%"])
        md_dis.change(model_md, md_dis, [mmd, gtab])
        with gr.Accordion("Model card (generated after every training run)", open=False):
            card = gr.Markdown((config.ARTIFACTS / f"model_card_{config.PRIMARY_DISEASE}.md").read_text(encoding="utf-8")
                               if (config.ARTIFACTS / f"model_card_{config.PRIMARY_DISEASE}.md").exists() else "")
        md_dis.change(lambda d: (config.ARTIFACTS / f"model_card_{d}.md").read_text(encoding="utf-8")
                      if (config.ARTIFACTS / f"model_card_{d}.md").exists() else "", md_dis, card)

    with gr.Tab("🛡 Data quality log"):
        gr.HTML('<p class="lead">Every decision of the Data Quality Agent in the latest run: FIX (certain correction), '
                'FILL (marked estimate) or FLAG (left as is, with a warning).</p>')
        gr.Dataframe(quality_df(), interactive=False, wrap=True, max_height=600, show_search="search",
                     elem_classes="nicetable", column_widths=["10%", "6%", "8%", "6%", "6%", "8%", "56%"])

    with gr.Tab("ℹ Limits"):
        gr.HTML("""<div class="answer limits"><h4>What this dashboard can and cannot tell you</h4><ul>
          <li>Counts are <b>suspected</b> cases reported by districts to NIH IDSR. They are not lab-confirmed, and they are not deaths.</li>
          <li>Punjab and Islamabad districts are not in the district tables (<code>NR</code>), so there are no district forecasts there.</li>
          <li>Each bulletin prints only each province's most frequent diseases. A disease missing from that list is
              <b>missing, not zero</b> (status <code>not_listed</code>).</li>
          <li>There is no sex or age breakdown in the district tables, so errors cannot be checked by gender.</li>
          <li>Bulletins appear 1-2 weeks after the week ends. "Next week" means the week after the latest bulletin.</li>
          </ul><div class="emergency">⚕ In an emergency go to the nearest hospital or call <b>Rescue 1122</b>.</div>
          <p class="ur">یہ صرف معلومات ہیں، طبی مشورہ نہیں۔ ہنگامی صورت میں قریبی ہسپتال جائیں یا 1122 پر کال کریں۔</p></div>""")

if __name__ == "__main__":
    demo.launch(theme=THEME, css=CSS, head=HEAD, server_name=os.environ.get("HOST", "127.0.0.1"))
