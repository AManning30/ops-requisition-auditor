# Privacy Architecture, Data Minimization, Deletion API & Threat Model

*Reference specification for `scripts/privacy_data_minimization.py` (`ops-req-privacy`) and its integration across `scripts/explainable_screening_assistant.py` (`ops-req-screen`) and `scripts/internal_mobility_matcher.py` (`ops-req-mobility`).*

---

## 1. Core Privacy Principles & Ephemeral-by-Default Processing

Resumes, career-transition profiles, and voluntary EEO self-identification forms contain sensitive personal information (PII) and protected-class attributes. `ops-requisition-auditor` enforces six architectural privacy controls:

1. **Zero Network Exfiltration of Resume Data:** The scoring and reskilling engines run 100% locally on the Python 3.10+ standard library. No resume text, candidate name, contact detail, or demographic attribute is ever transmitted to an external LLM API or third-party cloud service.
2. **Ephemeral Processing by Default (`ephemeral_in_memory`):** Unless an operator explicitly passes a persistent output flag (`--output`, `--json-out`, `--csv-out`, `--vault-out`, or `--notices-dir`), all parsing, redaction, and scoring occur strictly in volatile memory and are discarded upon process termination (`mobility_profile_retention_days = 0`).
3. **Explicit, Revocable Subject Consent (`ConsentRecord`):** Both batch screening (`ops-req-screen --consent-json ... --require-consent`) and internal mobility (`ops-req-mobility --consent-json ... --require-consent`) verify subject consent prior to scoring. Revoked (`revoked: true`), declined (`consented: false`), expired (`expires_at < reference_date`), or out-of-scope consent records fail closed at the privacy gate (exit code `2`).
4. **Three-Vault Data Segregation (`SegregatedDataVault`):** Identity/PII, Pseudonymized Scores, and Voluntary Self-ID Demographics are partitioned into three separate logical vaults keyed by a salted HMAC-SHA256 pseudonym token (`SUBJ-<16hex>`), with automated structural verification (`verify_vault_isolation()`) proving zero PII or demographic leakage into the scoring vault.
5. **Configurable Retention & Cryptographic Right-to-Erasure (`DeletionReceipt`):** Each vault enforces independent retention TTLs (`pii_vault_retention_days`, `demographics_vault_retention_days`, `scoring_audit_retention_days`) and supports single-subject right-to-erasure (`purge_subject_across_vaults()`) and secure file overwriting (`secure_delete_file()`).
6. **Authenticated Export Encryption & Log Redaction:** Optional persistent JSON/vault exports can be encrypted with `--encrypt-exports` (`PBKDF2-HMAC-SHA256` 200,000 iterations + `SHA256-CTR` + Encrypt-then-MAC `HMAC-SHA256`), and diagnostic logs are scrubbed via `redact_log_message()`.

---

## 2. Three-Vault Segregated Data Architecture

```
                     ┌──────────────────────────────────────┐
                     │ Raw Candidate Resume + Self-ID Input │
                     └──────────────────┬───────────────────┘
                                        │
                    [1] Explicit Consent Gate (`verify_explicit_consent`)
                    [2] Salted HMAC-SHA256 Pseudonymization (`SUBJ-<16hex>`)
                    [3] Blind-Mode PII / ZIP / Grad-Year Redactor
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        ▼                               ▼                               ▼
┌───────────────────────────┐   ┌───────────────────────────┐   ┌───────────────────────────┐
│ VAULT 1: Identity & PII   │   │ VAULT 2: Scoring & Audit  │   │ VAULT 3: Voluntary EEO    │
│ (`pii_vault`)             │   │ (`scoring_vault`)         │   │ (`demographics_vault`)    │
├───────────────────────────┤   ├───────────────────────────┤   ├───────────────────────────┤
│ • `pseudonym_token`       │   │ • `pseudonym_token`       │   │ • `pseudonym_token`       │
│ • `candidate_id`          │   │ • `scored_sha256`         │   │ • `gender`                │
│ • `source_file` (basename)│   │ • `composite_score`       │   │ • `race_ethnicity`        │
│ • `raw_sha256`            │   │ • `comp1..comp5` scores   │   │ • `retention_expires_at`  │
│ • `extracted_emails`      │   │ • PII-scrubbed quotes     │   │                           │
│ • `extracted_phones/urls` │   │ • `decision_status`       │   │ [NO candidate_id]         │
│ • `retention_expires_at`  │   │ • `retention_expires_at`  │   │ [NO contact PII]          │
│                           │   │                           │   │ [NO component scores]     │
│ [NO scores or overrides]  │   │ [NO candidate_id or PII]  │   │                           │
│ [NO EEO demographics]     │   │ [NO EEO demographics]     │   │ TTL: 180 days (default)   │
│ TTL: 30 days (default)    │   │ TTL: 365 days (audit log) │   │ Roles: hr_compliance,     │
│ Roles: recruiter, ta_lead,│   │ Roles: recruiter, ta_lead,│   │        read_only_auditor  │
│        hr_compliance      │   │        hiring_manager,    │   └───────────────────────────┘
└───────────────────────────┘   │        wfm_director,      │
                                │        hr_compliance,     │
                                │        read_only_auditor  │
                                └───────────────────────────┘
```

