#!/usr/bin/env python3
"""
ops-requisition-auditor: Internal Mobility & Outplacement Matcher (Employer Edition, Module 3)
==============================================================================================
Matches an employee's or displaced worker's resume against a portfolio of open requisitions
using the deterministic 100-point JD Alignment engine in `offline_ats_auditor.py`, and
builds a taxonomy-driven 30/60/90-day reskilling and gap-closing plan for each role.

Supports two operating modes:
  1. Internal Mobility Mode (default): employee-initiated, opt-in career-path matching
     (e.g., Contact-Center Agent -> RTA -> WFM Scheduler -> Forecasting Analyst -> Capacity
     Planner / Analytics Manager) with 30/60/90-day reskilling milestones.
  2. Outplacement & Workforce Board Mode (`--outplacement-pack`): ranks a displaced worker's
     most attainable target roles, calculates projected scores after honest Tier 1/2 resume
     updates, and generates role-specific gap-confirmation questions.

Guardrails:
  - 100% deterministic Python 3 standard library (no LLM in the scoring or planning path).
  - Zero fabrication: never invents metrics, tools, or credentials; missing facts stay
    explicit questions or `[INSERT: ...]` placeholders.
  - Restricted job boards remain paste-only via `job_posting_collector.py`.
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import offline_ats_auditor as oaa  # noqa: E402
import job_posting_collector as jpc  # noqa: E402
import governance_middleware as gov  # noqa: E402
import privacy_data_minimization as pdm  # noqa: E402

ENGINE_VERSION = gov.ENGINE_VERSION
MODULE_NAME = "internal-mobility"
RESKILLING_CATALOG_VERSION = "wfm-reskill-cat-2.0"

DISCLAIMER = (
    "*A deterministic career-path and outplacement matching aid. Scores and 30/60/90-day plans "
    "are transparent heuristics, not automated promotion decisions or legal advice.*"
)

# Governed Taxonomy-Driven Learning, Prerequisite & Stretch-Project Catalog for WFM, Operations, and BI
RESKILLING_CATALOG: Dict[str, Dict[str, Any]] = {
    "erlang c": {
        "catalog_item_id": "CAT-WFM-ERLANG-C",
        "domain": "WFM Staffing Math",
        "day_60_learning": "Complete queueing theory & Erlang C staffing module (arrival rate, AHT, occupancy cap, shrinkage, and service-level 80/20 trade-offs).",
        "day_90_project": "Build a parallel Erlang C interval staffing workbook for one inbound queue and compare against actuals for 2 weeks.",
        "learning_resources": [
            "WFM Queueing Theory & Erlang C Interval Staffing Workbook (12 hrs)",
            "Intraday Service-Level & Occupancy Trade-Off Simulation Lab",
        ],
        "prerequisites": ["Excel or SQL fundamentals", "Contact-center metrics (AHT, ASA, Shrinkage)"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "peer_reviewed_staffing_workbook",
        "catalog_owner": "WFM & Operations Analytics L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "workforce capacity modeling": {
        "catalog_item_id": "CAT-WFM-CAPACITY-MODEL",
        "domain": "WFM & Capacity Planning",
        "day_60_learning": "Complete multi-week capacity modeling training (headcount supply, attrition, hiring ramp, shrinkage, and FTE demand).",
        "day_90_project": "Shadow the capacity planner on a quarterly 13-week FTE capacity review and co-author the assumptions table.",
        "learning_resources": [
            "13-Week & 52-Week FTE Capacity Modeling Curriculum (16 hrs)",
            "Hiring Ramp, Attrition & Shrinkage Assumptions Playbook",
        ],
        "prerequisites": ["Demand Forecasting", "SQL or Advanced Spreadsheet Modeling"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "co_authored_13_week_capacity_model",
        "catalog_owner": "WFM & Operations Analytics L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "capacity planning": {
        "catalog_item_id": "CAT-WFM-CAPACITY-PLAN",
        "domain": "WFM & Capacity Planning",
        "day_60_learning": "Complete capacity planning fundamentals (FTE demand vs. net supply, shrinkage, and hiring-class lead times).",
        "day_90_project": "Build a 13-week staffing outlook for one site or queue and review with the WFM lead.",
        "learning_resources": [
            "Capacity Planning & Net Supply Fundamentals (10 hrs)",
        ],
        "prerequisites": ["Forecasting fundamentals"],
        "requires_credential_or_prerequisite": False,
        "verification_artifact_type": "reviewed_staffing_outlook",
        "catalog_owner": "WFM & Operations Analytics L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "demand forecasting": {
        "catalog_item_id": "CAT-WFM-DEMAND-FORECAST",
        "domain": "Forecasting & Analytics",
        "day_60_learning": "Complete time-series forecasting module (seasonality, day-of-week/interval patterns, WAPE/MAPE error tracking).",
        "day_90_project": "Produce a shadow weekly and intraday volume forecast for 4 consecutive weeks and track MAPE against actuals.",
        "learning_resources": [
            "Time-Series Volume Forecasting & WAPE/MAPE Error Tracking (14 hrs)",
            "Seasonality & Holiday Arrival-Pattern Calibration Guide",
        ],
        "prerequisites": ["SQL or Python data aggregation"],
        "requires_credential_or_prerequisite": False,
        "verification_artifact_type": "four_week_shadow_forecast_mape_log",
        "catalog_owner": "WFM & Operations Analytics L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "forecasting": {
        "catalog_item_id": "CAT-WFM-FORECAST-BASE",
        "domain": "Forecasting & Analytics",
        "day_60_learning": "Complete contact-volume forecasting fundamentals (historical arrival patterns, holiday adjustments, MAPE/bias).",
        "day_90_project": "Co-build the weekly interval forecast for one support channel.",
        "learning_resources": [
            "Contact-Volume Forecasting Fundamentals (8 hrs)",
        ],
        "prerequisites": [],
        "requires_credential_or_prerequisite": False,
        "verification_artifact_type": "weekly_interval_forecast_deliverable",
        "catalog_owner": "WFM & Operations Analytics L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "nice iex": {
        "catalog_item_id": "CAT-WFM-NICE-IEX",
        "domain": "Contact-Center WFM Platform",
        "day_60_learning": "Complete NICE IEX administrator/scheduler training (forecaster, schedule bidding, exception codes, and intraday RTA views).",
        "day_90_project": "Shadow a WFM scheduler on NICE IEX shift generation and intraday exception management for two scheduling cycles.",
        "learning_resources": [
            "NICE IEX Forecaster & Schedule Bidding Sandbox Lab (12 hrs)",
            "Intraday RTA & Exception Management Standard Operating Procedure",
        ],
        "prerequisites": ["WFM scheduling & adherence concepts"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "two_cycle_shadow_schedule_signoff",
        "catalog_owner": "WFM & Operations Analytics L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "verint": {
        "catalog_item_id": "CAT-WFM-VERINT",
        "domain": "Contact-Center WFM Platform",
        "day_60_learning": "Complete Verint Workforce Management forecasting and shift-scheduling workflow training.",
        "day_90_project": "Shadow a Verint planner on weekly schedule publication and adherence tracking.",
        "learning_resources": [
            "Verint WFM Forecasting & Shift Publication Lab (12 hrs)",
        ],
        "prerequisites": ["WFM scheduling & adherence concepts"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "shadow_schedule_publication_signoff",
        "catalog_owner": "WFM & Operations Analytics L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "genesys cloud": {
        "catalog_item_id": "CAT-WFM-GENESYS",
        "domain": "Contact-Center WFM Platform",
        "day_60_learning": "Complete Genesys Cloud WFM module (short-term forecasting, load-based scheduling, real-time adherence).",
        "day_90_project": "Configure and monitor a test schedule in Genesys Cloud WFM alongside a senior analyst.",
        "learning_resources": [
            "Genesys Cloud WFM Short-Term Forecasting & Load-Based Scheduling (10 hrs)",
        ],
        "prerequisites": ["WFM scheduling & adherence concepts"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "sandbox_schedule_configuration_signoff",
        "catalog_owner": "WFM & Operations Analytics L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "sql": {
        "catalog_item_id": "CAT-DATA-SQL",
        "domain": "Data & Analytics",
        "day_60_learning": "Complete operational SQL curriculum (SELECT, JOIN, GROUP BY, window functions, and date/interval aggregations).",
        "day_90_project": "Convert one manual spreadsheet report into a repeatable SQL query validated by the analytics team.",
        "learning_resources": [
            "Operational SQL & Window Functions for WFM/BI Analysts (15 hrs)",
        ],
        "prerequisites": [],
        "requires_credential_or_prerequisite": False,
        "verification_artifact_type": "peer_reviewed_sql_query",
        "catalog_owner": "Enterprise BI & Data Engineering L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "python": {
        "catalog_item_id": "CAT-DATA-PYTHON",
        "domain": "Data & Forecasting",
        "day_60_learning": "Complete Python for data analysis & forecasting (pandas data cleaning, time-series aggregation, automated reporting).",
        "day_90_project": "Automate one recurring data-prep or forecasting workflow in Python.",
        "learning_resources": [
            "Python & Pandas Time-Series Automation for Operations Analysts (18 hrs)",
        ],
        "prerequisites": ["SQL or structured data manipulation"],
        "requires_credential_or_prerequisite": False,
        "verification_artifact_type": "code_reviewed_automation_script",
        "catalog_owner": "Enterprise BI & Data Engineering L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "power bi": {
        "catalog_item_id": "CAT-BI-POWERBI",
        "domain": "BI & Visualization",
        "day_60_learning": "Complete Power BI data modeling & DAX fundamentals (star schema, KPI cards, drill-through operational views).",
        "day_90_project": "Build and publish a self-service Power BI SLA/adherence tracker for team leads.",
        "learning_resources": [
            "Power BI Star-Schema Modeling & DAX KPI Design (14 hrs)",
        ],
        "prerequisites": ["SQL"],
        "requires_credential_or_prerequisite": False,
        "verification_artifact_type": "published_powerbi_dashboard",
        "catalog_owner": "Enterprise BI & Data Engineering L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "tableau": {
        "catalog_item_id": "CAT-BI-TABLEAU",
        "domain": "BI & Visualization",
        "day_60_learning": "Complete Tableau visual analytics & calculated-fields module.",
        "day_90_project": "Build an interactive Tableau operational scorecard for weekly leadership review.",
        "learning_resources": [
            "Tableau Visual Analytics & LOD Expressions Module (14 hrs)",
        ],
        "prerequisites": ["SQL"],
        "requires_credential_or_prerequisite": False,
        "verification_artifact_type": "published_tableau_scorecard",
        "catalog_owner": "Enterprise BI & Data Engineering L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "snowflake": {
        "catalog_item_id": "CAT-CLOUD-SNOWFLAKE",
        "domain": "Cloud Data Warehouse",
        "day_60_learning": "Complete Snowflake SQL & data-pipeline querying fundamentals.",
        "day_90_project": "Query warehouse views in Snowflake to validate weekly SLA and volume metrics.",
        "learning_resources": [
            "Snowflake Data Warehouse & Analytical Views Lab (10 hrs)",
        ],
        "prerequisites": ["SQL"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "validated_snowflake_warehouse_query",
        "catalog_owner": "Enterprise BI & Data Engineering L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "sap s/4hana": {
        "catalog_item_id": "CAT-ERP-SAP-S4HANA",
        "domain": "ERP & Enterprise Systems",
        "day_60_learning": "Complete SAP S/4HANA operational reporting & master-data navigation overview.",
        "day_90_project": "Partner with finance/supply-chain ops to reconcile one weekly SAP S/4HANA data extract.",
        "learning_resources": [
            "SAP S/4HANA Operational Reporting & Master-Data Navigation (12 hrs)",
        ],
        "prerequisites": ["SQL or ERP reporting fundamentals"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "reconciled_sap_data_extract",
        "catalog_owner": "Enterprise BI & Data Engineering L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "p&l ownership": {
        "catalog_item_id": "CAT-FIN-PNL-SCOPE",
        "domain": "Financial & Operational Scope",
        "day_60_learning": "Complete operational finance & budget variance training (cost per contact, labor budget vs. actuals, unit economics).",
        "day_90_project": "Co-present the monthly labor variance and overtime cost analysis with the operations manager.",
        "learning_resources": [
            "Operational Finance, Labor Variance & Unit Economics for Leaders (10 hrs)",
        ],
        "prerequisites": ["Workforce Capacity Modeling"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "monthly_budget_variance_presentation",
        "catalog_owner": "Operations Leadership L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
    "six sigma black belt": {
        "catalog_item_id": "CAT-PROC-SSBB",
        "domain": "Process Improvement",
        "day_60_learning": "Complete Lean Six Sigma DMAIC project methodology and root-cause analysis coursework.",
        "day_90_project": "Lead a scoped DMAIC continuous-improvement project targeting shrinkage, AHT, or reporting cycle time.",
        "learning_resources": [
            "ASQ Lean Six Sigma DMAIC & Statistical Process Control Prep (24 hrs)",
        ],
        "prerequisites": ["Six Sigma Green Belt or 2 Completed DMAIC Projects", "Formal ASQ/IASSC Certification Exam"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "certified_dmaic_charter_or_credential",
        "catalog_owner": "Continuous Improvement & Quality Board",
        "last_reviewed_date": "2026-11-01",
    },
    "kubernetes": {
        "catalog_item_id": "CAT-CLOUD-K8S",
        "domain": "Cloud & Container Infrastructure",
        "day_60_learning": "Complete container orchestration & Kubernetes fundamentals for analytics/data services.",
        "day_90_project": "Shadow the platform team on a containerized batch-job deployment.",
        "learning_resources": [
            "Container Fundamentals & Kubernetes Batch Workloads Lab (12 hrs)",
        ],
        "prerequisites": ["Python", "Docker / Container Fundamentals"],
        "requires_credential_or_prerequisite": True,
        "verification_artifact_type": "shadow_container_deployment_signoff",
        "catalog_owner": "Enterprise BI & Data Engineering L&D Board",
        "last_reviewed_date": "2026-11-01",
    },
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ReskillingStep:
    horizon: str            # "Days 1-30" | "Days 31-60" | "Days 61-90"
    requirement: str
    tier: str
    gap_type: str           # "Tier 1 Synonym" | "Tier 2 Placement" | "Tier 3 Skill/Gate Gap"
    potential_score_gain: float
    action: str
    verification_milestone: str
    catalog_item_id: str = "CAT-GENERIC"
    employee_confirmation_status: str = "PENDING_EMPLOYEE_CONFIRMATION"  # "CONFIRMED_BY_EMPLOYEE" | "PENDING_EMPLOYEE_CONFIRMATION" | "DECLINED_NOT_HELD"
    employee_evidence_note: str = ""
    learning_resources: List[str] = field(default_factory=list)
    prerequisites: List[str] = field(default_factory=list)
    requires_credential_or_prerequisite: bool = False
    verification_artifact_type: str = "employee_and_mentor_review"
    mentor_approval_status: str = "PENDING_MENTOR_REVIEW"
    manager_approval_status: str = "NOT_SHARED_WITH_MANAGER"


@dataclass
class MentorAndManagerApprovalRecord:
    mentor_id: str = ""
    mentor_approval_status: str = "PENDING_MENTOR_REVIEW"     # "APPROVED" | "IN_REVIEW" | "PENDING_MENTOR_REVIEW"
    mentor_approved_timestamp: str = ""
    mentor_verified_milestones: List[str] = field(default_factory=list)
    manager_id: str = ""
    manager_approval_status: str = "NOT_SHARED_PRIVATE"       # "NOT_SHARED_PRIVATE" | "PENDING_MANAGER_REVIEW" | "APPROVED"
    manager_approved_timestamp: str = ""
    approval_notes: str = ""


@dataclass
class MobilityPlanOutcomeRecord:
    role_id: str
    role_title: str
    baseline_score: float
    confirmed_30d_score: float
    projected_30d_score: float
    day_60_milestones_completed: int
    day_60_milestones_total: int
    day_90_post_upskilling_score: Optional[float] = None
    credentials_or_prerequisites_cleared: bool = False
    transition_outcome: str = "ACTIVE_UPSKILLING_IN_PROGRESS"  # "INTERNAL_TRANSFER_COMPLETED" | "PROMOTED_TO_TARGET_ROLE" | "OUTPLACEMENT_OFFER_ACCEPTED" | "ACTIVE_UPSKILLING_IN_PROGRESS" | "RETAINED_IN_CURRENT_ROLE"
    day_90_performance_status: str = "PENDING_90D_MILESTONE"   # "MEETS_OR_EXCEEDS_90D_BAR" | "RAMPING_ON_TRACK" | "PENDING_90D_MILESTONE"
    retained_at_90_days: Optional[bool] = None


@dataclass
class RoleMatchRecord:
    rank: int
    role_id: str
    title: str
    company: str
    location: str
    source: str
    url: str
    current_score: float
    literal_score: float
    projected_30d_score: float
    readiness_band: str
    knockout_passed: bool
    unverified_gates: List[str]
    matched_count: int
    partial_count: int
    missing_count: int
    strengths: List[str]
    plan_30_60_90: List[ReskillingStep]
    gap_questions: List[str]
    confirmed_30d_score: float = 0.0
    confirmed_30d_items_count: int = 0
    unconfirmed_30d_items_count: int = 0
    credential_or_prerequisite_alerts: List[str] = field(default_factory=list)
    approvals: MentorAndManagerApprovalRecord = field(default_factory=MentorAndManagerApprovalRecord)
    outcome_tracking: Optional[MobilityPlanOutcomeRecord] = None


@dataclass
class MobilityPortfolioReport:
    tool: str
    module: str
    mode: str               # "internal-mobility" | "outplacement-pack"
    engine_version: str
    reference_date: str
    employee_initiated: bool
    share_with_manager: bool
    resume_sha256: str
    roles_evaluated: int
    ready_now_count: int
    near_ready_count: int
    stretch_count: int
    matches: List[RoleMatchRecord]
    subject_pseudonym_token: str = ""
    consent_verified: bool = True
    consent_sha256: str = ""
    processing_mode: str = "ephemeral_in_memory"
    retention_days: int = 0
    reskilling_catalog_version: str = RESKILLING_CATALOG_VERSION
    reskilling_catalog_sha256: str = ""
    sharing_permissions: Dict[str, bool] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Catalog Governance & Core Mobility Engine
# ---------------------------------------------------------------------------

def validate_reskilling_catalog_governance(
    catalog: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Tuple[bool, str]:
    """
    Verify that every reskilling catalog entry has required governance metadata:
    `catalog_item_id`, `domain`, `day_60_learning`, `day_90_project`, `learning_resources`,
    `prerequisites`, `verification_artifact_type`, `catalog_owner`, and `last_reviewed_date`.
    Returns `(True, catalog_sha256)`.
    """
    cat = catalog or RESKILLING_CATALOG
    required_keys = (
        "catalog_item_id",
        "domain",
        "day_60_learning",
        "day_90_project",
        "learning_resources",
        "prerequisites",
        "verification_artifact_type",
        "catalog_owner",
        "last_reviewed_date",
    )
    for skill_key, entry in cat.items():
        for rk in required_keys:
            if rk not in entry:
                raise ValueError(f"[CATALOG GOVERNANCE] Skill '{skill_key}' is missing required field '{rk}'.")
        if not isinstance(entry["learning_resources"], list) or len(entry["learning_resources"]) == 0:
            raise ValueError(f"[CATALOG GOVERNANCE] Skill '{skill_key}' must list at least one learning resource.")
    canonical = json.dumps(cat, sort_keys=True)
    return (True, hashlib.sha256(canonical.encode("utf-8")).hexdigest())


def readiness_band_for(score: float) -> str:
    if score >= 80.0:
        return "Ready Now (Immediate Transfer / Application)"
    if score >= 65.0:
        return "Near-Ready (30-to-60-Day Bridge)"
    return "Stretch Role (90-Day Reskilling Path)"


def _lookup_catalog(req_name: str) -> Dict[str, Any]:
    key = req_name.lower().strip()
    if key in RESKILLING_CATALOG:
        return RESKILLING_CATALOG[key]
    for k, val in RESKILLING_CATALOG.items():
        if k in key or key in k:
            return val
    return {
        "catalog_item_id": "CAT-GENERIC-COMPETENCY",
        "domain": "Core Role Competency",
        "day_60_learning": f"Complete targeted training or guided self-study on '{req_name}' using internal documentation or approved coursework.",
        "day_90_project": f"Complete a shadowed project or peer-reviewed deliverable demonstrating '{req_name}' in a live operational workflow.",
        "learning_resources": [f"Approved Internal L&D Module & Documentation for '{req_name}'"],
        "prerequisites": [],
        "requires_credential_or_prerequisite": False,
        "verification_artifact_type": "peer_reviewed_deliverable",
        "catalog_owner": "Enterprise L&D Governance Board",
        "last_reviewed_date": "2026-11-01",
    }


def build_role_match(
    role_meta: Dict[str, Any],
    reqs: List[oaa.JDRequirement],
    align_cfg: oaa.AlignmentConfig,
    rep: oaa.AuditReport,
    employee_confirmations: Optional[Mapping[str, Mapping[str, Any]]] = None,
    approval_record: Optional[MentorAndManagerApprovalRecord] = None,
    outcome_record: Optional[MobilityPlanOutcomeRecord] = None,
    share_with_manager: bool = False,
) -> RoleMatchRecord:
    if approval_record is not None and not share_with_manager:
        if approval_record.manager_id or approval_record.manager_approval_status not in ("", "NOT_SHARED_PRIVATE"):
            raise pdm.PrivacyConsentError(
                "[MOBILITY PRIVACY GATE] BLOCKED: Manager approval cannot be recorded when share_with_manager is False."
            )

    emp_conf_map = {k.lower().strip(): v for k, v in (employee_confirmations or {}).items()}
    strengths: List[str] = []
    plan: List[ReskillingStep] = []
    gap_questions: List[str] = []
    cred_prereq_alerts: List[str] = []
    matched_count = 0
    partial_count = 0
    missing_count = 0
    immediate_gain = 0.0
    confirmed_gain = 0.0
    confirmed_30d_cnt = 0
    unconfirmed_30d_cnt = 0

    total_max = rep.total_max_item_weights if rep.total_max_item_weights > 0 else 1.0
    mgr_step_status = (
        approval_record.manager_approval_status
        if (approval_record and share_with_manager)
        else ("PENDING_MANAGER_REVIEW" if share_with_manager else "NOT_SHARED_WITH_MANAGER")
    )
    mnt_step_status = approval_record.mentor_approval_status if approval_record else "PENDING_MENTOR_REVIEW"

    for row in rep.ledger:
        r_name = row.requirement.display_name
        raw_req_key = row.requirement.name.lower().strip()
        tier = row.requirement.tier
        max_w = row.max_weight
        kw_gain = ((max_w - row.keyword_weighted_score) / total_max) * 45.0
        ctx_gain = ((max_w - row.earned_item_score) / total_max) * 20.0
        item_gain = round(kw_gain + ctx_gain, 2)
        cat = _lookup_catalog(row.requirement.name)

        conf_entry = emp_conf_map.get(raw_req_key) or emp_conf_map.get(r_name.lower().strip())
        if conf_entry is not None:
            is_conf = bool(conf_entry.get("confirmed", False))
            ev_note = str(conf_entry.get("evidence_note", "")).strip()
            if is_conf and len(ev_note) >= 5:
                emp_status = "CONFIRMED_BY_EMPLOYEE"
            elif not is_conf:
                emp_status = "DECLINED_NOT_HELD"
            else:
                emp_status = "PENDING_EMPLOYEE_CONFIRMATION"
        else:
            emp_status = "PENDING_EMPLOYEE_CONFIRMATION"
            ev_note = ""

        if row.match_credit == 1.0 and row.placement_multiplier == 1.0:
            matched_count += 1
            strengths.append(f"{r_name} ({tier})")
        elif row.match_credit == 0.5:
            partial_count += 1
            immediate_gain += item_gain
            if emp_status == "CONFIRMED_BY_EMPLOYEE":
                confirmed_gain += item_gain
                confirmed_30d_cnt += 1
            else:
                unconfirmed_30d_cnt += 1
            plan.append(
                ReskillingStep(
                    horizon="Days 1-30",
                    requirement=r_name,
                    tier=tier,
                    gap_type="Tier 1 Synonym Alignment",
                    potential_score_gain=item_gain,
                    action=(
                        f"Your profile already shows `{row.matched_term}` in *{row.placement_zone}*. "
                        f"If that work covered `{r_name}`, align the terminology to `{r_name}` without inventing metrics."
                    ),
                    verification_milestone=f"Updated bullet reviewed against source quote: \"{row.exact_quote}\"",
                    catalog_item_id=str(cat.get("catalog_item_id", "CAT-GENERIC")),
                    employee_confirmation_status=emp_status,
                    employee_evidence_note=ev_note,
                    learning_resources=list(cat.get("learning_resources", [])),
                    prerequisites=list(cat.get("prerequisites", [])),
                    requires_credential_or_prerequisite=bool(cat.get("requires_credential_or_prerequisite", False)),
                    verification_artifact_type="employee_confirmed_terminology_alignment",
                    mentor_approval_status=mnt_step_status,
                    manager_approval_status=mgr_step_status,
                )
            )
        elif row.match_credit == 1.0 and row.placement_multiplier < 1.0:
            partial_count += 1
            immediate_gain += item_gain
            if emp_status == "CONFIRMED_BY_EMPLOYEE":
                confirmed_gain += item_gain
                confirmed_30d_cnt += 1
            else:
                unconfirmed_30d_cnt += 1
            plan.append(
                ReskillingStep(
                    horizon="Days 1-30",
                    requirement=r_name,
                    tier=tier,
                    gap_type="Tier 2 Contextual Elevation",
                    potential_score_gain=item_gain,
                    action=(
                        f"`{r_name}` appears in *{row.placement_zone}* (`{row.placement_multiplier}x`). "
                        f"Document where and how you used `{r_name}` inside the relevant work-experience role with `[INSERT: concrete outcome/scale]`."
                    ),
                    verification_milestone=f"Experience bullet added for `{r_name}` in a recent role.",
                    catalog_item_id=str(cat.get("catalog_item_id", "CAT-GENERIC")),
                    employee_confirmation_status=emp_status,
                    employee_evidence_note=ev_note,
                    learning_resources=list(cat.get("learning_resources", [])),
                    prerequisites=list(cat.get("prerequisites", [])),
                    requires_credential_or_prerequisite=bool(cat.get("requires_credential_or_prerequisite", False)),
                    verification_artifact_type="employee_confirmed_work_experience_bullet",
                    mentor_approval_status=mnt_step_status,
                    manager_approval_status=mgr_step_status,
                )
            )
        else:
            missing_count += 1
            prereqs = list(cat.get("prerequisites", []))
            needs_cred_or_prereq = bool(row.requirement.is_knockout_gate or cat.get("requires_credential_or_prerequisite", False) or prereqs)
            if row.requirement.is_knockout_gate:
                cred_prereq_alerts.append(
                    f"[REQUIRED CREDENTIAL / GATE] '{r_name}' is a required knockout gate; transition requires formal credential verification or approved HR equivalency pathway."
                )
            elif prereqs:
                cred_prereq_alerts.append(
                    f"[PREREQUISITE CHAIN] '{r_name}' requires prerequisite foundation ({', '.join(prereqs)}) plus verified milestone ({cat.get('verification_artifact_type', 'deliverable')})."
                )
            gap_questions.append(
                f"The '{align_cfg.target_title or role_meta.get('title', 'target')}' role weights **{r_name}** ({tier}) at {max_w:g} weight units (+{item_gain:.2f} pts). "
                f"Have you used {r_name} (or a listed alternative) in a prior role or project?"
            )
            if row.requirement.is_knockout_gate:
                plan.append(
                    ReskillingStep(
                        horizon="Days 61-90",
                        requirement=r_name,
                        tier=tier,
                        gap_type="Required Gate / Credential Verification",
                        potential_score_gain=item_gain,
                        action=f"Verify whether you hold `{r_name}` or qualify via an equivalent-experience pathway with HR/TA.",
                        verification_milestone=f"Credential or equivalency pathway confirmed for `{r_name}`.",
                        catalog_item_id=str(cat.get("catalog_item_id", "CAT-GATE")),
                        employee_confirmation_status=emp_status,
                        employee_evidence_note=ev_note,
                        learning_resources=list(cat.get("learning_resources", [])),
                        prerequisites=prereqs or ["Formal Credential or Approved Equivalency"],
                        requires_credential_or_prerequisite=True,
                        verification_artifact_type="formal_credential_or_hr_equivalency_signoff",
                        mentor_approval_status=mnt_step_status,
                        manager_approval_status=mgr_step_status,
                    )
                )
            else:
                plan.append(
                    ReskillingStep(
                        horizon="Days 31-60",
                        requirement=r_name,
                        tier=tier,
                        gap_type=f"Upskilling ({cat['domain']})",
                        potential_score_gain=item_gain,
                        action=cat["day_60_learning"],
                        verification_milestone=cat["day_90_project"],
                        catalog_item_id=str(cat.get("catalog_item_id", "CAT-GENERIC")),
                        employee_confirmation_status=emp_status,
                        employee_evidence_note=ev_note,
                        learning_resources=list(cat.get("learning_resources", [])),
                        prerequisites=prereqs,
                        requires_credential_or_prerequisite=needs_cred_or_prereq,
                        verification_artifact_type=str(cat.get("verification_artifact_type", "peer_reviewed_deliverable")),
                        mentor_approval_status=mnt_step_status,
                        manager_approval_status=mgr_step_status,
                    )
                )

    horizon_order = {"Days 1-30": 0, "Days 31-60": 1, "Days 61-90": 2}
    plan.sort(key=lambda s: (horizon_order.get(s.horizon, 9), -s.potential_score_gain, s.requirement))

    cur_sc = round(rep.composite_score, 1)
    conf_30d = min(100.0, round(rep.composite_score + confirmed_gain, 1))
    projected_30d = min(100.0, round(rep.composite_score + immediate_gain, 1))
    role_id_str = str(role_meta.get("job_id") or role_meta.get("id") or "role")
    role_title_str = str(role_meta.get("title") or align_cfg.target_title or "Untitled Role")

    active_approvals = approval_record or MentorAndManagerApprovalRecord(
        manager_approval_status="PENDING_MANAGER_REVIEW" if share_with_manager else "NOT_SHARED_PRIVATE"
    )
    d60_total = sum(1 for s in plan if s.horizon in ("Days 31-60", "Days 61-90"))
    d60_done = len(active_approvals.mentor_verified_milestones)
    active_outcome = outcome_record or MobilityPlanOutcomeRecord(
        role_id=role_id_str,
        role_title=role_title_str,
        baseline_score=cur_sc,
        confirmed_30d_score=conf_30d,
        projected_30d_score=projected_30d,
        day_60_milestones_completed=d60_done,
        day_60_milestones_total=d60_total,
        credentials_or_prerequisites_cleared=(len(cred_prereq_alerts) == 0),
    )

    return RoleMatchRecord(
        rank=0,
        role_id=role_id_str,
        title=role_title_str,
        company=str(role_meta.get("company") or "Internal"),
        location=str(role_meta.get("location") or "Not stated"),
        source=str(role_meta.get("source") or "internal"),
        url=str(role_meta.get("url") or ""),
        current_score=cur_sc,
        literal_score=round(rep.legacy_literal_score, 1),
        projected_30d_score=projected_30d,
        readiness_band=readiness_band_for(rep.composite_score),
        knockout_passed=rep.knockout_passed,
        unverified_gates=list(rep.failed_knockouts),
        matched_count=matched_count,
        partial_count=partial_count,
        missing_count=missing_count,
        strengths=strengths,
        plan_30_60_90=plan,
        gap_questions=gap_questions,
        confirmed_30d_score=conf_30d,
        confirmed_30d_items_count=confirmed_30d_cnt,
        unconfirmed_30d_items_count=unconfirmed_30d_cnt,
        credential_or_prerequisite_alerts=cred_prereq_alerts,
        approvals=active_approvals,
        outcome_tracking=active_outcome,
    )


def match_resume_to_roles(
    resume_text: str,
    role_entries: Sequence[Dict[str, Any]],
    reference_date: datetime,
    mode: str = "internal-mobility",
    top_n: Optional[int] = None,
    share_with_manager: bool = False,
    subject_id: str = "EMP-001",
    consent_map: Optional[Dict[str, pdm.ConsentRecord]] = None,
    require_explicit_consent: bool = False,
    employee_confirmations: Optional[Mapping[str, Mapping[str, Any]]] = None,
    approvals_by_role: Optional[Mapping[str, MentorAndManagerApprovalRecord]] = None,
    outcomes_by_role: Optional[Mapping[str, MobilityPlanOutcomeRecord]] = None,
    share_with_mentor: bool = True,
    share_with_target_hiring_manager: bool = False,
) -> MobilityPortfolioReport:
    """
    Evaluate `resume_text` against a sequence of role dicts. Each dict can contain either:
      - pre-built `requirements` (list of JDRequirement dicts) and `alignment` (AlignmentConfig dict), OR
      - `description_text` and `title` (parsed via `oaa.extract_reqs_consistent`).
    Enforces explicit employee/candidate consent, employee skill-evidence confirmation,
    mentor/manager approval boundaries, and defaults to ephemeral-in-memory processing.
    """
    _, cat_sha = validate_reskilling_catalog_governance()
    flow_type = "outplacement_pack" if mode == "outplacement-pack" else "internal_mobility"
    c_rec = pdm.verify_explicit_consent(
        subject_id=subject_id,
        flow_type=flow_type,
        consent_map=consent_map,
        reference_date=reference_date,
        require_explicit_consent=require_explicit_consent,
        require_manager_share_consent=share_with_manager and bool(consent_map),
    )
    approvals_map = approvals_by_role or {}
    outcomes_map = outcomes_by_role or {}
    matches: List[RoleMatchRecord] = []
    for entry in role_entries:
        if "requirements" in entry and isinstance(entry["requirements"], list):
            reqs = [
                r if isinstance(r, oaa.JDRequirement) else oaa.JDRequirement(**r)
                for r in entry["requirements"]
            ]
            align_raw = dict(entry.get("alignment", {}))
            align_raw.pop("reference_date", None)
            align_cfg = oaa.AlignmentConfig(**align_raw)
        else:
            jd_text = str(entry.get("description_text") or "")
            title = str(entry.get("title") or "")
            reqs, align_cfg = oaa.extract_reqs_consistent(jd_text, title=title)

        rid = str(entry.get("job_id") or entry.get("id") or "role")
        rep = oaa.run_ats_audit(resume_text, reqs, align_cfg, reference_date=reference_date)
        matches.append(
            build_role_match(
                entry,
                reqs,
                align_cfg,
                rep,
                employee_confirmations=employee_confirmations,
                approval_record=approvals_map.get(rid),
                outcome_record=outcomes_map.get(rid),
                share_with_manager=share_with_manager,
            )
        )

    matches.sort(key=lambda m: (-m.current_score, -m.projected_30d_score, m.title))
    if top_n is not None and top_n > 0:
        matches = matches[:top_n]

    for idx, m in enumerate(matches, start=1):
        m.rank = idx

    ready_now = sum(1 for m in matches if m.current_score >= 80.0)
    near_ready = sum(1 for m in matches if 65.0 <= m.current_score < 80.0)
    stretch = sum(1 for m in matches if m.current_score < 65.0)

    return MobilityPortfolioReport(
        tool="ops-requisition-auditor",
        module=MODULE_NAME,
        mode=mode,
        engine_version=ENGINE_VERSION,
        reference_date=reference_date.strftime("%Y-%m-%d"),
        employee_initiated=True,
        share_with_manager=share_with_manager,
        resume_sha256=hashlib.sha256(resume_text.encode("utf-8")).hexdigest(),
        roles_evaluated=len(matches),
        ready_now_count=ready_now,
        near_ready_count=near_ready,
        stretch_count=stretch,
        matches=matches,
        subject_pseudonym_token=pdm.compute_pseudonym_token(subject_id),
        consent_verified=c_rec.consented and not c_rec.revoked,
        consent_sha256=c_rec.consent_sha256,
        processing_mode="ephemeral_in_memory",
        retention_days=0,
        reskilling_catalog_version=RESKILLING_CATALOG_VERSION,
        reskilling_catalog_sha256=cat_sha,
        sharing_permissions={
            "share_with_current_manager": share_with_manager,
            "share_with_mentor_or_coach": share_with_mentor,
            "share_with_target_hiring_manager": share_with_target_hiring_manager,
        },
    )


# ---------------------------------------------------------------------------
# Markdown Report Renderer
# ---------------------------------------------------------------------------

def render_mobility_markdown(report: MobilityPortfolioReport) -> str:
    heading = (
        "# Outplacement & Career Transition Packet"
        if report.mode == "outplacement-pack"
        else "# Internal Mobility & 30/60/90-Day Reskilling Plan"
    )
    privacy_line = (
        "Employee-initiated opt-in (`share_with_manager: true` — shared at employee's request)"
        if report.share_with_manager
        else "Employee-initiated opt-in (`share_with_manager: false` — private to employee/candidate)"
    )
    md: List[str] = [
        heading,
        "",
        DISCLAIMER,
        "",
        "## 1. Portfolio Summary",
        "",
        f"- **Mode:** `{report.mode}` (Engine v{report.engine_version})",
        f"- **Reference Date:** `{report.reference_date}`",
        f"- **Privacy & Governance:** {privacy_line}",
        f"- **Reskilling Catalog Governance:** `{report.reskilling_catalog_version}` (SHA-256: `{report.reskilling_catalog_sha256[:16]}…`)",
        f"- **Profile Hash (SHA-256):** `{report.resume_sha256[:16]}…`",
        f"- **Target Roles Evaluated:** `{report.roles_evaluated}` "
        f"({report.ready_now_count} Ready Now, {report.near_ready_count} Near-Ready 30–60d, {report.stretch_count} Stretch 90d)",
        "",
        "## 2. Ranked Career-Path & Role Matches",
        "",
        "| Rank | Role Title | Location | Current Score | Confirmed 30d Score | Projected (If All 30d Confirmed) | Gates | Matched / Partial / Missing | Readiness Band |",
        "| :-: | :--- | :--- | :-: | :-: | :-: | :-: | :-: | :--- |",
    ]
    for m in report.matches:
        gate_str = "PASS" if m.knockout_passed else f"UNVERIFIED ({', '.join(m.unverified_gates)})"
        counts = f"{m.matched_count} / {m.partial_count} / {m.missing_count}"
        md.append(
            f"| {m.rank} | **{m.title}** ({m.company}) | {m.location} | **{m.current_score:.1f}** | "
            f"`{m.confirmed_30d_score:.1f}` ({m.confirmed_30d_items_count}/{m.confirmed_30d_items_count + m.unconfirmed_30d_items_count} conf) | "
            f"`{m.projected_30d_score:.1f}` | {gate_str} | {counts} | {m.readiness_band} |"
        )

    md += ["", "## 3. Role-by-Role 30/60/90-Day Reskilling & Transition Plans", ""]
    for m in report.matches:
        md += [
            "---",
            "",
            f"### {m.rank}. {m.title} — {m.company} (`{m.role_id}`)",
            "",
            f"- **Current JD Alignment Score:** `{m.current_score:.1f} / 100` (Literal-only: `{m.literal_score:.1f}%`) — **{m.readiness_band}**",
            f"- **Employee-Confirmed 30-Day Score:** `{m.confirmed_30d_score:.1f} / 100` (`{m.confirmed_30d_items_count}` confirmed, `{m.unconfirmed_30d_items_count}` pending employee confirmation)",
            f"- **Projected Score After Days 1–30 Profile Alignment:** `{m.projected_30d_score:.1f} / 100` *(honest Tier 1/2 alignment only; never fabricates missing skills)*",
            f"- **Mentor & Manager Approval Status:** Mentor `{m.approvals.mentor_approval_status}` (`{m.approvals.mentor_id or 'unassigned'}`) | Manager `{m.approvals.manager_approval_status}`",
            f"- **Verified Strengths (`1.0x`):** {', '.join(m.strengths) if m.strengths else '*(None yet at full 1.0x recent-role credit)*'}",
            "",
        ]
        if m.credential_or_prerequisite_alerts:
            md += ["#### Credential & Prerequisite Chain Warnings", ""]
            for alert in m.credential_or_prerequisite_alerts:
                md.append(f"- {alert}")
            md.append("")
        if m.plan_30_60_90:
            md += [
                "#### 30 / 60 / 90-Day Action & Reskilling Roadmap",
                "",
                "| Horizon | Requirement | Gap Type | Max Score Impact | Employee Confirmation | Prerequisites & Learning Resources | Concrete Action | Verification / Project Milestone |",
                "| :--- | :--- | :--- | :-: | :--- | :--- | :--- | :--- |",
            ]
            _CONF_STATUS_LABELS = {
                "CONFIRMED_BY_EMPLOYEE": "CONFIRMED_BY_EMPLOYEE",
                "PENDING_EMPLOYEE_CONFIRMATION": "PENDING_EMPLOYEE_CONFIRMATION",
                "DECLINED_NOT_HELD": "DECLINED_NOT_HELD",
            }
            for s in m.plan_30_60_90:
                prereq_str = f"**Prereqs:** {', '.join(s.prerequisites)}; " if s.prerequisites else ""
                res_str = f"**Resources:** {', '.join(s.learning_resources)}" if s.learning_resources else "—"
                conf_badge = _CONF_STATUS_LABELS.get(s.employee_confirmation_status, "PENDING_EMPLOYEE_CONFIRMATION")
                md.append(
                    f"| **{s.horizon}** | **{s.requirement}** ({s.tier}) | {s.gap_type} | `+{s.potential_score_gain:.2f} pts` | "
                    f"`{conf_badge}` | {prereq_str}{res_str} | {s.action} | {s.verification_milestone} |"
                )
            md.append("")
        else:
            md.append("*All requirements for this role are already verified at full credit in recent experience.*\n")

        if m.gap_questions:
            md += ["#### Gap-Confirmation Questions (Ask Before Rewriting or Applying)", ""]
            for q in m.gap_questions:
                md.append(f"- {q}")
            md.append("")

    return "\n".join(md)


# ---------------------------------------------------------------------------
# Built-In WFM / Analytics Career Ladder Demo Fixture (4 Internal Roles)
# ---------------------------------------------------------------------------

def get_demo_career_ladder() -> Tuple[str, List[Dict[str, Any]]]:
    """
    Returns a synthetic WFM Scheduler / Operations Analyst profile and a 4-role internal
    career ladder:
      1. Workforce Management Analyst II (Ready Now / Near-Ready -> 90+ after 30d alignment)
      2. Senior Analytics Manager (canonical 12-req config -> 88.3 Ready Now)
      3. Capacity Planning Lead (Near-Ready 30-to-60-Day Bridge)
      4. Principal Data & Cloud Platform Architect (Stretch 90-Day Path)
    """
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    base_resume_path = os.path.join(root_dir, "examples", "sample_resume.txt")
    cfg_path = os.path.join(root_dir, "examples", "sample_ats_config.json")
    if os.path.exists(base_resume_path):
        resume_text = oaa.load_resume_file(base_resume_path)
    else:
        resume_text, _, _ = oaa.get_demo_fixture()

    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as fh:
            sam_cfg = json.load(fh)
    else:
        _, reqs, align_cfg = oaa.get_demo_fixture()
        sam_cfg = {
            "requirements": [dataclasses.asdict(r) for r in reqs],
            "alignment": dataclasses.asdict(align_cfg),
        }

    roles: List[Dict[str, Any]] = [
        {
            "job_id": "INT-WFM-204",
            "title": "Senior Analytics Manager",
            "company": "Northstar Contact Solutions",
            "location": "St. Louis, MO (Hybrid)",
            "source": "internal",
            "requirements": sam_cfg["requirements"],
            "alignment": sam_cfg["alignment"],
        },
        {
            "job_id": "INT-WFM-108",
            "title": "Forecasting & Capacity Planning Lead",
            "company": "Northstar Contact Solutions",
            "location": "Remote - US",
            "source": "internal",
            "requirements": [
                {"name": "Demand Forecasting", "category": "hard_skill", "tier": "required", "synonyms": ["demand planning"]},
                {"name": "SQL", "category": "hard_skill", "tier": "required", "synonyms": []},
                {"name": "Python", "category": "hard_skill", "tier": "required", "synonyms": []},
                {"name": "Workforce Capacity Modeling", "category": "hard_skill", "tier": "required", "synonyms": ["capacity planning"]},
                {"name": "Power BI", "category": "hard_skill", "tier": "required", "synonyms": [], "alternatives": ["Tableau"]},
                {"name": "Erlang C", "category": "hard_skill", "tier": "required", "synonyms": []},
                {"name": "NICE IEX", "category": "hard_skill", "tier": "preferred", "synonyms": [], "alternatives": ["Verint", "Genesys Cloud"]},
                {"name": "Cross-Functional Leadership", "category": "soft_skill", "tier": "required", "synonyms": ["cross-functional"]},
                {"name": "B.S.", "category": "hard_skill", "tier": "required", "synonyms": ["Bachelor"], "is_knockout_gate": True},
            ],
            "alignment": {
                "target_title": "Forecasting & Capacity Planning Lead",
                "title_synonyms": ["Senior Analytics Manager", "Analytics Manager", "Supply Chain Analytics"],
                "min_years_required": 5.0,
            },
        },
        {
            "job_id": "INT-WFM-310",
            "title": "Director, Contact-Center Workforce Management",
            "company": "Northstar Contact Solutions",
            "location": "St. Louis, MO",
            "source": "internal",
            "requirements": [
                {"name": "Workforce Capacity Modeling", "category": "hard_skill", "tier": "required", "synonyms": ["capacity planning"]},
                {"name": "Demand Forecasting", "category": "hard_skill", "tier": "required", "synonyms": ["demand planning"]},
                {"name": "P&L Ownership", "category": "title_scope", "tier": "required", "synonyms": ["operating budget"]},
                {"name": "NICE IEX", "category": "hard_skill", "tier": "required", "synonyms": [], "alternatives": ["Verint", "Genesys Cloud"]},
                {"name": "Erlang C", "category": "hard_skill", "tier": "required", "synonyms": []},
                {"name": "SQL", "category": "hard_skill", "tier": "preferred", "synonyms": []},
                {"name": "Six Sigma Black Belt", "category": "hard_skill", "tier": "preferred", "synonyms": ["Lean Six Sigma"]},
                {"name": "Cross-Functional Leadership", "category": "soft_skill", "tier": "required", "synonyms": ["cross-functional"]},
                {"name": "B.S.", "category": "hard_skill", "tier": "required", "synonyms": ["Bachelor"], "is_knockout_gate": True},
            ],
            "alignment": {
                "target_title": "Director, Workforce Management",
                "title_synonyms": ["Senior Analytics Manager", "Analytics Manager"],
                "min_years_required": 8.0,
            },
        },
    ]
    return resume_text, roles


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-mobility`)
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops-req-mobility",
        description="Internal Mobility & Outplacement Matcher (Employer Edition, Module 3): matches 1 resume against N open requisitions and generates a 30/60/90-day reskilling and transition plan.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the built-in 3-role WFM/Analytics internal mobility career-ladder demo.")
    parser.add_argument("--resume", help="Path to the employee's or candidate's resume (.txt, .md, .docx, .pdf).")
    parser.add_argument("--reqs-dir", help="Directory of requisition files (.txt, .md, .html, .json).")
    parser.add_argument("--from-json", help="Cached normalized jobs JSON or list of role configs.")
    parser.add_argument("--greenhouse", help="Public Greenhouse board token to match against.")
    parser.add_argument("--lever", help="Public Lever board token to match against.")
    parser.add_argument("--ashby", help="Public Ashby board token to match against.")
    parser.add_argument("--title-filter", help="Optional regex filter on job titles when matching across a board.")
    parser.add_argument("--top", type=int, default=5, help="Maximum number of top-ranked roles to include (default: 5).")
    parser.add_argument("--reference-date", default="2026-11-01", help="Reference date YYYY-MM-DD for reproducible tenure math.")
    parser.add_argument("--outplacement-pack", action="store_true", help="Switch report mode to Outplacement & Career Transition Packet.")
    parser.add_argument("--share-with-manager", action="store_true", help="Mark the mobility plan as employee-approved for manager sharing (default: private to employee).")
    parser.add_argument("--confirmations-json", help="Optional JSON file containing employee skill-evidence confirmations, mentor/manager approvals, and 90-day outcomes.")
    parser.add_argument("--subject-id", default="EMP-001", help="Employee or candidate subject identifier for consent verification (default: EMP-001).")
    parser.add_argument("--consent-json", help="Optional JSON file containing explicit employee/candidate consent records.")
    parser.add_argument("--require-consent", action="store_true", help="Fail closed (exit code 2) if the subject lacks an explicit, non-revoked consent record in --consent-json.")
    parser.add_argument("--encrypt-exports", action="store_true", help="Encrypt persistent --json-out exports using PBKDF2-HMAC-SHA256 + SHA256-CTR-HMAC-SHA256-ETM.")
    parser.add_argument("--passphrase-env", default="OPS_REQ_EXPORT_PASSPHRASE", help="Environment variable holding the export encryption passphrase (default: OPS_REQ_EXPORT_PASSPHRASE).")
    parser.add_argument("--output", help="Write Markdown report to this path.")
    parser.add_argument("--json-out", help="Write full JSON mobility portfolio to this path.")
    parser.add_argument("--csv-out", help="Write UTF-8 CSV role rankings & 30/60/90-day counts to this path.")

    args = parser.parse_args(argv)
    ref_dt = datetime.strptime(args.reference_date, "%Y-%m-%d")
    mode = "outplacement-pack" if args.outplacement_pack else "internal-mobility"

    try:
        consent_map = pdm.load_consent_records(args.consent_json)
        emp_confirmations: Dict[str, Any] = {}
        approvals_by_role: Dict[str, MentorAndManagerApprovalRecord] = {}
        outcomes_by_role: Dict[str, MobilityPlanOutcomeRecord] = {}
        if args.confirmations_json:
            c_path = gov.resolve_input_path(args.confirmations_json)
            with open(c_path, "r", encoding="utf-8") as fh:
                c_raw = json.load(fh)
            emp_confirmations = dict(c_raw.get("employee_confirmations", {}))
            for rid, ap_raw in dict(c_raw.get("approvals_by_role", {})).items():
                approvals_by_role[rid] = MentorAndManagerApprovalRecord(**ap_raw)
            for rid, out_raw in dict(c_raw.get("outcomes_by_role", {})).items():
                outcomes_by_role[rid] = MobilityPlanOutcomeRecord(**out_raw)
    except (pdm.PrivacyError, FileNotFoundError, json.JSONDecodeError, TypeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    if args.demo:
        resume_text, role_entries = get_demo_career_ladder()
    else:
        if not args.resume:
            parser.error("Provide --demo or --resume <path>.")
        resume_text = oaa.load_resume_file(gov.resolve_input_path(args.resume))
        role_entries = []

        if args.reqs_dir:
            reqs_dir = gov.resolve_input_path(args.reqs_dir)
            for entry in sorted(os.listdir(reqs_dir)):
                p = os.path.join(reqs_dir, entry)
                ext = os.path.splitext(entry)[1].lower()
                if ext == ".json":
                    with open(p, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    if isinstance(data, list):
                        role_entries.extend(data)
                    elif isinstance(data, dict):
                        data.setdefault("job_id", Path(p).stem)
                        data.setdefault("title", data.get("alignment", {}).get("target_title", Path(p).stem))
                        role_entries.append(data)
                elif ext in (".txt", ".md", ".html"):
                    with open(p, "r", encoding="utf-8") as fh:
                        txt = fh.read()
                    rec = jpc.parse_job_posting_text(txt)
                    role_entries.append({
                        "job_id": rec.job_id or Path(p).stem,
                        "title": rec.job_title or Path(p).stem,
                        "company": rec.company_name or "Internal",
                        "location": rec.location or "Not stated",
                        "source": "file",
                        "description_text": txt,
                    })

        if args.from_json or args.greenhouse or args.lever or args.ashby:
            loaded_jobs = oaa.load_jds_for_audit(
                from_json=gov.resolve_input_path(args.from_json) if args.from_json else None,
                greenhouse=args.greenhouse,
                lever=args.lever,
                ashby=args.ashby,
                title_filter=args.title_filter,
            )
            role_entries.extend(loaded_jobs)

        if not role_entries:
            parser.error("Provide at least one requisition source (--reqs-dir, --from-json, --greenhouse, --lever, or --ashby).")

    try:
        report = match_resume_to_roles(
            resume_text=resume_text,
            role_entries=role_entries,
            reference_date=ref_dt,
            mode=mode,
            top_n=args.top,
            share_with_manager=args.share_with_manager,
            subject_id=args.subject_id,
            consent_map=consent_map,
            require_explicit_consent=args.require_consent,
            employee_confirmations=emp_confirmations,
            approvals_by_role=approvals_by_role,
            outcomes_by_role=outcomes_by_role,
        )
    except pdm.PrivacyError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    md_out = render_mobility_markdown(report)
    if args.output:
        gov.write_owner_only_text(args.output, md_out)
    else:
        sys.stdout.write(md_out + "\n")

    if args.json_out:
        try:
            json_str = json.dumps(dataclasses.asdict(report), indent=2)
            if args.encrypt_exports:
                passphrase = os.environ.get(args.passphrase_env, "")
                env = pdm.encrypt_persistent_export(
                    json_str,
                    passphrase=passphrase,
                    associated_metadata={"module": MODULE_NAME, "mode": mode, "reference_date": args.reference_date},
                )
                gov.write_owner_only_text(args.json_out, json.dumps(env, indent=2))
            else:
                gov.write_owner_only_text(args.json_out, json_str)
        except pdm.PrivacyError as exc:
            sys.stderr.write(f"{exc}\n")
            return 2

    if args.csv_out:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow([
            "rank", "role_id", "title", "company", "location",
            "current_score", "literal_score", "projected_30d_score", "readiness_band",
            "knockout_passed", "unverified_gates", "matched_count", "partial_count", "missing_count",
            "days_1_30_steps", "days_31_60_steps", "days_61_90_steps", "top_reskilling_items",
        ])
        for m in report.matches:
            d30 = [s for s in m.plan_30_60_90 if s.horizon == "Days 1-30"]
            d60 = [s for s in m.plan_30_60_90 if s.horizon == "Days 31-60"]
            d90 = [s for s in m.plan_30_60_90 if s.horizon == "Days 61-90"]
            top_items = "; ".join(f"{s.requirement} ({s.horizon}, +{s.potential_score_gain:.2f} pts)" for s in m.plan_30_60_90[:5])
            writer.writerow([
                m.rank,
                gov.sanitize_csv_cell(m.role_id),
                gov.sanitize_csv_cell(m.title),
                gov.sanitize_csv_cell(m.company),
                gov.sanitize_csv_cell(m.location),
                f"{m.current_score:.1f}",
                f"{m.literal_score:.1f}",
                f"{m.projected_30d_score:.1f}",
                gov.sanitize_csv_cell(m.readiness_band),
                m.knockout_passed,
                gov.sanitize_csv_cell("; ".join(m.unverified_gates)),
                m.matched_count,
                m.partial_count,
                m.missing_count,
                len(d30),
                len(d60),
                len(d90),
                gov.sanitize_csv_cell(top_items),
            ])
        gov.write_owner_only_text(args.csv_out, buf.getvalue())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
