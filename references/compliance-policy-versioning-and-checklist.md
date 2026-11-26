# Compliance Policy Versioning, Update Workflow & Legal-Review Checklist

*Reference specification for `scripts/compliance_policy_registry.py` (`ops-req-policy`) and custom organization policy packs (`--policy-pack`) in `scripts/explainable_screening_assistant.py` (`ops-req-screen`).*

---

## 1. Architectural Separation: Decoupling Legal Notices from the Scoring Engine

Employment, algorithmic transparency, civil-rights, and pay-transparency regulations evolve on independent statutory schedules across federal, state, and municipal jurisdictions (e.g., Colorado SB 24-205 taking effect June 30, 2026; Illinois HB 3773 taking effect January 1, 2026; NYC Local Law 144 enforced since July 5, 2023).

To ensure that statutory or counsel updates never require modifying core scoring logic (`scripts/offline_ats_auditor.py`), `ops-requisition-auditor` strictly separates:

1. **The Deterministic Scoring Engine (`offline_ats_auditor.py`):** Computes pure 100-point math ($C_1 + C_2 + C_3 + C_4 + C_5$) from resume text and confirmed requirement configurations. It contains **zero** jurisdiction-specific legal notices or statutory citations.
2. **The Versioned Compliance Policy Registry (`compliance_policy_registry.py` / `ops-req-policy`):** Stores dated, cryptographically fingerprinted (`policy_pack_sha256`) policy modules containing effective dates, geography metadata, official statutory/agency URLs, legal counsel review status, engine control mappings, and candidate disclosure clauses.
3. **Organization-Selectable Policy Packs (`--policy-pack <path>`):** Allows any enterprise, agency, or legal department to pass its own counsel-reviewed JSON policy pack to `ops-req-screen --policy-pack org_policy_pack.json --location "Denver, CO" --require-counsel-approved`.

---

## 2. Versioned Policy Pack Schema (`policy-pack-schema-1.0`)

Every policy pack JSON file validated by `validate_and_build_policy_pack()` adheres to the following schema:

### 2.1 Top-Level Policy Pack Envelope

| Field | Type | Required | Description |
| :--- | :--- | :-: | :--- |
| `schema_version` | `string` | Yes | Schema identifier (`"policy-pack-schema-1.0"`). |
| `policy_pack_id` | `string` | Yes | Organization-assigned pack identifier (e.g., `"us-employment-screening-pack"`). |
| `policy_pack_version` | `string` | Yes | Dated semantic version (e.g., `"2026.11.0"`). |
| `published_date` | `YYYY-MM-DD` | Yes | ISO-8601 release date of the policy pack. |
| `organization` | `string` | Yes | Issuing organization or legal entity name. |
| `reviewed_by` | `string` | Yes | Role or counsel group responsible for reviewing the pack. |
| `default_disclaimer` | `string` | Yes | Top-level legal and human-in-the-loop disclaimer rendered on batch reports and candidate notices. |
| `right_to_clarify_template` | `string` | Yes | Candidate instructions rendered in Section 5 of the Candidate Factor & Gap Disclosure Notice when unverified requirements exist. |
| `rules` | `array<Rule>` | Yes | Non-empty list of dated `JurisdictionPolicyRule` objects. |

### 2.2 Per-Jurisdiction Rule Schema (`JurisdictionPolicyRule`)

