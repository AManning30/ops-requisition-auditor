# Human-Approval & Anti-Automation Governance Specification (`ops-req-governance`)

This specification documents the governance middleware, schema validation rules, role-based access controls (RBAC), environment safeguards, and cryptographic hash-chain immutability implemented in `scripts/governance_middleware.py` (`ops-req-governance`).

---

## 1. Anti-Automation Controls & Mandatory `PENDING_HUMAN_REVIEW`

To prevent a heuristic alignment score from being misused as an autonomous hiring or rejection decision:

1. **Mandatory Initial State (`PENDING_HUMAN_REVIEW`):**
   - Every candidate evaluated by `ops-req-screen` or `ops-req-pilot` starts in `PENDING_HUMAN_REVIEW`.
   - No score threshold (`< 80.0` or `< 65.0`) or unverified knockout gate can automatically transition a candidate to `DO_NOT_ADVANCE` or `ADVANCE`.
2. **Prohibited CLI Flags & API Keys (`AutomationPolicyViolationError`, Exit Code `2`):**
   - CLI invocations containing `--auto-reject`, `--auto-advance`, `--auto-decide`, `--autonomous-mode`, `--autonomous-decision`, `--hiring-recommendation`, `--auto-disposition`, or `--skip-human-review` are blocked before argument parsing.
   - API/JSON payloads containing truthy `auto_reject`, `auto_advance`, `auto_decide`, `autonomous_mode`, `autonomous_decision`, `hiring_recommendation`, `auto_disposition`, or `skip_human_review` keys are immediately rejected.
3. **Neutral Evidence-Tier Framing (`neutral_evidence_tier_for`):**
   - Audit chain entries label scores as neutral evidence tiers (`Evidence Tier A (>=80.0): High Requirement Alignment — Human Review Required`, `Evidence Tier B (65.0-79.9): Partial Requirement Alignment — Human Review Required`, `Evidence Tier C (<65.0 or Unverified Gate): Evidence Gaps Detected — Human Review Required`), never as autonomous hiring recommendations.

---

## 2. Human Decision Schema Validation (`validate_and_seal_decision`)

Any transition out of `PENDING_HUMAN_REVIEW` (`ADVANCE`, `HOLD`, `DO_NOT_ADVANCE`) must pass five deterministic schema checks:

| Field | Validation Rule | Error Raised on Failure |
| :--- | :--- | :--- |
| `status` | Must be one of `PENDING_HUMAN_REVIEW`, `ADVANCE`, `HOLD`, `DO_NOT_ADVANCE` | `GovernanceValidationError` |
| `reviewer` | Required when `status != PENDING_HUMAN_REVIEW` ($\ge 3$ chars). Rejects bot/system identities matching `^(auto\|automated\|bot\|system\|ai\|llm\|agent\|cron\|pipeline\|daemon\|script\|unattended\|none\|null\|n/a\|default)` | `GovernanceValidationError` / `AutomationPolicyViolationError` |
| `reviewer_role` | Must be a valid role in `RBAC_ROLE_PERMISSIONS` with `can_record_disposition = True` (`recruiter`, `ta_lead`, `hiring_manager`, `wfm_director`, `hr_compliance`) | `RBACPermissionError` |
| `timestamp` | Required when `status != PENDING_HUMAN_REVIEW`; must match ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ` or offset) | `GovernanceValidationError` |
| `override_reason` | Required ($\ge 10$ chars) whenever `is_override == True` (e.g., `ADVANCE` when score $< 80.0$ or unverified gate; `DO_NOT_ADVANCE` when score $\ge 80.0$ and gates pass) **AND** on all `DO_NOT_ADVANCE` dispositions so no candidate is ever rejected without a documented human rationale | `GovernanceValidationError` |

---

## 3. Role-Based (`RBAC`) & Environment-Level Safeguards

### 3.1 Role-Based Access Control Matrix (`RBAC_ROLE_PERMISSIONS`)

| Role | `can_score` | `can_record_disposition` | `can_override` | Allowed States |
| :--- | :-: | :-: | :-: | :--- |
| `recruiter` | Yes | Yes | Yes | `PENDING_HUMAN_REVIEW`, `ADVANCE`, `HOLD`, `DO_NOT_ADVANCE` |
| `ta_lead` | Yes | Yes | Yes | `PENDING_HUMAN_REVIEW`, `ADVANCE`, `HOLD`, `DO_NOT_ADVANCE` |
| `hiring_manager` | Yes | Yes | Yes | `PENDING_HUMAN_REVIEW`, `ADVANCE`, `HOLD`, `DO_NOT_ADVANCE` |
| `wfm_director` | Yes | Yes | Yes | `PENDING_HUMAN_REVIEW`, `ADVANCE`, `HOLD`, `DO_NOT_ADVANCE` |
| `hr_compliance` | Yes | Yes | Yes | `PENDING_HUMAN_REVIEW`, `ADVANCE`, `HOLD`, `DO_NOT_ADVANCE` |
| `mobility_counselor` | Yes | **No** | **No** | `PENDING_HUMAN_REVIEW` only |
| `read_only_auditor` | Yes | **No** | **No** | `PENDING_HUMAN_REVIEW` only |
| `candidate` | Yes | **No** | **No** | `PENDING_HUMAN_REVIEW` only |

### 3.2 Environment-Level Safeguards (`enforce_environment_safeguards`)
- Inspects environment variables prior to execution and raises `EnvironmentSafeguardError` (exit code `2`) if any of `OPS_REQ_AUTO_REJECT`, `OPS_REQ_AUTO_ADVANCE`, `OPS_REQ_AUTONOMOUS_MODE`, `OPS_REQ_SKIP_HUMAN_REVIEW`, `ATS_AUTO_REJECT`, or `ATS_AUTO_DECIDE` is enabled (`1`, `true`, `yes`, `on`).
- Reads `OPS_REQ_REVIEWER_ROLE` (or `--reviewer-role`) to enforce RBAC permissions across CLI and batch runs.

---

## 4. Configuration Version Logging & Cryptographic Audit Chain Immutability

Every batch audit logs a frozen `GovernanceVersionManifest`:
- `engine_version`: `"1.5.0"`
- `jd_alignment_rubric_version`: `"jd-align-1.5"`
- `rqs_rubric_version`: `"rqs-0.2"`
- `governance_policy_version`: `"gov-hitl-1.0"`
- `config_sha256`: 64-character SHA-256 hash of the canonical requirement & alignment config

Each candidate record in `immutable_audit_chain` is sealed with:
1. **`decision_sha256`:** SHA-256 over `(candidate_id, status, reviewer, reviewer_role, timestamp, override_reason, is_override, config_sha256, score_at_decision, knockout_passed_at_decision, governance_policy_version)`.
2. **`entry_sha256`:** SHA-256 chaining `(sequence_index, candidate_id, composite_score, knockout_passed, evidence_tier, decision_sha256, config_sha256, rubric_versions, previous_entry_sha256)` starting from `GENESIS_CHAIN_HASH` (`"0" * 64`).

Calling `verify_audit_chain_integrity(chain)` (or `ops-req-governance --verify-audit-json batch_audit.json`) recomputes every decision hash and chain link, raising `AuditImmutabilityError` if any score, status, reviewer, timestamp, or chain order was modified after sign-off.
