# Samjho Guide: kya banaya, kyun banaya, kaise chalta hai

Yeh guide team ke har member ke liye hai. Viva mein jury koi bhi module khol kar pooch sakti hai, is liye har
hissa yahan simple Roman Urdu mein samjhaya gaya hai. Pehle poori guide parho, phir code saath khol kar dobara.

---

## 0. Ek line mein project

**District health officer** (Sindh, Balochistan, KP) ko har hafte batana ke **agle hafte kis district mein
malaria / dengue ke suspected cases sab se zyada honge** (range ke saath), aur NIH/WHO ki official guidance
**citation ke saath** dena, English aur Urdu dono mein. Faisla: dawaiyan, RDT test kits, beds aur fumigation
teams kahan bhejni hain.

```
NIH IDSR PDFs ─┐
Open-Meteo ────┼─> RAW (as-is + manifest.csv) ─[GATE 1]─> CLEANED ─[GATE 2]─> GOLD ─┬─> Model (forecast + range)
OpenDengue ────┤        Data Quality Agent: FIX / FILL / FLAG + quality_log         ├─> Insight Agent (SQL + citations)
HDX (MPI/pop) ─┘                                                                    └─> Dashboard (HF Space)
            Prefect: har ghante 5 minute par (cron "5 * * * *") yahi sab dobara
```

---

## 1. Data: kahan se aaya aur kyun (stage 02)

| Source | Kya hai | Kyun liya |
|---|---|---|
| **NIH Pakistan IDSR weekly bulletins** (nih.org.pk) | Har hafte ki PDF. Har province ki district table: Malaria, Dengue, Typhoid... suspected cases | Yeh **asal Pakistani surveillance data** hai, district level par. Track list mein nahi hai, to "non-track credible source" wali shart poori hoti hai |
| **Open-Meteo archive + forecast** | Har district ke centroid par hourly temperature, humidity, baarish (UTC) | Machhar ko paani aur garmi chahiye. Baarish ke 2-4 hafte baad cases barhte hain |
| **OpenDengue v1.3** (Figshare, CC BY 4.0) | Pakistan dengue ki purani national/province series | Benchmark aur purani history; validation ke liye local reference |
| **HDX** (OCHA boundaries, 2017 census population, OPHI MPI) | District codes (pcodes) aur centroids, population, province poverty index | District ko ek stable ID dena, per-100k rate, aur **income group fairness** |

**Kyun yeh "credible" hai:** original publisher ka naam hai (NIH, WMO-based ERA5, OCHA, OPHI), licence
manifest mein likha hai, NIH ki `robots.txt` sab allow karti hai, aur Open-Meteo ka free tier
non-commercial hai (hum student research hain).

**Rate limit ka faisla (viva mein zaroor batana):** Open-Meteo ka free tier ek request ko
"locations × 2-hafte ke blocks" calls ginta hai. 100 districts × 4 saal ≈ 10,000 calls hote hain, jabke limit
10,000 per din, 5,000 per ghanta aur 600 per minute hai. Pehli koshish par 429 (rate limit) aaya. Humne:
(1) NASA POWER dekha, lekin us ki `robots.txt` mein `Disallow: /api/` hai, aur rules robots.txt ke khilaf data
lene se mana karte hain, is liye reject kiya; (2) Open-Meteo ke liye **call ledger** banaya
(`artifacts/open_meteo_ledger.json`) jo har request se pehle weight ginta hai aur 90% limit par rukta hai. Baqi
history agle hourly runs mein khud aati hai; (3) forecast har 3 ghante, har ghante nahi. Jo hafte abhi weather ke
baghair hain un mein weather `NaN` hai, aur LightGBM NaN ko sambhal leta hai.

**Volume:** ~200 bulletins × ~1,400 cells = 2.5 lakh+ IDSR rows, aur ~100 districts × 2021 se ab tak hourly
weather = lakhon rows. Minimum (50,000 rows / 2 saal hourly) dono tarah se poora.

---

## 2. Raw layer aur manifest (`manifest.py`, `ingest/*`)

* **Raw files kabhi edit nahi hoti.** Yeh "evidence" hain.
* Har file ke liye `manifest.csv` mein: path, source, exact URL/query, fetch time (UTC), **SHA-256**, licence.
* `make data` har raw file ka hash dobara nikaalta hai. Agar kisi ne file badli to hash match nahi karega,
  file delete ho kar source se dobara aayegi. Phir bhi mismatch ho to **build ruk jata hai** (Gate 0).
