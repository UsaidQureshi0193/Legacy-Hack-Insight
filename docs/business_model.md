# Business model: value, users and sustainability

## Value proposition
For **district health officers and provincial vector-borne disease programme managers** in Pakistan, who must
decide each week **where to send RDT kits, antimalarials, beds and fumigation teams**, our system **forecasts
next week's suspected malaria and dengue cases per district with an 80% range and gives the official NIH/WHO
guidance with citations, in English and Urdu**, so that **stock and teams arrive before the peak instead of
after it**, unlike **today's practice of reading a 25-page PDF bulletin one to two weeks late and reacting to
last week's numbers**.

## The five questions
| Question | Answer |
|---|---|
| **User** | District health officer / DHIS focal person; provincial malaria & dengue control programme |
| **Customer / payer** | Provincial health departments (Sindh, Balochistan, KP), or a partner programme that already funds malaria/dengue surveillance (e.g. WHO country office, Global Fund malaria grant, UNICEF in flood response) |
| **Value** | Earlier and better-targeted allocation; fewer stock-outs in hotspot districts; less manual work reading bulletins; a consistent, cited, bilingual brief |
| **Sustainability** | **Contract, grant or public budget**: the benefit is public health, so a free-to-user service paid by the programme fits best. Optional later: "free basic, paid advanced" (SMS/WhatsApp alerts per district, custom reports) |
| **Running cost** | See below. Small, because data is public and the model is light |

## Running cost: components and assumptions
Every figure below is an **assumption to confirm with current price lists**. None are measured results.

| Item | Assumption | Notes |
|---|---|---|
| Data | 0 PKR | NIH bulletins are public, OCHA/OPHI/OpenDengue are open licences. **Commercial use of Open-Meteo needs its paid API plan**, or a switch to NASA POWER / PMD data |
| Compute for the hourly pipeline | 1 small always-on VM (2 vCPU, 4 GB) | An incremental run re-parses only new PDFs (hash cache) and skips past-year weather |
| Dashboard | Hugging Face Space (free CPU tier for a pilot) | Upgrade if many concurrent users |
| LLM wording (optional) | Claude Opus 5.5 at $4 / $20 per million input/output tokens (Anthropic list price). One answer ≈ 1.5k input + 0.2k output tokens ≈ $0.01 | Briefs are regenerated only when a new bulletin arrives (weekly), not hourly. ~12 briefs/week ≈ $0.12/week. Without a key the template wording is used at $0 |
| People | Part-time data engineer (parser maintenance when NIH changes the PDF layout) + epidemiologist review | The biggest real cost |

**Net benefit** = (value of earlier, better-targeted response + staff hours saved reading bulletins) − (VM +
maintenance). The time-saved part can be estimated locally: hours per week a district officer spends on the
bulletin × officers × cost of an hour. NIH reports **158 IDSR-implemented districts** (Week 39, 2026 bulletin).
We have not measured hours saved, so we do not claim a number.

## Risks and mitigations
| Risk | Mitigation |
|---|---|
| Users do not trust it | Every number links to the PDF page it came from (row id + page); "I don't know" when unsupported; errors by group are shown openly |
| NIH changes the PDF layout | The parser refuses to guess, the quality gate fails loudly, and the dashboard keeps the last good data |
| Licence changes (Open-Meteo commercial) | Source is isolated in one module; alternatives listed above |
| Model drift (new outbreak pattern, floods) | PSI drift check every run triggers retraining. Champion/challenger keeps the better model |
| Harm from wrong output | Outputs are suspected-case forecasts, not diagnoses. Every answer points to hospitals / Rescue 1122 |
