#!/usr/bin/env python3
"""
ops-requisition-auditor: Human-Approval & Anti-Automation Governance Middleware
================================================================================
Prevents misuse of heuristic alignment scores as autonomous hiring decisions by enforcing:
  1. `PENDING_HUMAN_REVIEW` as the mandatory initial disposition state for every candidate.
  2. Hard blocking of CLI flags, API parameters, and environment variables that attempt
     auto-rejection, auto-advancement, or autonomous hiring recommendations.
  3. Strict schema validation for human dispositions (`ADVANCE`, `HOLD`, `DO_NOT_ADVANCE`):
     - Verified human `reviewer` identity (rejects bot/system/automated actor IDs)
     - Authorized `reviewer_role` via Role-Based Access Control (RBAC)
     - Valid ISO-8601 `timestamp`
     - Mandatory human `override_reason` whenever a disposition diverges from the score
       tier OR records a `DO_NOT_ADVANCE` decision
  4. Cryptographic immutability: seals every decision and batch audit with a SHA-256
     hash chain (`decision_sha256`, `previous_entry_sha256`, `entry_sha256`) and provides
     `verify_audit_log_integrity()` to detect post-sign-off tampering.
  5. Mandatory logging of every configuration hash and rubric version (`engine_version`,
     `jd_alignment_rubric_version`, `rqs_rubric_version`, `governance_policy_version`,
     `config_sha256`).

Runs on the Python 3.10+ standard library only (zero external dependencies).
"""
from __future__ import annotations

import argparse
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

ENGINE_VERSION = "1.6.0"
JD_ALIGNMENT_RUBRIC_VERSION = "jd-align-1.6"
RQS_RUBRIC_VERSION = "rqs-0.2"
GOVERNANCE_POLICY_VERSION = "gov-hitl-1.0"

MANDATORY_INITIAL_STATE = "PENDING_HUMAN_REVIEW"
ALLOWED_DISPOSITION_STATES = {
    "PENDING_HUMAN_REVIEW",
    "ADVANCE",
    "HOLD",
    "DO_NOT_ADVANCE",
}

# Neutral evidence-tier labels (never phrased as autonomous hiring recommendations)
EVIDENCE_TIER_HIGH = "Evidence Tier A (>=80.0): High Requirement Alignment — Human Review Required"
EVIDENCE_TIER_MID = "Evidence Tier B (65.0-79.9): Partial Requirement Alignment — Human Review Required"
EVIDENCE_TIER_LOW = "Evidence Tier C (<65.0 or Unverified Gate): Evidence Gaps Detected — Human Review Required"

# Prohibited CLI flags & API keys that attempt autonomous decisioning or hiring recommendations
PROHIBITED_CLI_FLAGS = {
    "--auto-reject",
    "--auto-advance",
    "--auto-decide",
    "--autonomous-mode",
    "--autonomous-decision",
    "--hiring-recommendation",
    "--auto-disposition",
    "--skip-human-review",
}

PROHIBITED_API_KEYS = {
    "auto_reject",
    "auto_advance",
    "auto_decide",
    "autonomous_mode",
    "autonomous_decision",
    "hiring_recommendation",
    "auto_disposition",
    "skip_human_review",
}

# Prohibited environment variables that indicate unattended automated rejection/decisioning
PROHIBITED_ENV_VARS = (
    "OPS_REQ_AUTO_REJECT",
    "OPS_REQ_AUTO_ADVANCE",
    "OPS_REQ_AUTONOMOUS_MODE",
    "OPS_REQ_SKIP_HUMAN_REVIEW",
    "ATS_AUTO_REJECT",
    "ATS_AUTO_DECIDE",
)

# Forbidden non-human reviewer identities
PROHIBITED_BOT_REVIEWER_PATTERNS = re.compile(
    r"^(?:auto|automated|bot|system|ai|llm|agent|cron|pipeline|daemon|script|unattended|none|null|n/a|default)"
    r"(?:[_\-\s0-9].*)?$",
    re.IGNORECASE,
)

ISO_8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)

