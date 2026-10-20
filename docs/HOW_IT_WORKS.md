# How it works: the methodology image, step by step

Har stage ke liye: **kya karta hai → kaise (kaunsi file) → outcome (asli numbers) → technology aur kyun**.
Numbers 20 Oct 2026 ki run ke hain (`artifacts/*.json`). Naye run ke baad file khol kar dobara dekh lena.

---

## Stage 01 · Frame the Problem
**Kya:** asli problem, user, decision aur success ki definition tay karna.
**Kaise:** `docs/problem_analysis.md` (7 steps + PDF ke worksheet ke saare jawab).
**Outcome:**
- User: Sindh, Balochistan, KP ka district health officer.
- Decision: RDT kits, dawai, beds aur fumigation teams agle hafte kahan bhejni hain.
- Evidence: Week 39 2026 mein Pakistan mein 79,781 suspected malaria cases, jin mein Sindh 67,795 (`2026_W39.pdf` p.5).
- KPI: next-week MAE vs baseline, 80% range coverage, aur agent ke liye refusal, citation aur number accuracy.

**Kyun:** jury pehle "kis ke liye, kyun" poochti hai, aur har technical choice isi se nikalti hai. Misaal ke taur par
district user hai, is liye grain district-week hai.

## Stage 02 · Source & Document Data
**Kya:** credible sources chunna, unke licence aur terms check karna, aur kamzor source ko andar aane se pehle rokna.

**Kaise:**
- `ingest/profile_sources.py`: **Gate 0** har run se pehle har candidate source ki robots.txt, licence aur endpoint
  check karta hai.
- `docs/datasheet.md`: licence, provenance, update frequency, known biases.
- `docs/datasetproposal.pdf`: dataset proposal.

**Outcome (Gate 0):**
- NIH: ACCEPT.
- OpenDengue: ACCEPT.
- HDX: ACCEPT.
- Open-Meteo: **ACCEPT_API (flagged)**. API host ki robots.txt `Disallow: /` kehti hai (yeh crawlers ke liye hai),
  lekin API terms free programmatic use ki ijazat deti hain, aur guide bhi ise recommend karti hai. *Organisers se
  confirm karna.*
- NASA POWER: NOT_USED. Halat Open-Meteo jaisi hai, lekin zaroorat nahi.

**Technology:** `urllib.robotparser`, aur `requests` with retries.
**Kyun:** rules kehte hain ke robots.txt ya terms ke khilaf data nahi lena. Yeh faisla code mein hai aur har run mein
log hota hai, haath se nahi.

## Stage 03 · Warehouse + Data Quality Agent
**Kya:** raw → cleaned → gold, ek command `make data` se, aur har gate pe agent faisla karta hai.

**Kaise:**

| Hissa | File | Kya karta hai |
|---|---|---|
| Fetch + manifest | `ingest/nih_idsr.py`, `ingest/open_meteo.py`, `manifest.py` | Har raw file ka source, query, fetch time, SHA-256 aur licence `manifest.csv` mein |
| Bronze (raw) | `data/raw/` | Files bilkul waise jaise aayin. Kabhi edit nahi hotin (hash check) |
| PDF parsing | `ingest/parse_idsr.py` | Toote headers, toote district names (3 layouts, gazetteer ki madad se), national table |
| **DQ Gate 1** | `quality/agent.py` (`gate1_idsr`, `gate1_weather`) | Schema, types, duplicates, ranges (pandera) |
| Silver (cleaned) | `warehouse/build.py` | Typed values, OCHA pcode match, `ingested_at_utc` (ingest time) vs `week_start` / `ts_utc` (event time, UTC) |
| **Quarantine** | `quarantine.idsr_rows` | Jo rows gold mein nahi ja saktin (unmatched district, unknown province), woh wajah ke saath yahan jati hain. Kabhi chupchaap drop nahi |
| **DQ Gate 2** | `agent.gate2` | Completeness (top-10 trap), consistency (district total vs province total), freshness, drift (PSI) |
| Gold | `gold.district_week` waghera | Stable `row_id`, aur har number ke saath PDF file + page |
| Fail | `GOLD-G6`, `GOLD-SCHEMA` | Run rukta hai, alert jata hai, aur gold aur dashboard last good version rakhte hain |

**Agent ke faisle (asli rules):**
- **FIX:** `"5,133"` ko 5133 banana; PDF ligature `(cid:425)` ko "tt" banana; ek district ke kai units ka jor.
- **FILL:** 3 ghante tak ka weather gap, `is_filled` mark ke saath.
- **FLAG:** `NR` (8,992 cells) aur `not_listed`, dono missing rehte hain, zero nahi; outliers; unmatched names.
- **Human-in-the-loop:** jo FIX table ki 5% se zyada rows badle, woh `hi approve` ke baghair apply nahi hota.

