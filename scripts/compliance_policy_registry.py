#!/usr/bin/env python3
"""
ops-requisition-auditor: Versioned Compliance Policy Registry & Notice Renderer
===============================================================================
Separates jurisdiction-specific legal notices, blind-redaction triggers, adverse-impact
thresholds, and pay-transparency rules from the core deterministic scoring engine.

Why this exists:
  Employment and AI compliance laws (Colorado SB 24-205, NYC Local Law 144, Illinois
  HB 3773, EEOC Uniform Guidelines, ADEA, and state pay-transparency statutes) change
  their effective dates, scope, and disclosure wording over time. Hardcoding legal
  assumptions inside a scoring script creates compliance drift.

Capabilities:
  1. Dated Jurisdiction Policy Rules (`JurisdictionPolicyRule`):
     - `rule_id`, `jurisdiction_code`, `title`, `category`
     - `effective_date`, `last_amended_date`, `last_reviewed_date`, `next_review_due_date`
     - `geography` (`country`, `state_or_province`, `locality`, `applies_to_remote`, `location_patterns`)
     - `official_source_url` (official legislature / agency URL)
     - `legal_review_status` (`COUNSEL_APPROVED` | `PENDING_LEGAL_REVIEW` | `DRAFT_UNREVIEWED` | `SUPERSEDED`)
     - `reviewed_by`, `review_notes`
     - `engine_controls` (e.g. `redact_zip_code`, `redact_graduation_year`, `four_fifths_ratio_threshold`,
       `require_pay_range`, `require_factor_notice`, `require_right_to_clarify`)
     - `notice_clause` (counsel-reviewed disclosure paragraph rendered into candidate notices)
  2. Organization-Selectable Policy Packs (`--policy-pack <path>` or `OPS_REQ_POLICY_PACK`):
     - Organizations can load their own counsel-reviewed JSON policy pack, filter active rules
       by `--reference-date` and requisition location (`resolve_applicable_rules`), and enforce
       `--require-counsel-approved` (which blocks unreviewed or stale rules past `next_review_due_date`).
  3. Decoupled Legal Notice Rendering (`render_policy_driven_candidate_notice`):
     - Renders candidate factor & gap disclosure notices using the active policy pack's templates
       and logs `policy_pack_id`, `policy_pack_version`, and `policy_pack_sha256`.

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
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import governance_middleware as gov  # noqa: E402

DEFAULT_POLICY_PACK_ID = "us-employment-ai-transparency-baseline"
DEFAULT_POLICY_PACK_VERSION = "2026.11.0"

ALLOWED_REVIEW_STATUSES = {
    "COUNSEL_APPROVED",
    "PENDING_LEGAL_REVIEW",
    "DRAFT_UNREVIEWED",
    "SUPERSEDED",
}

ALLOWED_RULE_CATEGORIES = {
    "ai_automated_assessment_notice",
    "bias_audit_and_selection_rates",
    "demographic_proxy_redaction",
    "pay_transparency",
    "equal_opportunity_and_accommodation",
}

ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PolicyPackError(Exception):
    """Base error for compliance policy pack schema, staleness, or approval failures."""


class PolicySchemaValidationError(PolicyPackError):
    """Raised when a policy pack JSON violates required schema fields or date formats."""


class PolicyReviewApprovalError(PolicyPackError):
    """Raised when `--require-counsel-approved` is active and a rule is unapproved or stale."""


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class GeographyScope:
    country: str = "US"
    state_or_province: str = "ALL"        # e.g. "CO", "NY", "IL", "CA", "WA", "ALL"
    locality: str = ""                    # e.g. "New York City"
    applies_to_remote: bool = True
    location_patterns: List[str] = field(default_factory=list)  # regexes matching location strings


@dataclass
class JurisdictionPolicyRule:
    rule_id: str
    jurisdiction_code: str                # e.g. "US-CO", "US-NYC", "US-IL", "US-FED", "US-MULTI-PAY"
    title: str
    statute_citation: str
    category: str                         # one of ALLOWED_RULE_CATEGORIES
    policy_version: str
    effective_date: str                   # YYYY-MM-DD
    last_amended_date: str                # YYYY-MM-DD
    last_reviewed_date: str               # YYYY-MM-DD
    next_review_due_date: str             # YYYY-MM-DD
    official_source_url: str
    legal_review_status: str              # one of ALLOWED_REVIEW_STATUSES
    reviewed_by: str
    review_notes: str
    enabled: bool
    geography: GeographyScope
    engine_controls: Dict[str, Any]
    notice_heading: str
    notice_clause: str


@dataclass
class CompliancePolicyPack:
    policy_pack_id: str
    policy_pack_version: str
    organization_name: str
    created_date: str
    default_disclaimer: str
    right_to_clarify_template: str
    rules: List[JurisdictionPolicyRule]
    policy_pack_sha256: str = ""


# ---------------------------------------------------------------------------
# Built-In Baseline Dated Policy Pack (`2026.11.0`)
# ---------------------------------------------------------------------------

def get_default_policy_pack_dict() -> Dict[str, Any]:
    return {
        "policy_pack_id": DEFAULT_POLICY_PACK_ID,
        "policy_pack_version": DEFAULT_POLICY_PACK_VERSION,
        "organization_name": "Baseline Reference Policy Pack (Replace with Organization Counsel-Reviewed Pack)",
        "created_date": "2026-11-01",
        "default_disclaimer": (
            "*A deterministic, human-in-the-loop screening aid governed by a versioned compliance policy pack. "
            "It never auto-rejects candidates, is not legal advice, and does not replace an independent bias audit "
            "or organization counsel review where required by law.*"
        ),
        "right_to_clarify_template": (
            "The following job requirements were **not detected** on your submitted application text. "
            "If you hold any of these qualifications under different terminology or omitted them from your resume, "
            "you have the right to submit a clarification or updated resume to the recruiting team to trigger a "
            "deterministic re-score and human review:"
        ),
        "rules": [
            {
                "rule_id": "POL-CO-SB24-205",
                "jurisdiction_code": "US-CO",
                "title": "Colorado Artificial Intelligence Act (SB 24-205) — Principal Factor & Correction Notice",
                "statute_citation": "Colo. Rev. Stat. § 6-1-1701 et seq. (SB 24-205, as amended)",
                "category": "ai_automated_assessment_notice",
                "policy_version": "2026.11",
                "effective_date": "2026-06-30",
                "last_amended_date": "2025-08-25",
                "last_reviewed_date": "2026-11-01",
                "next_review_due_date": "2027-05-01",
                "official_source_url": "https://leg.colorado.gov/bills/sb24-205",
                "legal_review_status": "COUNSEL_APPROVED",
                "reviewed_by": "reference_policy_maintainer",
                "review_notes": "Covers principal scoring factors (Components 1-5), human oversight status, and statutory right to correct inaccurate application data.",
                "enabled": True,
                "geography": {
                    "country": "US",
                    "state_or_province": "CO",
                    "locality": "",
                    "applies_to_remote": True,
                    "location_patterns": [r"\bCO\b", r"\bColorado\b", r"\bDenver\b", r"\bBoulder\b", r"\bRemote\b"],
                },
                "engine_controls": {
                    "require_factor_notice": True,
                    "require_right_to_clarify": True,
                    "require_human_oversight_disclosure": True,
                },
                "notice_heading": "Colorado SB 24-205 Automated Assessment & Factor Disclosure",
                "notice_clause": (
                    "Pursuant to Colorado SB 24-205, this notice discloses the principal job-related factors evaluated "
                    "by the deterministic requirement-alignment rubric, confirms that a human reviewer makes all disposition "
                    "decisions, and provides a direct mechanism to clarify or correct any unverified qualification."
                ),
            },
            {
                "rule_id": "POL-NYC-LL144",
                "jurisdiction_code": "US-NYC",
                "title": "New York City Local Law 144 (AEDT) — Candidate Notice & Selection-Rate Tables",
                "statute_citation": "N.Y.C. Admin. Code § 20-871 et seq. / 6 RCNY § 5-300 et seq.",
                "category": "bias_audit_and_selection_rates",
                "policy_version": "2026.11",
                "effective_date": "2023-07-05",
                "last_amended_date": "2023-07-05",
                "last_reviewed_date": "2026-11-01",
                "next_review_due_date": "2027-05-01",
                "official_source_url": "https://www.nyc.gov/site/dca/about/automated-employment-decision-tools.page",
                "legal_review_status": "COUNSEL_APPROVED",
                "reviewed_by": "reference_policy_maintainer",
                "review_notes": "Provides deterministic reference scoring, job qualifications evaluated, and sex/race/ethnicity selection-rate & impact-ratio tables for independent bias auditors.",
                "enabled": True,
                "geography": {
                    "country": "US",
                    "state_or_province": "NY",
                    "locality": "New York City",
                    "applies_to_remote": False,
                    "location_patterns": [r"\bNew\s+York\b", r"\bNYC\b", r"\bManhattan\b", r"\bBrooklyn\b", r",\s*NY\b"],
                },
                "engine_controls": {
                    "require_factor_notice": True,
                    "require_selection_rate_table": True,
                    "advance_notice_business_days": 10,
                },
                "notice_heading": "NYC Local Law 144 Job Qualifications & Features Disclosure",
                "notice_clause": (
                    "In support of NYC Local Law 144 transparency workflows, this notice lists the specific job "
                    "qualifications and features evaluated from your submitted resume text alongside the exact quoted lines."
                ),
            },
            {
                "rule_id": "POL-IL-HB3773",
                "jurisdiction_code": "US-IL",
                "title": "Illinois Human Rights Act AI Amendment (HB 3773) — AI Notice & ZIP Proxy Prohibition",
                "statute_citation": "775 ILCS 5/2-102(L) (Public Act 103-0804 / HB 3773)",
                "category": "demographic_proxy_redaction",
                "policy_version": "2026.11",
                "effective_date": "2026-01-01",
                "last_amended_date": "2024-08-09",
                "last_reviewed_date": "2026-11-01",
                "next_review_due_date": "2027-05-01",
                "official_source_url": "https://www.ilga.gov/legislation/publicacts/fulltext.asp?Name=103-0804",
                "legal_review_status": "COUNSEL_APPROVED",
                "reviewed_by": "reference_policy_maintainer",
                "review_notes": "Enforces 5/9-digit ZIP code stripping in blind mode so geographic codes are never used as a proxy for protected classes, and discloses automated assessment usage.",
                "enabled": True,
                "geography": {
                    "country": "US",
                    "state_or_province": "IL",
                    "locality": "",
                    "applies_to_remote": True,
                    "location_patterns": [r"\bIL\b", r"\bIllinois\b", r"\bChicago\b", r"\bRemote\b"],
                },
                "engine_controls": {
                    "redact_zip_code": True,
                    "require_factor_notice": True,
                },
                "notice_heading": "Illinois HB 3773 Automated Assessment & Geographic Proxy Protection Notice",
                "notice_clause": (
                    "Pursuant to Illinois HB 3773 (effective January 1, 2026), 5- and 9-digit ZIP codes are stripped "
                    "prior to scoring so geographic location cannot act as a proxy for protected classes."
                ),
            },
            {
                "rule_id": "POL-FED-EEOC-UGESP",
                "jurisdiction_code": "US-FED",
                "title": "EEOC Uniform Guidelines on Employee Selection Procedures — Four-Fifths (80%) Rule",
                "statute_citation": "29 CFR § 1607.4(D)",
                "category": "bias_audit_and_selection_rates",
                "policy_version": "2026.11",
                "effective_date": "1978-08-25",
                "last_amended_date": "1978-08-25",
                "last_reviewed_date": "2026-11-01",
                "next_review_due_date": "2027-11-01",
                "official_source_url": "https://www.ecfr.gov/current/title-29/subtitle-B/chapter-XIV/part-1607",
                "legal_review_status": "COUNSEL_APPROVED",
                "reviewed_by": "reference_policy_maintainer",
                "review_notes": "Computes group selection rates and 0.80 impact ratios post-scoring from segregated voluntary self-ID data.",
                "enabled": True,
                "geography": {
                    "country": "US",
                    "state_or_province": "ALL",
                    "locality": "",
                    "applies_to_remote": True,
                    "location_patterns": [r".*"],
                },
                "engine_controls": {
                    "four_fifths_ratio_threshold": 0.80,
                    "small_sample_warning_min_n": 5,
                    "segregate_demographics_from_scorer": True,
                },
                "notice_heading": "EEOC Job-Relatedness & Uniform Guidelines Notice",
                "notice_clause": (
                    "All scored items trace directly to documented job requirements in the requisition configuration, "
                    "and demographic self-identification data is never accessible to the scoring engine."
                ),
            },
            {
                "rule_id": "POL-FED-ADEA-GRAD-YEAR",
                "jurisdiction_code": "US-FED",
                "title": "Age Discrimination in Employment Act (ADEA) — Graduation-Year Proxy Redaction",
                "statute_citation": "29 U.S.C. § 621 et seq.",
                "category": "demographic_proxy_redaction",
                "policy_version": "2026.11",
                "effective_date": "1968-06-12",
                "last_amended_date": "1990-10-16",
                "last_reviewed_date": "2026-11-01",
                "next_review_due_date": "2027-11-01",
                "official_source_url": "https://www.eeoc.gov/statutes/age-discrimination-employment-act-1967",
                "legal_review_status": "COUNSEL_APPROVED",
                "reviewed_by": "reference_policy_maintainer",
                "review_notes": "Redacts graduation dates inside the Education section during blind screening while preserving work-experience date ranges for net tenure math.",
                "enabled": True,
                "geography": {
                    "country": "US",
                    "state_or_province": "ALL",
                    "locality": "",
                    "applies_to_remote": True,
                    "location_patterns": [r".*"],
                },
                "engine_controls": {
                    "redact_graduation_year": True,
                    "flag_age_coded_jd_phrases": True,
                },
                "notice_heading": "ADEA Age-Proxy Redaction Safeguard",
                "notice_clause": (
                    "Education graduation dates are redacted prior to scoring to prevent age-proxy inference while "
                    "preserving work-experience date ranges for relevant tenure calculation."
                ),
            },
            {
                "rule_id": "POL-US-PAY-TRANSPARENCY",
                "jurisdiction_code": "US-MULTI-PAY",
                "title": "Multi-State Pay Transparency Statutes (CO, CA, NY, WA, IL, MN, MD, DC, MA)",
                "statute_citation": "C.R.S. § 8-5-201; Cal. Lab. Code § 432.3; N.Y. Lab. Law § 194-b; RCW 49.58.110; 820 ILCS 112/10",
                "category": "pay_transparency",
                "policy_version": "2026.11",
                "effective_date": "2021-01-01",
                "last_amended_date": "2025-10-29",
                "last_reviewed_date": "2026-11-01",
                "next_review_due_date": "2027-05-01",
                "official_source_url": "https://cdle.colorado.gov/equalpaytransparency",
                "legal_review_status": "COUNSEL_APPROVED",
                "reviewed_by": "reference_policy_maintainer",
                "review_notes": "Controls Requisition Quality Audit Rule P1 (-8 pts when pay range is omitted on covered or remote-eligible job postings).",
                "enabled": True,
                "geography": {
                    "country": "US",
                    "state_or_province": "CO,CA,NY,WA,IL,MN,MD,DC,MA,HI,NJ,VT",
                    "locality": "",
                    "applies_to_remote": True,
                    "location_patterns": [
                        r"\b(?:CO|CA|NY|WA|IL|MN|MD|DC|MA|HI|NJ|VT)\b",
                        r"\b(?:Colorado|California|New\s+York|Washington|Illinois|Minnesota|Maryland|Massachusetts|Remote)\b",
                    ],
                },
                "engine_controls": {
                    "require_pay_range": True,
                    "rqs_p1_deduction_points": 8.0,
                },
                "notice_heading": "Pay Range Transparency Verification",
                "notice_clause": (
                    "Requisition Quality Audit Rule P1 verifies whether a good-faith base salary or hourly wage range "
                    "is disclosed in the job posting."
                ),
            },
        ],
    }


# ---------------------------------------------------------------------------
# Schema Validation & Policy Pack Loader
# ---------------------------------------------------------------------------

def compute_policy_pack_sha256(raw_dict: Mapping[str, Any]) -> str:
    canonical = dict(raw_dict)
    canonical.pop("policy_pack_sha256", None)
    payload = json.dumps(canonical, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_iso_date(date_str: str, field_name: str, context: str) -> datetime:
    s = str(date_str or "").strip()
    if not ISO_DATE_RE.match(s):
        raise PolicySchemaValidationError(
            f"{context}: field '{field_name}' must be an ISO date 'YYYY-MM-DD' (got '{date_str}')."
        )
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except ValueError as exc:
        raise PolicySchemaValidationError(f"{context}: invalid calendar date in '{field_name}': {exc}") from exc


def validate_and_build_policy_pack(raw_data: Mapping[str, Any]) -> CompliancePolicyPack:
    """
    Validates a policy pack dictionary against the versioned compliance schema
    and returns a `CompliancePolicyPack` with a verified `policy_pack_sha256`.
    """
    if not isinstance(raw_data, Mapping):
        raise PolicySchemaValidationError("Policy pack root must be a JSON object.")

    for req_top in ("policy_pack_id", "policy_pack_version", "organization_name", "created_date", "rules"):
        if req_top not in raw_data or raw_data[req_top] in (None, ""):
            raise PolicySchemaValidationError(f"Policy pack missing required top-level field '{req_top}'.")

    _parse_iso_date(str(raw_data["created_date"]), "created_date", "PolicyPack")
    raw_rules = raw_data.get("rules")
    if not isinstance(raw_rules, list) or not raw_rules:
        raise PolicySchemaValidationError("Policy pack 'rules' must be a non-empty list of rule objects.")

    seen_ids: set[str] = set()
    parsed_rules: List[JurisdictionPolicyRule] = []

    for idx, r in enumerate(raw_rules, start=1):
        ctx = f"Rule #{idx}"
        if not isinstance(r, Mapping):
            raise PolicySchemaValidationError(f"{ctx}: must be a JSON object.")

        for req_field in (
            "rule_id", "jurisdiction_code", "title", "statute_citation", "category",
            "policy_version", "effective_date", "last_amended_date", "last_reviewed_date",
            "next_review_due_date", "official_source_url", "legal_review_status",
            "reviewed_by", "geography", "engine_controls", "notice_heading", "notice_clause",
        ):
            if req_field not in r or r[req_field] is None:
                raise PolicySchemaValidationError(f"{ctx}: missing required field '{req_field}'.")

        rid = str(r["rule_id"]).strip()
        ctx = f"Rule '{rid}'"
        if not rid or rid in seen_ids:
            raise PolicySchemaValidationError(f"{ctx}: duplicate or empty 'rule_id'.")
        seen_ids.add(rid)

        cat = str(r["category"]).strip()
        if cat not in ALLOWED_RULE_CATEGORIES:
            raise PolicySchemaValidationError(
                f"{ctx}: invalid category '{cat}'. Allowed: {', '.join(sorted(ALLOWED_RULE_CATEGORIES))}."
            )

        status = str(r["legal_review_status"]).strip().upper()
        if status not in ALLOWED_REVIEW_STATUSES:
            raise PolicySchemaValidationError(
                f"{ctx}: invalid legal_review_status '{status}'. Allowed: {', '.join(sorted(ALLOWED_REVIEW_STATUSES))}."
            )

        url = str(r["official_source_url"]).strip()
        if not url.startswith(("https://", "http://")):
            raise PolicySchemaValidationError(f"{ctx}: 'official_source_url' must be a valid http(s) URL.")

        _parse_iso_date(str(r["effective_date"]), "effective_date", ctx)
        _parse_iso_date(str(r["last_amended_date"]), "last_amended_date", ctx)
        rev_dt = _parse_iso_date(str(r["last_reviewed_date"]), "last_reviewed_date", ctx)
        due_dt = _parse_iso_date(str(r["next_review_due_date"]), "next_review_due_date", ctx)
        if due_dt < rev_dt:
            raise PolicySchemaValidationError(
                f"{ctx}: 'next_review_due_date' ({r['next_review_due_date']}) cannot precede 'last_reviewed_date' ({r['last_reviewed_date']})."
            )

        geo_raw = r["geography"]
        if not isinstance(geo_raw, Mapping):
            raise PolicySchemaValidationError(f"{ctx}: 'geography' must be an object.")
        geo = GeographyScope(
            country=str(geo_raw.get("country", "US")),
            state_or_province=str(geo_raw.get("state_or_province", "ALL")),
            locality=str(geo_raw.get("locality", "")),
            applies_to_remote=bool(geo_raw.get("applies_to_remote", True)),
            location_patterns=[str(p) for p in (geo_raw.get("location_patterns") or [])],
        )

        parsed_rules.append(
            JurisdictionPolicyRule(
                rule_id=rid,
                jurisdiction_code=str(r["jurisdiction_code"]).strip(),
                title=str(r["title"]).strip(),
                statute_citation=str(r["statute_citation"]).strip(),
                category=cat,
                policy_version=str(r["policy_version"]).strip(),
                effective_date=str(r["effective_date"]).strip(),
                last_amended_date=str(r["last_amended_date"]).strip(),
                last_reviewed_date=str(r["last_reviewed_date"]).strip(),
                next_review_due_date=str(r["next_review_due_date"]).strip(),
                official_source_url=url,
                legal_review_status=status,
                reviewed_by=str(r["reviewed_by"]).strip(),
                review_notes=str(r.get("review_notes", "")).strip(),
                enabled=bool(r.get("enabled", True)),
                geography=geo,
                engine_controls=dict(r["engine_controls"]),
                notice_heading=str(r["notice_heading"]).strip(),
                notice_clause=str(r["notice_clause"]).strip(),
            )
        )

    pack_sha = compute_policy_pack_sha256(raw_data)
    return CompliancePolicyPack(
        policy_pack_id=str(raw_data["policy_pack_id"]).strip(),
        policy_pack_version=str(raw_data["policy_pack_version"]).strip(),
        organization_name=str(raw_data["organization_name"]).strip(),
        created_date=str(raw_data["created_date"]).strip(),
        default_disclaimer=str(raw_data.get("default_disclaimer") or ""),
        right_to_clarify_template=str(raw_data.get("right_to_clarify_template") or ""),
        rules=parsed_rules,
        policy_pack_sha256=pack_sha,
    )


def load_policy_pack(path: Optional[str] = None) -> CompliancePolicyPack:
    """
    Loads and schema-validates an organization policy pack from `path`, `OPS_REQ_POLICY_PACK`,
    or the built-in dated baseline pack (`get_default_policy_pack_dict()`).
    """
    target = path or os.environ.get("OPS_REQ_POLICY_PACK")
    if target:
        resolved = gov.resolve_input_path(target)
        with open(resolved, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        return validate_and_build_policy_pack(raw)
    return validate_and_build_policy_pack(get_default_policy_pack_dict())


# ---------------------------------------------------------------------------
# Effective-Date, Geography & Counsel-Approval Resolution
# ---------------------------------------------------------------------------

def _coerce_dt(dt_or_str: Any) -> datetime:
    if isinstance(dt_or_str, datetime):
        return dt_or_str
    return datetime.strptime(str(dt_or_str).strip(), "%Y-%m-%d")


def is_rule_effective_on(rule: JurisdictionPolicyRule, reference_date: Any) -> bool:
    ref_dt = _coerce_dt(reference_date)
    eff_dt = datetime.strptime(rule.effective_date, "%Y-%m-%d")
    return rule.enabled and (ref_dt >= eff_dt) and (rule.legal_review_status != "SUPERSEDED")


def is_rule_stale_on(rule: JurisdictionPolicyRule, reference_date: Any) -> bool:
    ref_dt = _coerce_dt(reference_date)
    due_dt = datetime.strptime(rule.next_review_due_date, "%Y-%m-%d")
    return ref_dt > due_dt


def rule_matches_location(rule: JurisdictionPolicyRule, location: str) -> bool:
    if not location or rule.geography.state_or_province.upper() == "ALL":
        return True
    if rule.geography.applies_to_remote and re.search(r"\bremote\b", location, re.IGNORECASE):
        return True
    for pat in rule.geography.location_patterns:
        if re.search(pat, location, re.IGNORECASE):
            return True
    return False


def resolve_applicable_rules(
    pack: CompliancePolicyPack,
    reference_date: Any = "2026-11-01",
    location: str = "",
    jurisdiction_filter: Optional[Sequence[str]] = None,
    require_counsel_approved: bool = False,
) -> List[JurisdictionPolicyRule]:
    """
    Returns all rules in `pack` that are:
      1. Enabled and effective on or before `reference_date` (`effective_date <= reference_date`).
      2. Applicable to `location` (or matching `jurisdiction_filter` if explicitly specified).
      3. Counsel-approved and not past `next_review_due_date` when `require_counsel_approved=True`.
    """
    ref_dt = _coerce_dt(reference_date)
    j_filter = {j.strip().upper() for j in jurisdiction_filter if j.strip()} if jurisdiction_filter else set()
    active: List[JurisdictionPolicyRule] = []

    for rule in pack.rules:
        if not is_rule_effective_on(rule, ref_dt):
            continue
        if j_filter:
            code_up = rule.jurisdiction_code.upper()
            state_up = rule.geography.state_or_province.upper()
            if not (
                code_up in j_filter
                or state_up in j_filter
                or any(code_up.endswith(f"-{jf}") for jf in j_filter)
                or rule.rule_id.upper() in j_filter
            ):
                continue
        else:
            if not rule_matches_location(rule, location):
                continue

        if require_counsel_approved:
            if rule.legal_review_status != "COUNSEL_APPROVED":
                raise PolicyReviewApprovalError(
                    f"[POLICY GATE] Rule '{rule.rule_id}' ({rule.title}) has status "
                    f"'{rule.legal_review_status}' (require_counsel_approved=True)."
                )
            if is_rule_stale_on(rule, ref_dt):
                raise PolicyReviewApprovalError(
                    f"[POLICY GATE] Rule '{rule.rule_id}' ({rule.title}) is past its next_review_due_date "
                    f"('{rule.next_review_due_date}') as of reference_date '{ref_dt.strftime('%Y-%m-%d')}'."
                )
        active.append(rule)

    return active


def audit_policy_pack_freshness(
    pack: CompliancePolicyPack,
    reference_date: Any = "2026-11-01",
) -> Dict[str, Any]:
    """Inspect all rules in `pack` for effective status, upcoming/expired review dates, and unapproved drafts."""
    ref_dt = _coerce_dt(reference_date)
    active_rules: List[str] = []
    future_rules: List[str] = []
    stale_rules: List[str] = []
    unapproved_rules: List[str] = []

    for r in pack.rules:
        if is_rule_effective_on(r, reference_date):
            active_rules.append(r.rule_id)
        elif r.enabled and datetime.strptime(r.effective_date, "%Y-%m-%d") > reference_date:
            future_rules.append(f"{r.rule_id} (effective {r.effective_date})")
        if is_rule_stale_on(r, reference_date):
            stale_rules.append(f"{r.rule_id} (due {r.next_review_due_date})")
        if r.legal_review_status != "COUNSEL_APPROVED":
            unapproved_rules.append(f"{r.rule_id} ({r.legal_review_status})")

    return {
        "policy_pack_id": pack.policy_pack_id,
        "policy_pack_version": pack.policy_pack_version,
        "policy_pack_sha256": pack.policy_pack_sha256,
        "reference_date": reference_date.strftime("%Y-%m-%d"),
        "total_rules": len(pack.rules),
        "active_rules_count": len(active_rules),
        "active_rules": active_rules,
        "future_not_yet_effective_rules": future_rules,
        "stale_past_review_due_rules": stale_rules,
        "unapproved_rules": unapproved_rules,
        "ready_for_production": (len(stale_rules) == 0 and len(unapproved_rules) == 0),
    }


# ---------------------------------------------------------------------------
# Decoupled Policy-Driven Notice & Report Renderers
# ---------------------------------------------------------------------------

def render_policy_pack_markdown(pack: CompliancePolicyPack, reference_date: datetime) -> str:
    fresh = audit_policy_pack_freshness(pack, reference_date)
    lines: List[str] = [
        f"# Versioned Compliance Policy Pack Report: `{pack.policy_pack_id}` (`v{pack.policy_pack_version}`)",
        "",
        pack.default_disclaimer,
        "",
        "## 1. Policy Pack Governance & Freshness Telemetry",
        "",
        f"- **Organization:** {pack.organization_name}",
        f"- **Policy Pack ID & Version:** `{pack.policy_pack_id}` (`v{pack.policy_pack_version}`, created `{pack.created_date}`)",
        f"- **Policy Pack SHA-256:** `{pack.policy_pack_sha256}`",
        f"- **Evaluation Reference Date:** `{fresh['reference_date']}`",
        f"- **Active Rules on Reference Date:** `{fresh['active_rules_count']} / {fresh['total_rules']}`",
        f"- **Stale Rules (Past `next_review_due_date`):** `{len(fresh['stale_past_review_due_rules'])}`",
        f"- **Unapproved Rules (`!= COUNSEL_APPROVED`):** `{len(fresh['unapproved_rules'])}`",
        f"- **Production Readiness Gate:** `{'PASS (All rules counsel-approved and current)' if fresh['ready_for_production'] else 'REVIEW REQUIRED'}`",
        "",
        "## 2. Dated Jurisdiction Policy Rules",
        "",
        "| Rule ID | Jurisdiction (`Geography`) | Statute Citation | Effective Date | Next Review Due | Review Status (`Reviewer`) | Official Source |",
        "| :--- | :--- | :--- | :-: | :-: | :--- | :--- |",
    ]
    for r in pack.rules:
        eff_flag = "ACTIVE" if is_rule_effective_on(r, reference_date) else "FUTURE/INACTIVE"
        stale_flag = " [STALE]" if is_rule_stale_on(r, reference_date) else ""
        geo_str = f"{r.geography.country}:{r.geography.state_or_province}"
        if r.geography.locality:
            geo_str += f" ({r.geography.locality})"
        lines.append(
            f"| `{r.rule_id}` | `{r.jurisdiction_code}` (`{geo_str}`) | {r.statute_citation} | "
            f"`{r.effective_date}` ({eff_flag}) | `{r.next_review_due_date}`{stale_flag} | "
            f"`{r.legal_review_status}` (`{r.reviewed_by}`) | [Source]({r.official_source_url}) |"
        )

    lines += ["", "## 3. Decoupled Legal Notice Clauses & Engine Controls", ""]
    for r in pack.rules:
        ctrls = ", ".join(f"`{k}={v}`" for k, v in sorted(r.engine_controls.items()))
        lines += [
            f"### `{r.rule_id}` — {r.title}",
            f"- **Engine Controls:** {ctrls}",
            f"- **Notice Heading:** *{r.notice_heading}*",
            f"- **Notice Clause:** {r.notice_clause}",
            "",
        ]
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-policy`)
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    try:
        gov.enforce_anti_automation_cli(raw_args)
    except gov.GovernanceError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    parser = argparse.ArgumentParser(
        prog="ops-req-policy",
        description="Versioned Compliance Policy Registry: validates dated jurisdiction policy packs, effective dates, geography matching, and legal-counsel approval status.",
    )
    parser.add_argument("--demo", action="store_true", help="Inspect and validate the built-in dated compliance policy pack.")
    parser.add_argument("--policy-pack", help="Path to an organization's custom compliance policy pack JSON.")
    parser.add_argument("--reference-date", default="2026-11-01", help="Reference date YYYY-MM-DD for effective-date and review-staleness checks.")
    parser.add_argument("--location", default="", help="Optional requisition location (e.g. 'Denver, CO', 'New York, NY', 'Chicago, IL') to filter applicable rules.")
    parser.add_argument("--require-counsel-approved", action="store_true", help="Exit with code 2 if any active rule is not COUNSEL_APPROVED or is past next_review_due_date.")
    parser.add_argument("--export-default-pack", help="Write the baseline dated policy pack JSON to this path for customization.")
    parser.add_argument("--output", help="Write Markdown policy report to this path.")
    parser.add_argument("--json-out", help="Write JSON policy freshness audit to this path.")

    args = parser.parse_args(raw_args)
    ref_dt = datetime.strptime(args.reference_date, "%Y-%m-%d")

    if args.export_default_pack:
        default_dict = get_default_policy_pack_dict()
        default_dict["policy_pack_sha256"] = compute_policy_pack_sha256(default_dict)
        with open(args.export_default_pack, "w", encoding="utf-8") as fh:
            json.dump(default_dict, fh, indent=2)
        sys.stdout.write(f"[POLICY OK] Exported baseline policy pack to {args.export_default_pack}\n")
        return 0

    try:
        pack = load_policy_pack(args.policy_pack)
        active_rules = resolve_applicable_rules(
            pack=pack,
            reference_date=ref_dt,
            location=args.location,
            require_counsel_approved=args.require_counsel_approved,
        )
    except PolicyPackError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    display_pack = dataclasses.replace(pack, rules=active_rules) if args.location else pack
    md_out = render_policy_pack_markdown(display_pack, reference_date=ref_dt)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md_out)
    else:
        sys.stdout.write(md_out)

    if args.json_out:
        fresh = audit_policy_pack_freshness(display_pack, reference_date=ref_dt)
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(fresh, fh, indent=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
