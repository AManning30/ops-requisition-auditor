# ATS-Neutral Integration Contract, Vendor Adapters, Webhooks & Batch Pipelines

*Reference specification for `scripts/integration_adapters_and_webhooks.py` (`ops-req-integrate`) and the OpenAPI 3.1.0 / JSON Schema 2020-12 contract (`references/openapi_and_ats_neutral_schema.json`).*

---

## 1. Versioned ATS-Neutral JSON Contract (`ats-neutral-contract-1.0`)

To integrate with enterprise recruiting, HRIS, and talent-mobility ecosystems without coupling scoring logic to any single vendor's proprietary payload layout, `ops-requisition-auditor` defines three canonical envelopes in `ats-neutral-contract-1.0` (formally specified in [references/openapi_and_ats_neutral_schema.json](openapi_and_ats_neutral_schema.json)):

1. **`NeutralRequisitionEnvelope` (Inbound Requisition Contract):**
   - Normalizes job postings from Greenhouse (`job`), Lever (`posting`), Ashby (`jobPosting`), Workday HRIS, or HR-Open 4.x (`PositionOpening`) into a vendor-neutral structure with `requisition_id`, `external_system`, `title`, `department`, `location`, `requirements` (`JDRequirement[]`), and `alignment` (`AlignmentConfig`).
2. **`NeutralCandidateApplicationEnvelope` (Inbound Application Contract):**
   - Encapsulates `application_id`, `candidate_id`, non-reversible `pseudonym_token` (`SUBJ-<16hex>`), `resume_text`, `raw_resume_sha256`, `blind_mode` (`true` by default), explicit `consent` (`ConsentRecord`), and optional `human_decision`.
3. **`NeutralEvaluationAndGovernanceEnvelope` (Outbound Evaluation & Governance Contract):**
   - Guarantees that every evaluation exported back to an ATS, HRIS, data warehouse, or webhook consumer preserves **all** uncertainty and human-review governance fields:
     - **Score & Three-Pillar Uncertainty:** `composite_score`, `literal_only_score`, `plausible_score_interval` (`[low, high]`), `uncertainty_band`, `measurement_confidence`, `evidence_completeness_pct`, `component_scores`, and `point_deductions_ledger`.
     - **Human-in-the-Loop Governance:** `disposition_status` (defaults to `"PENDING_HUMAN_REVIEW"`), `auto_rejection_permitted: false`, `reviewer_id`, `reviewer_role`, `decision_timestamp`, `is_override`, `override_reason`, `decision_sha256`, and `chain_entry_sha256`.
     - **Full Version & Policy Provenance (`version_manifest`):** `engine_version`, `jd_alignment_rubric_version`, `rqs_rubric_version`, `governance_policy_version`, `config_sha256`, `policy_pack_id`, `policy_pack_version`, `policy_pack_sha256`, `org_profile_id`, `org_profile_version`, `org_profile_sha256`, and `vault_isolation_proof_sha256`.

---

## 2. Bidirectional Vendor Adapters (`Greenhouse`, `Lever`, `Ashby`, `Workday HRIS`, `HR-Open`)