**Outcome:** 203,895 IDSR cells; 2.9 million hourly weather rows; gold mein 34,920 rows (97 districts); quality gate
PASS. Local validation: OpenDengue se 20 mein se 15 mahine exact match (r = 0.997).

**Technology:**
- **DuckDB:** ek file, server nahi, tez analytics. Guide yahi recommend karti hai.
- **pdfplumber:** PDF tables nikalne ke liye.
- **pandera:** schema checks ke liye.
- **rapidfuzz:** district naam matching ke liye.
- **Parquet:** processed tables ke liye.

**Kyun:** 40 points automatic checks ke hain, jo reproducibility aur honesty dekhte hain. Hash, manifest, quarantine
aur quality log mil kar har row ka saboot hain.

## Stage 04 · Model & Evaluate
**Kya:** declared baseline ko harana, uncertainty dikhana, aur errors group ke hisaab se dikhana.

**Kaise:**
- `model/features.py`: sirf week t tak ka data use hota hai; target week t+1 hai.
- `model/train.py`: model, split, aur served-method ka rule.
- `model/card.py`: model card.

**Steps:**
1. **Split first:** time-based (train < 2025-01 ≤ validation < 2025-10 ≤ test).
   *Imaandari ka note:* image "CV-tuned" kehti hai. Hum ek time-based validation fold use karte hain (rolling
   k-fold CV nahi), aur tuning sirf train/validation pe hoti hai.
2. **Baseline:** "agla hafta = yeh hafta", pehle se declare.
3. **Explainable model:** LightGBM (gradient boosting), feature importance model card mein.
4. **Uncertainty:** quantile models (10/50/90), aur headline gain ka 95% block bootstrap.
5. **Errors by group:** province, poverty tier, season, outbreak size, 10 sab se kamzor districts. *Gender data mojood
   nahi hai*, aur yeh limit likhi hui hai.

**Outcome (malaria):** test MAE 117.0 (baseline) → 110.1 (model), yaani −6.0%. Interval −0.3% se +12.0% hai, matlab
pakka nahi. 80% range coverage 78%. Validation pe barabari hai, is liye pehle se tay rule ke mutabiq dashboard abhi
**baseline** dikhata hai. Weather backfill aate hi flow champion/challenger se dobara faisla karta hai. Dengue model
baseline se kharab hai, is liye baseline serve hota hai.

**Technology:**
- **LightGBM:** chhote tabular data pe tez, NaN (missing weather) khud sambhalta hai, aur explainable hai.
- **Quantile loss:** isse range bina extra assumption ke milti hai.

**Kyun:** guide kehti hai "simplest model that clearly beats the baseline". Jab model nahi harata, to hum wohi
seedhi baat kehte hain.

## Stage 05 · Retrieval Layer (RAG)
**Kya:** trusted documents dhoondna, aur numbers SQL se lena.

**Kaise:** `rag/index.py` index banata hai. Library mein yeh hain:
- NIH ki 4 advisories aur 1 guideline;
- WHO ke 2 fact sheets;
- latest 6 bulletins ke narrative pages;
- **model card**.

Har chunk ke saath `doc_id` aur page hota hai.

**Search:** BM25 (exact lafz) + multilingual embeddings (matlab, aur Urdu sawal) ko **reciprocal rank fusion** se
milaya jata hai.
*Note:* image "re-ranked" kehti hai. Hamara re-ranking RRF fusion hai, alag cross-encoder nahi.

**Outcome:** 118 chunks. Har jawab ke saath ek "evidence bundle" hota hai: gold rows (row_id + PDF page) aur
passages (document + page).

**Technology:** sentence-transformers (`paraphrase-multilingual-MiniLM-L12-v2`), FAISS aur rank-bm25. Embedding
cache rakhte hain, jisse indexing 42 s se 9 s ho gayi.

**Kyun:** sirf embeddings district ke naam aur numbers miss kar dete hain, aur sirf BM25 Urdu sawal ko nahi
samajhta. Dono mila kar dono kaam hote hain.

## Stage 06 · Generative Layer
**Kya:** evidence se jawab banana: Q&A, policy briefs, what-if scenarios, chart captions, English aur اردو.

**Kaise:**

| Output | File | Wazahat |
|---|---|---|
| Q&A | `rag/agent.py` | Ek seedha headline jumla, saaf table, quoted guidance, sources, aur IDs "Audit details" mein |
| Policy briefs | `rag/briefs.py` | Har province × disease ka ek page: kahan cases sab se zyada (table) + kya karna hai (NIH/WHO quotes) |
| What-if | `model/whatif.py` | "Baarish X% zyada ho to?" Model ke rain inputs badal kar dobara prediction. Saaf label: *scenario, forecast nahi*. Sirf jab model serve ho raha ho |
| Chart captions | `rag/captions.py` | EN + Urdu jumle, har number ke saath gold row id |

