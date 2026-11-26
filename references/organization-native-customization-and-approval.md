# Organization-Native Customization, Leveling Guides, Approval Workflow & Config Audit History

*Reference specification for `scripts/org_customization_registry.py` (`ops-req-org-config`) and `--org-profile` support across `ops-req-audit`, `ops-req-jd-audit`, `ops-req-screen`, and the interactive workbench (`demo.html` / `docs/index.html`).*

---

## 1. Why Organization-Native Customization Matters

Generic industry benchmarks (such as a 4-year experience ceiling for `Level II` or a fixed `45 / 20 / 15 / 10 / 10` component weight split) are a transparent starting point, but individual employers have distinct job architectures, skills-first degree-equivalency policies, and validated competency weights. Hardcoding generic benchmarks without a governed customization mechanism forces employers either to accept inappropriate defaults or to make ad-hoc, unlogged edits.

`ops-req-org-config` provides a versioned, dual-approved, and cryptographically hash-chained **Organization Customization Profile (`org-customization-profile-1.0`)** that governs:

1. **Organization-Specific Leveling Guides (`leveling_guide`):** Maps internal level codes (`L1`..`L6`, `M1`..`M2`, `D1`, `VP`) and canonical level keys (`entry` through `executive`) to employer-approved experience ceilings (`max_benchmark_years`), minimum expected tenure, people/budget scope flags, and default degree policies.
2. **Configurable Required Gate Types & Equivalency Paths (`gate_and_equivalency_policy`):** Configures which gate types are permitted as hard knockout gates (`degree`, `certification`, `license`, `security_clearance`, `work_authorization`), defines experience-for-degree equivalency ratios (`bachelor_equivalency_years`, `master_equivalency_years`), lists accepted alternative credentials (e.g., `Six Sigma Black Belt`, `Military Operations / Logistics Experience`), and stores the employer's standard equivalency clause.
3. **Customizable Scoring Weights & Verdict Thresholds (`scoring_and_thresholds`):** Allows employers to tune the five JD Alignment component weights (enforcing the mathematical invariant $C_1 + C_2 + C_3 + C_4 + C_5 = 100.0$), category base weights, tier multipliers, placement multipliers, and verdict thresholds (`jd_strong_match_threshold`, `jd_borderline_threshold`, `rqs_clear_inclusive_threshold`, `rqs_needs_tuning_threshold`).
4. **Dual-Signoff Change-Approval Workflow & Separation of Duties (`approval_workflow`):** Requires changes to progress through `DRAFT -> PENDING_APPROVAL -> APPROVED -> SUPERSEDED`, enforcing **Separation of Duties (`SoD`)** across three distinct actors: `author_id`, `ta_or_comp_approver_id` (TA / Compensation Lead), and `compliance_or_legal_approver_id` (HR Compliance / Legal Counsel).
5. **Effective Configuration Display in Reports:** Every scorecard and screening report displays the exact effective organization profile ID, version, `config_sha256`, approval status, component weights, and verdict thresholds used for the evaluation.

---

## 2. Organization Customization Profile Schema (`org-customization-profile-1.0`)

| Section / Field | Type | Invariant / Validation Rule |
| :--- | :--- | :--- |
| `org_id`, `org_name`, `config_version`, `effective_date` | `string` | Required non-empty identifiers and `YYYY-MM-DD` effective date. |
| `leveling_guide.<level>.org_level_code` | `string` | Employer's internal grade/band code (e.g., `"L3"`, `"L5"`, `"M2"`). |
| `leveling_guide.<level>.min_years_expected` | `float` | Must satisfy $0 \le \text{min\_years\_expected} \le \text{max\_benchmark\_years}$. |
| `leveling_guide.<level>.max_benchmark_years` | `float` | Maximum experience years before RQS Gate Necessity flags experience inflation. |
| `gate_and_equivalency_policy.allowed_hard_gate_types` | `array<enum>` | Subset of `["degree", "certification", "license", "security_clearance", "work_authorization"]`. |
| `gate_and_equivalency_policy.bachelor_equivalency_years` | `float` | Years of relevant hands-on experience accepted in lieu of a Bachelor's degree (e.g., `3.0` or `4.0`). |
| `scoring_and_thresholds.comp1..comp5` | `float` | All non-negative; must satisfy $C_1 + C_2 + C_3 + C_4 + C_5 = 100.00$. |
| `scoring_and_thresholds.jd_strong_match_threshold` | `float` | Must satisfy $0 < \text{jd\_borderline\_threshold} < \text{jd\_strong\_match\_threshold} \le 100.0$. |
| `scoring_and_thresholds.rqs_clear_inclusive_threshold` | `float` | Must satisfy $0 < \text{rqs\_needs\_tuning\_threshold} < \text{rqs\_clear\_inclusive\_threshold} \le 100.0$. |
| `approval_workflow.approval_status` | `enum` | One of: `DRAFT`, `PENDING_APPROVAL`, `APPROVED`, `SUPERSEDED`, `REJECTED`. |

---

## 3. Versioned Approval Workflow & Separation of Duties (`SoD`)

```
┌────────────────────────────────────────────────────────────────────────────┐
│ 1. DRAFT                                                                   │
│    • Author (`author_id`) modifies leveling ceilings, equivalency rules,   │
│      or component weights (`Sum = 100.0`) and documents `change_rationale`.│
└────────────────────────────────────┬───────────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 2. PENDING_APPROVAL                                                        │
│    • Automated schema validator checks weight sum = 100.0, threshold       │
│      ordering, and computes parameter diff (`compute_org_profile_diff`).   │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 3. APPROVED (Dual Sign-Off + Separation of Duties Enforced)                │
│    • Sign-Off 1: `ta_or_comp_approver_id` (TA / Compensation Lead)         │
│    • Sign-Off 2: `compliance_or_legal_approver_id` (HR Compliance / Legal) │
│    • SoD Gate: `author_id != ta_approver != compliance_approver`           │
│    • Sealed into hash-chained `sample_org_config_audit_history.json`.      │
└────────────────────────────────────┬───────────────────────────────────────┘
                                     ▼
┌────────────────────────────────────────────────────────────────────────────┐
│ 4. PRODUCTION EXECUTION (`--org-profile ... --require-approved`)           │
│    • `ops-req-audit`, `ops-req-jd-audit`, and `ops-req-screen` verify      │
│      `approval_status == "APPROVED"` and render the exact effective        │
│      configuration manifest directly inside the output report.             │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 4. CLI Usage (`ops-req-org-config`)

```bash
# 1. Run the Northstar Contact Solutions customization, diff, and hash-chain verification demo
ops-req-org-config --demo

# 2. Validate an organization profile JSON and enforce APPROVED dual-signoff status
ops-req-org-config --org-profile examples/sample_org_profile_northstar.json --require-approved

# 3. Verify the cryptographic hash chain of the configuration audit history
ops-req-org-config --verify-audit-history examples/sample_org_config_audit_history.json

# 4. Run Candidate JD Alignment, Requisition Quality (RQS), or Batch Screening with an org profile
ops-req-audit --demo --org-profile examples/sample_org_profile_northstar.json --require-approved-org-profile
ops-req-jd-audit --demo --org-profile examples/sample_org_profile_northstar.json --require-approved-org-profile
ops-req-screen --demo --org-profile examples/sample_org_profile_northstar.json --require-approved-org-profile
```
