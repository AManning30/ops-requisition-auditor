#!/usr/bin/env python3
"""
ops-requisition-auditor: ATS-Neutral Integration SDK, Vendor Adapters, Webhooks & Batch Engine
==============================================================================================
Bridges the deterministic scoring, RQS, screening, mobility, governance, and privacy modules
with external Applicant Tracking Systems (Greenhouse, Lever, Ashby), HRIS & Internal Mobility
platforms (Workday / SuccessFactors), and HR-Open Standards (`ops-req-integrate`).

Core capabilities:
  1. Versioned ATS-Neutral JSON Contract (`ats-neutral-contract-1.0`):
     - `NeutralRequisitionEnvelope`: vendor-neutral job requisition & confirmed requirement config.
     - `NeutralCandidateApplicationEnvelope`: vendor-neutral candidate application, consent record,
       and segregated pseudonym token (`SUBJ-...`).
     - `NeutralEvaluationAndGovernanceEnvelope`: canonical outbound evaluation payload containing
       the 100-point score, plausible score interval `[low, high]`, measurement confidence,
       evidence completeness, point-deduction ledger, and mandatory human-review audit fields.
  2. Bidirectional Vendor Import / Export Adapters:
     - `GreenhouseAdapter`: imports Greenhouse Job & Application payloads; exports Candidate Note
       & Custom Fields while blocking any unapproved stage transition or auto-rejection.
     - `LeverAdapter`: imports Lever Posting & Opportunity payloads; exports Opportunity Feedback
       Note, tags, and cryptographic audit hashes.
     - `AshbyAdapter`: imports Ashby Job & Application payloads; exports Custom Assessment Result.
     - `WorkdayHRISMobilityAdapter`: imports HRIS Worker Profile; exports 30/60/90-day Talent
       Mobility & L&D Learning Plan while enforcing `share_with_manager` employee privacy.
     - `HROpenStandardsAdapter`: imports/exports HR-Open 4.x Assessment Report envelopes.
  3. Outbound Audit & Human-Review Preservation Gate (`verify_outbound_governance_contract`):
     - Guarantees that every outbound vendor export, webhook response, and batch-job record
       preserves `PENDING_HUMAN_REVIEW` (or a cryptographically sealed human decision with
       `reviewer_id`, `reviewer_role`, `decision_timestamp`, `decision_sha256`, and
       `chain_entry_sha256`), `config_sha256`, `policy_pack_id`, `org_profile_id`, and
       `plausible_score_interval`.
  4. Authenticated Webhook Event Processor & Batch-Job Pipeline:
     - `process_signed_webhook_event()`: verifies `HMAC-SHA256` webhook signatures
       (`X-Ops-Req-Signature-256`), enforces replay-window timestamps, and routes
       `application.submitted`, `requisition.updated`, `mobility.profile_submitted`, and
       `privacy.erasure_requested` events.
     - `run_batch_integration_job()`: processes multi-record batch manifests (`.json` / `.jsonl`)
       and emits vendor-adapted outbound records with 100% governance verification.
     - `handle_integration_api_request()`: deterministic REST/OpenAPI request router for both
       in-process SDK calls and the stdlib HTTP server (`--serve-port`).

Runs on the Python 3.10+ standard library only (zero external dependencies).
"""
from __future__ import annotations

import argparse
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import sys
import tempfile
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import offline_ats_auditor as oaa  # noqa: E402
import requisition_quality_auditor as rqa  # noqa: E402
import explainable_screening_assistant as esa  # noqa: E402
import internal_mobility_matcher as imm  # noqa: E402
import governance_middleware as gov  # noqa: E402
import compliance_policy_registry as cpr  # noqa: E402
import privacy_data_minimization as pdm  # noqa: E402
import org_customization_registry as ocr  # noqa: E402

ATS_NEUTRAL_CONTRACT_VERSION = "ats-neutral-contract-1.0"
OPENAPI_SPEC_VERSION = "3.1.0"

SUPPORTED_VENDOR_ADAPTERS = {
    "neutral",
    "greenhouse",
    "lever",
    "ashby",
    "workday_hris",
    "hr_open_xml",
}

