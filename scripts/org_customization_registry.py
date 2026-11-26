#!/usr/bin/env python3
"""
ops-requisition-auditor: Organization-Native Customization & Approval Registry (`ops-req-org-config`)
=====================================================================================================
Enables organizations to replace generic defaults with their own governed, versioned,
and dual-approved configuration profiles:
  1. Organization-Specific Leveling Guides (`OrgLevelBand`):
     - Maps internal level codes (`L1`..`L6`, `M1`..`M3`, `D1`) and canonical level keys
       (`entry`, `level_1`, `level_2`, `level_3`, `mid`, `senior`, `lead`, `staff_principal`,
       `manager`, `senior_manager`, `director`, `executive`) to custom experience ceilings,
       scope requirements, and default degree policies.
  2. Configurable Gate Types & Equivalency Paths (`GateAndEquivalencyPolicy`):
     - Configures allowed hard knockout gate types (`degree`, `certification`, `license`,
       `security_clearance`, `work_authorization`), automatic experience-for-degree equivalency
       thresholds (`bachelor_equivalency_years`, `master_equivalency_years`), accepted
       alternative credentials, and organization-standard equivalency clauses.
  3. Custom Scoring Weights & Verdict Thresholds (`ScoringWeightsAndThresholds`):
     - Customizes the 5 JD Alignment component maximums (`comp1_keyword_max` +
       `comp2_context_max` + `comp3_alignment_max` + `comp4_knockout_max` + `comp5_schema_max`
       must sum to `100.0`), category base weights, tier multipliers, placement multipliers,
       and verdict cutoffs (`jd_strong_match_threshold`, `jd_borderline_threshold`,
       `rqs_clear_inclusive_threshold`, `rqs_needs_tuning_threshold`).
  4. Versioned Change-Approval Workflow & Separation of Duties (`ApprovalWorkflowRecord`):
     - Enforces a 4-stage lifecycle (`DRAFT` -> `PENDING_APPROVAL` -> `APPROVED` -> `SUPERSEDED`).
     - Enforces Separation of Duties (`SoD`): requires a non-bot `author_id`, a distinct
       `ta_or_comp_approver_id` (TA / Compensation Lead), and a distinct
       `compliance_or_legal_approver_id` (HR Compliance / Legal Counsel) before a profile can
       be marked `APPROVED`.
  5. Cryptographically Hash-Chained Configuration Audit History (`ConfigAuditHistoryEntry`):
     - Computes exact parameter-by-parameter diffs (`compute_org_profile_diff`) between
       versions and seals every transition in a tamper-evident SHA-256 hash chain.
  6. Effective Configuration Report Block (`render_effective_config_markdown_block`):
     - Renders the exact effective configuration table embedded in Candidate JD Alignment,
       Requisition Quality (RQS), and Explainable Batch Screening reports.

Runs on the Python 3.10+ standard library only (zero external dependencies).
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import re
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import governance_middleware as gov  # noqa: E402

ORG_PROFILE_SCHEMA_VERSION = "org-customization-profile-1.0"
DEFAULT_ORG_ID = "default-baseline-org"
DEFAULT_ORG_VERSION = "1.6.0"

ALLOWED_APPROVAL_STATES = {
    "DRAFT",
    "PENDING_APPROVAL",
    "APPROVED",
    "SUPERSEDED",
    "REJECTED",
}

ALLOWED_GATE_TYPES = {
    "degree",
    "certification",
    "license",
    "security_clearance",
    "work_authorization",
}

CANONICAL_LEVEL_KEYS = (
    "entry",
    "level_1",
    "level_2",
    "level_3",
    "mid",
    "senior",
    "lead",
    "staff_principal",
    "manager",
    "senior_manager",
    "director",
    "executive",
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class OrgCustomizationError(Exception):
    """Base error for organization profile validation, approval workflow, or audit chain failures."""


class OrgSchemaValidationError(OrgCustomizationError):
    """Raised when component weights do not sum to 100.0 or thresholds are inverted."""


class OrgApprovalWorkflowError(OrgCustomizationError):
    """Raised when an unapproved profile is used in strict mode or Separation of Duties is violated."""


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class OrgLevelBand:
    canonical_level: str                  # one of CANONICAL_LEVEL_KEYS
    org_level_code: str                   # e.g. "L2", "L3", "L4", "M1", "M2", "D1"
    display_title: str                    # e.g. "WFM Analyst II (L3)"
    min_years_expected: float
    max_benchmark_years: float            # ceiling used by RQS Gate Necessity check
    requires_people_or_budget_scope: bool
    degree_policy: str                    # "equivalency_accepted" | "optional" | "required_with_equivalency"


@dataclass
class GateAndEquivalencyPolicy:
    allowed_hard_gate_types: List[str]
    require_degree_equivalency_by_default: bool
    bachelor_equivalency_years: float
    master_equivalency_years: float
    allow_certification_substitution: bool
    accepted_alternative_credentials: List[str]
    standard_equivalency_clause: str


@dataclass
class ScoringWeightsAndThresholds:
    # Component maximums (must sum to 100.0)
    comp1_keyword_max: float = 45.0
    comp2_context_max: float = 20.0
    comp3_alignment_max: float = 15.0
    comp4_knockout_max: float = 10.0
    comp5_schema_max: float = 10.0
    # Category base weights & tier multipliers
    category_weights: Dict[str, int] = field(
        default_factory=lambda: {"hard_skill": 3, "title_scope": 2, "soft_skill": 1}
    )
    tier_multipliers: Dict[str, float] = field(
        default_factory=lambda: {"required": 2.0, "preferred": 1.0}
    )
    synonym_match_credit: float = 0.5
    placement_multipliers: Dict[str, float] = field(
        default_factory=lambda: {
            "recent_experience": 1.0,
            "historical_experience": 0.8,
            "skills_or_summary_only": 0.4,
        }
    )
    # Candidate JD Alignment verdict cutoffs
    jd_strong_match_threshold: float = 80.0
    jd_borderline_threshold: float = 65.0
    # Employer Requisition Quality (RQS) cutoffs
    rqs_clear_inclusive_threshold: float = 85.0
    rqs_needs_tuning_threshold: float = 70.0
    # Optional construct-validity decoupling: keep layout/table formatting in Pillar 2 parser confidence rather than deducting C5 points
    decouple_formatting_from_capability_score: bool = False


@dataclass
class ApprovalWorkflowRecord:
    approval_status: str                  # "DRAFT" | "PENDING_APPROVAL" | "APPROVED" | "SUPERSEDED" | "REJECTED"
    author_id: str
    created_timestamp: str
    change_rationale: str
    ta_or_comp_approver_id: str = ""
    ta_approved_timestamp: str = ""
    compliance_or_legal_approver_id: str = ""
    compliance_approved_timestamp: str = ""
    previous_version: str = ""
    previous_config_sha256: str = ""


@dataclass
class OrganizationCustomizationProfile:
    schema_version: str
    org_id: str
    org_name: str
    config_version: str
    effective_date: str
    policy_pack_id: str
    leveling_guide: Dict[str, OrgLevelBand]
    gate_and_equivalency_policy: GateAndEquivalencyPolicy
    scoring_and_thresholds: ScoringWeightsAndThresholds
    approval_workflow: ApprovalWorkflowRecord
    config_sha256: str = ""


@dataclass
class ConfigAuditHistoryEntry:
    seq: int
    org_id: str
    config_version: str
    previous_version: str
    approval_status: str
    author_id: str
    ta_or_comp_approver_id: str
    compliance_or_legal_approver_id: str
    approved_timestamp: str
    change_rationale: str
    parameter_diffs: List[Dict[str, Any]]
    config_sha256: str
    previous_entry_sha256: str
    entry_sha256: str


# ---------------------------------------------------------------------------
# Default Baseline & Sample Enterprise Organization Profiles
# ---------------------------------------------------------------------------

def get_default_org_profile_dict() -> Dict[str, Any]:
    """Return the canonical baseline configuration matching default engine behavior."""
    return {
        "schema_version": ORG_PROFILE_SCHEMA_VERSION,
        "org_id": DEFAULT_ORG_ID,
        "org_name": "Baseline Reference Configuration (Standard 100-Point Heuristic)",
        "config_version": DEFAULT_ORG_VERSION,
        "effective_date": "2026-11-01",
        "policy_pack_id": "us-employment-ai-transparency-baseline",
        "leveling_guide": {
            "entry": {"org_level_code": "L1", "display_title": "Entry / Associate Analyst (L1)", "min_years_expected": 0.0, "max_benchmark_years": 2.0, "requires_people_or_budget_scope": False, "degree_policy": "equivalency_accepted"},
            "level_1": {"org_level_code": "L2", "display_title": "Workforce / Operations Analyst I (L2)", "min_years_expected": 1.0, "max_benchmark_years": 2.0, "requires_people_or_budget_scope": False, "degree_policy": "equivalency_accepted"},
            "level_2": {"org_level_code": "L3", "display_title": "Workforce / Operations Analyst II (L3)", "min_years_expected": 2.0, "max_benchmark_years": 4.0, "requires_people_or_budget_scope": False, "degree_policy": "equivalency_accepted"},
            "level_3": {"org_level_code": "L4", "display_title": "Workforce / Operations Analyst III (L4)", "min_years_expected": 4.0, "max_benchmark_years": 6.0, "requires_people_or_budget_scope": False, "degree_policy": "equivalency_accepted"},
            "mid": {"org_level_code": "L3", "display_title": "Mid-Level Operations Analyst (L3)", "min_years_expected": 3.0, "max_benchmark_years": 5.0, "requires_people_or_budget_scope": False, "degree_policy": "equivalency_accepted"},
            "senior": {"org_level_code": "L5", "display_title": "Senior WFM / BI Analyst (L5)", "min_years_expected": 5.0, "max_benchmark_years": 8.0, "requires_people_or_budget_scope": False, "degree_policy": "equivalency_accepted"},
            "lead": {"org_level_code": "L5", "display_title": "Lead WFM / Capacity Planner (L5)", "min_years_expected": 5.0, "max_benchmark_years": 8.0, "requires_people_or_budget_scope": False, "degree_policy": "equivalency_accepted"},
            "staff_principal": {"org_level_code": "L6", "display_title": "Principal / Staff Analytics Architect (L6)", "min_years_expected": 8.0, "max_benchmark_years": 10.0, "requires_people_or_budget_scope": False, "degree_policy": "equivalency_accepted"},
            "manager": {"org_level_code": "M1", "display_title": "WFM / Analytics Manager (M1)", "min_years_expected": 5.0, "max_benchmark_years": 8.0, "requires_people_or_budget_scope": True, "degree_policy": "equivalency_accepted"},
            "senior_manager": {"org_level_code": "M2", "display_title": "Senior Analytics Manager (M2)", "min_years_expected": 6.0, "max_benchmark_years": 10.0, "requires_people_or_budget_scope": True, "degree_policy": "equivalency_accepted"},
            "director": {"org_level_code": "D1", "display_title": "Director, Workforce Operations (D1)", "min_years_expected": 8.0, "max_benchmark_years": 12.0, "requires_people_or_budget_scope": True, "degree_policy": "equivalency_accepted"},
            "executive": {"org_level_code": "VP", "display_title": "VP, Global Operations & Analytics (VP)", "min_years_expected": 10.0, "max_benchmark_years": 15.0, "requires_people_or_budget_scope": True, "degree_policy": "equivalency_accepted"},
        },
        "gate_and_equivalency_policy": {
            "allowed_hard_gate_types": ["degree", "certification", "license", "security_clearance", "work_authorization"],
            "require_degree_equivalency_by_default": True,
            "bachelor_equivalency_years": 4.0,
            "master_equivalency_years": 6.0,
            "allow_certification_substitution": True,
            "accepted_alternative_credentials": [
                "Six Sigma Black Belt",
                "Six Sigma Green Belt",
                "Military Operations / Logistics Experience",
                "Workforce Board / Registered Apprenticeship Certificate",
            ],
            "standard_equivalency_clause": "Bachelor's degree or equivalent practical experience (4+ years of relevant WFM, BI, or operations experience accepted in lieu of degree).",
        },
        "scoring_and_thresholds": {
            "comp1_keyword_max": 45.0,
            "comp2_context_max": 20.0,
            "comp3_alignment_max": 15.0,
            "comp4_knockout_max": 10.0,
            "comp5_schema_max": 10.0,
            "category_weights": {"hard_skill": 3, "title_scope": 2, "soft_skill": 1},
            "tier_multipliers": {"required": 2.0, "preferred": 1.0},
            "synonym_match_credit": 0.5,
            "placement_multipliers": {
                "recent_experience": 1.0,
                "historical_experience": 0.8,
                "skills_or_summary_only": 0.4,
            },
            "jd_strong_match_threshold": 80.0,
            "jd_borderline_threshold": 65.0,
            "rqs_clear_inclusive_threshold": 85.0,
            "rqs_needs_tuning_threshold": 70.0,
        },
        "approval_workflow": {
            "approval_status": "APPROVED",
            "author_id": "arch_a_manning",
            "created_timestamp": "2026-11-01T08:00:00Z",
            "change_rationale": "Baseline 100-point deterministic rubric and HBS Hidden Workers leveling ceilings.",
            "ta_or_comp_approver_id": "ta_lead_m_chen",
            "ta_approved_timestamp": "2026-11-01T09:00:00Z",
            "compliance_or_legal_approver_id": "hr_compliance_r_davis",
            "compliance_approved_timestamp": "2026-11-01T09:30:00Z",
            "previous_version": "1.4.0",
            "previous_config_sha256": "0" * 64,
        },
    }


def get_northstar_enterprise_org_profile_dict() -> Dict[str, Any]:
    """
    Return a customized organization profile for 'Northstar Contact Solutions' with:
      - Skills-first leveling ceilings (`level_2: 3.0 yrs`, `senior: 6.0 yrs`, `senior_manager: 8.0 yrs`)
      - Higher contextual-placement weighting (`C1=40.0, C2=25.0, C3=15.0, C4=10.0, C5=10.0`)
      - Skills-first inclusive shortlist threshold (`Strong >= 78.0`, `Borderline >= 62.0`)
      - Dual sign-off by TA Compensation Lead and HR Compliance Counsel.
    """
    base = copy.deepcopy(get_default_org_profile_dict())
    prev_profile = validate_and_build_org_profile(base)

    base["org_id"] = "northstar-contact-solutions"
    base["org_name"] = "Northstar Contact Solutions (Skills-First WFM & BI Customization)"
    base["config_version"] = "2026.11.2"
    base["effective_date"] = "2026-11-15"
    base["leveling_guide"]["level_2"]["max_benchmark_years"] = 3.0
    base["leveling_guide"]["level_3"]["max_benchmark_years"] = 5.0
    base["leveling_guide"]["senior"]["max_benchmark_years"] = 6.0
    base["leveling_guide"]["manager"]["max_benchmark_years"] = 6.0
    base["leveling_guide"]["senior_manager"]["max_benchmark_years"] = 8.0

    base["gate_and_equivalency_policy"]["bachelor_equivalency_years"] = 3.0
    base["gate_and_equivalency_policy"]["standard_equivalency_clause"] = (
        "Bachelor's degree or 3+ years of practical contact-center WFM, capacity planning, or BI experience accepted as full equivalency."
    )

    base["scoring_and_thresholds"]["comp1_keyword_max"] = 40.0
    base["scoring_and_thresholds"]["comp2_context_max"] = 25.0
    base["scoring_and_thresholds"]["jd_strong_match_threshold"] = 78.0
    base["scoring_and_thresholds"]["jd_borderline_threshold"] = 62.0

    base["approval_workflow"] = {
        "approval_status": "APPROVED",
        "author_id": "wfm_director_k_patel",
        "created_timestamp": "2026-11-14T14:00:00Z",
        "change_rationale": (
            "Adopted Northstar skills-first WFM leveling ceilings (L3=3y, L5=6y), elevated Component 2 recent-experience "
            "weight from 20 to 25 pts (C1=40, C2=25), lowered bachelor equivalency to 3 years, and set Strong shortlist threshold to 78.0."
        ),
        "ta_or_comp_approver_id": "ta_comp_lead_s_jenkins",
        "ta_approved_timestamp": "2026-11-15T10:15:00Z",
        "compliance_or_legal_approver_id": "legal_counsel_d_thompson",
        "compliance_approved_timestamp": "2026-11-15T11:00:00Z",
        "previous_version": DEFAULT_ORG_VERSION,
        "previous_config_sha256": prev_profile.config_sha256,
    }
    return base


# ---------------------------------------------------------------------------
# Schema Validation, Separation-of-Duties Approval Gate & Diff Engine
# ---------------------------------------------------------------------------

def validate_and_build_org_profile(
    raw: Mapping[str, Any],
    require_approved: bool = False,
) -> OrganizationCustomizationProfile:
    if not isinstance(raw, Mapping):
        raise OrgSchemaValidationError("[ORG CONFIG] Profile must be a JSON object.")

    org_id = str(raw.get("org_id", "")).strip()
    org_name = str(raw.get("org_name", "")).strip()
    cfg_ver = str(raw.get("config_version", "")).strip()
    eff_date = str(raw.get("effective_date", "2026-11-01")).strip()
    if not org_id or not org_name or not cfg_ver:
        raise OrgSchemaValidationError("[ORG CONFIG] 'org_id', 'org_name', and 'config_version' are required.")

    # 1. Validate Scoring Weights & Thresholds
    st_raw = raw.get("scoring_and_thresholds", {})
    if not isinstance(st_raw, Mapping):
        raise OrgSchemaValidationError("[ORG CONFIG] 'scoring_and_thresholds' must be an object.")

    c1 = float(st_raw.get("comp1_keyword_max", 45.0))
    c2 = float(st_raw.get("comp2_context_max", 20.0))
    c3 = float(st_raw.get("comp3_alignment_max", 15.0))
    c4 = float(st_raw.get("comp4_knockout_max", 10.0))
    c5 = float(st_raw.get("comp5_schema_max", 10.0))
    if any(x < 0.0 for x in (c1, c2, c3, c4, c5)):
        raise OrgSchemaValidationError("[ORG CONFIG] Component maximum weights must be non-negative.")
    total_max = round(c1 + c2 + c3 + c4 + c5, 4)
    if not math.isclose(total_max, 100.0, abs_tol=1e-2):
        raise OrgSchemaValidationError(
            f"[ORG CONFIG] Component maximum weights must sum to exactly 100.0 (got {total_max:.2f}: "
            f"C1={c1}, C2={c2}, C3={c3}, C4={c4}, C5={c5})."
        )

    jd_strong = float(st_raw.get("jd_strong_match_threshold", 80.0))
    jd_border = float(st_raw.get("jd_borderline_threshold", 65.0))
    if not (0.0 < jd_border < jd_strong <= 100.0):
        raise OrgSchemaValidationError(
            f"[ORG CONFIG] JD thresholds must satisfy 0 < borderline ({jd_border}) < strong ({jd_strong}) <= 100."
        )

    rqs_clear = float(st_raw.get("rqs_clear_inclusive_threshold", 85.0))
    rqs_tune = float(st_raw.get("rqs_needs_tuning_threshold", 70.0))
    if not (0.0 < rqs_tune < rqs_clear <= 100.0):
        raise OrgSchemaValidationError(
            f"[ORG CONFIG] RQS thresholds must satisfy 0 < needs_tuning ({rqs_tune}) < clear_inclusive ({rqs_clear}) <= 100."
        )

    syn_cred = float(st_raw.get("synonym_match_credit", 0.5))
    if not (0.0 <= syn_cred <= 1.0):
        raise OrgSchemaValidationError("[ORG CONFIG] synonym_match_credit must be between 0.0 and 1.0.")

    cat_w = {str(k): int(v) for k, v in dict(st_raw.get("category_weights", {"hard_skill": 3, "title_scope": 2, "soft_skill": 1})).items()}
    tier_m = {str(k): float(v) for k, v in dict(st_raw.get("tier_multipliers", {"required": 2.0, "preferred": 1.0})).items()}
    place_m = {
        str(k): float(v)
        for k, v in dict(
            st_raw.get(
                "placement_multipliers",
                {"recent_experience": 1.0, "historical_experience": 0.8, "skills_or_summary_only": 0.4},
            )
        ).items()
    }
    scoring_obj = ScoringWeightsAndThresholds(
        comp1_keyword_max=c1,
        comp2_context_max=c2,
        comp3_alignment_max=c3,
        comp4_knockout_max=c4,
        comp5_schema_max=c5,
        category_weights=cat_w,
        tier_multipliers=tier_m,
        synonym_match_credit=syn_cred,
        placement_multipliers=place_m,
        jd_strong_match_threshold=jd_strong,
        jd_borderline_threshold=jd_border,
        rqs_clear_inclusive_threshold=rqs_clear,
        rqs_needs_tuning_threshold=rqs_tune,
    )

    # 2. Validate Leveling Guide
    lg_raw = raw.get("leveling_guide", {})
    if not isinstance(lg_raw, Mapping) or not lg_raw:
        raise OrgSchemaValidationError("[ORG CONFIG] 'leveling_guide' must be a non-empty mapping.")
    level_guide: Dict[str, OrgLevelBand] = {}
    for lvl_key, b_raw in lg_raw.items():
        min_y = float(b_raw.get("min_years_expected", 0.0))
        max_y = float(b_raw.get("max_benchmark_years", 5.0))
        if min_y < 0.0 or max_y < min_y:
            raise OrgSchemaValidationError(
                f"[ORG CONFIG] Level '{lvl_key}' has invalid years range: min={min_y}, max_benchmark={max_y}."
            )
        level_guide[str(lvl_key)] = OrgLevelBand(
            canonical_level=str(lvl_key),
            org_level_code=str(b_raw.get("org_level_code", lvl_key.upper())),
            display_title=str(b_raw.get("display_title", lvl_key)),
            min_years_expected=min_y,
            max_benchmark_years=max_y,
            requires_people_or_budget_scope=bool(b_raw.get("requires_people_or_budget_scope", False)),
            degree_policy=str(b_raw.get("degree_policy", "equivalency_accepted")),
        )

    # 3. Validate Gate & Equivalency Policy
    ge_raw = raw.get("gate_and_equivalency_policy", {})
    allowed_gates = [str(g).strip() for g in ge_raw.get("allowed_hard_gate_types", list(ALLOWED_GATE_TYPES))]
    for g in allowed_gates:
        if g not in ALLOWED_GATE_TYPES:
            raise OrgSchemaValidationError(f"[ORG CONFIG] Unsupported gate type '{g}'. Allowed: {sorted(ALLOWED_GATE_TYPES)}.")
    gate_obj = GateAndEquivalencyPolicy(
        allowed_hard_gate_types=allowed_gates,
        require_degree_equivalency_by_default=bool(ge_raw.get("require_degree_equivalency_by_default", True)),
        bachelor_equivalency_years=float(ge_raw.get("bachelor_equivalency_years", 4.0)),
        master_equivalency_years=float(ge_raw.get("master_equivalency_years", 6.0)),
        allow_certification_substitution=bool(ge_raw.get("allow_certification_substitution", True)),
        accepted_alternative_credentials=[str(c) for c in ge_raw.get("accepted_alternative_credentials", [])],
        standard_equivalency_clause=str(ge_raw.get("standard_equivalency_clause", "")),
    )

    # 4. Validate Approval Workflow & Separation of Duties (SoD)
    aw_raw = raw.get("approval_workflow", {})
    status = str(aw_raw.get("approval_status", "DRAFT")).strip().upper()
    if status not in ALLOWED_APPROVAL_STATES:
        raise OrgApprovalWorkflowError(f"[ORG CONFIG] Invalid approval_status '{status}'.")

    author_id = str(aw_raw.get("author_id", "")).strip()
    rationale = str(aw_raw.get("change_rationale", "")).strip()
    ta_app = str(aw_raw.get("ta_or_comp_approver_id", "")).strip()
    comp_app = str(aw_raw.get("compliance_or_legal_approver_id", "")).strip()

    if status == "APPROVED":
        if not author_id or len(rationale) < 10:
            raise OrgApprovalWorkflowError(
                "[ORG APPROVAL GATE] APPROVED profile requires non-empty 'author_id' and 'change_rationale' (>=10 chars)."
            )
        if not ta_app or not comp_app:
            raise OrgApprovalWorkflowError(
                "[ORG APPROVAL GATE] Dual sign-off required for APPROVED status: both 'ta_or_comp_approver_id' "
                "and 'compliance_or_legal_approver_id' must be recorded."
            )
        if author_id == ta_app or author_id == comp_app or ta_app == comp_app:
            raise OrgApprovalWorkflowError(
                f"[ORG APPROVAL GATE] Separation-of-Duties (SoD) violation: author ('{author_id}'), "
                f"TA/Comp approver ('{ta_app}'), and Compliance/Legal approver ('{comp_app}') must be distinct individuals."
            )

    if require_approved and status != "APPROVED":
        raise OrgApprovalWorkflowError(
            f"[ORG APPROVAL GATE] BLOCKED: Organization profile '{org_id}' v'{cfg_ver}' has status '{status}', "
            "not 'APPROVED' (--require-approved-org-profile is active)."
        )

    aw_obj = ApprovalWorkflowRecord(
        approval_status=status,
        author_id=author_id,
        created_timestamp=str(aw_raw.get("created_timestamp", "")),
        change_rationale=rationale,
        ta_or_comp_approver_id=ta_app,
        ta_approved_timestamp=str(aw_raw.get("ta_approved_timestamp", "")),
        compliance_or_legal_approver_id=comp_app,
        compliance_approved_timestamp=str(aw_raw.get("compliance_approved_timestamp", "")),
        previous_version=str(aw_raw.get("previous_version", "")),
        previous_config_sha256=str(aw_raw.get("previous_config_sha256", "")),
    )

    profile = OrganizationCustomizationProfile(
        schema_version=str(raw.get("schema_version", ORG_PROFILE_SCHEMA_VERSION)),
        org_id=org_id,
        org_name=org_name,
        config_version=cfg_ver,
        effective_date=eff_date,
        policy_pack_id=str(raw.get("policy_pack_id", "us-employment-ai-transparency-baseline")),
        leveling_guide=level_guide,
        gate_and_equivalency_policy=gate_obj,
        scoring_and_thresholds=scoring_obj,
        approval_workflow=aw_obj,
    )
    canonical_payload = {
        "org_id": profile.org_id,
        "config_version": profile.config_version,
        "effective_date": profile.effective_date,
        "policy_pack_id": profile.policy_pack_id,
        "leveling_guide": {k: dataclasses.asdict(v) for k, v in sorted(profile.leveling_guide.items())},
        "gate_and_equivalency_policy": dataclasses.asdict(profile.gate_and_equivalency_policy),
        "scoring_and_thresholds": dataclasses.asdict(profile.scoring_and_thresholds),
    }
    profile.config_sha256 = hashlib.sha256(
        json.dumps(canonical_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return profile


def load_org_profile(
    path: Optional[str] = None,
    require_approved: bool = False,
) -> OrganizationCustomizationProfile:
    if not path:
        return validate_and_build_org_profile(get_default_org_profile_dict(), require_approved=require_approved)
    resolved = gov.resolve_input_path(path)
    with open(resolved, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return validate_and_build_org_profile(raw, require_approved=require_approved)


def to_rqs_level_benchmarks(profile: OrganizationCustomizationProfile) -> Dict[str, float]:
    """Extract `{canonical_level: max_benchmark_years}` for `requisition_quality_auditor.py`."""
    return {k: v.max_benchmark_years for k, v in profile.leveling_guide.items()}


def compute_org_profile_diff(
    old_profile: OrganizationCustomizationProfile,
    new_profile: OrganizationCustomizationProfile,
) -> List[Dict[str, Any]]:
    """Compute a structured parameter-by-parameter diff between two organization profiles."""
    diffs: List[Dict[str, Any]] = []

    old_st = dataclasses.asdict(old_profile.scoring_and_thresholds)
    new_st = dataclasses.asdict(new_profile.scoring_and_thresholds)
    for k in sorted(new_st.keys()):
        if old_st.get(k) != new_st.get(k):
            diffs.append({
                "section": "scoring_and_thresholds",
                "parameter": k,
                "old_value": old_st.get(k),
                "new_value": new_st.get(k),
            })

    old_ge = dataclasses.asdict(old_profile.gate_and_equivalency_policy)
    new_ge = dataclasses.asdict(new_profile.gate_and_equivalency_policy)
    for k in sorted(new_ge.keys()):
        if old_ge.get(k) != new_ge.get(k):
            diffs.append({
                "section": "gate_and_equivalency_policy",
                "parameter": k,
                "old_value": old_ge.get(k),
                "new_value": new_ge.get(k),
            })

    all_levels = sorted(set(old_profile.leveling_guide.keys()) | set(new_profile.leveling_guide.keys()))
    for lvl in all_levels:
        ob = old_profile.leveling_guide.get(lvl)
        nb = new_profile.leveling_guide.get(lvl)
        if ob is None or nb is None:
            diffs.append({
                "section": "leveling_guide",
                "parameter": lvl,
                "old_value": dataclasses.asdict(ob) if ob else None,
                "new_value": dataclasses.asdict(nb) if nb else None,
            })
        elif ob.max_benchmark_years != nb.max_benchmark_years or ob.org_level_code != nb.org_level_code:
            diffs.append({
                "section": "leveling_guide",
                "parameter": f"{lvl}.max_benchmark_years",
                "old_value": ob.max_benchmark_years,
                "new_value": nb.max_benchmark_years,
            })
    return diffs


# ---------------------------------------------------------------------------
# Cryptographically Hash-Chained Configuration Audit History
# ---------------------------------------------------------------------------

def build_config_audit_history_chain(
    profiles_in_order: Sequence[OrganizationCustomizationProfile],
) -> List[ConfigAuditHistoryEntry]:
    """
    Build a tamper-evident SHA-256 hash-chained configuration audit history across
    successive versions of an organization's customization profile.
    """
    chain: List[ConfigAuditHistoryEntry] = []
    prev_entry_sha = "0" * 64
    prev_prof: Optional[OrganizationCustomizationProfile] = None

    for idx, prof in enumerate(profiles_in_order, start=1):
        diffs = compute_org_profile_diff(prev_prof, prof) if prev_prof is not None else []
        aw = prof.approval_workflow
        approved_ts = aw.compliance_approved_timestamp or aw.ta_approved_timestamp or aw.created_timestamp

        entry_payload = {
            "seq": idx,
            "org_id": prof.org_id,
            "config_version": prof.config_version,
            "previous_version": aw.previous_version,
            "approval_status": aw.approval_status,
            "author_id": aw.author_id,
            "ta_or_comp_approver_id": aw.ta_or_comp_approver_id,
            "compliance_or_legal_approver_id": aw.compliance_or_legal_approver_id,
            "approved_timestamp": approved_ts,
            "change_rationale": aw.change_rationale,
            "parameter_diffs": diffs,
            "config_sha256": prof.config_sha256,
            "previous_entry_sha256": prev_entry_sha,
        }
        entry_sha = hashlib.sha256(json.dumps(entry_payload, sort_keys=True).encode("utf-8")).hexdigest()
        chain.append(ConfigAuditHistoryEntry(**entry_payload, entry_sha256=entry_sha))
        prev_entry_sha = entry_sha
        prev_prof = prof

    return chain


def verify_org_config_audit_history(entries: Sequence[Mapping[str, Any]]) -> Tuple[bool, str]:
    """Verify the cryptographic hash chain of an organization configuration audit history."""
    if not entries:
        return (False, "Empty configuration audit history.")
    expected_prev = "0" * 64
    for idx, raw_e in enumerate(entries, start=1):
        if int(raw_e.get("seq", 0)) != idx:
            return (False, f"Sequence mismatch at index {idx}.")
        if raw_e.get("previous_entry_sha256") != expected_prev:
            return (False, f"Broken hash chain link at seq {idx}: previous_entry_sha256 mismatch.")
        payload = {
            "seq": int(raw_e["seq"]),
            "org_id": str(raw_e["org_id"]),
            "config_version": str(raw_e["config_version"]),
            "previous_version": str(raw_e["previous_version"]),
            "approval_status": str(raw_e["approval_status"]),
            "author_id": str(raw_e["author_id"]),
            "ta_or_comp_approver_id": str(raw_e["ta_or_comp_approver_id"]),
            "compliance_or_legal_approver_id": str(raw_e["compliance_or_legal_approver_id"]),
            "approved_timestamp": str(raw_e["approved_timestamp"]),
            "change_rationale": str(raw_e["change_rationale"]),
            "parameter_diffs": list(raw_e["parameter_diffs"]),
            "config_sha256": str(raw_e["config_sha256"]),
            "previous_entry_sha256": str(raw_e["previous_entry_sha256"]),
        }
        computed_sha = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
        if computed_sha != raw_e.get("entry_sha256"):
            return (False, f"Tampered entry_sha256 detected at seq {idx} (version {raw_e.get('config_version')}).")
        expected_prev = computed_sha
    return (True, f"VERIFIED ({len(entries)} hash-chained configuration versions intact)")


# ---------------------------------------------------------------------------
# Display Effective Configuration in Reports
# ---------------------------------------------------------------------------

def render_effective_config_markdown_block(profile: OrganizationCustomizationProfile) -> str:
    """
    Render a compact, standardized 'Effective Organization Configuration & Approval Manifest'
    Markdown section to embed in Candidate JD Alignment, RQS, and Batch Screening reports.
    """
    st = profile.scoring_and_thresholds
    ge = profile.gate_and_equivalency_policy
    aw = profile.approval_workflow
    lvl_summary = ", ".join(
        f"`{k}` ({b.org_level_code}): <={b.max_benchmark_years:g}y"
        for k, b in list(profile.leveling_guide.items())[:7]
    )
    lines = [
        "## Effective Organization Configuration & Governance Manifest",
        "",
        f"- **Organization Profile:** `{profile.org_id}` — *{profile.org_name}* (Version `{profile.config_version}`, Effective `{profile.effective_date}`)",
        f"- **Configuration SHA-256:** `{profile.config_sha256}`",
        f"- **Approval Workflow Status:** `{aw.approval_status}` (Author: `{aw.author_id}` | TA/Comp Sign-Off: `{aw.ta_or_comp_approver_id or '—'}` | Compliance/Legal Sign-Off: `{aw.compliance_or_legal_approver_id or '—'}`)",
        f"- **Effective Component Weights (`Sum = 100.0`):** C1 Coverage `{st.comp1_keyword_max:.1f}` | C2 Placement `{st.comp2_context_max:.1f}` | C3 Title/Tenure/Scale `{st.comp3_alignment_max:.1f}` | C4 Gates `{st.comp4_knockout_max:.1f}` | C5 Hygiene `{st.comp5_schema_max:.1f}`",
        f"- **Effective Category & Tier Multipliers:** Hard Skill `{st.category_weights.get('hard_skill', 3)}`, Scope `{st.category_weights.get('title_scope', 2)}`, Soft Skill `{st.category_weights.get('soft_skill', 1)}` | Required `{st.tier_multipliers.get('required', 2.0):.1f}x`, Preferred `{st.tier_multipliers.get('preferred', 1.0):.1f}x` | Synonym Credit `{st.synonym_match_credit:.2f}x`",
        f"- **Effective Verdict Cutoffs:** JD Strong `>= {st.jd_strong_match_threshold:.1f}`, Borderline `>= {st.jd_borderline_threshold:.1f}` | RQS Clear & Inclusive `>= {st.rqs_clear_inclusive_threshold:.1f}`, Needs Tuning `>= {st.rqs_needs_tuning_threshold:.1f}`",
        f"- **Gate & Equivalency Policy:** Degree Equivalency Default `{ge.require_degree_equivalency_by_default}` (Bachelor = `{ge.bachelor_equivalency_years:g}+ yrs`, Master = `{ge.master_equivalency_years:g}+ yrs`) | Allowed Gate Types: `{', '.join(ge.allowed_hard_gate_types)}`",
        f"- **Leveling Guide Ceilings (Sample):** {lvl_summary}",
    ]
    return "\n".join(lines)


def render_org_customization_report_markdown(
    active_profile: OrganizationCustomizationProfile,
    audit_chain: Sequence[ConfigAuditHistoryEntry],
) -> str:
    chain_dicts = [dataclasses.asdict(e) for e in audit_chain]
    ok, verify_msg = verify_org_config_audit_history(chain_dicts)

    lines: List[str] = [
        f"# Organization-Native Customization & Approval Audit Report (`ops-req-org-config`)",
        "",
        "*Validates organization-specific leveling guides, gate equivalency policies, custom component weights & verdict thresholds, dual-signoff Separation-of-Duties (SoD) approval workflows, and hash-chained configuration history.*",
        "",
        render_effective_config_markdown_block(active_profile),
        "",
        "## 2. Organization Leveling Guide",
        "",
        "| Canonical Level | Org Code | Display Title | Min Expected Yrs | Max Benchmark Ceiling (RQS) | Requires People/Budget Scope | Default Degree Policy |",
        "| :--- | :-: | :--- | :-: | :-: | :-: | :--- |",
    ]
    for k, b in active_profile.leveling_guide.items():
        lines.append(
            f"| `{k}` | `{b.org_level_code}` | **{b.display_title}** | `{b.min_years_expected:g}y` | "
            f"`{b.max_benchmark_years:g}y` | `{b.requires_people_or_budget_scope}` | `{b.degree_policy}` |"
        )

    lines += [
        "",
        f"## 3. Cryptographically Sealed Configuration Audit History (`{verify_msg}`)",
        "",
        "| Seq | Version (`Prev`) | Status | Author | TA/Comp Approver | Compliance/Legal Approver | Parameter Changes | Config SHA-256 | Chain Entry SHA-256 |",
        "| :-: | :--- | :--- | :--- | :--- | :--- | :-: | :--- | :--- |",
    ]
    for e in audit_chain:
        lines.append(
            f"| {e.seq} | `{e.config_version}` (`{e.previous_version or '—'}`) | `{e.approval_status}` | "
            f"`{e.author_id}` | `{e.ta_or_comp_approver_id}` | `{e.compliance_or_legal_approver_id}` | "
            f"`{len(e.parameter_diffs)}` | `{e.config_sha256[:12]}…` | `{e.entry_sha256[:12]}…` |"
        )

    latest = audit_chain[-1] if audit_chain else None
    if latest and latest.parameter_diffs:
        lines += [
            "",
            f"## 4. Approved Parameter Diffs (`v{latest.previous_version}` -> `v{latest.config_version}`)",
            "",
            f"- **Change Rationale:** *{latest.change_rationale}*",
            "",
            "| Section | Parameter | Baseline / Previous Value | Approved New Organization Value |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for d in latest.parameter_diffs:
            lines.append(
                f"| `{d['section']}` | `{d['parameter']}` | `{d['old_value']}` | **`{d['new_value']}`** |"
            )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-org-config`)
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops-req-org-config",
        description="Organization-Native Customization, Leveling, Weight/Threshold & Approval Workflow Engine.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the built-in Northstar Contact Solutions customization & approval audit demo.")
    parser.add_argument("--org-profile", help="Path to an organization customization profile JSON file to validate and inspect.")
    parser.add_argument("--require-approved", action="store_true", help="Fail closed (exit code 2) if the organization profile is not in APPROVED status with dual sign-off.")
    parser.add_argument("--export-sample-profile", help="Export the sample Northstar Contact Solutions organization profile JSON to this path.")
    parser.add_argument("--export-audit-history", help="Export the hash-chained configuration audit history JSON to this path.")
    parser.add_argument("--verify-audit-history", help="Verify an existing configuration audit history JSON file.")
    parser.add_argument("--output", help="Write Markdown report to this path.")
    parser.add_argument("--json-out", help="Write validated profile JSON to this path.")

    args = parser.parse_args(argv)

    try:
        base_prof = validate_and_build_org_profile(get_default_org_profile_dict(), require_approved=True)
        northstar_dict = get_northstar_enterprise_org_profile_dict()
        northstar_prof = validate_and_build_org_profile(northstar_dict, require_approved=True)
        default_chain = build_config_audit_history_chain([base_prof, northstar_prof])

        if args.export_sample_profile:
            with open(args.export_sample_profile, "w", encoding="utf-8") as fh:
                json.dump(northstar_dict, fh, indent=2)
            sys.stdout.write(f"[ORG CONFIG OK] Exported sample Northstar organization profile to {args.export_sample_profile}\n")
            if not args.export_audit_history and not args.demo:
                return 0

        if args.export_audit_history:
            payload = {
                "schema": "org-config-audit-history-1.0",
                "org_id": northstar_prof.org_id,
                "entries": [dataclasses.asdict(e) for e in default_chain],
            }
            with open(args.export_audit_history, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            sys.stdout.write(f"[ORG CONFIG OK] Exported hash-chained configuration audit history to {args.export_audit_history}\n")
            if not args.demo:
                return 0

        if args.verify_audit_history:
            p = gov.resolve_input_path(args.verify_audit_history)
            with open(p, "r", encoding="utf-8") as fh:
                h_raw = json.load(fh)
            entries = h_raw.get("entries", h_raw) if isinstance(h_raw, Mapping) else h_raw
            ok, msg = verify_org_config_audit_history(entries)
            if not ok:
                sys.stderr.write(f"[ORG AUDIT CHAIN ERROR] {msg}\n")
                return 2
            sys.stdout.write(f"[ORG AUDIT CHAIN OK] {msg}\n")
            return 0

        if args.org_profile:
            active_prof = load_org_profile(args.org_profile, require_approved=args.require_approved)
            chain = build_config_audit_history_chain([base_prof, active_prof]) if active_prof.org_id != base_prof.org_id else [ default_chain[0] ]
        else:
            active_prof = northstar_prof
            chain = default_chain

        md_out = render_org_customization_report_markdown(active_prof, chain)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(md_out)
        else:
            sys.stdout.write(md_out)

        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(dataclasses.asdict(active_prof), fh, indent=2)

        return 0

    except (OrgCustomizationError, FileNotFoundError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
