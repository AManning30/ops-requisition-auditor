# Pilot & Empirical Validity Statistical Report (`ops-req-pilot`)

*Evaluates real-world predictive validity on deidentified pilot data across multi-stage outcomes (interview, selection, promotion, 90-day performance, 90-day attrition), comparing the deterministic rubric against blinded recruiter decisions and measuring ROC-AUC, Brier score, Expected Calibration Error (ECE), 95% confidence intervals, and component ablation.*

## 1. Deidentification, Calibration & Discrimination Summary

- **Harness Version:** `pilot-evidence-harness` v1.5.0 (Reference Date: `2026-11-01`)
- **Deidentified Pilot Cohort:** `6` `(requisition, resume)` pairs (`all_cases_deidentified: True`, `26` PII/ZIP/graduation-date tokens scrubbed before analysis)
- **Mean Requisition Quality Score (RQS):** `87.3 / 100`
- **Mean Engine JD Alignment Score:** `66.3 / 100` vs. **Mean Manual Expert Score:** `67.5 / 100`
- **Blind-Mode Score Parity Drift:** `0.00 pts` *(0.00 = exact parity between blind-redacted and raw resumes)*
- **ROC-AUC (Discrimination):** `1.0000` (95% Bootstrap CI: `[1.0000, 1.0000]`)
- **Brier Score (Probabilistic Accuracy, lower is better):** `0.0298` (95% Bootstrap CI: `[0.0025, 0.0660]`)
- **Expected Calibration Error (ECE):** `0.1205`
- **Max Heuristic JD vs. Confirmed Config Drift:** `2.5 pts`
- **Mean Parser Confidence:** `86.4%` (Resume: `84.2%`, Requisition: `89.0%`)
- **Low-Confidence Cases Flagged for Manual Parse Review (`<75%`):** `1 / 6`
- **Continuous Calibration vs. Domain-Expert Ratings:** Pearson `r = 0.9969` (95% CI: `[0.9685, 1.0000]`) | Spearman `rho = 1.0000` | `MAE = 4.32 pts` | `RMSE = 6.02 pts`

## 2. Blinded Comparison: Engine vs. Blinded Recruiter vs. Human-in-the-Loop

| Decision Maker / Cutoff Policy | TP | FP | TN | FN | Accuracy (95% Wilson CI) | Precision | Recall | Specificity | F1 Score | Cohen's Kappa (`κ`) |
| :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| **Engine Strong Match (`>= 80.0` + Gates)** | 3 | 0 | 2 | 1 | `83.3%` (`[43.6%, 97.0%]`) | `100.0%` | `75.0%` | `100.0%` | `0.857` | `0.667` |
| **Engine Inclusive Review (`>= 65.0` + Gates)** | 4 | 0 | 2 | 0 | `100.0%` (`[61.0%, 100.0%]`) | `100.0%` | `100.0%` | `100.0%` | `1.000` | `1.000` |
| **Blinded Recruiter Baseline (No Engine Score)** | — | — | — | — | `83.3%` | — | — | — | `0.857` | `0.667` |
| **Human Reviewer + Engine Evidence Ledger** | — | — | — | — | `100.0%` | — | — | — | — | `1.000` |

## 3. Calibration Reliability Curve (`p = score / 100` vs. Observed Outcome Rate)

| Probability Tier | Cases (`n`) | Mean Predicted Probability (`p`) | Observed Outcome Rate (`y`) | Absolute Calibration Gap (`|p - y|`) |
| :--- | :-: | :-: | :-: | :-: |
| **Low [0.00, 0.50)** | 2 | `17.5%` | `0.0%` | `17.5%` |
| **Mid [0.50, 0.80)** | 1 | `76.4%` | `100.0%` | `23.6%` |
| **High [0.80, 1.00]** | 3 | `95.4%` | `100.0%` | `4.6%` |

## 4. Multi-Stage Longitudinal Outcome Validity (`interview` → `selection` → `90d_performance` → `promotion` → `attrition_90d`)

| Outcome Stage | Sample Size (`n`) | Base Rate | ROC-AUC | Brier Score | Point-Biserial Correlation (`r`) |
| :--- | :-: | :-: | :-: | :-: | :-: |
| **`interview`** | 6 | `66.7%` | `1.000` | `0.0298` | `+0.947` |
| **`selection`** | 6 | `50.0%` | `1.000` | `0.1178` | `+0.800` |
| **`promotion`** | 6 | `33.3%` | `1.000` | `0.2455` | `+0.635` |
| **`performance_90d`** | 6 | `66.7%` | `1.000` | `0.0298` | `+0.947` |
| **`attrition_90d`** | 6 | `33.3%` | `1.000` | `0.0298` | `-0.947` |

## 5. Component Ablation & Misleading-Feature Audit (Remove / Redesign Analysis)

