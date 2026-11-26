# Governance Charter, Ethics Review & Known Failure Modes (`ops-requisition-auditor`)

**Charter Version:** `gov-charter-1.6.0`  
**Effective Date:** `2026-11-26`  
**Scope:** Candidate JD Alignment (`ops-req-audit`), Employer Requisition Quality (`ops-req-jd-audit`), Explainable Batch Screening (`ops-req-screen`), Internal Mobility (`ops-req-mobility`), and all supporting governance, privacy, fairness, and integration modules.

---

## 1. Mission & Non-Negotiable Anti-Automation Boundaries

`ops-requisition-auditor` exists to make candidate-to-requisition matching and job-description quality auditing **transparent, deterministic, reproducible, and human-governed**.

To protect candidates, employees, and employers from opaque algorithmic screening, every release of `ops-requisition-auditor` is bound by five immutable principles:

1. **Never Auto-Rejects Candidates:** No module may emit an automated rejection decision. All screening records initialize to `PENDING_HUMAN_REVIEW`, and `ops-req-governance` blocks prohibited disposition statuses (`AUTO_REJECT`, `SYSTEM_REJECT`, `KNOCKOUT_AUTO_REJECT`).
2. **Zero Fabrication in Candidate & Requisition Rewrites:** Suggested resume bullets and requisition edits never invent metrics, tools, titles, team sizes, or pay ranges. Missing facts are emitted strictly as `[INSERT: ...]` confirmation prompts.
3. **Separation of Duties (SoD):** The individual who authors or runs a scoring rubric or batch screen cannot approve their own configuration or override a candidate below the advisory threshold without an independent secondary reviewer (`approver_id != reviewer_id`).
4. **Blind-Mode Score Parity & Segregated Demographics:** Removing candidate PII, ZIP codes, and graduation years must produce **`0.0` points of score drift**. Voluntary EEO self-identification data is stored in a segregated schema (`--demographics-json`) that is never accessible to the scoring function.
5. **100% Deterministic & Offline-Verifiable:** Every score is computed using explicit, inspectable arithmetic on the Python 3.10+ standard library with zero external LLM calls or hidden weights.

---

## 2. Independent Ethics, I-O Psychology & HR Review

Before any major or minor rubric release (`rqs-*`, `jd_config`, or `wfm-reskill-cat-*`), the benchmark corpus and scoring math undergo three-pillar review:

| Review ID | Discipline | Scope Reviewed | Verdict |
| :--- | :--- | :--- | :--- |
| **`ETHICS-REV-2026-11-A`** | **Industrial-Organizational (I-O) Psychology & Construct Validity** | 100-Point Candidate JD Alignment Rubric, 3-Pillar Uncertainty Model (`score`, `confidence`, `completeness`), and Pilot Evidence Harness (`ops-req-pilot`) | **`APPROVED_FOR_DECISION_SUPPORT`** |
| **`HR-COMP-REV-2026-11-B`** | **Employment Law, Equal Opportunity & AI Governance** | Blind-Mode Redaction (`ops-req-screen`), 6-Dimension Fairness Suite (`ops-req-fairness`), Policy Registry (`ops-req-policy`), and Governance Middleware (`ops-req-governance`) | **`APPROVED_WITH_HUMAN_IN_THE_LOOP_MANDATE`** |
| **`WFM-PRAC-REV-2026-11-C`** | **Contact-Center Workforce Management (WFM) & Operations Domain Calibration** | Employer RQS Rubric (`rqs-0.2` across 113 live postings) & WFM/BI 30/60/90-Day Reskilling Catalog (`wfm-reskill-cat-2.0`) | **`APPROVED_DOMAIN_CALIBRATED`** |

---

## 3. Known Failure Modes & Deterministic Mitigations

No heuristic parser or rubric is infallible. We document all 10 known failure modes openly and test their mitigations in `ops-req-trust-bench`:

1. **`FM-01` — Image-Only Scanned PDFs:** Mitigated via multi-tier `pdftotext` / `tesseract` OCR fallback (`extract_resume_with_ocr_fallback`) and explicit `parse_confidence` reduction (`0.72`).
2. **`FM-02` — Two-Column Side-by-Side PDF / Table Interleaving:** Mitigated via `normalize_multicolumn_resume_text()` gutter splitting and table-cell pipe normalization.
3. **`FM-03` — Domain Synonym Ambiguity (`0.5x` vs `1.0x`):** Mitigated via side-by-side Semantic vs. Literal tables, plausible score bands (`[82.9, 96.8]`), and candidate confirmation prompts.
4. **`FM-04` — Competing Vendor Products vs. Transferable Skills:** Competing tools (Tableau vs. Power BI; Verint vs. NICE IEX) earn `0.0x` literal product credit while surfacing transferable domain skills (`evaluate_competing_vs_transferable_skills`).
5. **`FM-05` — Concurrent Consulting / Dual-Role Overlap Inflation:** Mitigated by merging overlapping `(start_month, end_month)` intervals before computing net tenure and explicitly recognizing career breaks.
6. **`FM-06` — Unconfirmed Pasted JD Extraction Noise:** Mitigated by lowering Pillar 2 `measurement_confidence_score` (`~78%`) until a human confirms `jd_config.json`.
7. **`FM-07` — Four-Fifths (80%) Rule Instability in Small Samples (`n < 30`):** Mitigated via `CRITICAL_N_LT_5` / `MODERATE_N_LT_30` warnings, Fisher's Exact Two-Tailed $p$-values, and Wilson Score 95% confidence intervals (`ops-req-fairness`).
8. **`FM-08` — Multilingual Resume & Requisition Parsing (EN / ES / FR):** Mitigated via multilingual section headers, date months, and currency regexes (`USD`, `EUR`, `GBP`, `CAD`).
9. **`FM-09` — Unconfirmed 30-Day Mobility Projections:** Mitigated by separating `confirmed_30d_score` (only employee-confirmed skills) from `projected_30d_score` (`ops-req-mobility`).
10. **`FM-10` — Automation Bias & Over-Reliance on Numeric Scores:** Mitigated via plausible score bands, mandatory human disposition sign-off, Separation of Duties, and reviewer override bias audits.

---

## 4. Public Synthetic Benchmark Corpus & Reproducibility Discipline

- All public benchmarks in `examples/public_synthetic_benchmark_corpus.json` are **100% synthetic** (`verify_zero_real_pii_in_corpus()`). Real candidate resumes must never be committed to the repository.
- Every scoring change must increment the semantic version in `pyproject.toml`, update `CHANGELOG.md`, re-verify `ops-req-workbench` (Python-to-HTML parity), and pass `ops-req-trust-bench --demo`.