SUPPORTED_WEBHOOK_EVENTS = {
    "application.submitted",
    "requisition.created",
    "requisition.updated",
    "mobility.profile_submitted",
    "privacy.erasure_requested",
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class IntegrationError(Exception):
    """Base error for contract validation, adapter translation, or webhook failures."""


class ContractGovernanceViolationError(IntegrationError):
    """Raised when an inbound or outbound integration payload drops human-review or audit fields."""


class WebhookSignatureError(IntegrationError):
    """Raised when a webhook event signature is missing, invalid, or outside the replay window."""


# ---------------------------------------------------------------------------
# Versioned ATS-Neutral JSON Contract Data Models (`ats-neutral-contract-1.0`)
# ---------------------------------------------------------------------------

@dataclass
class NeutralRequisitionEnvelope:
    contract_version: str
    requisition_id: str
    external_system: str                  # "greenhouse" | "lever" | "ashby" | "workday_hris" | "hr_open_xml" | "neutral"
    external_req_id: str
    title: str
    company: str
    department: str
    location: str
    raw_description_text: str
    requirements: List[Dict[str, Any]]
    alignment: Dict[str, Any]
    org_profile_id: str = ocr.DEFAULT_ORG_ID
    policy_pack_id: str = cpr.DEFAULT_POLICY_PACK_ID
    config_sha256: str = ""


@dataclass
class NeutralCandidateApplicationEnvelope:
    contract_version: str
    application_id: str
    candidate_id: str
    pseudonym_token: str
    external_system: str
    external_application_id: str
    requisition_id: str
    resume_text: str
    raw_resume_sha256: str
    blind_mode: bool = True
    consent: Dict[str, Any] = field(default_factory=dict)
    human_decision: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NeutralEvaluationAndGovernanceEnvelope:
    contract_version: str
    evaluation_id: str
    evaluated_at: str
    module: str                           # "explainable_screening" | "candidate_audit" | "internal_mobility" | "requisition_quality"
    external_system: str
    requisition_id: str
    application_id: str
    candidate_id: str
    pseudonym_token: str
    # Deterministic Score & Three-Pillar Uncertainty
    composite_score: float
    literal_only_score: float
    plausible_score_interval: List[float]
    uncertainty_band: str
    measurement_confidence: float
    evidence_completeness_pct: float
    verdict_band: str
    knockout_passed: bool
    unverified_gates: List[str]
    component_scores: Dict[str, float]
    verified_requirements: List[Dict[str, Any]]
    partial_requirements: List[Dict[str, Any]]
    unverified_requirements: List[Dict[str, Any]]
    point_deductions_ledger: List[Dict[str, Any]]
    # Mandatory Human-in-the-Loop & Audit Governance Fields
    disposition_status: str               # "PENDING_HUMAN_REVIEW" | "ADVANCE" | "HOLD" | "DO_NOT_ADVANCE"
    auto_rejection_permitted: bool        # Always False
    reviewer_id: str
    reviewer_role: str
    decision_timestamp: str
    is_override: bool
    override_reason: str
    decision_sha256: str
    chain_entry_sha256: str
    # Version & Policy Provenance Manifest
    version_manifest: Dict[str, Any]
    candidate_disclosure_notice_markdown: str = ""
    envelope_sha256: str = ""


# ---------------------------------------------------------------------------
# Outbound Audit & Human-Review Governance Verifier
# ---------------------------------------------------------------------------

def verify_outbound_governance_contract(payload: Mapping[str, Any], adapter_name: str = "neutral") -> bool:
    """
    Ensure every outbound integration payload (whether ATS-neutral or vendor-specific)
    preserves human-in-the-loop disposition safeguards and cryptographic audit fields.
    Raises `ContractGovernanceViolationError` if any required field is missing or violated.
    """
    gov.enforce_anti_automation_api_params(payload)

    # Extract governance sub-block (either top-level on neutral envelope or nested under `governance_audit` in vendor payloads)
    g_block = payload.get("governance_audit", payload) if isinstance(payload, Mapping) else {}
    if not isinstance(g_block, Mapping):
        raise ContractGovernanceViolationError(
            f"[INTEGRATION GOVERNANCE GATE] Outbound '{adapter_name}' payload is missing governance_audit object."
        )

    if g_block.get("auto_rejection_permitted") is not False:
        raise ContractGovernanceViolationError(
            f"[INTEGRATION GOVERNANCE GATE] Outbound '{adapter_name}' payload must explicitly set 'auto_rejection_permitted': False."
        )

    status = str(g_block.get("disposition_status", "")).strip()
    if status not in gov.ALLOWED_DISPOSITION_STATES:
        raise ContractGovernanceViolationError(
            f"[INTEGRATION GOVERNANCE GATE] Outbound '{adapter_name}' payload has invalid disposition_status '{status}'."
        )

    dec_sha = str(g_block.get("decision_sha256", "")).strip()
    chain_sha = str(g_block.get("chain_entry_sha256", "")).strip()
    if len(dec_sha) != 64 or len(chain_sha) != 64:
        raise ContractGovernanceViolationError(
            f"[INTEGRATION GOVERNANCE GATE] Outbound '{adapter_name}' payload is missing 64-char 'decision_sha256' or 'chain_entry_sha256'."
        )

    if status != gov.MANDATORY_INITIAL_STATE:
        rev_id = str(g_block.get("reviewer_id", "")).strip()
        rev_role = str(g_block.get("reviewer_role", "")).strip()
        dec_ts = str(g_block.get("decision_timestamp", "")).strip()
        if not rev_id or not rev_role or not dec_ts:
            raise ContractGovernanceViolationError(
                f"[INTEGRATION GOVERNANCE GATE] Non-initial disposition '{status}' in '{adapter_name}' export "
                "requires 'reviewer_id', 'reviewer_role', and 'decision_timestamp'."
            )
        if any(pat in rev_id.lower() for pat in ("bot", "auto", "system", "cron", "pipeline", "ai_agent")):
            raise ContractGovernanceViolationError(
                f"[INTEGRATION GOVERNANCE GATE] Automated reviewer_id '{rev_id}' is prohibited in '{adapter_name}' export."
            )
        if bool(g_block.get("is_override", False)) and len(str(g_block.get("override_reason", "")).strip()) < 10:
            raise ContractGovernanceViolationError(
                f"[INTEGRATION GOVERNANCE GATE] Override decision in '{adapter_name}' export is missing 'override_reason' (>=10 chars)."
            )

    v_man = g_block.get("version_manifest", payload.get("version_manifest", {}))
    if not isinstance(v_man, Mapping):
        raise ContractGovernanceViolationError(
            f"[INTEGRATION GOVERNANCE GATE] Outbound '{adapter_name}' payload is missing 'version_manifest'."
        )
    for req_key in ("engine_version", "config_sha256", "policy_pack_id", "org_profile_id"):
        if not str(v_man.get(req_key, "")).strip():
            raise ContractGovernanceViolationError(
                f"[INTEGRATION GOVERNANCE GATE] Outbound '{adapter_name}' version_manifest is missing '{req_key}'."
            )

    interval = g_block.get("plausible_score_interval", payload.get("plausible_score_interval"))
    if not isinstance(interval, list) or len(interval) != 2:
        raise ContractGovernanceViolationError(
            f"[INTEGRATION GOVERNANCE GATE] Outbound '{adapter_name}' payload must preserve 'plausible_score_interval' [low, high]."
        )

    return True


# ---------------------------------------------------------------------------
# Core Builder: Evaluate Application into ATS-Neutral Governance Envelope
# ---------------------------------------------------------------------------

def evaluate_to_neutral_envelope(
    req_env: NeutralRequisitionEnvelope,
    app_env: NeutralCandidateApplicationEnvelope,
    reference_date: str = "2026-11-01",
    org_profile: Optional[ocr.OrganizationCustomizationProfile] = None,
    policy_pack: Optional[cpr.CompliancePolicyPack] = None,
    require_explicit_consent: bool = False,
) -> NeutralEvaluationAndGovernanceEnvelope:
    """
    Evaluate a `NeutralCandidateApplicationEnvelope` against a `NeutralRequisitionEnvelope`
    and seal the result in a `NeutralEvaluationAndGovernanceEnvelope` preserving all
    audit, uncertainty, and human-review fields.
    """
    ref_dt = datetime.strptime(reference_date[:10], "%Y-%m-%d")
    active_org = org_profile or ocr.load_org_profile()
    active_pack = policy_pack or cpr.load_policy_pack()

    if req_env.requirements:
        reqs = [
            r if isinstance(r, oaa.JDRequirement) else oaa.JDRequirement(**r)
            for r in req_env.requirements
        ]
        align_raw = dict(req_env.alignment or {})
        align_raw.pop("reference_date", None)
        align_cfg = oaa.AlignmentConfig(**align_raw)
    else:
        reqs, align_cfg = oaa.extract_reqs_consistent(req_env.raw_description_text, title=req_env.title)

    consent_map: Dict[str, pdm.ConsentRecord] = {}
    if app_env.consent:
        c_dict = dict(app_env.consent)
        c_dict.setdefault("subject_id", app_env.candidate_id)
        c_rec = pdm.build_consent_record(c_dict)
        consent_map[f"{app_env.candidate_id}:batch_screening"] = c_rec
        consent_map[app_env.candidate_id] = c_rec

    decisions_map: Dict[str, esa.ReviewerDecision] = {}
    if app_env.human_decision:
        hd = app_env.human_decision
        decisions_map[app_env.candidate_id] = esa.ReviewerDecision(
            status=str(hd.get("status", gov.MANDATORY_INITIAL_STATE)),
            reviewer=str(hd.get("reviewer", "")),
            timestamp=str(hd.get("timestamp", "")),
            override_reason=str(hd.get("override_reason", "")),
            reviewer_role=str(hd.get("reviewer_role", "recruiter")),
            is_override=bool(hd.get("is_override", False)),
        )

    batch = esa.screen_batch(
        candidate_inputs=[(app_env.candidate_id, f"{app_env.application_id}.txt", app_env.resume_text)],
        reqs=reqs,
        align_cfg=align_cfg,
        reference_date=ref_dt,
        blind_mode=app_env.blind_mode,
        shortlist_threshold=active_org.scoring_and_thresholds.jd_strong_match_threshold,
        decisions_map=decisions_map,
        policy_pack=active_pack,
        location=req_env.location,
        consent_map=consent_map if consent_map else None,
        require_explicit_consent=require_explicit_consent,
        org_profile=active_org,
    )
    cand_rec = batch.candidates[0]
    chain_entry = batch.immutable_audit_chain[0]

    scored_txt, _ = (
        esa.redact_resume_for_blind_screening(app_env.resume_text)
        if app_env.blind_mode
        else (app_env.resume_text, {})
    )
    full_rep = oaa.run_ats_audit(scored_txt, reqs, align_cfg, reference_date=ref_dt, org_profile=active_org)
    unc = full_rep.uncertainty_and_explanations
    pillars = unc["three_pillar_distinction"]
    notice_md = esa.render_candidate_notice(batch, cand_rec, policy_pack=active_pack)

    version_manifest = {
        "engine_version": batch.engine_version,
        "jd_alignment_rubric_version": batch.jd_alignment_rubric_version,
        "rqs_rubric_version": batch.rqs_rubric_version,
        "governance_policy_version": batch.governance_policy_version,
        "contract_version": ATS_NEUTRAL_CONTRACT_VERSION,
        "config_sha256": batch.config_sha256,
        "policy_pack_id": batch.policy_pack_id,
        "policy_pack_version": batch.policy_pack_version,
        "policy_pack_sha256": batch.policy_pack_sha256,
        "org_profile_id": batch.org_profile_id,
        "org_profile_version": batch.org_profile_version,
        "org_profile_sha256": batch.org_profile_sha256,
        "vault_isolation_proof_sha256": batch.vault_isolation_proof_sha256,
    }

    eval_seed = f"{req_env.requisition_id}:{app_env.application_id}:{batch.config_sha256}:{cand_rec.raw_sha256}"
    eval_id = "EVAL-" + hashlib.sha256(eval_seed.encode("utf-8")).hexdigest()[:16].upper()

    env = NeutralEvaluationAndGovernanceEnvelope(
        contract_version=ATS_NEUTRAL_CONTRACT_VERSION,
        evaluation_id=eval_id,
        evaluated_at=f"{reference_date[:10]}T12:00:00Z",
        module="explainable_screening",
        external_system=app_env.external_system or req_env.external_system,
        requisition_id=req_env.requisition_id,
        application_id=app_env.application_id,
        candidate_id=app_env.candidate_id,
        pseudonym_token=cand_rec.pseudonym_token,
        composite_score=cand_rec.composite_score,
        literal_only_score=cand_rec.literal_score,
        plausible_score_interval=list(pillars["1_alignment_score"]["plausible_score_interval"]),
        uncertainty_band=str(pillars["1_alignment_score"]["uncertainty_band"]),
        measurement_confidence=float(pillars["2_measurement_confidence"]["combined_confidence"]),
        evidence_completeness_pct=float(pillars["3_evidence_completeness"]["weighted_evidence_completeness_pct"]),
        verdict_band=cand_rec.recommendation_band,
        knockout_passed=cand_rec.knockout_passed,
        unverified_gates=list(cand_rec.unverified_gates),
        component_scores={
            "comp1_keyword": cand_rec.comp1_kw_score,
            "comp2_context": cand_rec.comp2_context_score,
            "comp3_alignment": cand_rec.comp3_alignment_score,
            "comp4_knockout": cand_rec.comp4_knockout_score,
            "comp5_schema": cand_rec.comp5_schema_score,
        },
        verified_requirements=list(cand_rec.verified_requirements),
        partial_requirements=list(cand_rec.partial_requirements),
        unverified_requirements=list(cand_rec.unverified_requirements),
        point_deductions_ledger=list(unc["point_deductions_ledger"]),
        disposition_status=cand_rec.decision.status,
        auto_rejection_permitted=False,
        reviewer_id=cand_rec.decision.reviewer,
        reviewer_role=cand_rec.decision.reviewer_role,
        decision_timestamp=cand_rec.decision.timestamp,
        is_override=cand_rec.decision.is_override,
        override_reason=cand_rec.decision.override_reason,
        decision_sha256=cand_rec.decision.decision_sha256,
        chain_entry_sha256=str(chain_entry.get("entry_sha256") or chain_entry.get("chain_entry_sha256") or ""),
        version_manifest=version_manifest,
        candidate_disclosure_notice_markdown=notice_md,
    )
    canonical = json.dumps(
        {
            "evaluation_id": env.evaluation_id,
            "composite_score": env.composite_score,
            "plausible_score_interval": env.plausible_score_interval,
            "disposition_status": env.disposition_status,
            "decision_sha256": env.decision_sha256,
            "chain_entry_sha256": env.chain_entry_sha256,
            "version_manifest": env.version_manifest,
        },
        sort_keys=True,
    )
    env.envelope_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    verify_outbound_governance_contract(dataclasses.asdict(env), adapter_name="neutral")
    return env


# ---------------------------------------------------------------------------
# Bidirectional Vendor Import / Export Adapters (Greenhouse, Lever, Ashby, Workday HRIS, HR-Open)
# ---------------------------------------------------------------------------

class GreenhouseAdapter:
    """Adapter for Greenhouse Harvest / Webhook payloads."""

    @staticmethod
    def import_requisition(payload: Mapping[str, Any]) -> NeutralRequisitionEnvelope:
        job = payload.get("job", payload)
        req_id = str(job.get("requisition_id") or job.get("id") or "GH-REQ-001")
        loc = job.get("location", {})
        loc_name = loc.get("name", "Not stated") if isinstance(loc, Mapping) else str(loc or "Not stated")
        dept_list = job.get("departments", [])
        dept = dept_list[0].get("name", "Operations") if (isinstance(dept_list, list) and dept_list and isinstance(dept_list[0], Mapping)) else "Operations"
        return NeutralRequisitionEnvelope(
            contract_version=ATS_NEUTRAL_CONTRACT_VERSION,
            requisition_id=req_id,
            external_system="greenhouse",
            external_req_id=str(job.get("id", req_id)),
            title=str(job.get("title") or job.get("name") or "Untitled Role"),
            company=str(job.get("company") or "Enterprise Employer"),
            department=dept,
            location=loc_name,
            raw_description_text=str(job.get("content") or job.get("description_text") or ""),
            requirements=list(job.get("requirements", [])),
            alignment=dict(job.get("alignment", {})),
        )

    @staticmethod
    def import_application(payload: Mapping[str, Any]) -> NeutralCandidateApplicationEnvelope:
        app = payload.get("application", payload)
        cand = app.get("candidate", {}) if isinstance(app.get("candidate"), Mapping) else {}
        cid = str(cand.get("id") or app.get("candidate_id") or "CAND-GH-001")
        app_id = str(app.get("id") or app.get("application_id") or "APP-GH-001")
        req_id = str(app.get("requisition_id") or app.get("job_id") or "GH-REQ-001")
        resume_txt = str(app.get("resume_text") or "")
        return NeutralCandidateApplicationEnvelope(
            contract_version=ATS_NEUTRAL_CONTRACT_VERSION,
            application_id=app_id,
            candidate_id=cid,
            pseudonym_token=pdm.compute_pseudonym_token(cid),
            external_system="greenhouse",
            external_application_id=app_id,
            requisition_id=req_id,
            resume_text=resume_txt,
            raw_resume_sha256=hashlib.sha256(resume_txt.encode("utf-8")).hexdigest(),
            blind_mode=bool(app.get("blind_mode", True)),
            consent=dict(app.get("consent", {})),
            human_decision=dict(app.get("human_decision", {})),
        )

    @staticmethod
    def export_evaluation(env: NeutralEvaluationAndGovernanceEnvelope) -> Dict[str, Any]:
        out = {
            "adapter": "greenhouse_harvest_v1",
            "contract_version": env.contract_version,
            "external_application_id": env.application_id,
            "candidate_id": env.candidate_id,
            "pseudonym_token": env.pseudonym_token,
            "automated_stage_transition_blocked": True,
            "custom_fields": {
                "ops_req_jd_alignment_score": env.composite_score,
                "ops_req_literal_only_score": env.literal_only_score,
                "ops_req_plausible_score_band": f"[{env.plausible_score_interval[0]:.1f}, {env.plausible_score_interval[1]:.1f}]",
                "ops_req_measurement_confidence_pct": round(env.measurement_confidence * 100.0, 1),
                "ops_req_evidence_completeness_pct": env.evidence_completeness_pct,
                "ops_req_disposition_status": env.disposition_status,
                "ops_req_audit_chain_sha256": env.chain_entry_sha256,
            },
            "activity_feed_note": {
                "user_id": env.reviewer_id or "human_review_queue",
                "visibility": "admin_and_recruiter",
                "body": (
                    f"[ops-requisition-auditor v{env.version_manifest['engine_version']}] "
                    f"JD Alignment Score: {env.composite_score:.1f}/100 "
                    f"(Plausible Band [{env.plausible_score_interval[0]:.1f}, {env.plausible_score_interval[1]:.1f}], "
                    f"Literal {env.literal_only_score:.1f}%) | Disposition: {env.disposition_status} "
                    f"(No automated rejection permitted) | Verified: {len(env.verified_requirements)}, "
                    f"Partial: {len(env.partial_requirements)}, Unverified: {len(env.unverified_requirements)} | "
                    f"Chain SHA-256: {env.chain_entry_sha256[:16]}..."
                ),
            },
            "governance_audit": {
                "disposition_status": env.disposition_status,
                "auto_rejection_permitted": env.auto_rejection_permitted,
                "reviewer_id": env.reviewer_id,
                "reviewer_role": env.reviewer_role,
                "decision_timestamp": env.decision_timestamp,
                "is_override": env.is_override,
                "override_reason": env.override_reason,
                "decision_sha256": env.decision_sha256,
                "chain_entry_sha256": env.chain_entry_sha256,
                "plausible_score_interval": env.plausible_score_interval,
                "version_manifest": env.version_manifest,
            },
        }
        verify_outbound_governance_contract(out, adapter_name="greenhouse")
        return out


class LeverAdapter:
    """Adapter for Lever Postings & Opportunities API payloads."""

    @staticmethod
    def import_requisition(payload: Mapping[str, Any]) -> NeutralRequisitionEnvelope:
        p = payload.get("posting", payload)
        cats = p.get("categories", {}) if isinstance(p.get("categories"), Mapping) else {}
        req_id = str(p.get("id") or "LEV-REQ-001")
        return NeutralRequisitionEnvelope(
            contract_version=ATS_NEUTRAL_CONTRACT_VERSION,
            requisition_id=req_id,
            external_system="lever",
            external_req_id=req_id,
            title=str(p.get("text") or p.get("title") or "Untitled Role"),
            company=str(p.get("company") or "Enterprise Employer"),
            department=str(cats.get("team") or cats.get("department") or "Operations"),
            location=str(cats.get("location") or p.get("location") or "Not stated"),
            raw_description_text=str(p.get("descriptionPlain") or p.get("description_text") or ""),
            requirements=list(p.get("requirements", [])),
            alignment=dict(p.get("alignment", {})),
        )

    @staticmethod
    def import_application(payload: Mapping[str, Any]) -> NeutralCandidateApplicationEnvelope:
        opp = payload.get("opportunity", payload)
        opp_id = str(opp.get("id") or opp.get("application_id") or "OPP-LEV-001")
        cid = str(opp.get("contact") or opp.get("candidate_id") or opp_id)
        req_id = str(opp.get("posting_id") or opp.get("requisition_id") or "LEV-REQ-001")
        resume_txt = str(opp.get("resume_text") or "")
        return NeutralCandidateApplicationEnvelope(
            contract_version=ATS_NEUTRAL_CONTRACT_VERSION,
            application_id=opp_id,
            candidate_id=cid,
            pseudonym_token=pdm.compute_pseudonym_token(cid),
            external_system="lever",
            external_application_id=opp_id,
            requisition_id=req_id,
            resume_text=resume_txt,
            raw_resume_sha256=hashlib.sha256(resume_txt.encode("utf-8")).hexdigest(),
            blind_mode=bool(opp.get("blind_mode", True)),
            consent=dict(opp.get("consent", {})),
            human_decision=dict(opp.get("human_decision", {})),
        )

    @staticmethod
    def export_evaluation(env: NeutralEvaluationAndGovernanceEnvelope) -> Dict[str, Any]:
        out = {
            "adapter": "lever_v1_opportunity_note",
            "contract_version": env.contract_version,
            "opportunity_id": env.application_id,
            "pseudonym_token": env.pseudonym_token,
            "add_tags": [
                f"ops-req-status:{env.disposition_status.lower()}",
                f"ops-req-band:{env.composite_score:.0f}pts",
                "human-in-the-loop-enforced",
            ],
            "note": (
                f"JD Alignment: {env.composite_score:.1f}/100 (Band [{env.plausible_score_interval[0]:.1f}, "
                f"{env.plausible_score_interval[1]:.1f}]) | Disposition: {env.disposition_status} | "
                f"Decision SHA-256: {env.decision_sha256[:16]}..."
            ),
            "governance_audit": {
                "disposition_status": env.disposition_status,
                "auto_rejection_permitted": env.auto_rejection_permitted,
                "reviewer_id": env.reviewer_id,
                "reviewer_role": env.reviewer_role,
                "decision_timestamp": env.decision_timestamp,
                "is_override": env.is_override,
                "override_reason": env.override_reason,
                "decision_sha256": env.decision_sha256,
                "chain_entry_sha256": env.chain_entry_sha256,
                "plausible_score_interval": env.plausible_score_interval,
                "version_manifest": env.version_manifest,
            },
        }
        verify_outbound_governance_contract(out, adapter_name="lever")
        return out


class AshbyAdapter:
    """Adapter for Ashby Job Postings & Application Assessment API payloads."""

    @staticmethod
    def import_requisition(payload: Mapping[str, Any]) -> NeutralRequisitionEnvelope:
        j = payload.get("jobPosting", payload)
        req_id = str(j.get("id") or "ASH-REQ-001")
        return NeutralRequisitionEnvelope(
            contract_version=ATS_NEUTRAL_CONTRACT_VERSION,
            requisition_id=req_id,
            external_system="ashby",
            external_req_id=req_id,
            title=str(j.get("title") or "Untitled Role"),
            company=str(j.get("company") or "Enterprise Employer"),
            department=str(j.get("departmentName") or "Operations"),
            location=str(j.get("locationName") or j.get("location") or "Not stated"),
            raw_description_text=str(j.get("descriptionPlain") or j.get("description_text") or ""),
            requirements=list(j.get("requirements", [])),
            alignment=dict(j.get("alignment", {})),
        )

    @staticmethod
    def import_application(payload: Mapping[str, Any]) -> NeutralCandidateApplicationEnvelope:
        a = payload.get("application", payload)
        app_id = str(a.get("id") or a.get("application_id") or "APP-ASH-001")
        cid = str(a.get("candidateId") or a.get("candidate_id") or app_id)
        req_id = str(a.get("jobId") or a.get("requisition_id") or "ASH-REQ-001")
        resume_txt = str(a.get("resume_text") or "")
        return NeutralCandidateApplicationEnvelope(
            contract_version=ATS_NEUTRAL_CONTRACT_VERSION,
            application_id=app_id,
            candidate_id=cid,
            pseudonym_token=pdm.compute_pseudonym_token(cid),
            external_system="ashby",
            external_application_id=app_id,
            requisition_id=req_id,
            resume_text=resume_txt,
            raw_resume_sha256=hashlib.sha256(resume_txt.encode("utf-8")).hexdigest(),
            blind_mode=bool(a.get("blind_mode", True)),
            consent=dict(a.get("consent", {})),
            human_decision=dict(a.get("human_decision", {})),
        )

    @staticmethod
    def export_evaluation(env: NeutralEvaluationAndGovernanceEnvelope) -> Dict[str, Any]:
        out = {
            "adapter": "ashby_assessment_v1",
            "contract_version": env.contract_version,
            "applicationId": env.application_id,
            "assessmentResult": {
                "score": env.composite_score,
                "plausibleScoreInterval": env.plausible_score_interval,
                "measurementConfidence": env.measurement_confidence,
                "evidenceCompletenessPct": env.evidence_completeness_pct,
                "dispositionStatus": env.disposition_status,
                "humanReviewRequired": True,
            },
            "governance_audit": {
                "disposition_status": env.disposition_status,
                "auto_rejection_permitted": env.auto_rejection_permitted,
                "reviewer_id": env.reviewer_id,
                "reviewer_role": env.reviewer_role,
                "decision_timestamp": env.decision_timestamp,
                "is_override": env.is_override,
                "override_reason": env.override_reason,
                "decision_sha256": env.decision_sha256,
                "chain_entry_sha256": env.chain_entry_sha256,
                "plausible_score_interval": env.plausible_score_interval,
                "version_manifest": env.version_manifest,
            },
        }
        verify_outbound_governance_contract(out, adapter_name="ashby")
        return out


class WorkdayHRISMobilityAdapter:
    """Adapter for Workday / SuccessFactors HRIS Worker Profile & Internal Mobility L&D export."""

    @staticmethod
    def import_worker_profile(payload: Mapping[str, Any]) -> Dict[str, Any]:
        w = payload.get("worker", payload)
        emp_id = str(w.get("worker_id") or w.get("employee_id") or "EMP-001")
        share_mgr = bool(w.get("share_with_manager", False))
        resume_txt = str(w.get("resume_text") or w.get("profile_text") or "")
        return {
            "subject_id": emp_id,
            "pseudonym_token": pdm.compute_pseudonym_token(emp_id),
            "share_with_manager": share_mgr,
            "resume_text": resume_txt,
            "consent": dict(w.get("consent", {})),
        }

    @staticmethod
    def export_mobility_learning_plan(
        report: imm.MobilityPortfolioReport,
        worker_id: str = "EMP-001",
    ) -> Dict[str, Any]:
        return {
            "adapter": "workday_hris_talent_mobility_v1",
            "contract_version": ATS_NEUTRAL_CONTRACT_VERSION,
            "worker_pseudonym_token": report.subject_pseudonym_token or pdm.compute_pseudonym_token(worker_id),
            "employee_initiated": report.employee_initiated,
            "share_with_manager": report.share_with_manager,
            "manager_feed_visibility": "SHARED_AT_EMPLOYEE_REQUEST" if report.share_with_manager else "PRIVATE_TO_EMPLOYEE_ONLY",
            "consent_verified": report.consent_verified,
            "consent_sha256": report.consent_sha256,
            "processing_mode": report.processing_mode,
            "retention_days": report.retention_days,
            "recommended_career_paths": [
                {
                    "rank": m.rank,
                    "role_id": m.role_id,
                    "title": m.title,
                    "current_score": m.current_score,
                    "projected_30d_score": m.projected_30d_score,
                    "readiness_band": m.readiness_band,
                    "learning_milestones_30_60_90": [dataclasses.asdict(s) for s in m.plan_30_60_90],
                }
                for m in report.matches
            ],
        }


class HROpenStandardsAdapter:
    """Adapter for HR-Open Standards 4.x Candidate Assessment Report envelopes."""

    @staticmethod
    def export_evaluation(env: NeutralEvaluationAndGovernanceEnvelope) -> Dict[str, Any]:
        out = {
            "adapter": "hr_open_standards_4x",
            "documentId": env.evaluation_id,
            "contract_version": env.contract_version,
            "candidateSubjectToken": env.pseudonym_token,
            "positionOpeningId": env.requisition_id,
            "assessmentResult": {
                "overallScore": env.composite_score,
                "literalScore": env.literal_only_score,
                "plausibleScoreInterval": env.plausible_score_interval,
                "measurementConfidence": env.measurement_confidence,
                "evidenceCompletenessPct": env.evidence_completeness_pct,
                "dispositionStatus": env.disposition_status,
            },
            "governance_audit": {
                "disposition_status": env.disposition_status,
                "auto_rejection_permitted": env.auto_rejection_permitted,
                "reviewer_id": env.reviewer_id,
                "reviewer_role": env.reviewer_role,
                "decision_timestamp": env.decision_timestamp,
                "is_override": env.is_override,
                "override_reason": env.override_reason,
                "decision_sha256": env.decision_sha256,
                "chain_entry_sha256": env.chain_entry_sha256,
                "plausible_score_interval": env.plausible_score_interval,
                "version_manifest": env.version_manifest,
            },
        }
        verify_outbound_governance_contract(out, adapter_name="hr_open_xml")
        return out


def export_for_vendor(env: NeutralEvaluationAndGovernanceEnvelope, adapter_name: str) -> Dict[str, Any]:
    key = adapter_name.strip().lower()
    if key == "greenhouse":
        return GreenhouseAdapter.export_evaluation(env)
    if key == "lever":
        return LeverAdapter.export_evaluation(env)
    if key == "ashby":
        return AshbyAdapter.export_evaluation(env)
    if key == "hr_open_xml":
        return HROpenStandardsAdapter.export_evaluation(env)
    if key == "neutral":
        d = dataclasses.asdict(env)
        verify_outbound_governance_contract(d, adapter_name="neutral")
        return d
    raise IntegrationError(f"[INTEGRATION ADAPTER] Unsupported adapter '{adapter_name}'.")


# ---------------------------------------------------------------------------
# Authenticated Webhook Event Processor & Batch-Job Engine
# ---------------------------------------------------------------------------

MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024  # 2 MB payload DoS protection cap
_SEEN_WEBHOOK_NONCES: Dict[str, int] = {}


def reset_webhook_nonce_cache() -> None:
    """Clear the in-memory webhook replay-nonce deduplication cache (used in tests)."""
    _SEEN_WEBHOOK_NONCES.clear()


def compute_webhook_signature(secret: str, timestamp_str: str, raw_body_str: str) -> str:
    """Compute `sha256=<hex>` HMAC-SHA256 signature over `<timestamp>.<raw_body>`."""
    msg = f"{timestamp_str}.{raw_body_str}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_webhook_signature(
    secret: str,
    timestamp_str: str,
    raw_body_str: str,
    provided_signature: str,
    reference_epoch: Optional[int] = None,
    tolerance_seconds: int = 300,
) -> bool:
    """Verify HMAC-SHA256 webhook signature and replay-window timestamp tolerance."""
    if os.environ.get("OPS_REQ_REQUIRE_PROD_SECRETS") == "1" and secret == "ops-req-default-webhook-secret-2026":
        raise WebhookSignatureError(
            "[WEBHOOK AUTH] Default webhook secret is prohibited when OPS_REQ_REQUIRE_PROD_SECRETS=1."
        )
    if not secret or not provided_signature or not timestamp_str:
        raise WebhookSignatureError("[WEBHOOK AUTH] Missing webhook secret, timestamp, or signature header.")
    expected = compute_webhook_signature(secret, timestamp_str, raw_body_str)
    if not hmac.compare_digest(expected, provided_signature.strip()):
        raise WebhookSignatureError("[WEBHOOK AUTH] Invalid HMAC-SHA256 webhook signature.")
    effective_epoch = reference_epoch if reference_epoch is not None else int(time.time())
    try:
        ts_int = int(timestamp_str)
    except ValueError as exc:
        raise WebhookSignatureError(f"[WEBHOOK AUTH] Malformed epoch timestamp '{timestamp_str}'.") from exc
    if abs(effective_epoch - ts_int) > tolerance_seconds:
        raise WebhookSignatureError(
            f"[WEBHOOK AUTH] Webhook timestamp {ts_int} outside {tolerance_seconds}s replay tolerance (ref={effective_epoch})."
        )
    return True