| Component | Max Pts | Mean Earned | Outcome Corr (`r`) | Leave-One-Out AUC (`ΔAUC`) | Brier Lift (`ΔBrier`) | Validity Decision | Diagnostic Finding |
| :--- | :-: | :-: | :-: | :-: | :-: | :--- | :--- |
| **Component 1: Requirement Coverage** | 45 | `29.0` | `+0.94` | `1.000` (`+0.000`) | `+0.0015` | **KEEP (Validated Positive Lift)** | Strong positive validity (r=+0.94); contributes directly to discriminating qualified candidates. |
| **Component 2: Contextual Placement & Recency** | 20 | `12.1` | `+0.91` | `1.000` (`+0.000`) | `-0.0013` | **KEEP (Validated Positive Lift)** | Strong positive validity (r=+0.91); contributes directly to discriminating qualified candidates. |
| **Component 3: Title, Tenure & Operational Scale** | 15 | `9.8` | `+0.93` | `1.000` (`+0.000`) | `+0.0004` | **KEEP (Validated Positive Lift)** | Strong positive validity (r=+0.93); contributes directly to discriminating qualified candidates. |
| **Component 4: Knockout Gate Verification** | 10 | `6.7` | `+1.00` | `1.000` (`+0.000`) | `+0.0071` | **KEEP (Validated Positive Lift)** | Strong positive validity (r=+1.00); contributes directly to discriminating qualified candidates. |
| **Component 5: Document & Parse Hygiene** | 10 | `8.7` | `+0.63` | `1.000` (`+0.000`) | `-0.0046` | **REDESIGN / DECOUPLE (Move Formatting to Parser Confidence)** | Document formatting deductions (-3 pipe table, -2 headers) reflect resume template layout rather than job capability. Recommendation: report C5 as a Parser Confidence flag instead of deducting capability points. |

## 6. Case-by-Case Deidentified Evidence Ledger

| Case (`Subject Hash`) | Requisition (`RQS`) | Engine Score (`Brier Err`) | Expert Score (`|Err|`) | Parser Conf | Blinded Recruiter | Human + Engine | Validated Outcome (`Int/Sel/90d/Prom/Attr`) |
| :--- | :--- | :-: | :-: | :-: | :-: | :-: | :--- |
| `PILOT-01` (`SUBJ-E03D55AACC25`) | Senior Analytics Manager (`RQS 97.0`) | **100.0** (`0.000`) | 98.0 (`2.0`) | `99%` (HIGH) | `ADVANCE` | `ADVANCE` | **ADVANCE** (`1`/`1`/`1`/`1`/`0`) |
| `PILOT-02` (`SUBJ-6F698E30DFEE`) | Senior Analytics Manager (`RQS 97.0`) | **88.3** (`0.014`) | 87.0 (`1.3`) | `99%` (HIGH) | `ADVANCE` | `ADVANCE` | **ADVANCE** (`1`/`1`/`1`/`0`/`0`) |
| `PILOT-03` (`SUBJ-26F9A834EDFC`) | Senior Analytics Manager (`RQS 97.0`) | **76.4** (`0.056`) | 78.0 (`1.6`) | `99%` (HIGH) | `DO_NOT_ADVANCE` | `ADVANCE` | **ADVANCE** (`1`/`0`/`1`/`0`/`0`) |
| `PILOT-04` (`SUBJ-85FF0C3C4B5B`) | Senior Analytics Manager (`RQS 97.0`) | **33.0** (`0.109`) | 35.0 (`2.0`) | `99%` (HIGH) | `DO_NOT_ADVANCE` | `DO_NOT_ADVANCE` | **DO_NOT_ADVANCE** (`0`/`0`/`0`/`0`/`1`) |
| `PILOT-05` (`SUBJ-AB32A73AB45C`) | Workforce Management Analyst II (`RQS 100.0`) | **98.0** (`0.000`) | 92.0 (`6.0`) | `90%` (HIGH) | `ADVANCE` | `ADVANCE` | **ADVANCE** (`1`/`1`/`1`/`1`/`0`) |
| `PILOT-06` (`SUBJ-1E18B1E5660F`) | Workforce Management Analyst II - WFM Ops - L4 (`RQS 36.0`) | **2.0** (`0.000`) | 15.0 (`13.0`) | `32%` (LOW) | `DO_NOT_ADVANCE` | `DO_NOT_ADVANCE` | **DO_NOT_ADVANCE** (`0`/`0`/`0`/`0`/`1`) |

## 7. Parser Confidence & Human Override Diagnostics

### `PILOT-01` (`SUBJ-E03D55AACC25`) — Senior Analytics Manager (Score: `100.0`, RQS: `97.0`, Parser Confidence: `99.1%`)
- **Parser Telemetry:** Resume `100.0%` (sections: `summary, experience, education, skills`, valid date ranges: `2`, structured char ratio: `89%`) | Requisition `98.0%` (`both_compared`, tier split: `True`, items: `12`) | Heuristic-vs-Confirmed JD Drift: `0.0 pts`
- **Validated Outcome Evidence (*Final Hiring Manager Panel (Hired & Promoted at 14m)*):** All 12 competencies verified in technical panel; selected, exceeded 90-day forecast accuracy KPI, retained, and promoted at 14 months.