### Cryptographic Isolation Proof (`verify_vault_isolation`)
Every call to `partition_into_segregated_vaults()`, `purge_subject_across_vaults()`, or `enforce_retention_schedule()` runs `verify_vault_isolation(vault)`:
- Scans every entry in `scoring_vault` to verify the absence of `candidate_id`, contact PII fields, demographic keys (`gender`, `race_ethnicity`, `age_band`, `disability_status`, `veteran_status`), and regex matches for email addresses, phone numbers, or SSNs.
- Scans every entry in `demographics_vault` to verify the absence of `candidate_id`, contact PII, and scoring fields (`composite_score`, `comp1_kw_score`, etc.).
- Computes a deterministic `isolation_proof_sha256` recorded on both `SegregatedDataVault` and `ScreeningBatchAudit`.

---

## 3. Explicit Consent Schema (`examples/sample_consent_records.json`)

Both `ops-req-screen` and `ops-req-mobility` accept `--consent-json <path>` and `--require-consent`:

| Field | Type | Required | Description |
| :--- | :--- | :-: | :--- |
| `subject_id` | `string` | Yes | Candidate or employee identifier (e.g., `"CAND-001"`, `"EMP-001"`). |
| `flow_type` | `enum` | Yes | One of: `"batch_screening"`, `"internal_mobility"`, `"outplacement_pack"`, `"voluntary_demographics"`. |
| `consented` | `boolean` | Yes | Must be `true` to permit processing. If `false`, processing is blocked immediately. |
| `consent_scope` | `array<string>` | Yes | List of authorized operations (e.g., `["batch_screening", "jd_alignment_scoring", "factor_disclosure_notice"]`). |
| `consent_timestamp` | `ISO-8601` | Yes | UTC timestamp when the candidate or employee granted consent. |
| `consent_method` | `string` | Yes | Provenance of consent (e.g., `"applicant_portal_opt_in"`, `"employee_self_service_opt_in"`). |
| `expires_at` | `YYYY-MM-DD` | No | Optional consent expiration date. |
| `revoked` | `boolean` | No | If `true`, `verify_explicit_consent()` blocks processing with `PrivacyConsentError`. |
| `share_with_manager` | `boolean` | No | For `internal_mobility`: must be `true` if `--share-with-manager` is requested when `--consent-json` is provided. |

---

## 4. Configurable Retention Settings & Secure Deletion API

### 4.1 Retention Configuration (`examples/sample_privacy_retention_policy.json`)