| Field | Type | Required | Validation Rule & Description |
| :--- | :--- | :-: | :--- |
| `rule_id` | `string` | Yes | Unique immutable rule identifier (e.g., `"POL-CO-SB24-205"`, `"POL-NYC-LL144"`). |
| `jurisdiction` | `string` | Yes | Human-readable jurisdiction name (e.g., `"Colorado, USA"`). |
| `title` | `string` | Yes | Short statutory or regulatory title. |
| `citation` | `string` | Yes | Formal legal citation (e.g., `"C.R.S. § 6-1-1701 et seq. (SB 24-205)"`). |
| `category` | `enum` | Yes | One of: `AI_DECISION_TRANSPARENCY`, `AUTOMATED_EMPLOYMENT_DECISION_TOOL`, `PROXY_AND_ZIP_REDACTION`, `ADVERSE_IMPACT_FOUR_FIFTHS`, `AGE_PROXY_REDACTION`, `PAY_TRANSPARENCY`. |
| `effective_date` | `YYYY-MM-DD` | Yes | Date the statute/rule becomes active. Rules with `effective_date > reference_date` are held in `PENDING_EFFECTIVE_DATE` status and excluded from active enforcement until their effective date. |
| `last_amended_date` | `YYYY-MM-DD` | Yes | Date of the most recent statutory amendment or regulatory guidance update. |
| `last_reviewed_date` | `YYYY-MM-DD` | Yes | Date legal/compliance counsel last reviewed the rule module. |
| `next_review_due_date` | `YYYY-MM-DD` | Yes | Mandatory re-review deadline (`next_review_due_date >= last_reviewed_date`). Past this date, `is_rule_stale_on()` flags the rule as `STALE_REVIEW_OVERDUE`. |
| `sunset_date` | `YYYY-MM-DD` | No | Optional expiration date (`""` if permanent). If `reference_date > sunset_date`, the rule is inactive (`SUNSETTED`). |
| `legal_review_status` | `enum` | Yes | One of: `COUNSEL_APPROVED`, `PENDING_LEGAL_REVIEW`, `DRAFT_UNREVIEWED`, `SUPERSEDED`. |
| `official_source_url` | `string` | Yes | Official `.gov` / `.nyc` / legislative URL (`https://...`). |
| `geography` | `GeographyScope` | Yes | Structured geographic scope (`country`, `state_or_province`, `locality`, `applies_to_remote`, `location_patterns`). |
| `summary` | `string` | Yes | Concise operational summary of what the statute requires. |
| `engine_controls` | `array<string>` | Yes | Non-empty list of engine/governance controls satisfying the rule (e.g., `"blind_mode_zip_redaction"`, `"mandatory_pending_human_review"`). |
| `notice_clause` | `string` | Yes | Plain-language disclosure clause injected into Section 6 of the Candidate Factor & Gap Disclosure Notice when the rule applies. |

---

## 3. Built-In Baseline Policy Modules (`2026.11.0`)

The default policy pack (`examples/sample_policy_pack_2026_11.json`) ships with six dated modules:

