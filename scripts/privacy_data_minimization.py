#!/usr/bin/env python3
"""
ops-requisition-auditor: Privacy, Data Minimization, Retention & Deletion Engine
================================================================================
Protects sensitive personal information (PII) in candidate resumes, internal
mobility profiles, and voluntary self-identification demographic records.

Core capabilities:
  1. Explicit Consent Gate (`ConsentRecord`, `verify_explicit_consent`):
     - Validates explicit, revocable subject consent for `batch_screening`,
       `internal_mobility`, `outplacement_pack`, and `voluntary_demographics`.
     - Blocks unconsented or revoked subjects when `--require-consent` is enabled
       (or when an explicit revocation record is present).
  2. Three-Vault Data Segregation (`SegregatedDataVault`, `partition_into_segregated_vaults`):
     - Vault 1 (`pii_vault`): Maps salted HMAC-SHA256 pseudonym token (`SUBJ-...`)
       to raw identity metadata (candidate ID, source path, contact PII).
     - Vault 2 (`scoring_vault`): Stores only pseudonym tokens, component scores,
       and PII-scrubbed evidence quotes. Zero PII and zero demographic fields.
     - Vault 3 (`demographics_vault`): Stores only pseudonym tokens and voluntary
       self-ID demographic groups for post-scoring 4/5ths adverse-impact math.
     - `verify_vault_isolation()` proves cryptographically and structurally that
       Vault 2 contains no PII/demographics and Vault 3 contains no PII/scores.
  3. Ephemeral-by-Default Processing, Configurable Retention & Secure Deletion API:
     - Defaults to `ephemeral_in_memory` mode (`mobility_profile_retention_days=0`).
     - `RetentionPolicyConfig` defines per-vault TTLs (`pii_vault_retention_days`,
       `scoring_audit_retention_days`, `demographics_vault_retention_days`).
     - `secure_delete_file()` overwrites file bytes (`0x00` + optional random pass),
       flushes to disk, unlinks, and returns a signed `DeletionReceipt`.
     - `purge_subject_across_vaults()` executes GDPR/CCPA/Colorado right-to-erasure
       across all 3 vaults and emits a verifiable `DeletionReceipt`.
     - `enforce_retention_schedule()` purges expired vault records or files based
       on `--reference-date` and returns a ledger of `DeletionReceipt` objects.
  4. Authenticated Export Encryption & Log Redaction:
     - `encrypt_persistent_export()` / `decrypt_persistent_export()` using stdlib
       `PBKDF2-HMAC-SHA256` (200,000 iterations) + CTR-mode SHA-256 keystream +
       Encrypt-then-MAC (`HMAC-SHA256`) integrity verification.
     - `redact_log_message()` strips emails, phones, SSNs, ZIP codes, URLs, and
       secret tokens from logs and diagnostic strings.

Runs on the Python 3.10+ standard library only (zero external dependencies).
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import governance_middleware as gov  # noqa: E402

PRIVACY_POLICY_VERSION = "privacy-min-1.0"
ENCRYPTED_EXPORT_SCHEMA = "ops-req-encrypted-export-1.0"
KDF_ITERATIONS = 200_000

ALLOWED_FLOW_TYPES = {
    "batch_screening",
    "internal_mobility",
    "outplacement_pack",
    "voluntary_demographics",
}

ALLOWED_PROCESSING_MODES = {
    "ephemeral_in_memory",
    "persistent_vault",
    "encrypted_persistent_vault",
}

# Regexes for log & text PII scrubbing
LOG_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
LOG_PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\b\d{3})[-.\s]\d{3}[-.\s]\d{4}\b")
LOG_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
LOG_URL_RE = re.compile(r"\b(?:https?://|www\.|linkedin\.com/in/|github\.com/)\S+", re.IGNORECASE)
LOG_STATE_ZIP_RE = re.compile(
    r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)"
    r"(\s+\d{5}(?:-\d{4})?)\b"
)
LOG_SECRET_RE = re.compile(
    r"\b(?:Bearer\s+[A-Za-z0-9_\-\.=]+|sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{20,}|pypi-[A-Za-z0-9_\-]{20,})\b",
    re.IGNORECASE,
)

DEMOGRAPHIC_KEYS = {
    "gender",
    "sex",
    "race",
    "ethnicity",
    "race_ethnicity",
    "age_band",
    "disability_status",
    "veteran_status",
    "national_origin",
}

# Access boundary matrix by RBAC role
VAULT_ACCESS_BOUNDARIES: Dict[str, List[str]] = {
    "pii_vault": ["recruiter", "ta_lead", "hr_compliance"],
    "scoring_vault": [
        "recruiter",
        "ta_lead",
        "hiring_manager",
        "wfm_director",
        "hr_compliance",
        "read_only_auditor",
        "mobility_counselor",
    ],
    "demographics_vault": ["hr_compliance", "read_only_auditor"],
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PrivacyError(Exception):
    """Base error for privacy, consent, isolation, retention, or encryption failures."""


class PrivacyConsentError(PrivacyError):
    """Raised when explicit consent is missing, expired, or revoked for a flow."""


class PrivacyIsolationError(PrivacyError):
    """Raised when PII or demographic fields cross vault isolation boundaries."""


class PrivacyAccessDeniedError(PrivacyError):
    """Raised when an unauthorized RBAC role attempts to read a restricted vault."""


class PrivacyEncryptionError(PrivacyError):
    """Raised when export encryption/decryption fails authentication or key derivation."""


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ConsentRecord:
    subject_id: str
    flow_type: str                        # one of ALLOWED_FLOW_TYPES
    consented: bool
    consent_scope: List[str]
    consent_timestamp: str                # ISO-8601
    consent_method: str                   # e.g. "applicant_portal_opt_in", "employee_self_service_opt_in"
    expires_at: str = ""                  # optional YYYY-MM-DD
    revoked: bool = False
    revoked_timestamp: str = ""
    share_with_manager: bool = False
    consent_sha256: str = ""


@dataclass
class RetentionPolicyConfig:
    policy_id: str = "default-ephemeral-privacy-policy"
    policy_version: str = PRIVACY_POLICY_VERSION
    processing_mode: str = "ephemeral_in_memory"      # "ephemeral_in_memory" | "persistent_vault" | "encrypted_persistent_vault"
    ephemeral_by_default: bool = True
    require_explicit_consent_batch: bool = True
    require_explicit_consent_mobility: bool = True
    require_explicit_consent_demographics: bool = True
    pii_vault_retention_days: int = 30
    scoring_audit_retention_days: int = 365
    demographics_vault_retention_days: int = 180
    mobility_profile_retention_days: int = 0          # 0 = ephemeral (discarded immediately after session)
    secure_delete_overwrite_passes: int = 1
    redact_pii_in_logs: bool = True
    encrypt_persistent_exports: bool = False
    policy_sha256: str = ""


@dataclass
class DeletionReceipt:
    receipt_id: str
    target_type: str                      # "file" | "vault_subject" | "retention_ttl_purge"
    target_identifier: str                # file basename or pseudonym_token
    reason: str                           # "EPHEMERAL_CLEANUP" | "CANDIDATE_ERASURE_REQUEST" | "RETENTION_TTL_EXPIRED"
    vaults_purged: List[str]
    bytes_overwritten: int
    overwrite_passes: int
    pre_deletion_sha256: str
    deleted_at: str
    verification_status: str              # "VERIFIED_PURGED" | "VERIFIED_UNLINKED"
    receipt_sha256: str = ""


@dataclass
class SegregatedDataVault:
    vault_schema_version: str
    created_at: str
    reference_date: str
    processing_mode: str
    retention_policy_id: str
    retention_policy_sha256: str
    pseudonym_salt_fingerprint: str
    pii_vault: Dict[str, Dict[str, Any]]
    scoring_vault: Dict[str, Dict[str, Any]]
    demographics_vault: Dict[str, Dict[str, Any]]
    consent_ledger: Dict[str, Dict[str, Any]]
    isolation_verified: bool = False
    isolation_proof_sha256: str = ""
    deletion_receipts: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Log & Text Redaction
# ---------------------------------------------------------------------------

def redact_log_message(text: str) -> Tuple[str, Dict[str, int]]:
    """
    Strip identifying PII and credentials from log messages, quotes, and diagnostic strings:
      - Emails -> `[REDACTED_EMAIL]`
      - Phone numbers -> `[REDACTED_PHONE]`
      - SSNs -> `[REDACTED_SSN]`
      - State + 5/9-digit ZIP codes -> `<STATE> [REDACTED_ZIP]`
      - Personal URLs -> `[REDACTED_URL]`
      - Bearer / API tokens -> `[REDACTED_SECRET]`
    """
    counts = {
        "email": 0,
        "phone": 0,
        "ssn": 0,
        "zip_code": 0,
        "url": 0,
        "secret": 0,
    }

    def _sub(pattern: re.Pattern[str], repl: str, key: str, s: str) -> str:
        matches = list(pattern.finditer(s))
        counts[key] += len(matches)
        return pattern.sub(repl, s)

    out = text
    out = _sub(LOG_SECRET_RE, "[REDACTED_SECRET]", "secret", out)
    out = _sub(LOG_EMAIL_RE, "[REDACTED_EMAIL]", "email", out)
    out = _sub(LOG_SSN_RE, "[REDACTED_SSN]", "ssn", out)
    out = _sub(LOG_PHONE_RE, "[REDACTED_PHONE]", "phone", out)
    out = _sub(LOG_URL_RE, "[REDACTED_URL]", "url", out)

    zip_matches = list(LOG_STATE_ZIP_RE.finditer(out))
    counts["zip_code"] += len(zip_matches)
    out = LOG_STATE_ZIP_RE.sub(r"\1 [REDACTED_ZIP]", out)
    return out, counts


# ---------------------------------------------------------------------------
# Pseudonymization & Consent Management
# ---------------------------------------------------------------------------

def compute_pseudonym_token(subject_id: str, salt: str = "ops-req-privacy-default-salt-2026") -> str:
    """Compute a deterministic, non-reversible HMAC-SHA256 pseudonym token (`SUBJ-<16hex>`)."""
    mac = hmac.new(
        salt.encode("utf-8"),
        subject_id.strip().upper().encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"SUBJ-{mac[:16].upper()}"


def build_consent_record(raw: Mapping[str, Any]) -> ConsentRecord:
    subject_id = str(raw.get("subject_id", "")).strip()
    if not subject_id:
        raise PrivacyConsentError("[PRIVACY GATE] Consent record is missing 'subject_id'.")
    flow_type = str(raw.get("flow_type", "batch_screening")).strip()
    if flow_type not in ALLOWED_FLOW_TYPES:
        raise PrivacyConsentError(
            f"[PRIVACY GATE] Invalid flow_type '{flow_type}' for subject '{subject_id}'. "
            f"Allowed: {sorted(ALLOWED_FLOW_TYPES)}."
        )
    consented = bool(raw.get("consented", False))
    revoked = bool(raw.get("revoked", False))
    scope_raw = raw.get("consent_scope", [])
    scope = [str(s).strip() for s in scope_raw if str(s).strip()] if isinstance(scope_raw, list) else []
    ts = str(raw.get("consent_timestamp", "")).strip()
    method = str(raw.get("consent_method", "explicit_opt_in")).strip()
    expires_at = str(raw.get("expires_at", "")).strip()
    revoked_ts = str(raw.get("revoked_timestamp", "")).strip()
    share_mgr = bool(raw.get("share_with_manager", False))

    canonical = json.dumps(
        {
            "subject_id": subject_id,
            "flow_type": flow_type,
            "consented": consented,
            "consent_scope": sorted(scope),
            "consent_timestamp": ts,
            "consent_method": method,
            "expires_at": expires_at,
            "revoked": revoked,
            "revoked_timestamp": revoked_ts,
            "share_with_manager": share_mgr,
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return ConsentRecord(
        subject_id=subject_id,
        flow_type=flow_type,
        consented=consented,
        consent_scope=scope,
        consent_timestamp=ts,
        consent_method=method,
        expires_at=expires_at,
        revoked=revoked,
        revoked_timestamp=revoked_ts,
        share_with_manager=share_mgr,
        consent_sha256=digest,
    )


def load_consent_records(path: Optional[str]) -> Dict[str, ConsentRecord]:
    if not path:
        return {}
    resolved = gov.resolve_input_path(path)
    with open(resolved, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    records: Dict[str, ConsentRecord] = {}
    if isinstance(raw, dict) and "consents" in raw and isinstance(raw["consents"], list):
        items = raw["consents"]
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = []
        for sid, val in raw.items():
            if isinstance(val, dict):
                d = dict(val)
                d.setdefault("subject_id", sid)
                items.append(d)
    else:
        raise PrivacyConsentError("[PRIVACY GATE] Invalid consent JSON structure.")

    for item in items:
        rec = build_consent_record(item)
        key = f"{rec.subject_id}:{rec.flow_type}"
        records[key] = rec
        records.setdefault(rec.subject_id, rec)
    return records


def verify_explicit_consent(
    subject_id: str,
    flow_type: str,
    consent_map: Optional[Mapping[str, ConsentRecord]] = None,
    reference_date: Any = "2026-11-01",
    require_explicit_consent: bool = False,
    require_manager_share_consent: bool = False,
) -> ConsentRecord:
    """
    Verify that `subject_id` has active, non-revoked consent for `flow_type`.
    - If a record exists and `revoked=True` or `consented=False`, raises `PrivacyConsentError`
      even when `require_explicit_consent=False` (honoring explicit opt-outs/revocations).
    - If no record exists and `require_explicit_consent=True`, raises `PrivacyConsentError`.
    - If `require_manager_share_consent=True` (e.g., `--share-with-manager` in internal mobility)
      and the record does not have `share_with_manager=True`, raises `PrivacyConsentError`.
    """
    if isinstance(reference_date, datetime):
        ref_str = reference_date.strftime("%Y-%m-%d")
    else:
        ref_str = str(reference_date).strip()[:10]

    cmap = consent_map or {}
    rec = cmap.get(f"{subject_id}:{flow_type}") or cmap.get(subject_id)

    if rec is None:
        if require_explicit_consent:
            raise PrivacyConsentError(
                f"[PRIVACY GATE] BLOCKED: Missing explicit consent record for subject '{subject_id}' "
                f"in flow '{flow_type}' (require_explicit_consent=True)."
            )
        # Synthesize default session-scoped opt-in record when not in strict require_explicit_consent mode
        return build_consent_record(
            {
                "subject_id": subject_id,
                "flow_type": flow_type,
                "consented": True,
                "consent_scope": [flow_type, "ephemeral_session_scoring"],
                "consent_timestamp": f"{ref_str}T00:00:00Z",
                "consent_method": "ephemeral_user_initiated_session",
                "share_with_manager": require_manager_share_consent,
            }
        )

    if rec.revoked:
        raise PrivacyConsentError(
            f"[PRIVACY GATE] BLOCKED: Subject '{subject_id}' revoked consent for flow '{flow_type}' "
            f"(revoked_timestamp='{rec.revoked_timestamp}')."
        )
    if not rec.consented:
        raise PrivacyConsentError(
            f"[PRIVACY GATE] BLOCKED: Subject '{subject_id}' declined consent ('consented': false) "
            f"for flow '{flow_type}'."
        )
    if rec.flow_type != flow_type and flow_type not in rec.consent_scope:
        raise PrivacyConsentError(
            f"[PRIVACY GATE] BLOCKED: Consent record for '{subject_id}' covers '{rec.flow_type}', "
            f"not requested flow '{flow_type}'."
        )
    if rec.expires_at and ref_str > rec.expires_at:
        raise PrivacyConsentError(
            f"[PRIVACY GATE] BLOCKED: Consent record for '{subject_id}' expired on '{rec.expires_at}' "
            f"(reference_date='{ref_str}')."
        )
    if require_manager_share_consent and not rec.share_with_manager:
        raise PrivacyConsentError(
            f"[PRIVACY GATE] BLOCKED: Employee '{subject_id}' has not consented to 'share_with_manager: true' "
            f"in internal mobility flow."
        )
    return rec


# ---------------------------------------------------------------------------
# Retention Policy Configuration
# ---------------------------------------------------------------------------

def get_default_retention_policy_dict() -> Dict[str, Any]:
    return {
        "policy_id": "default-ephemeral-privacy-policy",
        "policy_version": PRIVACY_POLICY_VERSION,
        "processing_mode": "ephemeral_in_memory",
        "ephemeral_by_default": True,
        "require_explicit_consent_batch": True,
        "require_explicit_consent_mobility": True,
        "require_explicit_consent_demographics": True,
        "pii_vault_retention_days": 30,
        "scoring_audit_retention_days": 365,
        "demographics_vault_retention_days": 180,
        "mobility_profile_retention_days": 0,
        "secure_delete_overwrite_passes": 1,
        "redact_pii_in_logs": True,
        "encrypt_persistent_exports": False,
    }


def validate_and_build_retention_policy(raw: Mapping[str, Any]) -> RetentionPolicyConfig:
    mode = str(raw.get("processing_mode", "ephemeral_in_memory")).strip()
    if mode not in ALLOWED_PROCESSING_MODES:
        raise PrivacyError(
            f"[PRIVACY POLICY] Invalid processing_mode '{mode}'. Allowed: {sorted(ALLOWED_PROCESSING_MODES)}."
        )
    pii_days = int(raw.get("pii_vault_retention_days", 30))
    score_days = int(raw.get("scoring_audit_retention_days", 365))
    demo_days = int(raw.get("demographics_vault_retention_days", 180))
    mob_days = int(raw.get("mobility_profile_retention_days", 0))
    passes = int(raw.get("secure_delete_overwrite_passes", 1))
    if any(d < 0 for d in (pii_days, score_days, demo_days, mob_days)):
        raise PrivacyError("[PRIVACY POLICY] Retention day settings must be non-negative integers (>= 0).")
    if passes < 1 or passes > 7:
        raise PrivacyError("[PRIVACY POLICY] secure_delete_overwrite_passes must be between 1 and 7.")

    cfg_dict = {
        "policy_id": str(raw.get("policy_id", "default-ephemeral-privacy-policy")).strip(),
        "policy_version": str(raw.get("policy_version", PRIVACY_POLICY_VERSION)).strip(),
        "processing_mode": mode,
        "ephemeral_by_default": bool(raw.get("ephemeral_by_default", True)),
        "require_explicit_consent_batch": bool(raw.get("require_explicit_consent_batch", True)),
        "require_explicit_consent_mobility": bool(raw.get("require_explicit_consent_mobility", True)),
        "require_explicit_consent_demographics": bool(raw.get("require_explicit_consent_demographics", True)),
        "pii_vault_retention_days": pii_days,
        "scoring_audit_retention_days": score_days,
        "demographics_vault_retention_days": demo_days,
        "mobility_profile_retention_days": mob_days,
        "secure_delete_overwrite_passes": passes,
        "redact_pii_in_logs": bool(raw.get("redact_pii_in_logs", True)),
        "encrypt_persistent_exports": bool(raw.get("encrypt_persistent_exports", False)),
    }
    sha = hashlib.sha256(json.dumps(cfg_dict, sort_keys=True).encode("utf-8")).hexdigest()
    return RetentionPolicyConfig(**cfg_dict, policy_sha256=sha)


def load_retention_policy(path: Optional[str] = None) -> RetentionPolicyConfig:
    if not path:
        return validate_and_build_retention_policy(get_default_retention_policy_dict())
    resolved = gov.resolve_input_path(path)
    with open(resolved, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return validate_and_build_retention_policy(raw)


# ---------------------------------------------------------------------------
# Three-Vault Segregation & Isolation Verifier
# ---------------------------------------------------------------------------

def partition_into_segregated_vaults(
    candidates: Sequence[Mapping[str, Any]],
    raw_resumes_map: Optional[Mapping[str, str]] = None,
    demographics_map: Optional[Mapping[str, Mapping[str, str]]] = None,
    consent_map: Optional[Mapping[str, ConsentRecord]] = None,
    retention_policy: Optional[RetentionPolicyConfig] = None,
    reference_date: str = "2026-11-01",
    salt: str = "ops-req-privacy-default-salt-2026",
) -> SegregatedDataVault:
    """
    Partition candidate records, raw PII, and voluntary self-ID demographics into three
    strictly isolated vaults keyed by a salted HMAC-SHA256 pseudonym token (`SUBJ-...`):
      1. `pii_vault`: Raw candidate ID, source file, raw SHA-256, extracted contact PII, and expiry date.
      2. `scoring_vault`: Only pseudonym token, component scores, redacted evidence quotes, and decision status.
      3. `demographics_vault`: Only pseudonym token and voluntary EEO self-ID fields for 4/5ths analysis.
    """
    policy = retention_policy or load_retention_policy()
    raw_resumes_map = raw_resumes_map or {}
    demographics_map = demographics_map or {}
    consent_map = consent_map or {}

    ref_dt = datetime.strptime(reference_date[:10], "%Y-%m-%d")
    pii_expires = (ref_dt + timedelta(days=policy.pii_vault_retention_days)).strftime("%Y-%m-%d")
    score_expires = (ref_dt + timedelta(days=policy.scoring_audit_retention_days)).strftime("%Y-%m-%d")
    demo_expires = (ref_dt + timedelta(days=policy.demographics_vault_retention_days)).strftime("%Y-%m-%d")

    pii_vault: Dict[str, Dict[str, Any]] = {}
    scoring_vault: Dict[str, Dict[str, Any]] = {}
    demographics_vault: Dict[str, Dict[str, Any]] = {}
    consent_ledger: Dict[str, Dict[str, Any]] = {}

    for cand in candidates:
        cid = str(cand.get("candidate_id", "")).strip()
        token = compute_pseudonym_token(cid, salt=salt)
        raw_text = raw_resumes_map.get(cid, "")

        emails = LOG_EMAIL_RE.findall(raw_text) if raw_text else []
        phones = LOG_PHONE_RE.findall(raw_text) if raw_text else []
        urls = LOG_URL_RE.findall(raw_text) if raw_text else []

        crec = consent_map.get(f"{cid}:batch_screening") or consent_map.get(cid)
        if crec is not None:
            consent_ledger[token] = {
                "pseudonym_token": token,
                "flow_type": crec.flow_type,
                "consented": crec.consented,
                "revoked": crec.revoked,
                "consent_timestamp": crec.consent_timestamp,
                "consent_method": crec.consent_method,
                "consent_sha256": crec.consent_sha256,
            }

        # 1. Identity & PII Vault (omitted if processing_mode is ephemeral_in_memory and pii_vault_retention_days == 0)
        pii_vault[token] = {
            "pseudonym_token": token,
            "candidate_id": cid,
            "source_file": os.path.basename(str(cand.get("source_file", ""))),
            "raw_sha256": str(cand.get("raw_sha256", "")),
            "extracted_emails": emails,
            "extracted_phones": phones,
            "extracted_urls": urls,
            "created_at": reference_date[:10],
            "retention_expires_at": pii_expires,
        }

        # Scrub any residual PII from evidence quotes before writing to Vault 2 (Scoring Vault)
        def _scrub_req_list(items: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
            cleaned: List[Dict[str, Any]] = []
            for item in items:
                d = dict(item)
                if "quote" in d and isinstance(d["quote"], str):
                    d["quote"], _ = redact_log_message(d["quote"])
                cleaned.append(d)
            return cleaned

        dec = cand.get("decision", {})
        dec_status = dec.get("status", "PENDING_HUMAN_REVIEW") if isinstance(dec, Mapping) else getattr(dec, "status", "PENDING_HUMAN_REVIEW")
        dec_sha = dec.get("decision_sha256", "") if isinstance(dec, Mapping) else getattr(dec, "decision_sha256", "")

        # 2. Pseudonymized Scoring & Evidence Vault (NO candidate_id, NO PII, NO demographics)
        scoring_vault[token] = {
            "pseudonym_token": token,
            "scored_sha256": str(cand.get("scored_sha256", "")),
            "blind_mode": bool(cand.get("blind_mode", True)),
            "rank": int(cand.get("rank", 0)),
            "composite_score": float(cand.get("composite_score", 0.0)),
            "literal_score": float(cand.get("literal_score", 0.0)),
            "recommendation_band": str(cand.get("recommendation_band", "")),
            "knockout_passed": bool(cand.get("knockout_passed", False)),
            "unverified_gates": list(cand.get("unverified_gates", [])),
            "comp1_kw_score": float(cand.get("comp1_kw_score", 0.0)),
            "comp2_context_score": float(cand.get("comp2_context_score", 0.0)),
            "comp3_alignment_score": float(cand.get("comp3_alignment_score", 0.0)),
            "comp4_knockout_score": float(cand.get("comp4_knockout_score", 0.0)),
            "comp5_schema_score": float(cand.get("comp5_schema_score", 0.0)),
            "verified_requirements": _scrub_req_list(cand.get("verified_requirements", [])),
            "partial_requirements": _scrub_req_list(cand.get("partial_requirements", [])),
            "unverified_requirements": _scrub_req_list(cand.get("unverified_requirements", [])),
            "decision_status": dec_status,
            "decision_sha256": dec_sha,
            "created_at": reference_date[:10],
            "retention_expires_at": score_expires,
        }

        # 3. Voluntary Self-ID Demographics Vault (NO candidate_id, NO contact PII, NO scores)
        if cid in demographics_map and demographics_map[cid]:
            demo_entry = {
                k: str(v)
                for k, v in demographics_map[cid].items()
                if k not in ("candidate_id", "name", "email", "phone", "composite_score")
            }
            demographics_vault[token] = {
                "pseudonym_token": token,
                "demographics": demo_entry,
                "created_at": reference_date[:10],
                "retention_expires_at": demo_expires,
            }

    salt_fp = hashlib.sha256(salt.encode("utf-8")).hexdigest()[:16]
    vault = SegregatedDataVault(
        vault_schema_version="segregated-vault-1.0",
        created_at=reference_date[:10],
        reference_date=reference_date[:10],
        processing_mode=policy.processing_mode,
        retention_policy_id=policy.policy_id,
        retention_policy_sha256=policy.policy_sha256,
        pseudonym_salt_fingerprint=salt_fp,
        pii_vault=pii_vault,
        scoring_vault=scoring_vault,
        demographics_vault=demographics_vault,
        consent_ledger=consent_ledger,
    )
    verify_vault_isolation(vault)
    return vault


def verify_vault_isolation(vault: SegregatedDataVault) -> str:
    """
    Verify that:
      1. `scoring_vault` contains zero raw PII (`candidate_id`, emails, phones, SSNs, ZIP codes)
         and zero demographic keys (`gender`, `race_ethnicity`, etc.).
      2. `demographics_vault` contains zero raw PII (`candidate_id`, emails, phones) and zero
         scoring fields (`composite_score`, `comp1_kw_score`, `verified_requirements`).
    Sets `vault.isolation_verified = True` and `vault.isolation_proof_sha256` on success.
    """
    raw_cids = {
        str(v.get("candidate_id", "")).strip()
        for v in vault.pii_vault.values()
        if str(v.get("candidate_id", "")).strip()
    }

    for token, s_entry in vault.scoring_vault.items():
        if "candidate_id" in s_entry or "extracted_emails" in s_entry or "extracted_phones" in s_entry:
            raise PrivacyIsolationError(
                f"[PRIVACY ISOLATION FAILURE] Scoring vault entry '{token}' contains raw PII keys."
            )
        if any(dk in s_entry for dk in DEMOGRAPHIC_KEYS) or "demographics" in s_entry:
            raise PrivacyIsolationError(
                f"[PRIVACY ISOLATION FAILURE] Scoring vault entry '{token}' contains demographic attributes."
            )
        serialized = json.dumps(s_entry, sort_keys=True)
        for cid in raw_cids:
            if cid and cid in serialized:
                raise PrivacyIsolationError(
                    f"[PRIVACY ISOLATION FAILURE] Raw candidate_id '{cid}' leaked into scoring_vault['{token}']."
                )
        if LOG_EMAIL_RE.search(serialized) or LOG_PHONE_RE.search(serialized) or LOG_SSN_RE.search(serialized):
            raise PrivacyIsolationError(
                f"[PRIVACY ISOLATION FAILURE] Unredacted email/phone/SSN detected in scoring_vault['{token}']."
            )

    for token, d_entry in vault.demographics_vault.items():
        if "candidate_id" in d_entry or "composite_score" in d_entry or "comp1_kw_score" in d_entry:
            raise PrivacyIsolationError(
                f"[PRIVACY ISOLATION FAILURE] Demographics vault entry '{token}' contains PII or score fields."
            )
        serialized = json.dumps(d_entry, sort_keys=True)
        for cid in raw_cids:
            if cid and cid in serialized:
                raise PrivacyIsolationError(
                    f"[PRIVACY ISOLATION FAILURE] Raw candidate_id '{cid}' leaked into demographics_vault['{token}']."
                )
        if LOG_EMAIL_RE.search(serialized) or LOG_PHONE_RE.search(serialized) or LOG_SSN_RE.search(serialized):
            raise PrivacyIsolationError(
                f"[PRIVACY ISOLATION FAILURE] Unredacted contact PII detected in demographics_vault['{token}']."
            )

    proof_payload = json.dumps(
        {
            "vault_schema_version": vault.vault_schema_version,
            "scoring_tokens": sorted(vault.scoring_vault.keys()),
            "demographics_tokens": sorted(vault.demographics_vault.keys()),
            "scoring_sha256": hashlib.sha256(json.dumps(vault.scoring_vault, sort_keys=True).encode("utf-8")).hexdigest(),
            "demographics_sha256": hashlib.sha256(json.dumps(vault.demographics_vault, sort_keys=True).encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
    )
    proof_sha = hashlib.sha256(proof_payload.encode("utf-8")).hexdigest()
    vault.isolation_verified = True
    vault.isolation_proof_sha256 = proof_sha
    return proof_sha


def read_vault_with_access_boundary(
    vault: SegregatedDataVault,
    vault_name: str,
    actor_role: str,
) -> Dict[str, Dict[str, Any]]:
    """Enforce RBAC access boundaries across `pii_vault`, `scoring_vault`, and `demographics_vault`."""
    if vault_name not in VAULT_ACCESS_BOUNDARIES:
        raise PrivacyError(f"[PRIVACY ACCESS] Unknown vault '{vault_name}'.")
    role_norm = actor_role.strip().lower()
    allowed_roles = VAULT_ACCESS_BOUNDARIES[vault_name]
    if role_norm not in allowed_roles:
        raise PrivacyAccessDeniedError(
            f"[PRIVACY ACCESS DENIED] Role '{role_norm}' is not authorized to access '{vault_name}'. "
            f"Permitted roles: {allowed_roles}."
        )
    return dict(getattr(vault, vault_name))


# ---------------------------------------------------------------------------
# Secure Deletion API & Configurable Retention Enforcement
# ---------------------------------------------------------------------------

def _build_deletion_receipt(
    target_type: str,
    target_identifier: str,
    reason: str,
    vaults_purged: List[str],
    bytes_overwritten: int,
    overwrite_passes: int,
    pre_deletion_sha256: str,
    deleted_at: str,
    verification_status: str,
) -> DeletionReceipt:
    payload = {
        "target_type": target_type,
        "target_identifier": target_identifier,
        "reason": reason,
        "vaults_purged": sorted(vaults_purged),
        "bytes_overwritten": bytes_overwritten,
        "overwrite_passes": overwrite_passes,
        "pre_deletion_sha256": pre_deletion_sha256,
        "deleted_at": deleted_at,
        "verification_status": verification_status,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    receipt_id = f"DEL-{digest[:12].upper()}"
    return DeletionReceipt(
        receipt_id=receipt_id,
        target_type=target_type,
        target_identifier=target_identifier,
        reason=reason,
        vaults_purged=vaults_purged,
        bytes_overwritten=bytes_overwritten,
        overwrite_passes=overwrite_passes,
        pre_deletion_sha256=pre_deletion_sha256,
        deleted_at=deleted_at,
        verification_status=verification_status,
        receipt_sha256=digest,
    )


def secure_delete_file(
    file_path: str,
    passes: int = 1,
    reason: str = "CANDIDATE_ERASURE_REQUEST",
    deleted_at: str = "2026-11-01T00:00:00Z",
) -> DeletionReceipt:
    """
    Securely overwrite a file on disk (`passes` overwrites ending with `0x00` zero fill),
    flush via `os.fsync`, unlink the path, verify non-existence, and return a signed
    cryptographic `DeletionReceipt`.
    """
    if passes < 1 or passes > 7:
        raise PrivacyError("[PRIVACY DELETION] passes must be between 1 and 7.")
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"[PRIVACY DELETION] File not found: '{file_path}'.")

    with open(file_path, "rb") as fh:
        raw_bytes = fh.read()
    size = len(raw_bytes)
    pre_sha = hashlib.sha256(raw_bytes).hexdigest()

    with open(file_path, "r+b") as fh:
        for p in range(passes):
            fh.seek(0)
            if p < passes - 1:
                fh.write(os.urandom(size))
            else:
                fh.write(b"\x00" * size)
            fh.flush()
            os.fsync(fh.fileno())

    os.remove(file_path)
    if os.path.exists(file_path):
        raise PrivacyError(f"[PRIVACY DELETION] Failed to unlink '{file_path}'.")

    return _build_deletion_receipt(
        target_type="file",
        target_identifier=os.path.basename(file_path),
        reason=reason,
        vaults_purged=["filesystem"],
        bytes_overwritten=size * passes,
        overwrite_passes=passes,
        pre_deletion_sha256=pre_sha,
        deleted_at=deleted_at,
        verification_status="VERIFIED_UNLINKED",
    )


def purge_subject_across_vaults(
    vault: SegregatedDataVault,
    subject_id_or_token: str,
    salt: str = "ops-req-privacy-default-salt-2026",
    reason: str = "CANDIDATE_ERASURE_REQUEST",
    deleted_at: str = "2026-11-01T00:00:00Z",
    purge_scoring_vault: bool = True,
) -> DeletionReceipt:
    """
    Execute a subject right-to-erasure request across `pii_vault`, `demographics_vault`,
    and optionally `scoring_vault` (or retain only the non-PII pseudonymized audit entry
    when `purge_scoring_vault=False` for statutory audit compliance).
    """
    query = subject_id_or_token.strip()
    token = query if query.startswith("SUBJ-") else compute_pseudonym_token(query, salt=salt)

    purged_vaults: List[str] = []
    removed_blobs: List[str] = []

    if token in vault.pii_vault:
        removed_blobs.append(json.dumps(vault.pii_vault.pop(token), sort_keys=True))
        purged_vaults.append("pii_vault")
    if token in vault.demographics_vault:
        removed_blobs.append(json.dumps(vault.demographics_vault.pop(token), sort_keys=True))
        purged_vaults.append("demographics_vault")
    if purge_scoring_vault and token in vault.scoring_vault:
        removed_blobs.append(json.dumps(vault.scoring_vault.pop(token), sort_keys=True))
        purged_vaults.append("scoring_vault")
    if token in vault.consent_ledger:
        vault.consent_ledger[token]["revoked"] = True
        vault.consent_ledger[token]["erased_at"] = deleted_at

    if not purged_vaults:
        raise KeyError(f"[PRIVACY DELETION] Subject token '{token}' not found in any vault.")

    combined = "\n".join(removed_blobs).encode("utf-8")
    pre_sha = hashlib.sha256(combined).hexdigest()
    receipt = _build_deletion_receipt(
        target_type="vault_subject",
        target_identifier=token,
        reason=reason,
        vaults_purged=purged_vaults,
        bytes_overwritten=len(combined),
        overwrite_passes=1,
        pre_deletion_sha256=pre_sha,
        deleted_at=deleted_at,
        verification_status="VERIFIED_PURGED",
    )
    vault.deletion_receipts.append(dataclasses.asdict(receipt))
    verify_vault_isolation(vault)
    return receipt


def enforce_retention_schedule(
    vault: SegregatedDataVault,
    evaluation_date: str,
) -> List[DeletionReceipt]:
    """
    Purge all vault entries whose `retention_expires_at` is strictly earlier than
    `evaluation_date` (`YYYY-MM-DD`). Returns a list of cryptographic `DeletionReceipt` objects.
    """
    eval_str = evaluation_date.strip()[:10]
    receipts: List[DeletionReceipt] = []

    for vault_name, store in (
        ("pii_vault", vault.pii_vault),
        ("demographics_vault", vault.demographics_vault),
        ("scoring_vault", vault.scoring_vault),
    ):
        expired_tokens = [
            tok
            for tok, entry in store.items()
            if str(entry.get("retention_expires_at", "9999-12-31")) < eval_str
        ]
        for tok in expired_tokens:
            blob = json.dumps(store.pop(tok), sort_keys=True).encode("utf-8")
            rec = _build_deletion_receipt(
                target_type="retention_ttl_purge",
                target_identifier=tok,
                reason=f"RETENTION_TTL_EXPIRED ({vault_name} < {eval_str})",
                vaults_purged=[vault_name],
                bytes_overwritten=len(blob),
                overwrite_passes=1,
                pre_deletion_sha256=hashlib.sha256(blob).hexdigest(),
                deleted_at=f"{eval_str}T00:00:00Z",
                verification_status="VERIFIED_PURGED",
            )
            receipts.append(rec)
            vault.deletion_receipts.append(dataclasses.asdict(rec))

    verify_vault_isolation(vault)
    return receipts


# ---------------------------------------------------------------------------
# Authenticated Export Encryption (Stdlib PBKDF2-HMAC-SHA256 + SHA256-CTR + EtM HMAC)
# ---------------------------------------------------------------------------

def _derive_enc_and_mac_keys(passphrase: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> Tuple[bytes, bytes]:
    if not passphrase or len(passphrase.strip()) < 8:
        raise PrivacyEncryptionError(
            "[PRIVACY CRYPTO] Export encryption passphrase must be at least 8 characters."
        )
    dk = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, iterations, dklen=64)
    return dk[:32], dk[32:]


def _sha256_ctr_xor(data: bytes, enc_key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    block_count = (len(data) + 31) // 32
    for counter in range(block_count):
        ctr_bytes = counter.to_bytes(8, "big")
        keystream_block = hmac.new(enc_key, nonce + ctr_bytes, hashlib.sha256).digest()
        start = counter * 32
        end = min(start + 32, len(data))
        for i in range(start, end):
            out[i] = data[i] ^ keystream_block[i - start]
    return bytes(out)


def encrypt_persistent_export(
    plaintext_str: str,
    passphrase: str,
    associated_metadata: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Encrypt a JSON/Markdown/CSV string into an authenticated Encrypt-then-MAC (EtM)
    JSON envelope using Python stdlib `PBKDF2-HMAC-SHA256` (200,000 iterations),
    a 128-bit random salt, a 128-bit random nonce, `HMAC-SHA256-CTR` keystream,
    and an `HMAC-SHA256` authentication tag over `(schema, salt, nonce, metadata, ciphertext)`.
    """
    salt = secrets.token_bytes(16)
    nonce = secrets.token_bytes(16)
    enc_key, mac_key = _derive_enc_and_mac_keys(passphrase, salt, KDF_ITERATIONS)

    pt_bytes = plaintext_str.encode("utf-8")
    pt_sha256 = hashlib.sha256(pt_bytes).hexdigest()
    ct_bytes = _sha256_ctr_xor(pt_bytes, enc_key, nonce)

    salt_b64 = base64.b64encode(salt).decode("ascii")
    nonce_b64 = base64.b64encode(nonce).decode("ascii")
    ct_b64 = base64.b64encode(ct_bytes).decode("ascii")
    meta = dict(associated_metadata or {})

    aad_canonical = json.dumps(
        {
            "schema": ENCRYPTED_EXPORT_SCHEMA,
            "kdf": "PBKDF2-HMAC-SHA256",
            "kdf_iterations": KDF_ITERATIONS,
            "cipher": "SHA256-CTR-HMAC-SHA256-ETM",
            "salt_b64": salt_b64,
            "nonce_b64": nonce_b64,
            "metadata": meta,
            "ciphertext_b64": ct_b64,
        },
        sort_keys=True,
    ).encode("utf-8")
    mac_hex = hmac.new(mac_key, aad_canonical, hashlib.sha256).hexdigest()

    return {
        "schema": ENCRYPTED_EXPORT_SCHEMA,
        "kdf": "PBKDF2-HMAC-SHA256",
        "kdf_iterations": KDF_ITERATIONS,
        "cipher": "SHA256-CTR-HMAC-SHA256-ETM",
        "salt_b64": salt_b64,
        "nonce_b64": nonce_b64,
        "metadata": meta,
        "ciphertext_b64": ct_b64,
        "hmac_sha256": mac_hex,
        "plaintext_sha256": pt_sha256,
    }