| Setting | Default | Purpose |
| :--- | :-: | :--- |
| `processing_mode` | `"ephemeral_in_memory"` | Default operating mode (`"ephemeral_in_memory"`, `"persistent_vault"`, or `"encrypted_persistent_vault"`). |
| `ephemeral_by_default` | `true` | Processes resumes in volatile memory unless explicit file exports are requested. |
| `pii_vault_retention_days` | `30` | Maximum retention window for raw identifying contact PII in `pii_vault` after active recruiting concludes. |
| `demographics_vault_retention_days` | `180` | Retention window for segregated voluntary EEO self-ID records in `demographics_vault`. |
| `scoring_audit_retention_days` | `365` | Retention window for pseudonymized `scoring_vault` records to satisfy statutory audit recordkeeping (e.g., NYC LL144 / Colorado SB 24-205 / EEOC). |
| `mobility_profile_retention_days` | `0` | Employee internal-mobility exploration defaults to `0` days (strictly ephemeral session). |
| `secure_delete_overwrite_passes` | `1` | Number of byte-overwrite passes (`1`–`7`, ending with `0x00` zero fill + `os.fsync`) before unlinking files. |

### 4.2 Deletion & Right-to-Erasure API (`scripts/privacy_data_minimization.py`)

1. **Subject Right-to-Erasure (`purge_subject_across_vaults`):**
   - Accepts either a raw `subject_id` (e.g., `"CAND-004"`) or a pseudonym token (`"SUBJ-..."`).
   - Removes the subject's PII from `pii_vault` and demographic attributes from `demographics_vault`.
   - When `purge_scoring_vault=False` (statutory audit preservation mode), retains only the non-identifying pseudonymized scorecard in `scoring_vault` while permanently severing the link in `pii_vault`; when `purge_scoring_vault=True`, purges all three vaults.
   - Marks the subject's `consent_ledger` entry as `revoked: true` and appends a signed `DeletionReceipt` (`receipt_id`, `pre_deletion_sha256`, `bytes_overwritten`, `vaults_purged`, `receipt_sha256`).
2. **Automated TTL Retention Enforcement (`enforce_retention_schedule`):**
   - Compares each vault record's `retention_expires_at` date against `evaluation_date` (`YYYY-MM-DD`).
   - Purges expired records across `pii_vault`, `demographics_vault`, and `scoring_vault` independently (so 30-day PII expires while 365-day pseudonymized audit records remain intact) and returns a `DeletionReceipt` per purged record.
3. **Secure File Deletion (`secure_delete_file`):**
   - Overwrites file contents in-place (`passes` passes ending with `0x00`), flushes OS buffers via `os.fsync()`, unlinks the file, verifies non-existence, and returns a signed `DeletionReceipt` (`verification_status="VERIFIED_UNLINKED"`).

---

## 5. Authenticated Export Encryption & Diagnostic Log Redaction

- **Authenticated Export Encryption (`encrypt_persistent_export` / `decrypt_persistent_export`):**
  - Derived entirely from Python 3.10+ `hashlib`, `hmac`, and `secrets` (zero external dependencies).
  - Derives a 256-bit Encryption Key and a 256-bit MAC Key from the passphrase in `OPS_REQ_EXPORT_PASSPHRASE` using `PBKDF2-HMAC-SHA256` (`200,000` iterations, 128-bit random salt).
  - Encrypts the payload via `HMAC-SHA256-CTR` with a 128-bit random nonce and seals the envelope (`schema`, `kdf`, `kdf_iterations`, `cipher`, `salt_b64`, `nonce_b64`, `metadata`, `ciphertext_b64`) with an Encrypt-then-MAC `HMAC-SHA256` tag verified via constant-time `hmac.compare_digest()`.
- **Deterministic Log Redaction (`redact_log_message`):**
  - Scrubs emails (`[REDACTED_EMAIL]`), phone numbers (`[REDACTED_PHONE]`), SSNs (`[REDACTED_SSN]`), 5/9-digit ZIP codes (`<STATE> [REDACTED_ZIP]`), personal URLs (`[REDACTED_URL]`), and bearer/API tokens (`[REDACTED_SECRET]`).

---

## 6. Threat Model & Access Boundaries (STRIDE Analysis)

### 6.1 RBAC Vault Access Boundary Matrix