| Adapter Class | Inbound Import Methods | Outbound Export Method | Governance & Privacy Safeguards Preserved |
| :--- | :--- | :--- | :--- |
| **`GreenhouseAdapter`** | `import_requisition(gh_job)`<br>`import_application(gh_app)` | `export_evaluation(env)` (`greenhouse_harvest_v1`) | Populates `custom_fields` (score, plausible band `[low, high]`, confidence, completeness, `PENDING_HUMAN_REVIEW`, `chain_entry_sha256`) + `activity_feed_note`; sets `automated_stage_transition_blocked: true`. |
| **`LeverAdapter`** | `import_requisition(lever_posting)`<br>`import_application(lever_opp)` | `export_evaluation(env)` (`lever_v1_opportunity_note`) | Attaches `ops-req-status:pending_human_review` and `human-in-the-loop-enforced` tags, formatted opportunity note, and full `governance_audit` block. |
| **`AshbyAdapter`** | `import_requisition(ashby_job)`<br>`import_application(ashby_app)` | `export_evaluation(env)` (`ashby_assessment_v1`) | Emits `assessmentResult` with `plausibleScoreInterval`, `measurementConfidence`, `dispositionStatus`, `humanReviewRequired: true`, and `governance_audit`. |
| **`WorkdayHRISMobilityAdapter`** | `import_worker_profile(hris_worker)` | `export_mobility_learning_plan(report)` (`workday_hris_talent_mobility_v1`) | Exports 30/60/90-day reskilling milestones keyed by `worker_pseudonym_token`; enforces `manager_feed_visibility: "PRIVATE_TO_EMPLOYEE_ONLY"` when `share_with_manager: false` and `retention_days: 0`. |
| **`HROpenStandardsAdapter`** | Standard `NeutralRequisitionEnvelope` | `export_evaluation(env)` (`hr_open_standards_4x`) | Produces HR-Open 4.x `assessmentResult` with `candidateSubjectToken` (`SUBJ-...`) and sealed `governance_audit` block. |

### Outbound Contract Verifier (`verify_outbound_governance_contract`)
Every adapter calls `verify_outbound_governance_contract(payload, adapter_name)` before returning. If any payload sets `auto_rejection_permitted != False`, contains prohibited automation parameters (`auto_reject`, `stage_action: "AUTO_REJECT"`), omits 64-character `decision_sha256` / `chain_entry_sha256` digests, or records a non-initial disposition without a non-bot human `reviewer_id`, `reviewer_role`, and `decision_timestamp`, the adapter fails closed with `ContractGovernanceViolationError`.

---

## 3. Authenticated Webhook & Batch-Job Modes

### 3.1 Signed Webhook Event Processing (`process_signed_webhook_event`)
- **HMAC-SHA256 Signature Verification (`verify_webhook_signature`):**
  - Computes `sha256=<hex>` over `<X-Ops-Req-Timestamp>.<raw_json_body>` using the shared `webhook_secret` and compares in constant time via `hmac.compare_digest()`.
  - Enforces a configurable replay-protection window (default $\pm 300\text{ seconds}$).
- **Supported Event Types:**
  - `application.submitted` $\to$ Blind-mode JD alignment + plausible band + vendor adapter export (`PENDING_HUMAN_REVIEW`).
  - `requisition.created` / `requisition.updated` $\to$ Employer Module 1 Requisition Quality Score (`RQS`) + findings count.
  - `mobility.profile_submitted` $\to$ Employer Module 3 Internal Mobility 30/60/90-day reskilling plan (`WorkdayHRISMobilityAdapter`).
  - `privacy.erasure_requested` $\to$ Three-vault subject erasure (`purge_subject_across_vaults`) returning a signed `DeletionReceipt`.

### 3.2 Batch-Job & JSONL Pipeline (`run_batch_integration_job`)
- Processes multi-system batch manifests (`--batch-job examples/sample_ats_neutral_contract_bundle.json`) and exports:
  - Markdown integration report (`--output`)
  - Full JSON audit bundle (`--json-out`)
  - Streaming JSONL of `NeutralEvaluationAndGovernanceEnvelope` records (`--jsonl-out`)

---

## 4. CLI Usage (`ops-req-integrate`)

```bash
# 1. Run the multi-ATS (Greenhouse, Lever, Ashby, Workday HRIS) batch & signed webhook integration demo
ops-req-integrate --demo

# 2. Export the formal OpenAPI 3.1.0 & JSON Schema 2020-12 contract specification
ops-req-integrate --export-openapi references/openapi_and_ats_neutral_schema.json

# 3. Run a batch integration job and emit an atomic JSONL stream of ATS-neutral governance envelopes
ops-req-integrate --batch-job examples/sample_ats_neutral_contract_bundle.json --jsonl-out batch_envelopes.jsonl
```
