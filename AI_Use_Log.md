# AI_Use_Log.md

Honest record of material AI assistance, as required by the rules (chapter 12). Every team member must be
able to explain the code listed against their name. Fill the "Who can explain it" column before the viva.

| # | Tool and version | Task | What we kept or changed | How we checked it | Who can explain it |
|---|---|---|---|---|---|
| 1 | Claude Code (model: Claude Opus 5.5) | Researched free, credible data sources (NIH IDSR bulletins, Open-Meteo, OpenDengue, HDX) and checked licences/robots.txt | Kept the four sources; NIH robots.txt and Open-Meteo terms were read directly | Opened each source, downloaded samples, inspected Pakistan rows | _member name_ |
| 2 | Claude Code (Claude Opus 5.5) | Wrote the IDSR PDF parser (`ingest/parse_idsr.py`) incl. split-district-name handling | Kept; split-name rule added after inspecting real rows ("Tando / Muhammad / Khan") | Unit tests `tests/test_core.py`; manual check of parsed rows against PDF pages | _member name_ |
| 3 | Claude Code (Claude Opus 5.5) | Wrote fetchers, manifest, DuckDB warehouse (raw/cleaned/gold) and `make data` | Kept | `make data` run from empty folders; manifest tamper test | _member name_ |
| 4 | Claude Code (Claude Opus 5.5) | Wrote the Data Quality Agent (pandera tools, FIX/FILL/FLAG policy, human approval for high-risk fixes, quality_log) | Kept | Unit tests for NR-not-zero, gap fill vs flag, approval guard; reviewed quality_log of a real run | _member name_ |
| 5 | Claude Code (Claude Opus 5.5) | Wrote features, baseline, LightGBM quantile model, time split, bootstrap and per-group errors | Kept | Leakage unit test; test set scored once; numbers in model card come from `metrics.json` | _member name_ |
| 6 | Claude Code (Claude Opus 5.5) | Wrote the Insight Agent (hybrid retrieval, SQL tools, citation + "I don't know" rules, Urdu templates) and golden eval | Kept; a bug in the LLM-text digit check (it rejected its own [S1] markers) was caught by a unit test and fixed | `tests/golden_qa.jsonl` evaluated with `rag/evaluate.py` | _member name_ |
| 7 | Claude Code (Claude Opus 5.5) | Wrote Prefect hourly flow, publish step, Gradio dashboard, deploy script | Kept | Local dashboard run; flow run history | _member name_ |
| 8 | Claude Code (Claude Opus 5.5) | Drafted README, dataset proposal, summary and this log | Edited by the team | Every number in the documents was copied from generated files (`artifacts/*.json`) | _member name_ |
| 9 | Claude API, `claude-opus-5-5` (optional, runtime) | Writes 2-3 plain-language action points in answers when `ANTHROPIC_API_KEY` is set | Output accepted only if it has no digits and every line cites a retrieved passage; otherwise the template is used | `validate_llm_text` unit test | _member name_ |

Embedding model used at runtime: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` (Apache-2.0).