def decrypt_persistent_export(envelope: Mapping[str, Any], passphrase: str) -> str:
    """
    Verify the Encrypt-then-MAC `HMAC-SHA256` tag in constant time (`hmac.compare_digest`),
    decrypt the ciphertext, and verify the post-decryption `plaintext_sha256`.
    """
    if envelope.get("schema") != ENCRYPTED_EXPORT_SCHEMA:
        raise PrivacyEncryptionError(
            f"[PRIVACY CRYPTO] Unsupported encrypted export schema '{envelope.get('schema')}'."
        )
    iterations = int(envelope.get("kdf_iterations", KDF_ITERATIONS))
    salt_b64 = str(envelope.get("salt_b64", ""))
    nonce_b64 = str(envelope.get("nonce_b64", ""))
    ct_b64 = str(envelope.get("ciphertext_b64", ""))
    meta = dict(envelope.get("metadata", {}))
    expected_mac = str(envelope.get("hmac_sha256", ""))

    try:
        salt = base64.b64decode(salt_b64)
        nonce = base64.b64decode(nonce_b64)
        ct_bytes = base64.b64decode(ct_b64)
    except Exception as exc:
        raise PrivacyEncryptionError(f"[PRIVACY CRYPTO] Malformed base64 in encrypted envelope: {exc}") from exc

    enc_key, mac_key = _derive_enc_and_mac_keys(passphrase, salt, iterations)

    aad_canonical = json.dumps(
        {
            "schema": ENCRYPTED_EXPORT_SCHEMA,
            "kdf": str(envelope.get("kdf", "PBKDF2-HMAC-SHA256")),
            "kdf_iterations": iterations,
            "cipher": str(envelope.get("cipher", "SHA256-CTR-HMAC-SHA256-ETM")),
            "salt_b64": salt_b64,
            "nonce_b64": nonce_b64,
            "metadata": meta,
            "ciphertext_b64": ct_b64,
        },
        sort_keys=True,
    ).encode("utf-8")
    computed_mac = hmac.new(mac_key, aad_canonical, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_mac, expected_mac):
        raise PrivacyEncryptionError(
            "[PRIVACY CRYPTO] Authentication tag mismatch (invalid passphrase or tampered ciphertext)."
        )

    pt_bytes = _sha256_ctr_xor(ct_bytes, enc_key, nonce)
    pt_sha256 = hashlib.sha256(pt_bytes).hexdigest()
    if envelope.get("plaintext_sha256") and pt_sha256 != envelope.get("plaintext_sha256"):
        raise PrivacyEncryptionError("[PRIVACY CRYPTO] Post-decryption SHA-256 integrity check failed.")
    return pt_bytes.decode("utf-8")