| Rule ID | Jurisdiction & Citation | Effective Date | Next Review Due | Geography Scope | Primary Engine & Governance Controls |
| :--- | :--- | :-: | :-: | :--- | :--- |
| `POL-CO-SB24-205` | **Colorado AI Act** (`C.R.S. § 6-1-1701 et seq.`, SB 24-205, as amended by HB 25B-1004) | `2026-06-30` | `2027-04-15` | `US` / `CO` (`applies_to_remote: true`) | `mandatory_pending_human_review`, `candidate_factor_and_gap_notice`, `right_to_clarify_unverified_requirements`, `blind_mode_pii_redaction` |
| `POL-NYC-LL144` | **NYC Local Law 144 (AEDT)** (`N.Y.C. Admin. Code §§ 20-870 to 20-874`; `6 RCNY § 5-300`) | `2023-07-05` | `2027-04-15` | `US` / `NY` / `New York City` (`applies_to_remote: false`) | `mandatory_pending_human_review`, `anti_automation_cli_and_api_block`, `four_fifths_selection_rate_table`, `candidate_job_qualification_factor_notice` |
| `POL-IL-HB3773` | **Illinois Human Rights Act AI & ZIP-Code Amendment** (`775 ILCS 5/2-102(L)`, HB 3773) | `2026-01-01` | `2027-04-15` | `US` / `IL` (`applies_to_remote: true`) | `blind_mode_zip_redaction`, `blind_mode_pii_redaction`, `segregated_demographics_post_scoring_only`, `candidate_factor_and_gap_notice` |
| `POL-FED-EEOC-UGESP` | **EEOC Uniform Guidelines Four-Fifths (`80%`) Rule** (`29 C.F.R. Part 1607`, § 1607.4(D)) | `1978-08-25` | `2027-04-15` | `US` / `ALL` (`applies_to_remote: true`) | `four_fifths_selection_rate_table`, `small_sample_n_lt_5_caution_flag`, `segregated_demographics_post_scoring_only`, `component_validity_ablation_harness` |
| `POL-FED-ADEA-GRAD-YEAR` | **ADEA Age-Proxy Redaction** (`29 U.S.C. § 621 et seq.`) | `1967-12-15` | `2027-04-15` | `US` / `ALL` (`applies_to_remote: true`) | `blind_mode_education_grad_date_redaction`, `preserve_work_experience_dates_for_tenure_only` |
| `POL-US-PAY-TRANSPARENCY` | **Multi-State Pay-Range & Gate Transparency** (CO EPEWA, CA SB 1162, NY LL 2022/2023, WA RCW 49.58.110, IL Equal Pay Act) | `2025-01-01` | `2027-04-15` | `US` / `ALL` (`applies_to_remote: true`) | `rqs_comp5_pay_transparency_check`, `rqs_comp1_degree_equivalency_check`, `zero_placeholder_fabrication_guardrail` |

---

## 4. Four-Stage Policy Update Workflow

When a statute is enacted, amended, delayed, or interpreted by new agency rulemaking, compliance and engineering teams follow this deterministic 4-stage workflow:

```
┌────────────────────────────────────────────────────────────────────────────┐
│ Stage 1: Legislative & Regulatory Monitoring                               │
│  - Monitor official state/municipal/federal sources (.gov) and run         │
│    `ops-req-policy --demo` in CI to detect rules approaching               │
│    `next_review_due_date`.                                                 │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ Stage 2: Draft Policy Module in Versioned Pack JSON                        │
│  - Export current baseline:                                                │
│    `ops-req-policy --export-default-pack policy_pack_draft.json`           │
│  - Bump `policy_pack_version` (e.g., `2026.11.0` -> `2027.01.0`).          │
│  - Add or update the rule with `legal_review_status: "PENDING_LEGAL_REVIEW"`,│
│    `effective_date`, `official_source_url`, `geography`, and `notice_clause`.│
└────────────────────────────────────┬───────────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ Stage 3: Employment & AI Counsel Review (Checklist Sign-Off)               │
│  - Counsel completes the 10-item Legal-Review Checklist (Section 5 below). │
│  - Upon approval, counsel updates `legal_review_status` to                 │
│    `"COUNSEL_APPROVED"`, sets `last_reviewed_date`, and sets               │
│    `next_review_due_date` (typically +6 or +12 months).                    │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ Stage 4: Pre-Effective-Date Staging & Fail-Closed Production Cutover       │
│  - Commit the signed-off JSON pack to version control.                     │
│  - Because `resolve_applicable_rules()` gates on `effective_date`, future- │
│    dated statutes can be merged ahead of time and automatically activate   │
│    on their statutory effective date.                                      │
│  - Production screening runs with `--require-counsel-approved` so stale or │
│    unapproved drafts fail closed (exit code 2).                            │
└────────────────────────────────────────────────────────────────────────────┘
```

### CLI Commands for Policy Governance

```bash
# 1. Inspect the active policy pack, effective-date status, and SHA-256 fingerprint
ops-req-policy --demo --require-counsel-approved

# 2. Filter applicable jurisdiction rules for a specific requisition location and date
ops-req-policy --policy-pack examples/sample_policy_pack_2026_11.json \
  --location "Denver, CO" --reference-date 2026-11-01 --require-counsel-approved

# 3. Export the default pack as a starting template for your organization's legal team
ops-req-policy --export-default-pack my_org_policy_pack.json

# 4. Run batch candidate screening using your organization's custom policy pack and location
ops-req-screen --demo --policy-pack examples/sample_policy_pack_2026_11.json \
  --location "New York, NY" --require-counsel-approved
```