def _resolve_safe_vault_path(candidate_path: str, base_dir: str) -> str:
    candidate = (candidate_path or "").strip()
    if not candidate:
        return ""

    if os.path.isabs(candidate):
        raise WebhookSignatureError(
            "[WEBHOOK VALIDATION] Absolute paths are not permitted for vault_json_path."
        )

    if not candidate.lower().endswith(".json"):
        raise WebhookSignatureError(
            f"[WEBHOOK VALIDATION] vault_json_path '{candidate_path}' must reference a .json file."
        )

    vault_root = os.path.realpath(os.path.join(base_dir, "vaults"))
    resolved = os.path.realpath(os.path.join(vault_root, candidate))

    if os.path.commonpath([vault_root, resolved]) != vault_root:
        raise WebhookSignatureError(
            f"[WEBHOOK VALIDATION] Unsafe vault_json_path '{candidate_path}' is outside allowed vault directory."
        )
    return resolved


def process_signed_webhook_event(
    raw_body_str: str,
    signature_header: str,
    timestamp_header: str,
    webhook_secret: str,
    reference_date: str = "2026-11-01",
    reference_epoch: Optional[int] = None,
    enforce_nonce_dedup: bool = False,
    vault_json_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Verify webhook signature, payload size, and replay nonce, then process one of the 5 supported webhook events:
      - `application.submitted` -> scores candidate in blind mode, exports via vendor adapter, preserves `PENDING_HUMAN_REVIEW`.
      - `requisition.created` / `requisition.updated` -> runs Module 1 RQS audit and returns findings + auto-edited draft.
      - `mobility.profile_submitted` -> runs Module 3 internal mobility matching with employee privacy controls.
      - `privacy.erasure_requested` -> executes right-to-erasure across the 3 segregated vaults and returns a signed `DeletionReceipt`.
    """
    if len(raw_body_str.encode("utf-8")) > MAX_REQUEST_BODY_BYTES:
        raise IntegrationError(
            f"[WEBHOOK SECURITY] Request payload exceeds MAX_REQUEST_BODY_BYTES ({MAX_REQUEST_BODY_BYTES} bytes)."
        )
    verify_webhook_signature(
        secret=webhook_secret,
        timestamp_str=timestamp_header,
        raw_body_str=raw_body_str,
        provided_signature=signature_header,
        reference_epoch=reference_epoch,
    )
    event = json.loads(raw_body_str)
    event_id = str(event.get("event_id", "EVT-001"))
    nonce_key = f"{event_id}:{signature_header.strip()}"
    if enforce_nonce_dedup:
        if nonce_key in _SEEN_WEBHOOK_NONCES:
            raise WebhookSignatureError(
                f"[WEBHOOK REPLAY] Duplicate webhook delivery rejected for event_id '{event_id}'."
            )
        _SEEN_WEBHOOK_NONCES[nonce_key] = int(timestamp_header)
    event_type = str(event.get("event_type", "")).strip()
    vendor = str(event.get("source_system", "greenhouse")).strip().lower()

    if event_type not in SUPPORTED_WEBHOOK_EVENTS:
        raise IntegrationError(
            f"[WEBHOOK ROUTER] Unsupported event_type '{event_type}'. Supported: {sorted(SUPPORTED_WEBHOOK_EVENTS)}."
        )

    if event_type == "application.submitted":
        if vendor == "lever":
            req_env = LeverAdapter.import_requisition(event.get("requisition", {}))
            app_env = LeverAdapter.import_application(event.get("application", {}))
        elif vendor == "ashby":
            req_env = AshbyAdapter.import_requisition(event.get("requisition", {}))
            app_env = AshbyAdapter.import_application(event.get("application", {}))
        else:
            req_env = GreenhouseAdapter.import_requisition(event.get("requisition", {}))
            app_env = GreenhouseAdapter.import_application(event.get("application", {}))

        neutral_env = evaluate_to_neutral_envelope(req_env, app_env, reference_date=reference_date)
        vendor_export = export_for_vendor(neutral_env, adapter_name=vendor if vendor in SUPPORTED_VENDOR_ADAPTERS else "neutral")
        return {
            "status": "PROCESSED",
            "event_id": event_id,
            "event_type": event_type,
            "source_system": vendor,
            "neutral_envelope": dataclasses.asdict(neutral_env),
            "vendor_export": vendor_export,
        }

    if event_type in ("requisition.created", "requisition.updated"):
        req_data = event.get("requisition", {})
        jd_text = str(req_data.get("content") or req_data.get("description_text") or rqa.DEMO_REQUISITION)
        rqs_audit = rqa.audit_text(jd_text)
        return {
            "status": "PROCESSED",
            "event_id": event_id,
            "event_type": event_type,
            "source_system": vendor,
            "rqs_score": rqs_audit.score,
            "projected_rqs_score": rqs_audit.projected_score,
            "band": rqs_audit.band,
            "findings_count": len(rqs_audit.findings),
            "input_sha256": rqs_audit.input_sha256,
        }

    if event_type == "mobility.profile_submitted":
        worker_info = WorkdayHRISMobilityAdapter.import_worker_profile(event.get("worker", {}))
        demo_res, demo_roles = imm.get_demo_career_ladder()
        res_txt = worker_info["resume_text"] or demo_res
        roles = event.get("target_roles") or demo_roles
        mob_rep = imm.match_resume_to_roles(
            resume_text=res_txt,
            role_entries=roles,
            reference_date=datetime.strptime(reference_date[:10], "%Y-%m-%d"),
            share_with_manager=worker_info["share_with_manager"],
            subject_id=worker_info["subject_id"],
        )
        hris_export = WorkdayHRISMobilityAdapter.export_mobility_learning_plan(mob_rep, worker_id=worker_info["subject_id"])
        return {
            "status": "PROCESSED",
            "event_id": event_id,
            "event_type": event_type,
            "source_system": "workday_hris",
            "hris_export": hris_export,
        }

    # privacy.erasure_requested
    subject_id = str(event.get("subject_id", "CAND-004"))
    vault_file = vault_json_path or str(event.get("vault_json_path", ""))
    safe_vault_file = _resolve_safe_vault_path(vault_file, SCRIPT_DIR) if vault_file else ""
    if safe_vault_file and os.path.isfile(safe_vault_file):
        with open(safe_vault_file, "r", encoding="utf-8") as vfh:
            v_raw = json.load(vfh)
        vault = pdm.SegregatedVaultStore(**v_raw)
        receipt = pdm.purge_subject_across_vaults(vault, subject_id_or_token=subject_id, purge_scoring_vault=False)
        rec_dict = dataclasses.asdict(receipt)
        gov.atomic_write_json(safe_vault_file, dataclasses.asdict(vault))
    else:
        vault, _, _ = pdm.run_privacy_demo(reference_date=reference_date)
        tok = subject_id if subject_id.startswith("SUBJ-") else pdm.compute_pseudonym_token(subject_id)
        if tok in vault.pii_vault or tok in vault.demographics_vault or tok in vault.scoring_vault:
            receipt = pdm.purge_subject_across_vaults(vault, subject_id_or_token=subject_id, purge_scoring_vault=False)
            rec_dict = dataclasses.asdict(receipt)
        else:
            rec_dict = vault.deletion_receipts[-1]
    return {
        "status": "PROCESSED",
        "event_id": event_id,
        "event_type": event_type,
        "deletion_receipt": rec_dict,
    }


def run_batch_integration_job(
    job_items: Sequence[Mapping[str, Any]],
    reference_date: str = "2026-11-01",
) -> Dict[str, Any]:
    """
    Execute a batch integration job across multiple applications / systems (Greenhouse, Lever,
    Ashby, Workday HRIS, HR-Open), verifying that 100% of outbound records pass
    `verify_outbound_governance_contract()`.
    """
    processed: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for idx, item in enumerate(job_items, start=1):
        vendor = str(item.get("source_system", "greenhouse")).strip().lower()
        try:
            if vendor == "lever":
                req_env = LeverAdapter.import_requisition(item["requisition"])
                app_env = LeverAdapter.import_application(item["application"])
            elif vendor == "ashby":
                req_env = AshbyAdapter.import_requisition(item["requisition"])
                app_env = AshbyAdapter.import_application(item["application"])
            else:
                req_env = GreenhouseAdapter.import_requisition(item["requisition"])
                app_env = GreenhouseAdapter.import_application(item["application"])

            neutral_env = evaluate_to_neutral_envelope(req_env, app_env, reference_date=reference_date)
            vendor_payload = export_for_vendor(neutral_env, adapter_name=vendor if vendor in SUPPORTED_VENDOR_ADAPTERS else "neutral")
            hropen_payload = HROpenStandardsAdapter.export_evaluation(neutral_env)

            processed.append({
                "seq": idx,
                "source_system": vendor,
                "application_id": app_env.application_id,
                "pseudonym_token": neutral_env.pseudonym_token,
                "composite_score": neutral_env.composite_score,
                "plausible_score_interval": neutral_env.plausible_score_interval,
                "disposition_status": neutral_env.disposition_status,
                "decision_sha256": neutral_env.decision_sha256,
                "chain_entry_sha256": neutral_env.chain_entry_sha256,
                "governance_preserved": True,
                "neutral_envelope": dataclasses.asdict(neutral_env),
                "vendor_export": vendor_payload,
                "hr_open_export": hropen_payload,
            })
        except Exception as exc:
            errors.append({
                "seq": idx,
                "source_system": vendor,
                "error": str(exc),
            })

    return {
        "contract_version": ATS_NEUTRAL_CONTRACT_VERSION,
        "reference_date": reference_date,
        "total_jobs": len(job_items),
        "succeeded_count": len(processed),
        "failed_count": len(errors),
        "all_governance_contracts_verified": (len(errors) == 0 and all(p["governance_preserved"] for p in processed)),
        "results": processed,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# In-Process & HTTP OpenAPI Request Router
# ---------------------------------------------------------------------------

def get_openapi_3_1_spec() -> Dict[str, Any]:
    """Return the OpenAPI 3.1.0 + JSON Schema 2020-12 contract specification."""
    return {
        "openapi": OPENAPI_SPEC_VERSION,
        "jsonSchemaDialect": "https://json-schema.org/draft/2020-12/schema",
        "info": {
            "title": "ops-requisition-auditor ATS-Neutral Integration & Governance API",
            "version": "1.5.0",
            "x-contract-version": ATS_NEUTRAL_CONTRACT_VERSION,
            "description": (
                "Deterministic, human-in-the-loop JD Alignment, Requisition Quality (RQS), "
                "Explainable Batch Screening, and Internal Mobility integration contract. "
                "Enforces PENDING_HUMAN_REVIEW and never permits autonomous hiring decisions."
            ),
        },
        "paths": {
            "/healthz": {
                "get": {
                    "operationId": "getHealthStatus",
                    "summary": "Verify integration service health and human-in-the-loop governance mode.",
                    "responses": {"200": {"description": "Service healthy"}},
                }
            },
            "/openapi.json": {
                "get": {
                    "operationId": "getOpenApiSpec",
                    "summary": "Retrieve the OpenAPI 3.1.0 and ATS-neutral JSON Schema specification.",
                    "responses": {"200": {"description": "OpenAPI 3.1.0 JSON document"}},
                }
            },
            "/v1/evaluate/application": {
                "post": {
                    "operationId": "evaluateCandidateApplication",
                    "summary": "Score a candidate application in blind mode and return an ATS-neutral governance envelope.",
                    "responses": {
                        "200": {"description": "NeutralEvaluationAndGovernanceEnvelope"},
                        "422": {"description": "Governance or anti-automation policy violation"},
                    },
                }
            },
            "/v1/webhooks/events": {
                "post": {
                    "operationId": "receiveSignedWebhookEvent",
                    "summary": "Process an HMAC-SHA256 signed webhook event from Greenhouse, Lever, Ashby, or Workday HRIS.",
                    "responses": {
                        "200": {"description": "Webhook processed with audit & human-review preservation"},
                        "401": {"description": "Invalid HMAC-SHA256 signature or expired replay timestamp"},
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "NeutralRequisitionEnvelope": {
                    "type": "object",
                    "required": ["contract_version", "requisition_id", "external_system", "title"],
                    "properties": {
                        "contract_version": {"type": "string", "const": ATS_NEUTRAL_CONTRACT_VERSION},
                        "requisition_id": {"type": "string"},
                        "external_system": {"type": "string", "enum": sorted(SUPPORTED_VENDOR_ADAPTERS)},
                        "title": {"type": "string"},
                        "location": {"type": "string"},
                        "raw_description_text": {"type": "string"},
                        "requirements": {"type": "array", "items": {"type": "object"}},
                        "alignment": {"type": "object"},
                    },
                },
                "NeutralCandidateApplicationEnvelope": {
                    "type": "object",
                    "required": ["contract_version", "application_id", "candidate_id", "requisition_id", "resume_text"],
                    "properties": {
                        "contract_version": {"type": "string", "const": ATS_NEUTRAL_CONTRACT_VERSION},
                        "application_id": {"type": "string"},
                        "candidate_id": {"type": "string"},
                        "pseudonym_token": {"type": "string"},
                        "requisition_id": {"type": "string"},
                        "resume_text": {"type": "string"},
                        "blind_mode": {"type": "boolean", "default": True},
                        "consent": {"type": "object"},
                        "human_decision": {"type": "object"},
                    },
                },
                "NeutralEvaluationAndGovernanceEnvelope": {
                    "type": "object",
                    "required": [
                        "contract_version",
                        "evaluation_id",
                        "requisition_id",
                        "application_id",
                        "pseudonym_token",
                        "composite_score",
                        "literal_only_score",
                        "plausible_score_interval",
                        "measurement_confidence",
                        "evidence_completeness_pct",
                        "disposition_status",
                        "auto_rejection_permitted",
                        "decision_sha256",
                        "chain_entry_sha256",
                        "version_manifest",
                    ],
                    "properties": {
                        "contract_version": {"type": "string", "const": ATS_NEUTRAL_CONTRACT_VERSION},
                        "evaluation_id": {"type": "string"},
                        "composite_score": {"type": "number", "minimum": 0.0, "maximum": 100.0},
                        "literal_only_score": {"type": "number", "minimum": 0.0, "maximum": 100.0},
                        "plausible_score_interval": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                        "measurement_confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "evidence_completeness_pct": {"type": "number", "minimum": 0.0, "maximum": 100.0},
                        "disposition_status": {"type": "string", "enum": sorted(gov.ALLOWED_DISPOSITION_STATES)},
                        "auto_rejection_permitted": {"type": "boolean", "const": False},
                        "reviewer_id": {"type": "string"},
                        "reviewer_role": {"type": "string"},
                        "decision_timestamp": {"type": "string"},
                        "is_override": {"type": "boolean"},
                        "override_reason": {"type": "string"},
                        "decision_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                        "chain_entry_sha256": {"type": "string", "minLength": 64, "maxLength": 64},
                        "version_manifest": {"type": "object"},
                    },
                },
            }
        },
    }


def handle_integration_api_request(
    method: str,
    path: str,
    headers: Optional[Mapping[str, str]] = None,
    raw_body: str = "",
    webhook_secret: str = "ops-req-default-webhook-secret-2026",
) -> Tuple[int, Dict[str, Any]]:
    """Deterministic in-process router for OpenAPI / Webhook / Evaluation requests."""
    hdrs = {k.lower(): v for k, v in (headers or {}).items()}
    m_up = method.strip().upper()
    clean_path = path.split("?")[0].rstrip("/") or "/"

    if m_up == "GET" and clean_path == "/healthz":
        return (
            200,
            {
                "status": "ok",
                "contract_version": ATS_NEUTRAL_CONTRACT_VERSION,
                "engine_version": gov.ENGINE_VERSION,
                "environment_mode": "HUMAN_IN_THE_LOOP_ENFORCED",
                "auto_rejection_permitted": False,
            },
        )
    if m_up == "GET" and clean_path == "/openapi.json":
        return (200, get_openapi_3_1_spec())

    if m_up == "POST" and clean_path == "/v1/webhooks/events":
        sig = hdrs.get("x-ops-req-signature-256", "")
        ts = hdrs.get("x-ops-req-timestamp", "")
        ref_ep_hdr = hdrs.get("x-ops-req-reference-epoch")
        ref_ep = int(ref_ep_hdr) if (ref_ep_hdr and ref_ep_hdr.isdigit()) else int(ts) if (ts and ts.isdigit() and os.environ.get("OPS_REQ_STRICT_WALLCLOCK_WEBHOOK") != "1") else None
        try:
            res = process_signed_webhook_event(
                raw_body_str=raw_body,
                signature_header=sig,
                timestamp_header=ts,
                webhook_secret=webhook_secret,
                reference_epoch=ref_ep,
            )
            return (200, res)
        except WebhookSignatureError as exc:
            return (401, {"error": str(exc)})
        except (IntegrationError, gov.GovernanceError, pdm.PrivacyError) as exc:
            return (422, {"error": str(exc)})

    if m_up == "POST" and clean_path == "/v1/evaluate/application":
        try:
            if len(raw_body.encode("utf-8")) > MAX_REQUEST_BODY_BYTES:
                return (413, {"error": f"Payload exceeds {MAX_REQUEST_BODY_BYTES} bytes."})
            body_obj = json.loads(raw_body) if raw_body else {}
            gov.enforce_anti_automation_api_params(body_obj)
            vendor = str(body_obj.get("source_system", "greenhouse")).lower()
            if vendor == "lever":
                req_env = LeverAdapter.import_requisition(body_obj["requisition"])
                app_env = LeverAdapter.import_application(body_obj["application"])
            elif vendor == "ashby":
                req_env = AshbyAdapter.import_requisition(body_obj["requisition"])
                app_env = AshbyAdapter.import_application(body_obj["application"])
            else:
                req_env = GreenhouseAdapter.import_requisition(body_obj["requisition"])
                app_env = GreenhouseAdapter.import_application(body_obj["application"])
            neutral_env = evaluate_to_neutral_envelope(req_env, app_env)
            return (
                200,
                {
                    "neutral_envelope": dataclasses.asdict(neutral_env),
                    "vendor_export": export_for_vendor(neutral_env, adapter_name=vendor if vendor in SUPPORTED_VENDOR_ADAPTERS else "neutral"),
                },
            )
        except (IntegrationError, gov.GovernanceError, pdm.PrivacyError) as exc:
            return (422, {"error": str(exc)})

    return (404, {"error": f"Unknown endpoint '{m_up} {clean_path}'."})


# ---------------------------------------------------------------------------
# Sample Fixtures, Demo Runner & Markdown Renderer
# ---------------------------------------------------------------------------

def get_sample_integration_bundle() -> Dict[str, Any]:
    resume_txt, reqs, align_cfg = oaa.get_demo_fixture()
    opt_path = gov.resolve_input_path("examples/sample_resume_optimized.txt")
    if os.path.exists(opt_path):
        with open(opt_path, "r", encoding="utf-8") as fh:
            opt_resume_txt = fh.read()
    else:
        opt_resume_txt = esa.DEFAULT_OPTIMIZED_RESUME
    req_dicts = [dataclasses.asdict(r) for r in reqs]
    align_dict = dataclasses.asdict(align_cfg)

    batch_jobs = [
        {
            "source_system": "greenhouse",
            "requisition": {
                "id": "GH-REQ-901",
                "title": "Senior Analytics Manager",
                "company": "Northstar Contact Solutions",
                "location": {"name": "Denver, CO"},
                "departments": [{"name": "Workforce Operations & BI"}],
                "requirements": req_dicts,
                "alignment": align_dict,
            },
            "application": {
                "id": "GH-APP-1001",
                "candidate": {"id": "CAND-001"},
                "requisition_id": "GH-REQ-901",
                "resume_text": opt_resume_txt,
                "blind_mode": True,
                "consent": {
                    "subject_id": "CAND-001",
                    "flow_type": "batch_screening",
                    "consented": True,
                    "consent_scope": ["batch_screening"],
                    "consent_timestamp": "2026-11-01T08:00:00Z",
                    "consent_method": "applicant_portal_opt_in",
                },
                "human_decision": {
                    "status": "ADVANCE",
                    "reviewer": "recruiter_j_smith",
                    "reviewer_role": "recruiter",
                    "timestamp": "2026-11-01T10:00:00Z",
                    "override_reason": "",
                },
            },
        },
        {
            "source_system": "lever",
            "requisition": {
                "id": "LEV-REQ-902",
                "text": "Senior Analytics Manager",
                "company": "Northstar Contact Solutions",
                "categories": {"team": "Workforce Analytics", "location": "Chicago, IL"},
                "requirements": req_dicts,
                "alignment": align_dict,
            },
            "application": {
                "id": "LEV-OPP-2002",
                "contact": "CAND-002",
                "posting_id": "LEV-REQ-902",
                "resume_text": resume_txt,
                "blind_mode": True,
                "human_decision": {
                    "status": "PENDING_HUMAN_REVIEW",
                },
            },
        },
        {
            "source_system": "ashby",
            "requisition": {
                "id": "ASH-REQ-903",
                "title": "Senior Analytics Manager",
                "company": "Northstar Contact Solutions",
                "departmentName": "Operations Analytics",
                "locationName": "New York, NY",
                "requirements": req_dicts,
                "alignment": align_dict,
            },
            "application": {
                "id": "ASH-APP-3003",
                "candidateId": "CAND-003",
                "jobId": "ASH-REQ-903",
                "resume_text": resume_txt,
                "blind_mode": True,
                "human_decision": {
                    "status": "ADVANCE",
                    "reviewer": "ta_lead_m_chen",
                    "reviewer_role": "ta_lead",
                    "timestamp": "2026-11-01T11:15:00Z",
                    "is_override": True,
                    "override_reason": "Human override: verified capacity planning and operating budget scope during structured intake.",
                },
            },
        },
    ]

    sample_webhook_event = {
        "event_id": "EVT-GH-20261101-001",
        "event_type": "application.submitted",
        "source_system": "greenhouse",
        "occurred_at": "2026-11-01T12:00:00Z",
        "requisition": batch_jobs[0]["requisition"],
        "application": {
            "id": "GH-APP-1004",
            "candidate": {"id": "CAND-004"},
            "requisition_id": "GH-REQ-901",
            "resume_text": resume_txt,
            "blind_mode": True,
        },
    }

    return {
        "contract_version": ATS_NEUTRAL_CONTRACT_VERSION,
        "batch_jobs": batch_jobs,
        "sample_webhook_event": sample_webhook_event,
    }


def render_integration_demo_markdown(
    batch_res: Mapping[str, Any],
    webhook_res: Mapping[str, Any],
    mobility_hris_res: Mapping[str, Any],
) -> str:
    lines: List[str] = [
        "# ATS-Neutral Integration SDK, Vendor Adapters, Webhooks & Batch Report (`ops-req-integrate`)",
        "",
        "*Verifies the versioned ATS-neutral JSON contract (`ats-neutral-contract-1.0`), bidirectional adapters for Greenhouse, Lever, Ashby, Workday HRIS, and HR-Open Standards, HMAC-SHA256 signed webhook processing, batch-job execution, and 100% preservation of human-review and cryptographic audit fields.*",
        "",
        "## 1. Contract & Integration Pipeline Telemetry",
        "",
        f"- **ATS-Neutral Contract Version:** `{ATS_NEUTRAL_CONTRACT_VERSION}` (OpenAPI Specification: `{OPENAPI_SPEC_VERSION}`)",
        f"- **Supported Vendor Adapters:** `{', '.join(sorted(SUPPORTED_VENDOR_ADAPTERS))}`",
        f"- **Batch Jobs Processed:** `{batch_res['succeeded_count']} / {batch_res['total_jobs']}` (`Failed: {batch_res['failed_count']}`)",
        f"- **Outbound Audit & Human-Review Contract Verification:** `{'PASS (100% of outbound records preserve disposition, reviewer sign-off, plausible interval, and SHA-256 chain)' if batch_res['all_governance_contracts_verified'] else 'FAIL'}`",
        f"- **Signed Webhook Event Verification (`HMAC-SHA256`):** `{webhook_res['status']}` (Event `{webhook_res['event_id']}` `{webhook_res['event_type']}` -> Default Disposition `{webhook_res['neutral_envelope']['disposition_status']}`)",
        f"- **Workday HRIS Internal Mobility Adapter:** `PROCESSED` (Worker `{mobility_hris_res['worker_pseudonym_token']}` | Manager Feed Visibility: `{mobility_hris_res['manager_feed_visibility']}`)",
        "",
        "## 2. Multi-System Batch Job & Adapter Translation Ledger",
        "",
        "| Seq | Source ATS / System | Application ID | Pseudonym Token | Score (`Plausible Band`) | Disposition Status | Decision SHA-256 | Chain Entry SHA-256 | Governance Preserved |",
        "| :-: | :--- | :--- | :--- | :-: | :--- | :--- | :--- | :-: |",
    ]
    for r in batch_res["results"]:
        band = r["plausible_score_interval"]
        lines.append(
            f"| {r['seq']} | `{r['source_system']}` | `{r['application_id']}` | `{r['pseudonym_token']}` | "
            f"**{r['composite_score']:.1f}** (`[{band[0]:.1f}, {band[1]:.1f}]`) | `{r['disposition_status']}` | "
            f"`{r['decision_sha256'][:12]}…` | `{r['chain_entry_sha256'][:12]}…` | `{'VERIFIED' if r['governance_preserved'] else 'FAIL'}` |"
        )

    lines += [
        "",
        "## 3. Workday / SuccessFactors HRIS Internal Mobility & L&D Export Summary",
        "",
        f"- **Worker Pseudonym Token:** `{mobility_hris_res['worker_pseudonym_token']}`",
        f"- **Employee-Initiated Exploration:** `{mobility_hris_res['employee_initiated']}` (`share_with_manager: {mobility_hris_res['share_with_manager']}` -> `{mobility_hris_res['manager_feed_visibility']}`)",
        f"- **Processing Mode & Retention:** `{mobility_hris_res['processing_mode']}` (`retention_days: {mobility_hris_res['retention_days']}`)",
        f"- **Career Paths Exported to L&D Feed:** `{len(mobility_hris_res['recommended_career_paths'])}` roles with 30/60/90-day reskilling milestones",
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-integrate`)
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops-req-integrate",
        description="ATS-Neutral Integration SDK, Vendor Import/Export Adapters, Webhook Processor & Batch-Job Engine.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the built-in multi-ATS (Greenhouse, Lever, Ashby, Workday HRIS) batch & signed webhook integration demo.")
    parser.add_argument("--batch-job", help="Path to a batch integration job manifest JSON file.")
    parser.add_argument("--webhook-event", help="Path to a webhook event JSON file to verify and process.")
    parser.add_argument("--webhook-secret", default="ops-req-default-webhook-secret-2026", help="HMAC-SHA256 webhook secret.")
    parser.add_argument("--export-openapi", help="Export the OpenAPI 3.1.0 & JSON Schema 2020-12 contract specification to this path.")
    parser.add_argument("--export-sample-bundle", help="Export the sample ATS-neutral batch & webhook contract bundle JSON to this path.")
    parser.add_argument("--reference-date", default="2026-11-01", help="Reference date YYYY-MM-DD.")
    parser.add_argument("--output", help="Write Markdown integration report to this path.")
    parser.add_argument("--json-out", help="Write full JSON batch/webhook output to this path.")
    parser.add_argument("--jsonl-out", help="Write atomic JSONL stream of processed neutral envelopes to this path.")
    parser.add_argument("--serve-port", type=int, default=0, help="Run the local integration HTTP API server on this TCP port (binds 127.0.0.1 by default).")
    parser.add_argument("--serve-host", default="127.0.0.1", help="Bind address for --serve-port (default: 127.0.0.1).")

    args = parser.parse_args(argv)

    if args.serve_port > 0:
        secret = args.webhook_secret

        class _IntegrationHandler(BaseHTTPRequestHandler):
            def _dispatch(self, method_name: str) -> None:
                clen = int(self.headers.get("Content-Length", "0") or "0")
                if clen > MAX_REQUEST_BODY_BYTES:
                    self.send_response(413)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Payload Too Large"}).encode("utf-8"))
                    return
                raw = self.rfile.read(clen).decode("utf-8", errors="replace") if clen > 0 else ""
                hdr_dict = {k.lower(): v for k, v in self.headers.items()}
                code, resp = handle_integration_api_request(method_name, self.path, headers=hdr_dict, raw_body=raw, webhook_secret=secret)
                body_bytes = json.dumps(resp).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body_bytes)))
                self.end_headers()
                self.wfile.write(body_bytes)

            def do_GET(self) -> None:
                self._dispatch("GET")

            def do_POST(self) -> None:
                self._dispatch("POST")

        srv = HTTPServer((args.serve_host, args.serve_port), _IntegrationHandler)
        srv.timeout = 15
        sys.stdout.write(f"[INTEGRATE HTTP] Listening on http://{args.serve_host}:{args.serve_port} (max body {MAX_REQUEST_BODY_BYTES} bytes)\n")
        srv.serve_forever()
        return 0

    try:
        if args.export_openapi:
            with open(args.export_openapi, "w", encoding="utf-8") as fh:
                json.dump(get_openapi_3_1_spec(), fh, indent=2)
            sys.stdout.write(f"[INTEGRATE OK] Exported OpenAPI 3.1.0 & JSON Schema contract to {args.export_openapi}\n")
            if not args.export_sample_bundle and not args.demo:
                return 0

        bundle = get_sample_integration_bundle()
        if args.export_sample_bundle:
            with open(args.export_sample_bundle, "w", encoding="utf-8") as fh:
                json.dump(bundle, fh, indent=2)
            sys.stdout.write(f"[INTEGRATE OK] Exported sample ATS-neutral integration bundle to {args.export_sample_bundle}\n")
            if not args.demo:
                return 0

        if args.webhook_event:
            wp = gov.resolve_input_path(args.webhook_event)
            with open(wp, "r", encoding="utf-8") as fh:
                raw_ev = fh.read()
            ts = "1793534400"
            sig = compute_webhook_signature(args.webhook_secret, ts, raw_ev)
            res = process_signed_webhook_event(
                raw_body_str=raw_ev,
                signature_header=sig,
                timestamp_header=ts,
                webhook_secret=args.webhook_secret,
                reference_date=args.reference_date,
                reference_epoch=1793534400,
            )
            out_str = json.dumps(res, indent=2)
            if args.json_out:
                with open(args.json_out, "w", encoding="utf-8") as fh:
                    fh.write(out_str + "\n")
            sys.stdout.write(out_str + "\n")
            return 0

        if args.batch_job:
            bp = gov.resolve_input_path(args.batch_job)
            with open(bp, "r", encoding="utf-8") as fh:
                b_raw = json.load(fh)
            job_items = b_raw.get("batch_jobs", b_raw) if isinstance(b_raw, Mapping) else b_raw
        else:
            job_items = bundle["batch_jobs"]

        batch_res = run_batch_integration_job(job_items, reference_date=args.reference_date)

        raw_wh = json.dumps(bundle["sample_webhook_event"])
        ts = "1793534400"
        sig = compute_webhook_signature(args.webhook_secret, ts, raw_wh)
        webhook_res = process_signed_webhook_event(
            raw_body_str=raw_wh,
            signature_header=sig,
            timestamp_header=ts,
            webhook_secret=args.webhook_secret,
            reference_date=args.reference_date,
            reference_epoch=1793534400,
        )

        demo_res, demo_roles = imm.get_demo_career_ladder()
        mob_rep = imm.match_resume_to_roles(
            resume_text=demo_res,
            role_entries=demo_roles,
            reference_date=datetime.strptime(args.reference_date[:10], "%Y-%m-%d"),
            share_with_manager=False,
            subject_id="EMP-001",
        )
        mobility_hris_res = WorkdayHRISMobilityAdapter.export_mobility_learning_plan(mob_rep, worker_id="EMP-001")

        md_out = render_integration_demo_markdown(batch_res, webhook_res, mobility_hris_res)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as fh:
                fh.write(md_out)
        else:
            sys.stdout.write(md_out)

        if args.json_out:
            with open(args.json_out, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "batch_integration": batch_res,
                        "webhook_verification": webhook_res,
                        "workday_hris_mobility_export": mobility_hris_res,
                    },
                    fh,
                    indent=2,
                )

        if args.jsonl_out:
            with open(args.jsonl_out, "w", encoding="utf-8") as fh:
                for item in batch_res["results"]:
                    fh.write(json.dumps(item["neutral_envelope"]) + "\n")

        return 0 if batch_res["all_governance_contracts_verified"] else 1

    except (IntegrationError, gov.GovernanceError, pdm.PrivacyError, FileNotFoundError, json.JSONDecodeError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