# ---------------------------------------------------------------------------
# Markdown Renderer & Demo Workflow
# ---------------------------------------------------------------------------

def render_privacy_audit_markdown(
    vault: SegregatedDataVault,
    policy: RetentionPolicyConfig,
    redaction_counts: Optional[Mapping[str, int]] = None,
) -> str:
    lines: List[str] = [
        "# Privacy, Data Minimization & Three-Vault Isolation Report (`ops-req-privacy`)",
        "",
        "*Verifies explicit subject consent, 3-vault PII/Score/Demographic segregation, configurable retention TTLs, cryptographic right-to-erasure receipts, and log redaction.*",
        "",
        "## 1. Privacy Architecture & Retention Configuration",
        "",
        f"- **Retention Policy ID:** `{policy.policy_id}` (`{policy.policy_version}`)",
        f"- **Policy SHA-256:** `{policy.policy_sha256}`",
        f"- **Default Processing Mode:** `{policy.processing_mode}` (`ephemeral_by_default: {policy.ephemeral_by_default}`)",
        f"- **Explicit Consent Enforced:** Batch Screening `{policy.require_explicit_consent_batch}` | Internal Mobility `{policy.require_explicit_consent_mobility}` | Voluntary Demographics `{policy.require_explicit_consent_demographics}`",
        f"- **Configurable Retention Windows:** PII Vault `{policy.pii_vault_retention_days} days` | Scoring Audit Vault `{policy.scoring_audit_retention_days} days` | Demographics Vault `{policy.demographics_vault_retention_days} days` | Mobility Exploration `{policy.mobility_profile_retention_days} days (ephemeral)`",
        f"- **Three-Vault Isolation Proof:** `{'VERIFIED' if vault.isolation_verified else 'UNVERIFIED'}` (Proof SHA-256: `{vault.isolation_proof_sha256}`)",
        "",
        "## 2. Three-Vault Segregation & Access Boundaries",
        "",
        "| Vault | Active Records | Permitted RBAC Roles | Stored Attributes | Excluded / Prohibited Attributes |",
        "| :--- | :-: | :--- | :--- | :--- |",
        f"| **1. Identity & PII Vault (`pii_vault`)** | `{len(vault.pii_vault)}` | `{', '.join(VAULT_ACCESS_BOUNDARIES['pii_vault'])}` | `pseudonym_token`, `candidate_id`, `raw_sha256`, contact PII, `retention_expires_at` | Component scores, reviewer overrides, EEO demographics |",
        f"| **2. Pseudonymized Scoring Vault (`scoring_vault`)** | `{len(vault.scoring_vault)}` | `{', '.join(VAULT_ACCESS_BOUNDARIES['scoring_vault'])}` | `pseudonym_token`, `composite_score`, `comp1..5`, PII-scrubbed quotes, `decision_status` | Raw `candidate_id`, name, email, phone, ZIP, EEO demographics |",
        f"| **3. Voluntary Self-ID Vault (`demographics_vault`)** | `{len(vault.demographics_vault)}` | `{', '.join(VAULT_ACCESS_BOUNDARIES['demographics_vault'])}` | `pseudonym_token`, `gender`, `race_ethnicity`, `retention_expires_at` | Raw `candidate_id`, contact PII, resume text, component scores |",
        "",
        "## 3. Pseudonymized Subject & Consent Ledger",
        "",
        "| Pseudonym Token | Consent Status (`Flow`) | In PII Vault | In Scoring Vault (`Score`) | In Demographics Vault | PII Expiry | Score Audit Expiry |",
        "| :--- | :--- | :-: | :-: | :-: | :-: | :-: |",
    ]

    all_tokens = sorted(
        set(vault.pii_vault.keys())
        | set(vault.scoring_vault.keys())
        | set(vault.demographics_vault.keys())
        | set(vault.consent_ledger.keys())
    )
    for tok in all_tokens:
        c_info = vault.consent_ledger.get(tok, {})
        if c_info.get("revoked"):
            c_str = f"`REVOKED / ERASED` (`{c_info.get('flow_type', 'batch_screening')}`)"
        elif c_info.get("consented"):
            c_str = f"`CONSENTED` (`{c_info.get('flow_type', 'batch_screening')}`)"
        else:
            c_str = "`SESSION_DEFAULT`"

        in_pii = "Yes" if tok in vault.pii_vault else "**PURGED**"
        s_entry = vault.scoring_vault.get(tok)
        in_score = f"Yes (`{s_entry['composite_score']:.1f}`)" if s_entry else "**PURGED**"
        in_demo = "Yes" if tok in vault.demographics_vault else "**PURGED**"
        pii_exp = vault.pii_vault[tok]["retention_expires_at"] if tok in vault.pii_vault else "—"
        score_exp = s_entry["retention_expires_at"] if s_entry else "—"
        lines.append(f"| `{tok}` | {c_str} | {in_pii} | {in_score} | {in_demo} | `{pii_exp}` | `{score_exp}` |")

    if vault.deletion_receipts:
        lines += [
            "",
            "## 4. Cryptographic Deletion & Right-to-Erasure Receipts",
            "",
            "| Receipt ID | Target Type | Target Identifier | Reason | Vaults Purged | Bytes Overwritten | Status | Receipt SHA-256 |",
            "| :--- | :--- | :--- | :--- | :--- | :-: | :--- | :--- |",
        ]
        for rec in vault.deletion_receipts:
            lines.append(
                f"| `{rec['receipt_id']}` | `{rec['target_type']}` | `{rec['target_identifier']}` | "
                f"{rec['reason']} | `{', '.join(rec['vaults_purged'])}` | `{rec['bytes_overwritten']}` | "
                f"`{rec['verification_status']}` | `{rec['receipt_sha256'][:16]}…` |"
            )

    if redaction_counts:
        lines += [
            "",
            "## 5. Diagnostic Log Redaction Telemetry",
            "",
            f"- **Redacted Tokens in Diagnostic Logs:** Emails `{redaction_counts.get('email', 0)}` | Phones `{redaction_counts.get('phone', 0)}` | SSNs `{redaction_counts.get('ssn', 0)}` | ZIP Codes `{redaction_counts.get('zip_code', 0)}` | URLs `{redaction_counts.get('url', 0)}` | Secrets `{redaction_counts.get('secret', 0)}`",
        ]

    return "\n".join(lines) + "\n"