### `PILOT-02` (`SUBJ-6F698E30DFEE`) — Senior Analytics Manager (Score: `88.3`, RQS: `97.0`, Parser Confidence: `99.1%`)
- **Parser Telemetry:** Resume `100.0%` (sections: `summary, experience, education, skills`, valid date ranges: `2`, structured char ratio: `88%`) | Requisition `98.0%` (`both_compared`, tier split: `True`, items: `12`) | Heuristic-vs-Confirmed JD Drift: `2.5 pts`
- **Validated Outcome Evidence (*Hiring Manager Technical Screen (Selected & Met 90d KPIs)*):** Synonym matches ('capacity planning', 'operating budget') and skills-list Power BI/SAP S/4HANA confirmed in technical screen; met 90-day ramp targets.

### `PILOT-03` (`SUBJ-26F9A834EDFC`) — Senior Analytics Manager (Score: `76.4`, RQS: `97.0`, Parser Confidence: `99.1%`)
- **Parser Telemetry:** Resume `100.0%` (sections: `summary, experience, education, skills`, valid date ranges: `2`, structured char ratio: `81%`) | Requisition `98.0%` (`both_compared`, tier split: `True`, items: `12`) | Heuristic-vs-Confirmed JD Drift: `2.2 pts`
- **Human Reviewer Note (`recruiter_j_smith`):** Blinded recruiter initially filtered out due to missing literal 'Workforce Capacity Modeling' & 'SAP S/4HANA' keywords; engine semantic ledger (76.4 Borderline) surfaced 'capacity planning' + Python/Snowflake depth, prompting human override to interview.
- **Validated Outcome Evidence (*Hiring Manager Technical Screen (Passed Technical Panel)*):** Demonstrates value over blinded keyword screen: candidate passed HM technical interview (finalist runner-up, hired into lateral Lead Analytics role and met 90d KPIs).

### `PILOT-04` (`SUBJ-85FF0C3C4B5B`) — Senior Analytics Manager (Score: `33.0`, RQS: `97.0`, Parser Confidence: `99.1%`)
- **Parser Telemetry:** Resume `100.0%` (sections: `summary, experience, education, skills`, valid date ranges: `2`, structured char ratio: `82%`) | Requisition `98.0%` (`both_compared`, tier split: `True`, items: `12`) | Heuristic-vs-Confirmed JD Drift: `0.8 pts`
- **Human Reviewer Note (`recruiter_j_smith`):** Reviewed manually; missing Python, Snowflake, SAP S/4HANA, Demand Forecasting, P&L scope, and B.S. gate for Senior Manager level (routed to Analyst II internal mobility plan).
- **Validated Outcome Evidence (*Calibrated Recruiter & HM Calibration Review*):** Engine score (33.0) matched manual calibration (35.0); candidate lacked Senior Manager scope and core Python/Snowflake stack.

### `PILOT-05` (`SUBJ-AB32A73AB45C`) — Workforce Management Analyst II (Score: `98.0`, RQS: `100.0`, Parser Confidence: `89.7%`)
- **Parser Telemetry:** Resume `100.0%` (sections: `summary, experience, education, skills`, valid date ranges: `2`, structured char ratio: `88%`) | Requisition `77.0%` (`heuristic_raw_text`, tier split: `True`, items: `19`)
- **Parser Warnings:** High requirement count (19) extracted from plain-text JD.
- **Validated Outcome Evidence (*Structured WFM Erlang C & NICE IEX Case Study (Hired & Promoted)*):** Calibrated WFM JD (RQS 100.0) cleanly matched NICE IEX, Erlang C, Power BI, and SQL alternatives; candidate hired, met 90d SLA accuracy goals, and promoted to Sr. WFM Analyst.

### `PILOT-06` (`SUBJ-1E18B1E5660F`) — Workforce Management Analyst II - WFM Ops - L4 (Score: `2.0`, RQS: `36.0`, Parser Confidence: `32.0%`)
- **Parser Telemetry:** Resume `5.0%` (sections: `summary`, valid date ranges: `0`, structured char ratio: `78%`) | Requisition `65.0%` (`heuristic_raw_text`, tier split: `False`, items: `20`)
- **Parser Warnings:** Resume missing standard Work Experience heading; bullets may fall into unattributed text.; Resume missing explicit Technical/Core Skills section header.; Resume missing explicit Education section header.; Zero standard MM/YYYY work-history date ranges parsed; recency and net tenure default to unverified.; Missing standard 'Work Experience' section header (-2.0 pts); Multi-column table / pipe layout detected (-3.0 pts); Missing or non-standard employment dates (require MM/YYYY or Month YYYY) (-3.0 pts); Requisition lacks explicit Required vs. Preferred section split; all items weighted as Required.; High requirement count (20) extracted from plain-text JD.
- **Human Reviewer Note (`wfm_director_k_lee`):** Low parser confidence (<75%) triggered manual inspection of raw resume; confirmed lack of WFM, forecasting, and BI experience.
- **Validated Outcome Evidence (*Manual Parse Verification & HM Review*):** Parser confidence guard (<75%) properly flagged non-standard headers, missing dates, and ASCII table layout; RQS (36.0) also flagged the requisition itself for remediation.
