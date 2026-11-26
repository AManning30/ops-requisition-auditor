# Pilot & Empirical Evidence Harness Specification (`ops-req-pilot`)

This document specifies the dual-rubric pilot evaluation loop, deterministic parser confidence model, and statistical outcome-validation formulas implemented in `scripts/pilot_evidence_harness.py` (`ops-req-pilot`).

> **Why this harness exists:** Before an enterprise recruiting team, Industrial-Organizational (I-O) psychology group, or independent bias auditor deploys a scoring rubric in production, they need an empirical validation harness that runs each `(requisition, resume)` pair through both sides of the rubric, quantifies parser confidence, logs human reviewer decisions, and compares engine scores against manually validated interview or hiring outcomes.

---

## 1. Four-Stage Pilot Evaluation Pipeline

For every `(requisition, resume)` case in a pilot cohort:

1. **Employer Requisition Quality Audit (Module 1 RQS):**
   - Evaluates the raw requisition text via `requisition_quality_auditor.audit_text()` to record its 100-point **Requisition Quality Score (`rqs_score`)**, band, and HBS "Hidden Workers" gate count (`rqs_hidden_worker_flags`).
2. **Candidate & Screening JD Alignment Audit (Raw + Blind Parity):**
   - Evaluates the resume against the requisition (using either a recruiter-confirmed `jd_config`, heuristically extracted requirements from `jd_text`, or **both** to measure heuristic-vs-confirmed drift).
   - Runs `explainable_screening_assistant.redact_resume_for_blind_screening()` and re-scores the redacted text to record `blind_score` and `blind_parity_delta = blind_score - composite_score`.
3. **Deterministic Parser Confidence Diagnostics (`0.0–1.0`):**
   - Computes `resume_confidence`, `requisition_confidence`, and `combined_confidence = 0.55 * resume_confidence + 0.45 * requisition_confidence`.
   - Any case with `combined_confidence < 0.75` sets `requires_manual_parse_check = true`, routing the document to a human reviewer before relying on the automated score.
4. **Empirical Comparison vs. Human Decisions & Manually Validated Outcomes:**
   - Compares the engine's advisory cutoffs (`>= 80.0` Strong Match and `>= 65.0` Inclusive Review, with required knockout gates verified) and the human reviewer's screening decision (`ADVANCE` / `DO_NOT_ADVANCE`) against the manually validated ground-truth outcome (`manual_validated_outcome`) and optional human domain-expert 100-point rating (`manual_expert_score`).

---

## 2. Deterministic Parser Confidence Model

### 2.1 Resume Parse Confidence (`resume_confidence` $\in [0.05, 1.00]$)
Starts at `1.00` and applies transparent deductions when structural signals are missing or ambiguous:

| Signal Checked | Deduction | Rationale |
| :--- | :-: | :--- |
| Missing `Work Experience` section heading | `-0.30` | Work bullets cannot be reliably separated from summary or skills text for Component 2 recency multipliers. |
| Zero valid `MM/YYYY` date ranges (`valid_dates_found == 0`) | `-0.25` | Recency (`<= 5 yrs`) and interval-merged net tenure (`Component 3`) cannot be computed from dates. |
| Multi-column or pipe-table layout (`has_table_or_columns`) | `-0.15` | Multi-column PDF/Word extraction can interleave words across columns. |
| Low structured character attribution ratio (`< 60%`) | `-0.15` | Large portion of resume text fell outside recognized section headings. |
| Non-standard section headers (`non_standard_headers`) | `-0.12` | Creative headings required fallback bucket assignment. |
| Irregular or year-only date ranges (`irregular_date_Penalty`) | `-0.12` | Month precision missing for exact tenure calculation. |
| Missing `Technical & Core Skills` section heading | `-0.10` | Skills-list items cannot be isolated from narrative text. |
| Missing `Education` section heading | `-0.08` | Degree gates must be scanned across full text rather than an isolated Education block. |
| Unexpanded obscure acronyms (`obscure_acronyms_unexpanded`) | `-0.05` | Ambiguous uppercase tokens detected outside standard domain allowlists. |