| Role | Vault 1: `pii_vault` (Contact PII) | Vault 2: `scoring_vault` (Pseudonymized Scores) | Vault 3: `demographics_vault` (Voluntary EEO) | Can Record Screening Decisions? |
| :--- | :-: | :-: | :-: | :-: |
| `recruiter` / `ta_lead` | **ALLOW** | **ALLOW** | **DENY** | Yes |
| `hiring_manager` / `wfm_director` | **DENY** *(Blind Review)* | **ALLOW** | **DENY** | Yes |
| `hr_compliance` | **ALLOW** | **ALLOW** | **ALLOW** | Yes |
| `read_only_auditor` | **DENY** | **ALLOW** | **ALLOW** *(4/5ths Audit)* | No (Read-Only) |
| `mobility_counselor` | **DENY** | **ALLOW** | **DENY** | No (Read-Only) |

Enforced programmatically via `read_vault_with_access_boundary(vault, vault_name, actor_role)`, which raises `PrivacyAccessDeniedError` on unauthorized role access.

### 6.2 Threat Vector & Mitigation Ledger

| Threat ID & Category (STRIDE) | Attack / Misuse Scenario | Technical Mitigation in `ops-requisition-auditor` | Verification Test |
| :--- | :--- | :--- | :--- |
| **T1 — Information Disclosure (PII Leakage into Scoring)** | Candidate name, email, phone, ZIP code, or graduation year influences scoring or leaks to a blind reviewer. | `redact_resume_for_blind_screening()` strips PII/ZIP/graduation dates pre-scoring with `0.00 pts` score drift; `partition_into_segregated_vaults()` scrubs quotes and excludes `candidate_id` from `scoring_vault`. | `test_three_vault_segregation_and_isolation_proof` |
| **T2 — Information Disclosure / Bias (Demographic Cross-Contamination)** | Voluntary EEO self-ID data (`gender`, `race_ethnicity`) is joined with scores during ranking or exposed to hiring managers. | EEO records live exclusively in `demographics_vault`, are accessible only to `hr_compliance` and `read_only_auditor`, and `verify_vault_isolation()` fails closed if any demographic key enters `scoring_vault`. | `test_vault_isolation_detects_injected_pii_or_demographics` |
| **T3 — Elevation of Privilege / Surveillance (Unconsented Mobility Sharing)** | An employee's private internal-mobility exploration is shared with their current manager without explicit consent. | `ops-req-mobility` defaults to `share_with_manager: false` and `mobility_profile_retention_days: 0`; passing `--share-with-manager` with `--consent-json` requires `share_with_manager: true` in the employee's `ConsentRecord`. | `test_explicit_consent_enforcement_batch_and_mobility` |
| **T4 — Information Disclosure (Unconsented or Revoked Candidate Processing)** | A candidate who declined or revoked consent is included in a batch screening run. | `verify_explicit_consent()` blocks any subject with `consented: false`, `revoked: true`, or expired `expires_at` (exit code `2`). | `test_explicit_consent_enforcement_batch_and_mobility` |
| **T5 — Data Retention Drift (Indefinite Storage of Raw PII on Disk)** | Raw candidate contact PII remains stored long after a requisition closes. | `pii_vault_retention_days` defaults to `30` days (vs. `365` days for pseudonymized `scoring_vault`); `enforce_retention_schedule()` purges expired `pii_vault` entries while preserving pseudonymized audit trails. | `test_configurable_retention_ttl_and_right_to_erasure` |
| **T6 — Tampering & Exfiltration of Persistent Exports** | An exported JSON audit or vault file on shared storage is read or modified by an unauthorized party. | `--encrypt-exports` encrypts exports via `PBKDF2-HMAC-SHA256` (`200,000` iterations) + `SHA256-CTR` + Encrypt-then-MAC `HMAC-SHA256`. Any ciphertext or metadata modification triggers `PrivacyEncryptionError`. | `test_authenticated_export_encryption_and_tamper_detection` |
| **T7 — Information Disclosure in Diagnostic Logs** | Crash dumps or operational logs inadvertently print candidate emails, phones, SSNs, ZIP codes, or API tokens. | `redact_log_message()` scrubs all 6 sensitive token classes (`email`, `phone`, `ssn`, `zip_code`, `url`, `secret`) before logging. | `test_log_redaction_scrubs_pii_and_secrets` |