* NIH ki file names gande hain ("IDSR weekly report 38-NG (1).pdf"), is liye hum **listing page ka title**
  ("Week 38 Bulletin (2026)") padh kar `2026_W38.pdf` naam dete hain. URL guess nahi karte.
* Listing page ka har snapshot bhi save hota hai, taa ke saboot rahe ke us waqt NIH ne kya publish kiya tha.
* `http.py`: polite client. Apni pehchaan batata hai, har call ke beech 1 second rukta hai, aur
  429/5xx par backoff ke saath retry karta hai.

---

## 3. PDF parsing (`ingest/parse_idsr.py`): sab se mushkil engineering

Masle jo humne asli PDFs mein dekhe aur kaise hal kiye:

1. **Ek value 3 cells mein phaili hoti hai** (`'', '5,133', ''`). Is liye khaali cells hata kar ("compress")
   header aur values ko zip karte hain. Agar dono ki ginti barabar na ho to row **skip + report** hoti hai,
   alignment guess nahi karte.
2. **Header 2-3 lines mein toota hota hai** ("AD (Non-" / "Cholera)"). Columns ko upar se neeche merge karte
   hain, phir `disease_key()` se normalise ("Typhoi\nd" ban jata hai "typhoid").
3. **District ka naam do rows mein toota** ("Tando" / "Muhammad" / "Khan"). Continuation row ya to khaali
   hoti hai ya pichli row ki exact copy, to naam jod dete hain. **Saari zero wali rows nahi jodte**, kyunke do
   asal districts dono zero report kar sakte hain.
4. **2021-22 ke bulletins ka layout alag hai** (table text ke andar). Inhe parse nahi karte, aur yeh baat
   **chhupate bhi nahi**: quality log mein `no_supported_table` FLAG hota hai.
5. National table (province totals) bhi parse hoti hai. Lab wali tables ("Total Test") skip hoti hain.

Speed: parsing **parallel processes** mein hoti hai, aur result **SHA-256 cache** mein jata hai. Jo PDF badli
nahi, woh dobara parse nahi hoti (optimization log dekho).

---

## 4. Data Quality Agent (`quality/agent.py`): stage 03

Yeh sirf "checks ka script" nahi hai. Yeh **tools chalata hai** (pandera schemas + apne checks), har finding
par **decide karta hai**, **wajah likhta hai**, aur `quality_log` table mein timestamp ke saath **log karta
hai**.

| Action | Kab | Misaal (asli rules) |
|---|---|---|
| **FIX** | Pakki aur reversible correction | `"5,133"` ko 5133 banana (IDSR-R1, original `value_raw` bhi rakhte hain); Kelvin ko Celsius karna (WX-W1); archive/forecast ka duplicate hour (WX-W2) |
| **FILL** | Chhota gap, estimate **marked** | Weather mein ≤ 3 ghante ka gap: linear interpolation, `is_filled=True` (WX-W3); features ke liye 1 hafte ka gap (GOLD-G3). **Target kabhi fill nahi hota** |
| **FLAG** | Yaqeen nahi, to chhedo mat, warn karo | `NR` = not reported: **missing, zero nahi** (IDSR-R2); **top-10 trap** (GOLD-G1); lamba weather gap; outlier (robust z > 6, ho sakta hai asal outbreak ho); district naam match na hona |
| **FAIL** | Gate band | Gold schema toote, ya gold rows pichli dafa se 10% se zyada kam hon. Run rukta hai aur **dashboard last good version** dikhata hai |

**Top-10 trap (jury ko zaroor batana):** har bulletin mein har province ki sirf us hafte ki top-10 diseases
chhapti hain. Agar Sindh ki table mein dengue nahi hai, iska matlab "dengue list mein nahi aaya" hai, **zero
nahi**. Zero bharte to model seekhta ke dengue gayab ho gaya. Hum `cases_status = not_listed` rakhte hain.

**Human-in-the-loop:** agar koi FIX table ki 5% se zyada rows badlega to woh "high risk" hai. Woh tabhi apply
hota hai jab koi insaan `hi approve <RULE> --by "Naam"` chalaye. Tab tak agent FLAG karta hai aur decision
`pending_approval` rehta hai.

**Gate 2 ke extra checks:** district totals ka jor vs bulletin ka "Total" row (consistency), har source ki
freshness, aur **drift (PSI)**: aakhri 8 hafte vs training period. PSI 0.25 se zyada ho to retraining trigger
hoti hai.

---

## 5. Warehouse: cleaned aur gold (`warehouse/build.py`, DuckDB)

