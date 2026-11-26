# Model Card: `ops-requisition-auditor` (Deterministic Rubric & Pilot Harness v1.5.0)

*Structured in accordance with Mitchell et al. (2019), "Model Cards for Model Reporting," and aligned with Colorado SB 24-205, NYC Local Law 144, Illinois HB 3773, and EEOC Uniform Guidelines documentation standards.*

---

## 1. Model Details

- **Name & Version:** `ops-requisition-auditor` v1.5.0 (Rubric `rqs-0.2` / JD Alignment `1.5.0`)
- **Architecture:** **100% deterministic, rule- and taxonomy-based mathematical scoring engine** written in the Python 3.10+ standard library. **No Large Language Model (LLM), neural network, or stochastic generative model** is used anywhere in the scoring, redaction, or reskilling path.
- **Author & Maintainer:** Alexandru Manning
- **License:** Dual-licensed under `AGPL-3.0-only OR LicenseRef-Commercial`
- **Core Modules:**
  - `ops-req-audit` (`scripts/offline_ats_auditor.py`): 100-point Candidate JD Alignment Engine (`C1`–`C5`)
  - `ops-req-jd-audit` (`scripts/requisition_quality_auditor.py`): 100-point Employer Requisition Quality Score (`rqs-0.2`)
  - `ops-req-screen` (`scripts/explainable_screening_assistant.py`): Blind-mode batch screening, Colorado/NYC candidate factor disclosure notices, and EEOC four-fifths ($80\%$) selection-rate tables
  - `ops-req-mobility` (`scripts/internal_mobility_matcher.py`): Multi-role internal mobility & outplacement matcher with 30/60/90-day WFM/BI reskilling roadmaps
  - `ops-req-pilot` (`scripts/pilot_evidence_harness.py`): Empirical validity evaluation harness (ROC-AUC, Brier score, ECE, 95% CIs, blinded recruiter comparison, and component ablation)

---

## 2. Intended Use & Out-of-Scope Use

### 2.1 Primary Intended Uses
1. **Requisition Calibration (Employer Module 1):** Auditing draft or live job descriptions to remove uncalibrated degree gates (`G1`), inflated experience-year demands (`G3`), and competing-product requirements (`L3`) before posting.
2. **Human-in-the-Loop Screening Aid (Employer Module 2):** Providing recruiters with a blind-redacted, quoted-evidence checklist and plain-language candidate factor disclosure notice to support structured human review.
3. **Candidate & Internal Mobility Gap Analysis (Candidate & Module 3):** Identifying fixable terminology, placement, and reskilling gaps between a worker's resume and open WFM, BI, analytics, or operations requisitions without fabricating experience.

### 2.2 Out-of-Scope & Prohibited Uses
- **Never for Automated Rejection:** The engine must **never** be configured to auto-reject applicants. All candidates in `ops-req-screen` default to `PENDING_HUMAN_REVIEW`.
- **Not a Proxy for Proprietary Vendor Algorithms:** The score does not replicate or predict proprietary third-party ATS ranking models (Workday, iCIMS, Greenhouse, Lever, Eightfold, etc.).
- **Not a Standalone Substitute for a Third-Party Bias Audit:** Where a jurisdiction requires an independent external bias audit (e.g., NYC Local Law 144 when used as an AEDT), `ops-req-screen` and `ops-req-pilot` provide the deterministic data tables and factor notices for the auditor but do not replace independent legal/audit sign-off.

---

## 3. Factors, Domain Taxonomy & Blind-Mode Controls

- **Domain Specialization:** Built and calibrated specifically for **Workforce Management (WFM)** (NICE IEX, Verint, Genesys Cloud, Aspect, Calabrio, Erlang C, intraday/RTA, shrinkage, occupancy), **Labor Management** (UKG/Kronos), **Business Intelligence & Analytics** (SQL, Python, R, Power BI, Tableau, Snowflake), and **Enterprise Operations** (SAP S/4HANA, Six Sigma, capacity modeling, P&L scope).
- **Non-Interchangeability Rule:** Generic requirements (`"WFM platform"`, `"BI tool"`) are satisfied by named products, never the reverse (`"Power BI"` is never satisfied by `"Tableau"`). Competing platforms never earn synonym credit for each other.
- **Blind-Mode Demographic Proxy Mitigation (`ops-req-screen`):**
  - Strips candidate name headers, email addresses (`redacted@redacted.invalid`), phone numbers, personal URLs, **5/9-digit ZIP codes** (Illinois HB 3773 geographic proxy mitigation), and **education graduation dates** (ADEA age-proxy mitigation) prior to scoring.
  - Verified to produce **0.00-point score drift** ($\text{Score}_{\text{blind}} \equiv \text{Score}_{\text{raw}}$).

---