---

## 5. Enterprise Legal-Review Checklist (Before Promoting a Policy Pack to `COUNSEL_APPROVED`)

Use this checklist whenever creating or updating an organization policy pack (`--policy-pack`) before setting `legal_review_status` to `"COUNSEL_APPROVED"`:

- [ ] **1. Official Statutory Source Verification (`official_source_url`):** Have you verified the exact statutory text, effective date, and any subsequent postponement bills (e.g., Colorado HB 25B-1004 moving SB 24-205 to June 30, 2026) directly against the official `.gov` legislature or enforcement agency website?
- [ ] **2. Effective-Date & Sunset Accuracy (`effective_date`, `sunset_date`):** Does `effective_date` match the statutory enforcement date in `YYYY-MM-DD` format, and if an older rule is being replaced, is the prior rule marked `SUPERSEDED` or assigned a `sunset_date`?
- [ ] **3. Geographic & Remote-Work Scope (`geography`):** Have you confirmed with employment counsel whether the statute applies only to physical roles within the city/state (`applies_to_remote: false`, e.g., NYC LL144 office-location rules) or also to remote roles open to residents of that jurisdiction (`applies_to_remote: true`, e.g., Colorado SB 24-205 and pay-transparency laws)?
- [ ] **4. Human-in-the-Loop & AEDT Exemption Posture:** Have you verified that `PENDING_HUMAN_REVIEW` is enforced by `ops-req-governance`, that `--auto-reject` / `--auto-advance` flags are blocked at the CLI/API boundary, and whether your organization's use of the tool triggers independent bias-audit publication requirements under NYC Local Law 144?
- [ ] **5. Blind-Mode Redaction Verification (`POL-IL-HB3773`, `POL-FED-ADEA-GRAD-YEAR`):** Have you confirmed that `blind_mode: true` is active in your screening workflow so candidate name headers, emails, phone numbers, URLs, 5/9-digit ZIP codes (Illinois HB 3773), and education graduation years (ADEA) are stripped prior to scoring with `0.00 pts` score drift?
- [ ] **6. Candidate Factor & Gap Notice Language (`notice_clause`, `right_to_clarify_template`):** Has counsel reviewed the plain-language `notice_clause` and `right_to_clarify_template` to ensure candidates receive clear disclosure of the 5 scoring components, quoted evidence, unverified requirements, and the channel/deadline to submit a correction or clarification?
- [ ] **7. Segregated Self-ID Demographics & Four-Fifths Monitoring (`POL-FED-EEOC-UGESP`):** Have you verified that voluntary self-identification records (`--demographics-json`) are stored separately from resume files, never enter the scoring function, and include the small-sample caution flag (`n < 5`)?
- [ ] **8. Requisition Quality & Pay-Transparency Placeholders (`POL-US-PAY-TRANSPARENCY`):** Have you confirmed that requisitions published in pay-transparency jurisdictions include an employer-approved good-faith compensation range and benefits summary, and that no unapproved `[INSERT: ...]` placeholders remain in live postings?
- [ ] **9. Review Cadence & Staleness Guardrail (`last_reviewed_date`, `next_review_due_date`):** Is `next_review_due_date` set to no more than 180–365 days after `last_reviewed_date`, and is `--require-counsel-approved` enabled in CI/production so overdue packs fail closed?
- [ ] **10. Cryptographic Audit Trail (`policy_pack_sha256`):** Is the signed-off JSON file committed to version control, and does `ScreeningBatchAudit` record `policy_pack_id`, `policy_pack_version`, `policy_pack_sha256`, and `active_policy_rules` alongside the hash-chained reviewer decisions?
