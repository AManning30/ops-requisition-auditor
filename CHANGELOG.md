# Changelog

All notable changes to `ops-requisition-auditor` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html) alongside strict **Rubric & Policy Version Discipline**:
- Any change to employer requisition scoring increments `RUBRIC_VERSION` (`rqs-0.2`).
- Any change to regulatory compliance checklists increments the policy pack (`policy-pack-2026.11.1`).
- Any change to WFM/BI reskilling milestones increments `RESKILLING_CATALOG_VERSION` (`wfm-reskill-cat-2.0`).
- Any change to shared Python/HTML benchmark fixtures increments `SHARED_SCORING_VECTOR_VERSION` (`shared-vectors-1.0`).

---

## [1.6.0] - 2026-11-26

### Added
- **Workstreams 1–13 Complete Production Suite & Enterprise Hardening (`v1.6.0`):**
  - SQLite WAL append-only cryptographic audit ledger (`append_audit_chain_to_sqlite`, `load_and_verify_sqlite_audit_chain`) with `BEFORE UPDATE` / `BEFORE DELETE` immutability triggers and atomic temp-file JSON replacement (`atomic_write_json`).
  - Secondary approver Separation-of-Duties (`secondary_approver_id != reviewer`) enforcement on candidate score overrides in `governance_middleware.py`.
  - Webhook live timestamp replay enforcement, replay-nonce (`event_id:signature`) deduplication cache, 2 MB payload DoS protection cap, persistent `--vault-json` erasure routing, and `--serve-port` HTTP server in `integration_adapters_and_webhooks.py`.
  - 15 MB uncompressed stream size limits (`MAX_UNCOMPRESSED_DOC_BYTES`) on `.docx` XML archive members and `.pdf` `FlateDecode` streams to block ZIP/XML/Zlib decompression bombs, plus Poppler `pdftotext` CLI fallback in `offline_ats_auditor.py`.
  - Optional construct-validity decoupling flag (`decouple_formatting_from_capability_score`) in `org_customization_registry.py` so table/layout formatting lowers Pillar 2 `measurement_confidence` without deducting capability points from Component 5.

---

## [1.5.0] - 2026-11-16

### Added
- **Workstream 1 — Real-World Pilot Validation Harness (`ops-req-pilot`, `scripts/pilot_evidence_harness.py`):**
  - 6-case end-to-end validation harness pairing requisitions and resumes with Cohen's $\kappa$, Pearson $r$, Spearman $\rho$, and blind-mode score parity checks, plus `MODEL_CARD.md` and `references/pilot-protocol.md`.
- **Workstream 2 — Human-Approval & Anti-Automation Governance (`ops-req-governance`, `scripts/governance_middleware.py`):**
  - Enforces human-in-the-loop review (`PENDING_HUMAN_REVIEW`), blocks automated rejection statuses, enforces Separation of Duties (`approver_id != reviewer_id`), validates role permissions, and seals decisions into a SHA-256 hash-chained audit ledger.
- **Workstream 3 — Extraction Confidence & Multilingual Ambiguity Ledger (`scripts/job_posting_collector.py`):**
  - Per-field extraction confidence (`[0.0, 1.0]`), ambiguity flags, raw-vs-normalized field ledger, and 10-case multilingual/edge-case corpus (`examples/sample_ambiguity_multilingual_corpus.json`).
- **Workstream 4 — Compliance Policy Versioning (`ops-req-policy`, `scripts/compliance_policy_registry.py`):**
  - Versioned jurisdictional policy packs (`NYC_LL144`, `CO_SB24_205`, `IL_HB3773`, `EEOC_UGESP_4_5THS`, `EU_AI_ACT_ANNEX_III`) with effective dates, rule diffs, and machine-readable compliance checklists.
- **Workstream 5 — Privacy Architecture & Data Minimization (`ops-req-privacy`, `scripts/privacy_data_minimization.py`):**
  - Explicit consent verification for batch screening and internal mobility, 3-vault data segregation (PII vs. Scores vs. Voluntary Self-ID), configurable retention & cryptographic erasure tombstones, ephemeral default mode, authenticated encryption for optional exports, and PII-redacted structured logging.