## 4. Empirical Evaluation Metrics & Pilot Benchmark Results

### 4.1 Employer Requisition Quality Field Calibration (`N = 113` Live Public Postings)
Calibrated across **113 unique public requisitions from 15 employers** across 5 industry verticals (Large Tech, Contact-Center/WFM, Healthcare, Retail/E-Commerce, and Mid-Market/Logistics; see `references/requisition-quality-rubric.md`):
- **Median RQS:** `88.0 / 100` (range `62.0 – 100.0`)
- **Uncalibrated Degree Gates (`G1`):** Detected in `37.2%` of all postings (`50.0%` in Mid-Market/Logistics).

### 4.2 Pilot Validity & Calibration Benchmark (`ops-req-pilot --demo`, `N = 6` Deidentified Longitudinal Cases)
Evaluated across 5 outcome stages (`interview`, `selection`, `performance_90d`, `promotion`, `attrition_90d`):

| Metric | Value | 95% Confidence Interval / Notes |
| :--- | :-: | :--- |
| **ROC-AUC (Interview & Technical Screen Pass)** | `1.0000` | `[1.0000, 1.0000]` (1,000-iteration seeded bootstrap) |
| **Brier Score ($\frac{1}{N}\sum (p_i - y_i)^2$, lower is better)** | `0.0222` | `[0.0033, 0.0457]` (1,000-iteration seeded bootstrap) |
| **Expected Calibration Error (ECE)** | `0.1192` | Across Low `[0.00, 0.50)`, Mid `[0.50, 0.80)`, High `[0.80, 1.00]` tiers |
| **Inclusive Cutoff (`>= 65.0` + Gates) Accuracy & $\kappa$** | `100.0%` ($\kappa = 1.000$) | 95% Wilson CI: `[61.0%, 100.0%]` |
| **Blinded Recruiter Baseline Accuracy & $\kappa$** | `83.3%` ($\kappa = 0.667$) | Missed 1 borderline semantic-synonym candidate (`PILOT-03`) who passed technical interview |
| **Human Reviewer + Engine Ledger Accuracy & $\kappa$** | `100.0%` ($\kappa = 1.000$) | Recovered `PILOT-03` via quoted semantic synonym ledger |
| **Pearson $r$ / Spearman $\rho$ vs. Domain-Expert Score** | `r = 0.9969` / `ρ = 1.0000` | $\text{MAE} = 4.32\text{ pts}$, $\text{RMSE} = 6.02\text{ pts}$ |
| **Blind-Mode Score Drift** | `0.00 pts` | Exact parity across all deidentified cases |

---

## 5. Known Limitations & Feature Redesign Decisions (From Component Ablation)

Running the **Component Ablation & Misleading-Feature Audit (`Section 5` of `ops-req-pilot`)** identifies three concrete structural limitations and engineering rules:

1. **Component 5 (`Document & Parse Hygiene`, 10 pts) Can Confound Formatting with Capability:**
   - *Limitation:* Deductions for non-standard section headers (`-2 pts`), missing/irregular date formats (`-3 pts`), or pipe/table layouts (`-3 pts`) measure *resume formatting and parser readability*, not a candidate's underlying operational skill.
   - *Mitigation Implemented in `ops-req-pilot`:* Added an explicit **0–100% Parser Confidence Score** (`compute_parser_confidence`). When `combined_confidence < 75%`, the harness flags `requires_manual_parse_check = true` so recruiters inspect the raw document instead of treating a low hygiene score as lack of skill.
2. **Component 3 (`Operational Scale`, 5 pts) Is a Document-Wide Presence Heuristic:**
   - *Limitation:* Component 3 checks for quantified scope metrics (`$`, `%`, headcount/site counts) and seniority action verbs across the resume as a whole rather than attributing scale per individual role.
   - *Mitigation:* Always inspect the quoted evidence ledger in Component 1/2 to verify which role held the operational scope.
3. **Plain-Text JD Extraction vs. Confirmed `--jd-config`:**
   - *Limitation:* Heuristic extraction from narrative job postings can over-extract responsibilities or miss implicit tier splits (`heuristic_confidence <= 0.85`).
   - *Mitigation:* `ops-req-pilot` directly measures `heuristic_vs_confirmed_score_drift` and recommends locking a recruiter-confirmed `--jd-config` (`requisition_confidence = 1.00`) for production screening.
4. **Sample Size Warning on Pilot CIs:**
   - The built-in 6-case cohort (`examples/sample_pilot_manifest.json`) is a reference fixture for CI verification. Enterprise deployments must run `ops-req-pilot --manifest` on a pre-registered deidentified cohort of $N \ge 30\text{–}100$ requisitions/applicants per job family following [references/pilot-protocol.md](references/pilot-protocol.md).