* DuckDB: ek file, koi server nahi, tez SQL.
* **Time zones:** sab UTC mein store hota hai. Weekly weather UTC Monday weeks ka hai.
* **District matching (`geo.py`):** bulletin ke naam ("Naushero Feroze", "Kamber") OCHA pcode se match hote
  hain, usi province ke andar, rapidfuzz score ≥ 88 par. Jo match na ho woh FLAG hota hai aur
  `configs/district_aliases.csv` mein insaan review kar ke alias daalta hai. Kabhi guess nahi karte.
* **Gold `district_week`:** district × disease × har hafta. Har row ke saath:
  `row_id` (stable hash), `source_file` + `page` (kaunsi PDF ka kaunsa page), `cases_status` (reported /
  not_reported / not_listed / district_not_in_table / no_bulletin), weather, population, poverty tier, season.
  **Dashboard ya agent ka har number isi table se aata hai.**

---

## 6. Model (`model/features.py`, `model/train.py`): stage 04

* **Target:** district ke **agle hafte** ke suspected cases (week t+1). Features sirf week t tak ka data use
  karte hain (leakage test: `tests/test_core.py`).
* **Declared baseline (pehle se fix):** "agla hafta = yeh hafta" (naive last value).
* **Split time se hota hai:** train < 2025-01-01 ≤ validation < 2025-10-01 ≤ test. Tuning aur early stopping
  sirf validation par. Final model train+val par refit hota hai aur **test sirf ek dafa** score hota hai.
* **Model:** LightGBM, 3 models: 10th, 50th aur 90th percentile. Median forecast hai, aur 10-90 ek **80%
  prediction interval** hai. Log scale par train karte hain kyunke counts bahut skewed hain (Badin 5,000 vs
  Awaran 30). Quantiles log transform ke baad bhi sahi rehte hain.
* **Features ki wajah:** lags/rolling (outbreak persistent hota hai), baarish/humidity/temperature 1-4 hafte
  pehle (machhar ka life cycle + incubation), week-of-year sin/cos (season), province, poverty, population.
* **Uncertainty 2 tarah:** (1) har forecast ki 80% range, (2) headline improvement ka 95% **block bootstrap**
  interval (poore hafte resample, kyunke ek hafte ke errors aapas mein jude hote hain).
* **Errors by group:** province, poverty tier, season, size band, aur 10 sab se kamzor districts. Table
  "weakest first" sorted hai, taa ke kamzor group chhup na sake.
* **Champion/challenger:** naya model tabhi save hota hai jab validation par purane se behtar ho.
* **Gender:** IDSR district tables mein mard/aurat/bachon ka breakdown **hai hi nahi**. Hum ise limit
  likhte hain aur banawati data nahi banate. Groups: region, income (MPI tier), season.

---

## 7. Insight Agent (RAG) (`rag/index.py`, `rag/agent.py`): stages 05-06

* **Library:** NIH advisories (dengue 2024 aur 2025, monsoon 2026, floods 2025), NIH dengue guidelines, WHO
  dengue/malaria fact sheets, aur latest IDSR bulletins ke "Public Health Actions" pages. Sab manifest mein.
* **Retrieval hybrid hai:** BM25 (exact lafz, jaise "Badin") + multilingual embeddings (matlab, aur Urdu
  sawal ko English text se match karna). Dono ko reciprocal rank fusion se jodte hain.
* **Tools:** `sql_forecast`, `sql_history`, `sql_status`, `retrieve`.
* **Teen hard rules code mein enforce hain:**
  1. **Koi number LLM se nahi.** Numbers SQL se aate hain aur saath `row_id` hota hai. Optional LLM sirf
     wording likhta hai. Agar us ke text mein koi bhi digit aaya (English ya Urdu) to woh reject ho jata hai
     (`validate_llm_text`).
  2. **Har recommendation ke saath source.** Ya to passage ka exact quote hota hai, ya LLM ki line jis par
     `[S#]` marker ho jo kisi retrieved passage ki taraf ishara kare.
  3. **Support nahi to "I don't know"**, aur kya missing hai: misaal ke taur par Lahore (Punjab districts
     report nahi hote), deaths (IDSR sirf cases ginta hai), GDP, cricket.
* **Emergency:** har health jawab ke aakhir mein: "information, not medical advice … Rescue 1122".
* **Golden eval (`tests/golden_qa.jsonl`, `rag/evaluate.py`):** 16 sawal (English + Urdu, answerable,
  unanswerable aur misleading). Teen cheezein naapte hain: refusal accuracy, citation accuracy, aur number
  faithfulness (har number `row_id` se dobara padh kar match karte hain).