# Role-Based Access Control (RBAC) matrix
RBAC_ROLE_PERMISSIONS: Dict[str, Dict[str, Any]] = {
    "recruiter": {
        "can_score": True,
        "can_record_disposition": True,
        "can_override": True,
        "allowed_statuses": {"PENDING_HUMAN_REVIEW", "ADVANCE", "HOLD", "DO_NOT_ADVANCE"},
    },
    "ta_lead": {
        "can_score": True,
        "can_record_disposition": True,
        "can_override": True,
        "allowed_statuses": {"PENDING_HUMAN_REVIEW", "ADVANCE", "HOLD", "DO_NOT_ADVANCE"},
    },
    "hiring_manager": {
        "can_score": True,
        "can_record_disposition": True,
        "can_override": True,
        "allowed_statuses": {"PENDING_HUMAN_REVIEW", "ADVANCE", "HOLD", "DO_NOT_ADVANCE"},
    },
    "wfm_director": {
        "can_score": True,
        "can_record_disposition": True,
        "can_override": True,
        "allowed_statuses": {"PENDING_HUMAN_REVIEW", "ADVANCE", "HOLD", "DO_NOT_ADVANCE"},
    },
    "hr_compliance": {
        "can_score": True,
        "can_record_disposition": True,
        "can_override": True,
        "allowed_statuses": {"PENDING_HUMAN_REVIEW", "ADVANCE", "HOLD", "DO_NOT_ADVANCE"},
    },
    "mobility_counselor": {
        "can_score": True,
        "can_record_disposition": False,
        "can_override": False,
        "allowed_statuses": {"PENDING_HUMAN_REVIEW"},
    },
    "read_only_auditor": {
        "can_score": True,
        "can_record_disposition": False,
        "can_override": False,
        "allowed_statuses": {"PENDING_HUMAN_REVIEW"},
    },
    "candidate": {
        "can_score": True,
        "can_record_disposition": False,
        "can_override": False,
        "allowed_statuses": {"PENDING_HUMAN_REVIEW"},
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class GovernanceError(Exception):
    """Base exception for governance, anti-automation, RBAC, and immutability violations."""


class AutomationPolicyViolationError(GovernanceError):
    """Raised when a caller attempts to enable auto-rejection, auto-advancement, or autonomous hiring decisions."""


class GovernanceValidationError(GovernanceError):
    """Raised when a human decision record or configuration fails schema validation."""


class RBACPermissionError(GovernanceError):
    """Raised when an unauthorized role attempts to record or override a human disposition."""


class EnvironmentSafeguardError(GovernanceError):
    """Raised when environment variables violate human-in-the-loop safeguards."""


class AuditImmutabilityError(GovernanceError):
    """Raised when a sealed decision record or hash-chained audit trail fails tamper verification."""


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GovernanceVersionManifest:
    tool_name: str = "ops-requisition-auditor"
    engine_version: str = ENGINE_VERSION
    jd_alignment_rubric_version: str = JD_ALIGNMENT_RUBRIC_VERSION
    rqs_rubric_version: str = RQS_RUBRIC_VERSION
    governance_policy_version: str = GOVERNANCE_POLICY_VERSION
    environment_mode: str = "HUMAN_IN_THE_LOOP_ENFORCED"
    auto_rejection_permitted: bool = False
    default_disposition_state: str = MANDATORY_INITIAL_STATE
    config_sha256: str = ""


@dataclass(frozen=True)
class ValidatedHumanDecision:
    candidate_id: str
    status: str = MANDATORY_INITIAL_STATE
    reviewer: str = ""
    reviewer_role: str = "recruiter"
    timestamp: str = ""
    override_reason: str = ""
    is_override: bool = False
    config_sha256: str = ""
    score_at_decision: Optional[float] = None
    knockout_passed_at_decision: Optional[bool] = None
    decision_sha256: str = ""
    secondary_approver_id: str = ""
    secondary_approver_role: str = ""


@dataclass(frozen=True)
class ImmutableAuditEntry:
    sequence_index: int
    candidate_id: str
    composite_score: float
    knockout_passed: bool
    evidence_tier: str
    decision: ValidatedHumanDecision
    version_manifest: GovernanceVersionManifest
    previous_entry_sha256: str
    entry_sha256: str


# ---------------------------------------------------------------------------
# 1. Anti-Automation & Environment-Level Safeguards
# ---------------------------------------------------------------------------

def neutral_evidence_tier_for(
    composite_score: float,
    knockout_passed: bool,
    threshold: float = 80.0,
) -> str:
    """
    Returns a neutral evidence-alignment tier label that NEVER frames a heuristic
    score as an autonomous hiring recommendation or rejection.
    """
    if composite_score >= threshold and knockout_passed:
        return EVIDENCE_TIER_HIGH
    if composite_score >= 65.0 and knockout_passed:
        return EVIDENCE_TIER_MID
    return EVIDENCE_TIER_LOW


def enforce_anti_automation_cli(argv: Optional[Sequence[str]]) -> None:
    """
    Scans raw CLI arguments before `argparse` runs and blocks any flag that attempts
    to enable auto-rejection, auto-advancement, or autonomous hiring recommendations.
    """
    if not argv:
        return
    for arg in argv:
        flag = arg.split("=", 1)[0].strip().lower()
        if flag in PROHIBITED_CLI_FLAGS:
            raise AutomationPolicyViolationError(
                f"[GOVERNANCE GATE] BLOCKED: CLI flag '{flag}' is prohibited. "
                "ops-requisition-auditor is a human-in-the-loop evidence aid and never permits "
                "autonomous hiring recommendations, auto-rejection, or auto-advancement."
            )


def enforce_anti_automation_api_params(params: Optional[Mapping[str, Any]]) -> None:
    """
    Scans API/config dictionaries and blocks prohibited automation parameters.
    """
    if not params:
        return
    for key, val in params.items():
        k_norm = str(key).strip().lower().replace("-", "_")
        if k_norm in PROHIBITED_API_KEYS and bool(val):
            raise AutomationPolicyViolationError(
                f"[GOVERNANCE GATE] BLOCKED: API/config parameter '{key}={val}' is prohibited. "
                "Scores cannot be used for autonomous hiring decisions or auto-rejection."
            )


def enforce_environment_safeguards(
    env: Optional[Mapping[str, str]] = None,
    active_role: Optional[str] = None,
) -> str:
    """
    Verifies environment-level safeguards:
      1. Rejects any prohibited auto-reject / autonomous decision environment variables.
      2. Resolves and validates the active RBAC role (`OPS_REQ_REVIEWER_ROLE` or `active_role`).
    """
    environ = env if env is not None else os.environ
    for var_name in PROHIBITED_ENV_VARS:
        raw_val = str(environ.get(var_name, "")).strip().lower()
        if raw_val in ("1", "true", "yes", "on", "enabled"):
            raise EnvironmentSafeguardError(
                f"[GOVERNANCE GATE] BLOCKED: Environment variable '{var_name}={environ.get(var_name)}' "
                "violates anti-automation policy. Unattended auto-rejection and autonomous decisioning are disabled."
            )

    role = str(active_role or environ.get("OPS_REQ_REVIEWER_ROLE") or "recruiter").strip().lower()
    if role not in RBAC_ROLE_PERMISSIONS:
        allowed = ", ".join(sorted(RBAC_ROLE_PERMISSIONS.keys()))
        raise RBACPermissionError(
            f"[GOVERNANCE GATE] Unknown reviewer role '{role}'. Allowed roles: {allowed}."
        )
    return role


# ---------------------------------------------------------------------------
# 2. Configuration Version & Schema Validation
# ---------------------------------------------------------------------------

def build_version_manifest(
    reqs: Sequence[Any],
    align_cfg: Any,
) -> GovernanceVersionManifest:
    """
    Computes a deterministic SHA-256 fingerprint of the active requirement list,
    alignment configuration, and rubric versions.
    """
    req_dicts = [
        dataclasses.asdict(r) if dataclasses.is_dataclass(r) else dict(r)
        for r in reqs
    ]
    align_dict = (
        dataclasses.asdict(align_cfg)
        if dataclasses.is_dataclass(align_cfg)
        else dict(align_cfg or {})
    )
    payload = json.dumps(
        {
            "engine_version": ENGINE_VERSION,
            "jd_alignment_rubric_version": JD_ALIGNMENT_RUBRIC_VERSION,
            "rqs_rubric_version": RQS_RUBRIC_VERSION,
            "governance_policy_version": GOVERNANCE_POLICY_VERSION,
            "requirements": req_dicts,
            "alignment": align_dict,
        },
        sort_keys=True,
    )
    cfg_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return GovernanceVersionManifest(config_sha256=cfg_hash)


def compute_decision_sha256(
    candidate_id: str,
    status: str,
    reviewer: str,
    reviewer_role: str,
    timestamp: str,
    override_reason: str,
    is_override: bool,
    config_sha256: str,
    score_at_decision: Optional[float],
    knockout_passed_at_decision: Optional[bool],
) -> str:
    payload = json.dumps(
        {
            "candidate_id": candidate_id,
            "status": status,
            "reviewer": reviewer,
            "reviewer_role": reviewer_role,
            "timestamp": timestamp,
            "override_reason": override_reason,
            "is_override": is_override,
            "config_sha256": config_sha256,
            "score_at_decision": round(score_at_decision, 2) if score_at_decision is not None else None,
            "knockout_passed_at_decision": knockout_passed_at_decision,
            "governance_policy_version": GOVERNANCE_POLICY_VERSION,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_and_seal_decision(
    candidate_id: str,
    raw_decision: Optional[Mapping[str, Any]],
    composite_score: Optional[float] = None,
    knockout_passed: Optional[bool] = None,
    shortlist_threshold: float = 80.0,
    config_sha256: str = "",
    enforced_role: Optional[str] = None,
) -> ValidatedHumanDecision:
    """
    Validates a candidate disposition record against strict human-in-the-loop governance rules
    and returns an immutable, cryptographically sealed `ValidatedHumanDecision`:

      1. If `raw_decision` is None or empty, enforces `PENDING_HUMAN_REVIEW`.
      2. Rejects any unknown status or prohibited automation key.
      3. For any non-pending disposition (`ADVANCE`, `HOLD`, `DO_NOT_ADVANCE`):
         - Requires a non-empty human `reviewer` ID (blocks bot/system/auto strings).
         - Validates `reviewer_role` against `RBAC_ROLE_PERMISSIONS` (`can_record_disposition=True`).
         - Requires a valid ISO-8601 `timestamp`.
         - Detects whether the decision is a score/gate override (`is_override=True`) and
           requires a substantive `override_reason` (>= 10 characters) on all overrides
           AND on all `DO_NOT_ADVANCE` dispositions so no candidate is ever rejected
           without a documented human reason.
    """
    if not raw_decision:
        dec_hash = compute_decision_sha256(
            candidate_id=candidate_id,
            status=MANDATORY_INITIAL_STATE,
            reviewer="",
            reviewer_role=enforced_role or "recruiter",
            timestamp="",
            override_reason="",
            is_override=False,
            config_sha256=config_sha256,
            score_at_decision=composite_score,
            knockout_passed_at_decision=knockout_passed,
        )
        return ValidatedHumanDecision(
            candidate_id=candidate_id,
            status=MANDATORY_INITIAL_STATE,
            reviewer="",
            reviewer_role=enforced_role or "recruiter",
            timestamp="",
            override_reason="",
            is_override=False,
            config_sha256=config_sha256,
            score_at_decision=composite_score,
            knockout_passed_at_decision=knockout_passed,
            decision_sha256=dec_hash,
        )

    enforce_anti_automation_api_params(raw_decision)

    status = str(raw_decision.get("status", MANDATORY_INITIAL_STATE)).strip().upper()
    if status not in ALLOWED_DISPOSITION_STATES:
        allowed_str = ", ".join(sorted(ALLOWED_DISPOSITION_STATES))
        raise GovernanceValidationError(
            f"Candidate '{candidate_id}': invalid disposition status '{status}'. "
            f"Allowed states: {allowed_str}."
        )

    role = str(enforced_role or raw_decision.get("reviewer_role") or "recruiter").strip().lower()
    if role not in RBAC_ROLE_PERMISSIONS:
        raise RBACPermissionError(
            f"Candidate '{candidate_id}': unknown reviewer_role '{role}'."
        )
    perms = RBAC_ROLE_PERMISSIONS[role]

    reviewer = str(raw_decision.get("reviewer", "")).strip()
    timestamp = str(raw_decision.get("timestamp", "")).strip()
    override_reason = str(raw_decision.get("override_reason", "")).strip()

    if status == MANDATORY_INITIAL_STATE:
        dec_hash = compute_decision_sha256(
            candidate_id=candidate_id,
            status=MANDATORY_INITIAL_STATE,
            reviewer=reviewer,
            reviewer_role=role,
            timestamp=timestamp,
            override_reason=override_reason,
            is_override=False,
            config_sha256=config_sha256,
            score_at_decision=composite_score,
            knockout_passed_at_decision=knockout_passed,
        )
        return ValidatedHumanDecision(
            candidate_id=candidate_id,
            status=MANDATORY_INITIAL_STATE,
            reviewer=reviewer,
            reviewer_role=role,
            timestamp=timestamp,
            override_reason=override_reason,
            is_override=False,
            config_sha256=config_sha256,
            score_at_decision=composite_score,
            knockout_passed_at_decision=knockout_passed,
            decision_sha256=dec_hash,
        )

    # Non-pending human disposition: enforce RBAC, human identity, timestamp, and override rationale
    if not perms["can_record_disposition"] or status not in perms["allowed_statuses"]:
        raise RBACPermissionError(
            f"Candidate '{candidate_id}': role '{role}' is not authorized to record disposition '{status}'. "
            "Only authorized human reviewer roles (recruiter, ta_lead, hiring_manager, wfm_director, hr_compliance) "
            "may transition a candidate out of PENDING_HUMAN_REVIEW."
        )

    if not reviewer or len(reviewer) < 3:
        raise GovernanceValidationError(
            f"Candidate '{candidate_id}': disposition '{status}' requires a non-empty human 'reviewer' identity."
        )
    if PROHIBITED_BOT_REVIEWER_PATTERNS.match(reviewer):
        raise AutomationPolicyViolationError(
            f"Candidate '{candidate_id}': reviewer identity '{reviewer}' matches a prohibited automated/system actor pattern. "
            "Dispositions must be signed by an individual human reviewer."
        )

    if not timestamp or not ISO_8601_RE.match(timestamp):
        raise GovernanceValidationError(
            f"Candidate '{candidate_id}': disposition '{status}' by '{reviewer}' requires a valid ISO-8601 'timestamp' "
            "(e.g., '2026-11-15T09:12:00Z')."
        )

    # Determine if this decision is an override of the evidence tier
    is_override = bool(raw_decision.get("is_override", False))
    if composite_score is not None:
        meets_tier_a = (composite_score >= shortlist_threshold) and (knockout_passed is not False)
        if status == "ADVANCE" and not meets_tier_a:
            is_override = True
        elif status == "DO_NOT_ADVANCE" and meets_tier_a:
            is_override = True

    if is_override and not perms["can_override"]:
        raise RBACPermissionError(
            f"Candidate '{candidate_id}': role '{role}' is not permitted to record an override disposition."
        )

    if (is_override or status == "DO_NOT_ADVANCE") and len(override_reason) < 10:
        raise GovernanceValidationError(
            f"Candidate '{candidate_id}': disposition '{status}' (is_override={is_override}) requires a human "
            "'override_reason' of at least 10 characters explaining the job-related rationale."
        )

    secondary_approver_id = str(raw_decision.get("secondary_approver_id") or "").strip()
    secondary_approver_role = str(raw_decision.get("secondary_approver_role") or "").strip()
    require_secondary_sod = bool(raw_decision.get("require_secondary_approver", False)) or (
        os.environ.get("OPS_REQ_REQUIRE_OVERRIDE_SOD") == "1"
    )
    if is_override and (require_secondary_sod or secondary_approver_id):
        if not secondary_approver_id or len(secondary_approver_id) < 3:
            raise GovernanceValidationError(
                f"Candidate '{candidate_id}': override disposition requires an independent 'secondary_approver_id' under Separation of Duties."
            )
        if secondary_approver_id == reviewer:
            raise GovernanceValidationError(
                f"Candidate '{candidate_id}': Separation of Duties violation — 'secondary_approver_id' ('{secondary_approver_id}') "
                f"cannot be the same individual as 'reviewer' ('{reviewer}')."
            )
        if PROHIBITED_BOT_REVIEWER_PATTERNS.match(secondary_approver_id):
            raise AutomationPolicyViolationError(
                f"Candidate '{candidate_id}': secondary_approver_id '{secondary_approver_id}' matches a prohibited automated actor pattern."
            )

    dec_hash = compute_decision_sha256(
        candidate_id=candidate_id,
        status=status,
        reviewer=reviewer,
        reviewer_role=role,
        timestamp=timestamp,
        override_reason=override_reason,
        is_override=is_override,
        config_sha256=config_sha256,
        score_at_decision=composite_score,
        knockout_passed_at_decision=knockout_passed,
    )

    return ValidatedHumanDecision(
        candidate_id=candidate_id,
        status=status,
        reviewer=reviewer,
        reviewer_role=role,
        timestamp=timestamp,
        override_reason=override_reason,
        is_override=is_override,
        config_sha256=config_sha256,
        score_at_decision=composite_score,
        knockout_passed_at_decision=knockout_passed,
        decision_sha256=dec_hash,
        secondary_approver_id=secondary_approver_id,
        secondary_approver_role=secondary_approver_role,
    )


# ---------------------------------------------------------------------------
# 3. Cryptographic Hash-Chained Audit Ledger & Tamper Verification
# ---------------------------------------------------------------------------

GENESIS_CHAIN_HASH = "0" * 64


def compute_audit_entry_sha256(
    sequence_index: int,
    candidate_id: str,
    composite_score: float,
    knockout_passed: bool,
    evidence_tier: str,
    decision_sha256: str,
    config_sha256: str,
    previous_entry_sha256: str,
) -> str:
    payload = json.dumps(
        {
            "sequence_index": sequence_index,
            "candidate_id": candidate_id,
            "composite_score": round(composite_score, 2),
            "knockout_passed": knockout_passed,
            "evidence_tier": evidence_tier,
            "decision_sha256": decision_sha256,
            "config_sha256": config_sha256,
            "engine_version": ENGINE_VERSION,
            "jd_alignment_rubric_version": JD_ALIGNMENT_RUBRIC_VERSION,
            "rqs_rubric_version": RQS_RUBRIC_VERSION,
            "governance_policy_version": GOVERNANCE_POLICY_VERSION,
            "previous_entry_sha256": previous_entry_sha256,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_immutable_audit_chain(
    entries_spec: Sequence[Dict[str, Any]],
    version_manifest: GovernanceVersionManifest,
) -> List[ImmutableAuditEntry]:
    """
    Builds a hash-chained sequence of `ImmutableAuditEntry` records where each entry's
    `entry_sha256` commits to `previous_entry_sha256`, the candidate's score, the sealed
    `decision_sha256`, and the rubric/config version manifest.
    """
    chain: List[ImmutableAuditEntry] = []
    prev_hash = GENESIS_CHAIN_HASH

    for idx, item in enumerate(entries_spec, start=1):
        cid = str(item["candidate_id"])
        score = float(item["composite_score"])
        kp = bool(item["knockout_passed"])
        tier = str(item["evidence_tier"])
        dec: ValidatedHumanDecision = item["decision"]

        entry_hash = compute_audit_entry_sha256(
            sequence_index=idx,
            candidate_id=cid,
            composite_score=score,
            knockout_passed=kp,
            evidence_tier=tier,
            decision_sha256=dec.decision_sha256,
            config_sha256=version_manifest.config_sha256,
            previous_entry_sha256=prev_hash,
        )
        entry = ImmutableAuditEntry(
            sequence_index=idx,
            candidate_id=cid,
            composite_score=score,
            knockout_passed=kp,
            evidence_tier=tier,
            decision=dec,
            version_manifest=version_manifest,
            previous_entry_sha256=prev_hash,
            entry_sha256=entry_hash,
        )
        chain.append(entry)
        prev_hash = entry_hash

    return chain


def verify_audit_chain_integrity(
    chain_data: Sequence[Mapping[str, Any]],
) -> Tuple[bool, List[str]]:
    """
    Recomputes every `decision_sha256` and `entry_sha256` link in a serialized audit chain.
    Raises `AuditImmutabilityError` if any score, decision status, reviewer ID, timestamp,
    override reason, or chain link was tampered with after sealing.
    """
    errors: List[str] = []
    prev_hash = GENESIS_CHAIN_HASH

    for idx, row in enumerate(chain_data, start=1):
        cid = str(row.get("candidate_id", ""))
        score = float(row.get("composite_score", 0.0))
        kp = bool(row.get("knockout_passed", False))
        tier = str(row.get("evidence_tier", ""))
        dec = row.get("decision") or {}
        vm = row.get("version_manifest") or {}
        cfg_sha = str(vm.get("config_sha256") or dec.get("config_sha256") or "")

        expected_dec_sha = compute_decision_sha256(
            candidate_id=str(dec.get("candidate_id", cid)),
            status=str(dec.get("status", MANDATORY_INITIAL_STATE)),
            reviewer=str(dec.get("reviewer", "")),
            reviewer_role=str(dec.get("reviewer_role", "recruiter")),
            timestamp=str(dec.get("timestamp", "")),
            override_reason=str(dec.get("override_reason", "")),
            is_override=bool(dec.get("is_override", False)),
            config_sha256=str(dec.get("config_sha256", cfg_sha)),
            score_at_decision=dec.get("score_at_decision"),
            knockout_passed_at_decision=dec.get("knockout_passed_at_decision"),
        )
        actual_dec_sha = str(dec.get("decision_sha256", ""))
        if actual_dec_sha != expected_dec_sha:
            errors.append(
                f"Entry #{idx} ('{cid}'): decision_sha256 mismatch (expected {expected_dec_sha[:16]}..., got {actual_dec_sha[:16]}...)."
            )

        recorded_prev = str(row.get("previous_entry_sha256", ""))
        if recorded_prev != prev_hash:
            errors.append(
                f"Entry #{idx} ('{cid}'): broken hash chain link (expected previous_entry_sha256={prev_hash[:16]}..., got {recorded_prev[:16]}...)."
            )

        expected_entry_sha = compute_audit_entry_sha256(
            sequence_index=int(row.get("sequence_index", idx)),
            candidate_id=cid,
            composite_score=score,
            knockout_passed=kp,
            evidence_tier=tier,
            decision_sha256=actual_dec_sha,
            config_sha256=cfg_sha,
            previous_entry_sha256=prev_hash,
        )
        actual_entry_sha = str(row.get("entry_sha256", ""))
        if actual_entry_sha != expected_entry_sha:
            errors.append(
                f"Entry #{idx} ('{cid}'): entry_sha256 mismatch (expected {expected_entry_sha[:16]}..., got {actual_entry_sha[:16]}...)."
            )

        prev_hash = actual_entry_sha

    if errors:
        raise AuditImmutabilityError(
            "[GOVERNANCE GATE] TAMPER DETECTED in audit trail:\n  - " + "\n  - ".join(errors)
        )
    return True, []


def atomic_write_json(path_str: str, payload: Any) -> None:
    """Atomically write JSON to disk using a temporary file in the target directory + os.replace()."""
    abs_target = os.path.abspath(path_str)
    parent = os.path.dirname(abs_target) or "."
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".ops_req_tmp_", suffix=".json", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, abs_target)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def append_audit_chain_to_sqlite(
    db_path: str,
    entries_spec: Sequence[Dict[str, Any]],
    version_manifest: GovernanceVersionManifest,
) -> List[ImmutableAuditEntry]:
    """
    Concurrency-safe, ACID append-only audit ledger backed by Python stdlib `sqlite3` in WAL mode.
    Chains new entries onto the latest committed `entry_sha256` inside an `IMMEDIATE` write transaction,
    with SQL triggers preventing UPDATE or DELETE on committed rows.
    """
    abs_db = os.path.abspath(db_path)
    os.makedirs(os.path.dirname(abs_db) or ".", exist_ok=True)
    conn = sqlite3.connect(abs_db, timeout=30.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=FULL;")
        conn.execute("BEGIN IMMEDIATE;")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_chain_ledger (
                sequence_index INTEGER PRIMARY KEY,
                candidate_id TEXT NOT NULL,
                composite_score REAL NOT NULL,
                knockout_passed INTEGER NOT NULL,
                evidence_tier TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                version_manifest_json TEXT NOT NULL,
                previous_entry_sha256 TEXT NOT NULL,
                entry_sha256 TEXT NOT NULL UNIQUE
            );
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_audit_ledger_update
            BEFORE UPDATE ON audit_chain_ledger
            BEGIN
                SELECT RAISE(ABORT, 'Audit ledger is append-only: UPDATE prohibited');
            END;
            """
        )
        conn.execute(
            """
            CREATE TRIGGER IF NOT EXISTS prevent_audit_ledger_delete
            BEFORE DELETE ON audit_chain_ledger
            BEGIN
                SELECT RAISE(ABORT, 'Audit ledger is append-only: DELETE prohibited');
            END;
            """
        )
        row = conn.execute(
            "SELECT sequence_index, entry_sha256 FROM audit_chain_ledger ORDER BY sequence_index DESC LIMIT 1"
        ).fetchone()
        start_seq = (int(row[0]) + 1) if row else 1
        prev_hash = str(row[1]) if row else GENESIS_CHAIN_HASH

        appended: List[ImmutableAuditEntry] = []
        for offset, item in enumerate(entries_spec):
            seq = start_seq + offset
            cid = str(item["candidate_id"])
            score = float(item["composite_score"])
            kp = bool(item["knockout_passed"])
            tier = str(item["evidence_tier"])
            dec: ValidatedHumanDecision = item["decision"]
            entry_hash = compute_audit_entry_sha256(
                sequence_index=seq,
                candidate_id=cid,
                composite_score=score,
                knockout_passed=kp,
                evidence_tier=tier,
                decision_sha256=dec.decision_sha256,
                config_sha256=version_manifest.config_sha256,
                previous_entry_sha256=prev_hash,
            )
            entry = ImmutableAuditEntry(
                sequence_index=seq,
                candidate_id=cid,
                composite_score=score,
                knockout_passed=kp,
                evidence_tier=tier,
                decision=dec,
                version_manifest=version_manifest,
                previous_entry_sha256=prev_hash,
                entry_sha256=entry_hash,
            )
            conn.execute(
                """
                INSERT INTO audit_chain_ledger (
                    sequence_index, candidate_id, composite_score, knockout_passed,
                    evidence_tier, decision_json, version_manifest_json,
                    previous_entry_sha256, entry_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    seq,
                    cid,
                    score,
                    1 if kp else 0,
                    tier,
                    json.dumps(dataclasses.asdict(dec), sort_keys=True),
                    json.dumps(dataclasses.asdict(version_manifest), sort_keys=True),
                    prev_hash,
                    entry_hash,
                ),
            )
            appended.append(entry)
            prev_hash = entry_hash

        conn.execute("COMMIT;")
        return appended
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except Exception:
            pass
        raise
    finally:
        conn.close()


def load_and_verify_sqlite_audit_chain(db_path: str) -> List[Dict[str, Any]]:
    """Read all rows from an SQLite WAL audit ledger and verify 100% cryptographic hash-chain integrity."""
    conn = sqlite3.connect(os.path.abspath(db_path), timeout=30.0)
    try:
        rows = conn.execute(
            """
            SELECT sequence_index, candidate_id, composite_score, knockout_passed,
                   evidence_tier, decision_json, version_manifest_json,
                   previous_entry_sha256, entry_sha256
            FROM audit_chain_ledger
            ORDER BY sequence_index ASC
            """
        ).fetchall()
    finally:
        conn.close()

    serialized: List[Dict[str, Any]] = []
    for r in rows:
        serialized.append(
            {
                "sequence_index": int(r[0]),
                "candidate_id": str(r[1]),
                "composite_score": float(r[2]),
                "knockout_passed": bool(r[3]),
                "evidence_tier": str(r[4]),
                "decision": json.loads(r[5]),
                "version_manifest": json.loads(r[6]),
                "previous_entry_sha256": str(r[7]),
                "entry_sha256": str(r[8]),
            }
        )
    verify_audit_chain_integrity(serialized)
    return serialized


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-governance`)
# ---------------------------------------------------------------------------

def resolve_input_path(path_str: str) -> str:
    """Resolve a path against CWD first, then fall back to the repository root if running from outside the repo."""
    if not path_str or os.path.exists(path_str):
        return path_str
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for root_candidate in (here, os.path.join(os.path.expanduser("~"), "ops-requisition-auditor")):
        candidate = os.path.join(root_candidate, path_str)
        if os.path.exists(candidate):
            return candidate
    return path_str


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    try:
        enforce_anti_automation_cli(raw_args)
        enforce_environment_safeguards()
    except GovernanceError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    parser = argparse.ArgumentParser(
        prog="ops-req-governance",
        description="Human-Approval & Anti-Automation Governance Validator: validates reviewer decision schemas, RBAC permissions, and cryptographic audit-chain integrity.",
    )
    parser.add_argument("--demo", action="store_true", help="Run a built-in demonstration of schema validation, anti-automation blocking, and hash-chain verification.")
    parser.add_argument("--validate-decisions", help="Path to a decisions.json file to validate against human-approval schema rules.")
    parser.add_argument("--verify-audit-json", help="Path to a batch_audit.json file to verify cryptographic hash-chain immutability.")
    parser.add_argument("--reviewer-role", default="recruiter", help="Active RBAC role (recruiter, ta_lead, hiring_manager, wfm_director, hr_compliance, read_only_auditor).")

    args = parser.parse_args(raw_args)

    try:
        role = enforce_environment_safeguards(active_role=args.reviewer_role)
    except GovernanceError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    if args.validate_decisions:
        dec_path = resolve_input_path(args.validate_decisions)
        with open(dec_path, "r", encoding="utf-8") as fh:
            raw_map = json.load(fh)
        try:
            sealed: Dict[str, Any] = {}
            for cid, d in raw_map.items():
                dec = validate_and_seal_decision(cid, d if isinstance(d, dict) else {}, enforced_role=role)
                sealed[cid] = dataclasses.asdict(dec)
            sys.stdout.write(
                f"[GOVERNANCE OK] Validated and sealed {len(sealed)} decision record(s) under role '{role}'.\n"
            )
            return 0
        except GovernanceError as exc:
            sys.stderr.write(f"[GOVERNANCE VALIDATION FAILED] {exc}\n")
            return 2

    if args.verify_audit_json:
        audit_path = resolve_input_path(args.verify_audit_json)
        with open(audit_path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        chain = payload.get("immutable_audit_chain", payload if isinstance(payload, list) else [])
        if not chain:
            sys.stderr.write("[GOVERNANCE ERROR] No 'immutable_audit_chain' found in JSON file.\n")
            return 2
        try:
            verify_audit_chain_integrity(chain)
            sys.stdout.write(
                f"[GOVERNANCE OK] Verified cryptographic hash chain across {len(chain)} audit entries (0 tampered records).\n"
            )
            return 0
        except AuditImmutabilityError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2

    # Default / --demo output
    manifest = GovernanceVersionManifest(config_sha256="41e6c538dacece73d58cf5bac501470da32ece3b2d102a131b297b4309b52bed")
    d1 = validate_and_seal_decision(
        "CAND-001",
        {"status": "ADVANCE", "reviewer": "recruiter_j_smith", "reviewer_role": "recruiter", "timestamp": "2026-11-15T09:00:00Z", "override_reason": ""},
        composite_score=100.0,
        knockout_passed=True,
        config_sha256=manifest.config_sha256,
    )
    d2 = validate_and_seal_decision(
        "CAND-003",
        {
            "status": "ADVANCE",
            "reviewer": "recruiter_j_smith",
            "reviewer_role": "recruiter",
            "timestamp": "2026-11-15T09:12:00Z",
            "override_reason": "Human override: strong Python/SQL/Snowflake/Demand Forecasting in recent role; verify Power BI and SAP in phone screen.",
        },
        composite_score=76.4,
        knockout_passed=True,
        config_sha256=manifest.config_sha256,
    )
    d3 = validate_and_seal_decision(
        "CAND-004",
        None,
        composite_score=33.0,
        knockout_passed=False,
        config_sha256=manifest.config_sha256,
    )
    chain = build_immutable_audit_chain(
        [
            {"candidate_id": "CAND-001", "composite_score": 100.0, "knockout_passed": True, "evidence_tier": neutral_evidence_tier_for(100.0, True), "decision": d1},
            {"candidate_id": "CAND-003", "composite_score": 76.4, "knockout_passed": True, "evidence_tier": neutral_evidence_tier_for(76.4, True), "decision": d2},
            {"candidate_id": "CAND-004", "composite_score": 33.0, "knockout_passed": False, "evidence_tier": neutral_evidence_tier_for(33.0, False), "decision": d3},
        ],
        version_manifest=manifest,
    )
    verify_audit_chain_integrity([dataclasses.asdict(e) for e in chain])
    lines = [
        "# Human-Approval & Anti-Automation Governance Verification (`ops-req-governance`)",
        "",
        f"- **Governance Policy Version:** `{manifest.governance_policy_version}`",
        f"- **Engine & Rubric Versions:** Engine `v{manifest.engine_version}` | JD Alignment `{manifest.jd_alignment_rubric_version}` | RQS `{manifest.rqs_rubric_version}`",
        f"- **Environment Mode:** `{manifest.environment_mode}` (`auto_rejection_permitted: {manifest.auto_rejection_permitted}`)",
        f"- **Default Disposition State:** `{manifest.default_disposition_state}`",
        f"- **Configuration SHA-256:** `{manifest.config_sha256}`",
        "",
        "## Cryptographically Sealed & Hash-Chained Audit Entries",
        "",
        "| Seq | Candidate | Score | Evidence Tier | Status | Reviewer (`Role`) | Override | Decision SHA-256 | Chain Entry SHA-256 |",
        "| :-: | :--- | :-: | :--- | :--- | :--- | :-: | :--- | :--- |",
    ]
    for e in chain:
        rev_str = f"`{e.decision.reviewer}` (`{e.decision.reviewer_role}`)" if e.decision.reviewer else "*(unassigned)*"
        lines.append(
            f"| {e.sequence_index} | `{e.candidate_id}` | **{e.composite_score:.1f}** | {e.evidence_tier} | "
            f"`{e.decision.status}` | {rev_str} | `{e.decision.is_override}` | "
            f"`{e.decision.decision_sha256[:12]}…` | `{e.entry_sha256[:12]}…` |"
        )
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