- **Workstream 6 — 6-Dimension Fairness & Adverse-Impact Suite (`ops-req-fairness`, `scripts/fairness_adverse_impact_auditor.py`):**
  - Evaluates race/ethnicity, sex/gender, age-proxy bands, disability status, primary language, and career-gap status with small-sample warnings (`n < 5`, `n < 30`), Fisher's Exact Two-Tailed $p$-values, Agresti-Coull 95% CIs, parser/rubric error parity, and reviewer override confusion matrices.
- **Workstream 7 — Multi-Format & Multilingual Resume Parser Benchmark (`ops-req-parser-bench`, `scripts/parser_benchmark_suite.py`):**
  - 9-case parser corpus (`.txt`, `.docx`, `.pdf`, tables, two-column layouts, Spanish/French/English, career breaks, and scanned OCR fallback) plus 25-trial deterministic property-based fuzz testing and competing-product vs. transferable-skill separation.
- **Workstream 8 — Organization-Native Customization & Approval Registry (`ops-req-org-config`, `scripts/org_customization_registry.py`):**
  - Organization-specific leveling guides, gate equivalency policies, custom component weights (`C1..C5 = 100.0`), dual-signoff SoD approval workflow, SHA-256 hash-chained configuration audit history, and Tab 7 in the interactive workbench.
- **Workstream 9 — Transparent Uncertainty & Complete Deduction Explanations (`scripts/offline_ats_auditor.py`):**
  - Three-pillar distinction (Heuristic Alignment Score vs. Measurement Confidence vs. Evidence Completeness), plausible score confidence interval (`[82.9, 96.8]`), side-by-side Semantic vs. Literal comparison, explicit missing/partial evidence inventory, and 100% point-deduction reconciliation (`100.0 -> 88.33`).
- **Workstream 10 — ATS/HRIS Integrations & Signed Webhooks (`ops-req-integrate`, `scripts/integration_adapters_and_webhooks.py`):**
  - Versioned ATS-neutral JSON contract (`ats-neutral-contract-1.0`), OpenAPI 3.1 + JSON Schema (`references/openapi_and_ats_neutral_schema.json`), bidirectional adapters for Greenhouse, Lever, Ashby, Workday HRIS, and HR-Open 4.x, and HMAC-SHA256 signed webhook & batch-job modes preserving 100% of human-review and uncertainty fields.
- **Workstream 11 — Strengthened 30/60/90-Day Mobility Confirmation & Outcomes (`ops-req-mobility`, `scripts/internal_mobility_matcher.py`):**
  - Employee skill confirmation workflow (`confirmed_30d_score` vs. `projected_30d_score`), governed reskilling catalog (`wfm-reskill-cat-2.0`) with learning resources and prerequisite/credential chain alerts, mentor & manager approval tracking (blocking manager sign-off when `share_with_manager=False`), and 90-day transition outcome measurement.
- **Workstream 12 — Workbench Parity, Shared Test Vectors & WCAG Accessibility (`ops-req-workbench`, `scripts/workbench_parity_and_a11y.py`):**
  - Canonical shared test vector contract (`examples/shared_scoring_test_vectors.json`), automated Python-to-HTML parity verification, WAI-ARIA 1.2 tablist/tab/tabpanel semantics, roving-tabindex keyboard arrow navigation, skip-to-main-content link, explicit form labels and validation alerts, responsive mobile/tablet breakpoints, and prominent `SYNTHETIC DEMO MODE (ZERO REAL PII)` banner.
- **Workstream 13 — Public Benchmark Suite & Community Trust Governance (`ops-req-trust-bench`, `scripts/public_trust_and_benchmarks.py`):**
  - 6-domain reproducible public benchmark suite, zero-PII verified synthetic benchmark corpus (`examples/public_synthetic_benchmark_corpus.json`), 10-item Known Failure Modes registry, independent I-O psychology / HR compliance / WFM domain review records, `GOVERNANCE_CHARTER.md`, and `SECURITY.md`.

### Fixed
- Resolved relative file resolution in `offline_ats_auditor.py` and `job_posting_collector.py` via `_resolve_input_path()` so relative paths like `examples/sample_resume.txt` resolve seamlessly from any working directory when installed via pip.