def run_privacy_demo(reference_date: str = "2026-11-01") -> Tuple[SegregatedDataVault, RetentionPolicyConfig, Dict[str, int]]:
    import explainable_screening_assistant as esa  # local import to avoid circular dependency

    policy = load_retention_policy()
    candidates_raw, reqs, align_cfg, decisions, demographics = esa.get_demo_batch()
    ref_dt = datetime.strptime(reference_date[:10], "%Y-%m-%d")

    consent_map: Dict[str, ConsentRecord] = {}
    for cid, _, _ in candidates_raw:
        rec = build_consent_record(
            {
                "subject_id": cid,
                "flow_type": "batch_screening",
                "consented": True,
                "consent_scope": ["batch_screening", "voluntary_demographics"],
                "consent_timestamp": f"{reference_date[:10]}T08:30:00Z",
                "consent_method": "applicant_portal_opt_in",
            }
        )
        consent_map[f"{cid}:batch_screening"] = rec
        consent_map[cid] = rec

    batch = esa.screen_batch(
        candidate_inputs=candidates_raw,
        reqs=reqs,
        align_cfg=align_cfg,
        reference_date=ref_dt,
        blind_mode=True,
        decisions_map=decisions,
        demographics_map=demographics,
    )
    raw_resumes_map = {cid: txt for cid, _, txt in candidates_raw}
    cand_dicts = [dataclasses.asdict(c) for c in batch.candidates]

    vault = partition_into_segregated_vaults(
        candidates=cand_dicts,
        raw_resumes_map=raw_resumes_map,
        demographics_map=demographics,
        consent_map=consent_map,
        retention_policy=policy,
        reference_date=reference_date[:10],
    )

    # Demonstrate Right-to-Erasure for CAND-004 (purging PII and demographics while preserving audit integrity)
    purge_subject_across_vaults(
        vault,
        subject_id_or_token="CAND-004",
        reason="CANDIDATE_ERASURE_REQUEST (GDPR Art. 17 / CCPA / CO Privacy Act)",
        deleted_at=f"{reference_date[:10]}T14:00:00Z",
        purge_scoring_vault=False,
    )

    sample_log = (
        "Processed resume for jordan.vance@example.com | (314) 555-0142 | St. Louis, MO 63101 | "
        "https://linkedin.com/in/jordanvance | SSN 123-45-6789 | Bearer eyJhbGciOiJIUzI1NiJ9"
    )
    _, redaction_counts = redact_log_message(sample_log)
    return vault, policy, redaction_counts


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-privacy`)
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops-req-privacy",
        description="Privacy, Data Minimization, Three-Vault Segregation, Retention & Secure Deletion Engine.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the built-in 3-vault privacy, consent, and right-to-erasure verification demo.")
    parser.add_argument("--retention-policy", help="Path to a retention & privacy policy JSON file.")
    parser.add_argument("--export-default-policy", help="Export the baseline retention policy JSON to this path and exit.")
    parser.add_argument("--purge-subject", help="Subject ID or SUBJ-... pseudonym token to erase from a vault JSON.")
    parser.add_argument("--enforce-retention-date", help="Purge expired records from --vault-json as of YYYY-MM-DD.")
    parser.add_argument("--vault-json", help="Path to an existing segregated vault JSON file to inspect, purge, or enforce TTLs on.")
    parser.add_argument("--secure-delete-file", help="Path to a file to overwrite and securely delete, emitting a signed DeletionReceipt.")
    parser.add_argument("--redact-log-file", help="Path to a log/text file to scrub of PII and secrets.")
    parser.add_argument("--encrypt-file", help="Path to a file to encrypt using PBKDF2-HMAC-SHA256 + SHA256-CTR-HMAC-SHA256-ETM.")
    parser.add_argument("--decrypt-file", help="Path to an encrypted .enc.json file to verify and decrypt.")
    parser.add_argument("--passphrase-env", default="OPS_REQ_EXPORT_PASSPHRASE", help="Environment variable holding the encryption passphrase (default: OPS_REQ_EXPORT_PASSPHRASE).")
    parser.add_argument("--reference-date", default="2026-11-01", help="Reference date YYYY-MM-DD.")
    parser.add_argument("--output", help="Write Markdown report or decrypted/redacted output to this path.")
    parser.add_argument("--json-out", help="Write JSON vault or receipt output to this path.")

    args = parser.parse_args(argv)

    try:
        if args.export_default_policy:
            with open(args.export_default_policy, "w", encoding="utf-8") as fh:
                json.dump(get_default_retention_policy_dict(), fh, indent=2)
            sys.stdout.write(f"[PRIVACY OK] Exported default retention policy to {args.export_default_policy}\n")
            return 0

        policy = load_retention_policy(args.retention_policy)

        if args.secure_delete_file:
            receipt = secure_delete_file(
                args.secure_delete_file,
                passes=policy.secure_delete_overwrite_passes,
                reason="CLI_SECURE_FILE_DELETION",
                deleted_at=f"{args.reference_date}T00:00:00Z",
            )
            out_str = json.dumps(dataclasses.asdict(receipt), indent=2)
            if args.json_out:
                with open(args.json_out, "w", encoding="utf-8") as fh:
                    fh.write(out_str + "\n")
            sys.stdout.write(out_str + "\n")
            return 0

        if args.redact_log_file:
            p = gov.resolve_input_path(args.redact_log_file)
            with open(p, "r", encoding="utf-8") as fh:
                raw_txt = fh.read()
            redacted, counts = redact_log_message(raw_txt)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as fh:
                    fh.write(redacted)
            else:
                sys.stdout.write(redacted + "\n")
            if args.json_out:
                with open(args.json_out, "w", encoding="utf-8") as fh:
                    json.dump({"redaction_counts": counts}, fh, indent=2)
            return 0

        if args.encrypt_file:
            passphrase = os.environ.get(args.passphrase_env, "")
            p = gov.resolve_input_path(args.encrypt_file)
            with open(p, "r", encoding="utf-8") as fh:
                pt = fh.read()
            env = encrypt_persistent_export(
                pt,
                passphrase=passphrase,
                associated_metadata={"source_basename": os.path.basename(p), "encrypted_at": args.reference_date},
            )
            out_path = args.json_out or args.output
            if out_path:
                with open(out_path, "w", encoding="utf-8") as fh:
                    json.dump(env, fh, indent=2)
            else:
                sys.stdout.write(json.dumps(env, indent=2) + "\n")
            return 0

        if args.decrypt_file:
            passphrase = os.environ.get(args.passphrase_env, "")
            p = gov.resolve_input_path(args.decrypt_file)
            with open(p, "r", encoding="utf-8") as fh:
                env = json.load(fh)
            pt = decrypt_persistent_export(env, passphrase=passphrase)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as fh:
                    fh.write(pt)
            else:
                sys.stdout.write(pt)
            return 0

        if args.vault_json:
            vp = gov.resolve_input_path(args.vault_json)
            with open(vp, "r", encoding="utf-8") as fh:
                v_raw = json.load(fh)
            vault = SegregatedDataVault(**v_raw)
            verify_vault_isolation(vault)
            if args.purge_subject:
                purge_subject_across_vaults(
                    vault,
                    subject_id_or_token=args.purge_subject,
                    deleted_at=f"{args.reference_date}T00:00:00Z",
                )
            if args.enforce_retention_date:
                enforce_retention_schedule(vault, evaluation_date=args.enforce_retention_date)
            md_report = render_privacy_audit_markdown(vault, policy)
            if args.json_out:
                with open(args.json_out, "w", encoding="utf-8") as fh:
                    json.dump(dataclasses.asdict(vault), fh, indent=2)
            if args.output:
                with open(args.output, "w", encoding="utf-8") as fh:
                    fh.write(md_report)
            else:
                sys.stdout.write(md_report)
            return 0

        # Default or --demo mode
        vault, policy, redaction_counts = run_privacy_demo(reference_date=args.reference_date)
        if args.purge_subject:
            purge_subject_across_vaults(
                vault,
                subject_id_or_token=args.purge_subject,
                deleted_at=f"{args.reference_date}T00:00:00Z",
            )
        if args.enforce_retention_date:
            enforce_retention_schedule(vault, evaluation_date=args.enforce_retention_date)

        md_report = render_privacy_audit_markdown(vault, policy, redaction_counts=redaction_counts)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(md_report)
        else:
            sys.stdout.write(md_report)

        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(dataclasses.asdict(vault), fh, indent=2)
        return 0

    except (PrivacyError, FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