---

## 8. Automation aur dashboard: stage 07

* `pipeline/flow.py`: Prefect flow `hackinsight-hourly`, cron `5 * * * *`. Steps: data pipeline (retries),
  retrain sirf naye hafte ya drift par, re-index, briefs + golden eval, publish.
* `pipeline/publish.py`: sirf gold tables + status ka chhota snapshot banata hai aur HF dataset repo par
  upload karta hai. Space wahan se har 10 minute baad padhta hai.
* `app/app.py` (Gradio): freshness tiles, quality status, last successful run, forecast table + chart (missing
  hafte **gap** dikhte hain, zero nahi), Ask (EN/اردو), briefs, model vs baseline, quality log, limits.
* `hi export-runs`: Tue 20 Oct 4 PM se Wed 21 Oct 4 PM PKT tak ki Prefect history `prefect_runs.csv` mein.

---

## 9. Commands (sab se zaroori)

```powershell
.\make.ps1 setup        # pehli dafa: Python 3.12 venv + packages
.\make.ps1 data         # khaali folders se raw -> cleaned -> gold + quality summary
.\make.ps1 train        # baseline vs model, metrics by group, forecast
.\make.ps1 index        # RAG index
.venv\Scripts\python -m hackinsight.model.experiments   # optimization log (measured)
.venv\Scripts\python -c "from hackinsight.rag import briefs, evaluate; briefs.generate(); print(evaluate.run()['summary'])"
.\make.ps1 app          # dashboard local par
.\make.ps1 flow         # hourly scheduler (Tue 4 PM se pehle chalu karo, laptop band mat karna)
.\make.ps1 test         # unit tests
```
Linux/Mac par yahi targets `make data`, `make train` waghera se chalte hain.

---

## 10. Viva ke sawal: ek-ek line ke jawab

(Numbers `artifacts/model_malaria/metrics.json` aur `artifacts/eval_report.json` se bharo, upar wale
results section mein bhi hain.)

* **Yeh problem kyun?** Malaria aur dengue har saal monsoon ke baad Sindh/Balochistan/KP mein phailte hain.
  District officer ko pehle se pata ho to kits, dawai aur fumigation sahi jagah pahunchti hai.
* **Kaise pata ke problem asli hai?** NIH ke apne bulletins (jaise Week 39 2026 mein Sindh mein ek hafte mein
  ~68 hazaar suspected malaria cases) aur NIH ki dengue advisories.
* **Sources kyun, licence kya?** NIH (government public report, robots.txt allow), Open-Meteo CC BY 4.0
  (non-commercial), OpenDengue CC BY 4.0, HDX CC BY-IGO / CC0.
* **Raw data ka sab se bura masla?** PDF tables: phaili hui values, toote header aur naam, top-10 trap, Punjab
  NR, aur 2021-22 ka alag layout.
* **Agent ne fill ki jagah flag kab kiya?** `not_listed` aur `NR` mein. Inhe zero bharna jhoot hota.
  Lambe weather gaps (> 3 ghante) bhi flag hote hain.
* **Baseline kya hai aur model kitna behtar?** Last value. Test par MAE 117.0 se 110.1, yaani 6.0% behtar,
  lekin 95% CI −0.3% se +12.0% hai, to fark pakka nahi hai. Validation par barabar tha, is liye pehle se tay
  rule ke mutabiq dashboard abhi baseline dikhata hai. Isse imaandari se batao: hum ne rule results dekh kar
  nahi badla. Model ki asal kamai abhi calibrated range hai (80% interval coverage 78%).
* **Model sab se kharab kahan hai?** Errors-by-group table ka pehla row. Wahan officer ko range ka upper end
  dekh kar plan karna chahiye.
* **Agent number kabhi invent kyun nahi karta?** Numbers SQL se `row_id` ke saath aate hain. LLM ko numbers
  dikhte hi nahi, aur us ka koi digit text reject ho jata hai. Golden eval number faithfulness 100% check
  karta hai.
* **User kaun, paisa kaun dega?** User: district health officer / DHIS focal person. Payer: provincial health
  department, ya WHO/UNICEF/Global Fund malaria program (contract/grant). Business model `docs/business_model.md` mein hai.
* **Kya optimize kiya, kya keemat di?** `docs/optimization_log.md`: parallel parsing, hash cache, embedding
  cache, tuning grid. Har change ke saath accuracy/time/memory before-after likha hai.