### 2.2 Requisition Parse Confidence (`requisition_confidence` $\in [0.05, 1.00]$)
- **`confirmed_jd_config` (`1.00`):** Recruiter-confirmed requirement JSON supplied (`100%` structural fidelity).
- **`both_compared` (`0.98`):** Both confirmed `jd_config` and raw `jd_text` supplied; the harness computes `heuristic_vs_confirmed_score_drift = |Score_heuristic - Score_confirmed|`.
- **`heuristic_raw_text` (starts at `0.85`):**
  - `-0.12` if no explicit `Required` vs. `Preferred` section split is found (`C1` untiered list).
  - `-0.18` if fewer than `4` requirements are extracted from plain text (sparse/narrative JD).
  - `-0.08` if more than `18` requirements are extracted from plain text.
  - `-0.10` if target job title cannot be inferred.

### 2.3 Combined Confidence Bands
$$\text{Combined Confidence} = 0.55 \times \text{Resume Confidence} + 0.45 \times \text{Requisition Confidence}$$
- **HIGH (`>= 0.85`):** Cleanly structured resume and requisition.
- **MODERATE (`0.75 – 0.84`):** Minor structural ambiguity (for example untiered JD or year-only dates); inspect warnings.
- **LOW (`< 0.75 — Manual Parse Check`):** Sets `requires_manual_parse_check = true`.

---

## 3. Statistical Outcome-Validation Formulas

Given $N$ pilot cases with engine binary predictions $\hat{y}_i \in \{0, 1\}$, human reviewer decisions $h_i \in \{0, 1\}$, manually validated outcomes $y_i \in \{0, 1\}$, engine composite scores $s_i \in [0, 100]$, and independent human domain-expert ratings $e_i \in [0, 100]$:

1. **Confusion Matrix & Classification Metrics (at $\tau = 80.0$ and $\tau = 65.0$):**
   - $\text{Accuracy} = \frac{TP + TN}{N}$, $\quad \text{Precision} = \frac{TP}{TP + FP}$, $\quad \text{Recall} = \frac{TP}{TP + FN}$, $\quad \text{Specificity} = \frac{TN}{TN + FP}$
   - $F_1 = \frac{2 \cdot \text{Precision} \cdot \text{Recall}}{\text{Precision} + \text{Recall}}$
2. **Cohen's Kappa ($\kappa$, Chance-Corrected Agreement):**
   $$\kappa = \frac{p_o - p_e}{1 - p_e}, \quad p_o = \frac{TP + TN}{N}, \quad p_e = \left(\frac{TP+FP}{N}\cdot\frac{TP+FN}{N}\right) + \left(\frac{TN+FN}{N}\cdot\frac{TN+FP}{N}\right)$$
3. **Continuous Score Calibration vs. Domain-Expert Ratings:**
   - $\text{MAE} = \frac{1}{N}\sum_{i=1}^N |s_i - e_i|$, $\quad \text{RMSE} = \sqrt{\frac{1}{N}\sum_{i=1}^N (s_i - e_i)^2}$
   - **Pearson $r$:** linear correlation between engine scores $s$ and manual expert scores $e$.
   - **Spearman Rank $\rho$:** rank-order correlation between engine rankings and manual expert rankings.

---

## 4. CLI Usage (`ops-req-pilot`)

```bash
# 1. Run the built-in 6-case WFM & Analytics pilot validation cohort
python scripts/pilot_evidence_harness.py --demo \
  --output pilot_report.md --json-out pilot_evidence.json --csv-out pilot_ledger.csv

# 2. Run a custom pilot manifest JSON file
python scripts/pilot_evidence_harness.py --manifest examples/sample_pilot_manifest.json \
  --output pilot_report.md --csv-out pilot_ledger.csv

# 3. Evaluate a single (requisition, resume) pair with a known manual outcome
python scripts/pilot_evidence_harness.py --resume examples/sample_resume.txt \
  --jd-config examples/sample_ats_config.json --jd-file examples/sample_requisition_improved.txt \
  --human-decision ADVANCE --manual-outcome ADVANCE --manual-score 87.0
```
