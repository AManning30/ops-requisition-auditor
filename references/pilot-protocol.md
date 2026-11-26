# Pre-Registered Empirical Pilot Protocol (`ops-req-pilot`)

This document defines the standardized, blinded empirical validation protocol for evaluating whether the `ops-requisition-auditor` rubric predicts real-world interview and job outcomes in Workforce Management (WFM), Business Intelligence (BI), and operations hiring.

---

## 1. Study Objective & Falsifiable Hypothesis

A deterministic 0–100 score can look mathematically precise while failing to predict downstream interview performance or on-the-job execution. This protocol tests three falsifiable hypotheses on deidentified historical or prospective cohorts:

- **H1 (Discrimination):** The JD Alignment Score achieves $\text{ROC-AUC} \ge 0.75$ (with 95% bootstrap confidence interval lower bound $> 0.60$) in distinguishing candidates who pass structured technical/case interviews (`interview = 1`) from those who do not (`interview = 0`).
- **H2 (Calibration & Probabilistic Accuracy):** Normalized score probabilities $p_i = \text{Score}_i / 100$ achieve a **Brier Score** $< 0.20$ and **Expected Calibration Error (ECE)** $< 0.15$ against observed structured interview and selection rates.
- **H3 (Incremental Lift Over Blinded Keyword Screen):** Human reviewers equipped with the engine's semantic evidence ledger (`Human + Engine`) achieve higher recall on qualified non-traditional candidates (e.g., same-capability synonym matches or bridgeable skills) and higher Cohen's $\kappa$ than independent **Blinded Recruiters** screening without the ledger.

---

## 2. Five Standardized Longitudinal Outcome Definitions

Every pilot case $(r, c)$ tracks five standardized binary (`0` / `1`) longitudinal outcome variables:

| Outcome Variable | Stage Horizon | Operational Definition (`1` vs. `0`) |
| :--- | :--- | :--- |
| `interview` | Days 1–14 | **`1`:** Advanced to and passed structured hiring-manager technical or WFM/BI work-sample interview.<br>**`0`:** Did not pass technical screen or work-sample evaluation. |
| `selection` | Days 14–30 | **`1`:** Extended a formal job offer (or internal transfer selection) following final panel.<br>**`0`:** Not selected for offer. |
| `performance_90d` | Day 90 Post-Hire | **`1`:** Met or exceeded pre-defined 90-day operational ramp KPIs (e.g., forecast WAPE accuracy, schedule adherence target, BI deliverable SLA).<br>**`0`:** Missed 90-day operational ramp expectations. |
| `promotion` | Months 12–18 | **`1`:** Promoted to next career-ladder level or expanded operational scope within 12–18 months.<br>**`0`:** Not promoted within observation window. |
| `attrition_90d` | Day 90 Post-Hire | **`1`:** Early regrettable or involuntary turnover within 90 days of start date (inverse validity indicator).<br>**`0`:** Retained in good standing past 90 days. |

---

## 3. Deidentification & Data Hygiene Protocol

Before any historical or live pilot resume enters the evaluation manifest:
1. **Automated PII & Proxy Scrubbing:** Each resume is passed through `explainable_screening_assistant.redact_resume_for_blind_screening()`, stripping candidate name headers, email addresses (`redacted@redacted.invalid`), phone numbers, personal URLs, 5/9-digit ZIP codes (Illinois HB 3773 proxy protection), and education graduation dates (ADEA age-proxy protection).
2. **Salted Cryptographic Subject IDs:** Each candidate is assigned a deterministic salted hash (`SUBJ-<12-hex-chars>`), and `deidentified_verified` is asserted in the cohort telemetry.
3. **Segregated EEO Self-ID:** Voluntary demographic data (if evaluated for EEOC four-fifths impact ratios) is stored in a separate table and joined only post-scoring.

---

## 4. Blinded Recruiter Comparison Design

To prevent anchoring bias during the pilot:
1. **Arm A — Blinded Recruiter Baseline (`blinded_recruiter_decision`):** A recruiter or hiring manager reviews the deidentified resume against the requisition *without* seeing the `ops-requisition-auditor` score or ledger and records `ADVANCE` or `DO_NOT_ADVANCE`.
2. **Arm B — Deterministic Engine Cutoffs (`>= 80.0` Strong Match & `>= 65.0` Inclusive Review):** `ops-req-pilot` scores the same deidentified resume against the locked `--jd-config` (recording `composite_score`, `predicted_probability`, and `parser_confidence`).
3. **Arm C — Human-in-the-Loop + Evidence Ledger (`human_decision_status`):** A second reviewer inspects the engine's quoted evidence ledger, partial synonym matches (`0.5x`), skills-list placements (`0.4x`), and parser confidence warnings (`< 75%`), recording the final human decision and any override rationale.
4. **Independent Ground-Truth Criterion (`manual_validated_outcome` & `manual_expert_score`):** A structured technical panel or domain expert (blinded to the automated score) scores the candidate's work sample / technical interview (`0–100`) and records the longitudinal outcomes (`interview`, `selection`, `performance_90d`, `promotion`, `attrition_90d`).

---

## 5. Statistical Analysis & Feature-Pruning Rules

Upon running `ops-req-pilot --manifest <pilot_manifest.json>`, the harness computes:
1. **Discrimination:** Exact Mann-Whitney U **ROC-AUC** with 1,000-iteration seeded bootstrap 95% confidence intervals (`seed = 20261101`).
2. **Probabilistic Calibration:** **Brier Score** ($\frac{1}{N}\sum (p_i - y_i)^2$, with 95% bootstrap CI), **Expected Calibration Error (ECE)**, and a 3-tier **Calibration Reliability Curve** (`Low [0.00, 0.50)`, `Mid [0.50, 0.80)`, `High [0.80, 1.00]`).
3. **Classification Agreement:** Accuracy (with 95% Wilson score CI), Precision, Recall, Specificity, F1 Score, and **Cohen's Kappa ($\kappa$)** for Engine (`>=80`, `>=65`), Blinded Recruiter, and Human + Engine.
4. **Component Ablation & Feature Pruning (`analyze_component_validity`):**
   - For each component (`C1`..`C5`), the harness computes point-biserial correlation $r$ with outcomes, leave-one-out $\Delta \text{AUC}$, and leave-one-out $\Delta \text{Brier}$.
   - **Pruning / Redesign Rule:** Any component or sub-rule with $r < 0.15$ or $\Delta \text{Brier} \le 0.0$ across a pilot cohort ($N \ge 30$) must be **reweighted, redesigned, or moved out of the capability score** (for example, moving `Component 5: Document & Parse Hygiene` formatting deductions into `Parser Confidence` so resume template quirks never penalize a qualified candidate's capability score).