**Guardrails:**
- ✔ Har claim cited hai.
- ✔ Support na ho to "I don't know", wajah ke saath (Lahore, deaths, GDP, cricket).
- ✔ Dates event time (`week_start`) se aati hain, posting date se nahi.
- ✔ **PII filter** (`rag/pii.py`): phone, CNIC aur email mask ho kar hi log hote hain.
- ✔ **Versioned prompts** (`prompts/insight_wording_v1.txt`): version har answer pe likha jata hai.
- ✔ Koi number LLM se nahi aata: `validate_llm_text` digits wali wording reject kar deta hai.

**Evaluated:**
- Golden set: 17 sawal (EN + UR). Refusal 100%, citation 100%, number faithfulness 100%. *Yeh bugs isi set ne pakde
  the aur maine theek kiye, is liye yeh ab "unseen test" nahi raha.*
- **Human review of briefs:** `hi review-brief <id> --by "Naam"`. Brief ka text badle to review khud reset ho jata hai.

**Kyun:** health mein galat number nuksaan kar sakta hai. LLM sirf wording likhta hai, aur woh bhi optional hai.

## Stage 07 · Deploy, Monitor & Deliver
**Kya:** khud chalne wala system, live dashboard, aur alerts.

**Kaise:**
- `pipeline/flow.py`: Prefect flow, cron `5 * * * *`, retries ke saath.
- `pipeline/publish.py`: HF dataset repo pe publish.
- `app/app.py` + `app/render.py`: Gradio dashboard.
- `pipeline/alerts.py`: alerts.

**Dashboard:** "Data as of" (freshness tiles), DQ gates (PASS/FAIL), model metrics, last good run, aur alerts ka banner.

**Alerts:** gate fail, late source, drift, pending approval aur failed run pe banner dikhta hai. `.env` mein
`ALERT_WEBHOOK_URL` ho to Slack/Discord/Teams pe bhi message jata hai.

**Technology:**
- **Prefect:** guide recommend karti hai, aur run history `prefect_runs.csv` ke liye zaroori hai.
- **Gradio + Plotly:** HF Space pe seedha chalta hai.
- **Hugging Face Hub:** data snapshot ke liye.

---

## Running Pipeline · every hour (image ki neeche wali line)

| # | Step | Code | Detail |
|---|---|---|---|
| 1 | Fetch new data | `core.step_fetch`, `step_weather` | Naya bulletin aur weather. Open-Meteo call ledger din ki limit ka 90% rakhta hai, aur forecast ke liye budget reserve hai |
| 2 | Land in Bronze | `manifest.record` | Har file naye naam se append hoti hai. Purani files overwrite nahi hotin, aur hash verify hota hai |
| 3 | Gate 1 → Silver | `agent.gate1_*` | Fail ya unknown ho to quarantine. Hard fail pe alert aur run stop |
| 4 | Gate 2 → Gold | `agent.gate2`, `build_gold` | Freshness aur drift (PSI). Gold ki atomic replace sirf pass hone pe |
| 5 | Retrain if needed | `flow.retrain_if_needed` | Naya hafta, drift, forecast tables missing, ya weather coverage 5% se zyada badhne pe. Validation pe behtar ho to hi rakhte hain |
| 6 | Re-index | `flow.reindex_if_needed` | Naye bulletin ya documents pe |
| 7 | Regenerate briefs | `flow.briefs_and_eval` | Sirf jab model ya index badle (LLM ka kharcha kam). Golden eval har ghante |
| 8 | Refresh dashboard | `publish.run` | Gold snapshot + status → HF dataset → Space |

Upar se "A failed gate stops the run; Gold and the dashboard keep the last good version": `build_gold` gold tabhi
likhta hai jab Gate 2 pass ho.

## Principles across every stage
- **Reproducible:** `pyproject.toml` mein pinned dependencies, `SEED = 42`, raw files ke hash, aur `make data` ek
  command. Same raw files pe same numbers aate hain (do dafa train kar ke check kiya).
- **Traceable:** dashboard ka har number → `row_id` → PDF file + page (clickable link).
- **Responsible:** errors by group, limits likhe hue, PII filter, emergency line.
- **Honest AI use:** `AI_Use_Log.md`.

## Final Submission (image ka box)

| Item | Halat |
|---|---|
| Working pipeline & dashboard | ✅ Local chal raha hai. HF Space aap ke token se deploy karna hai |
| `Summary.pdf` | ✅ `docs/Summary.pdf` (1 page) |
| Model card & DQ report | ✅ `docs/model_card_*.md`, `artifacts/quality_report.json`, `quality_log` |
| Demo video | ❌ Aap ka kaam |
| `AI_Use_Log.md` | ✅ Members ke naam aap ko bharne hain |
| Generative eval results | ✅ `artifacts/eval_report.json` |
