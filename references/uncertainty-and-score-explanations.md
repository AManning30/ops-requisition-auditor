# Transparent Uncertainty, Confidence Bands & Point-Deduction Explanations

*Reference specification for the uncertainty, three-pillar separation, side-by-side semantic vs. literal comparison, explicit missing-evidence disclosure, and complete point-deduction reconciliation in `scripts/offline_ats_auditor.py` (`AuditReport.uncertainty_and_explanations`) and the interactive workbench (`demo.html` / `docs/index.html`).*

---

## 1. Epistemic Guardrail: Why `88.3 / 100` Must Never Imply Objective Measurement

A single decimal number such as `88.3 / 100` can create a false impression of physical or psychometric precision. In reality, a requisition-alignment score is a **text-alignment heuristic** that depends on three distinct factors that must never be conflated:

1. **Heuristic Alignment Score & Plausible Band (`1_alignment_score`):** How closely the resume's wording and placement match the requisition under the active rubric weights, bounded by a **Plausible Score Interval (`[lower_bound, upper_bound]`)** that shows how the score shifts between strict literal keyword filtering (`82.9`) and full recognition of synonyms and skills-list items already present on the resume (`96.8`).
2. **Measurement & Parser Confidence (`2_measurement_confidence`):** Technical certainty (`0.0–1.0`, e.g., `99.1% HIGH_MEASUREMENT_CONFIDENCE`) that the resume's sections, work-experience date intervals, and the requisition's requirements were parsed without structural loss. A resume can have `100%` parser confidence and still be a `33.0 / 100` Weak match; conversely, a qualified candidate with a 2-column table or scanned PDF may have low parser confidence (`< 70%`) requiring manual review.
3. **Evidence Completeness (`3_evidence_completeness`):** The proportion of requisition requirements backed by direct `1.0x` recent-experience evidence (`fully_verified_1x_count`), partial evidence (`partially_verified_count`: `0.5x` synonym or `0.4x`/`0.8x` placement), or zero evidence (`unverified_missing_count`: `0.0x`).

---

## 2. Plausible Score Interval (`[lower_bound, upper_bound]`) Math

For any evaluated `(resume, requisition)` pair with active component maximums $(C_1^{\max}, C_2^{\max}, C_3^{\max}, C_4^{\max}, C_5^{\max})$:

- **Lower Bound (`score_lower_bound` — Strict Literal & Extraction Floor):**
  $$\text{Lower Bound} = \max\left(0.0,\; \min(\text{Semantic Score},\; \text{Literal-Only Score}) - \Delta_{\text{extraction}}\right)$$
  where $\Delta_{\text{extraction}} = \min(4.0,\; 0.5 \times N_{\text{inferred\_reqs}})$ accounts for heuristic plain-text JD extraction uncertainty (`0.0` when a confirmed `--jd-config` is used).
- **Point Estimate (`composite_score`):**
  $$\text{Semantic Score} = C_1 + C_2 + C_3 + C_4 + C_5$$
- **Upper Bound (`score_upper_bound` — Existing-Evidence Ceiling Without Fabrication):**
  Credits all requirements that already have non-zero evidence on the resume ($c_i > 0$) at full `1.0x` match and `1.0x` placement, while holding all `0.0x` unverified/missing requirements at `0.0`:
  $$\text{Upper Bound} = \min\left(100.0,\; (C_1^{\max} + C_2^{\max})\frac{\sum_{i : c_i > 0} w_i}{\sum_{i} w_i} + C_3 + C_4 + C_5\right)$$

### Uncertainty Band Classification
- `HIGH_SCORE_STABILITY (Narrow Interval <= 3.0 pts)`: Minimal gap between literal and upper-bound views.
- `MODERATE_INTERPRETATION_SPREAD (Synonym / Placement Sensitivity)`: Interval width between `3.1` and `15.0 pts` (e.g., baseline fixture `[82.9, 96.8]`, width `13.9 pts`).
- `WIDE_INTERPRETATION_SPREAD (High Synonym / Placement / Extraction Sensitivity)`: Interval width `> 15.0 pts`.

---

## 3. JSON Uncertainty Schema (`AuditReport.uncertainty_and_explanations`)

Every JSON scorecard emitted by `ops-req-audit --json-out` includes the `uncertainty_and_explanations` object:

```json
{
  "epistemic_notice": "The JD Alignment Score is a deterministic text-alignment heuristic, not an objective measurement of human capability...",
  "three_pillar_distinction": {
    "1_alignment_score": {
      "point_estimate": 88.3,
      "literal_only_score": 82.9,
      "plausible_score_interval": [82.9, 96.8],
      "interval_width_pts": 13.9,
      "uncertainty_band": "MODERATE_INTERPRETATION_SPREAD (Synonym / Placement Sensitivity)"
    },
    "2_measurement_confidence": {
      "combined_confidence": 1.0,
      "combined_confidence_pct": 100.0,
      "resume_parse_confidence": 1.0,
      "requisition_extraction_confidence": 1.0,
      "confidence_band": "HIGH_MEASUREMENT_CONFIDENCE (>=85%)",
      "inferred_requirement_count": 0
    },
    "3_evidence_completeness": {
      "fully_verified_1x_count": 6,
      "partially_verified_count": 5,
      "unverified_missing_count": 1,
      "total_requirements_count": 12,
      "weighted_evidence_completeness_pct": 71.7,
      "item_verification_rate_pct": 70.8,
      "completeness_band": "PARTIAL_EVIDENCE (65%-84% Weighted Contextual Verification — Clarify Gaps)"
    }
  },
  "semantic_vs_literal_comparison": {
    "semantic_score": 88.3,
    "literal_score": 82.9,
    "semantic_minus_literal_delta": 5.4,
    "component_comparison": [...],
    "divergent_requirements": [...]
  },
  "missing_evidence_items": [...],
  "partial_evidence_items": [...],
  "point_deductions_ledger": [...],
  "total_points_deducted_from_100": 11.67
}
```

---

## 4. Complete Point-Deduction Reconciliation Invariant

Every point deducted between `100.0` and `composite_score` is accounted for in `point_deductions_ledger` across all five components:
1. **`C1_C2_Requirement_Alignment`:**
   - `MISSING_REQUIREMENT_0.0X` (loses $C_1^{\max}\frac{w_i}{W} + C_2^{\max}\frac{w_i}{W}$)
   - `SYNONYM_DILUTION_0.5X` (loses $0.5 \times C_1^{\max}\frac{w_i}{W} + 0.5 \times C_2^{\max}\frac{w_i}{W}$)
   - `PLACEMENT_PENALTY_SKILLS_OR_HISTORICAL` (loses $(1 - m_i) \times C_2^{\max}\frac{w_i}{W}$)
2. **`C3_Title_Tenure_Scale`:** `TITLE_ALIGNMENT_GAP`, `TENURE_SHORTFALL`, `SCALE_OR_SENIORITY_UNVERIFIED`.
3. **`C4_Knockout_Gate`:** `KNOCKOUT_GATE_UNVERIFIED`.
4. **`C5_Document_Hygiene`:** `SCHEMA_HYGIENE_DEFECT`.

The sum of `points_deducted` across `point_deductions_ledger` plus `composite_score` always equals `100.0` (within rounding).
