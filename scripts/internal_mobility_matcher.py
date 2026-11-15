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

ENGINE_VERSION = "1.5.0"
MODULE_NAME = "internal-mobility"

DISCLAIMER = (
    "*A deterministic career-path and outplacement matching aid. Scores and 30/60/90-day plans "
    "are transparent heuristics, not automated promotion decisions or legal advice.*"
)

# Taxonomy-driven learning & stretch-project catalog for WFM, Operations, and BI skills
RESKILLING_CATALOG: Dict[str, Dict[str, str]] = {
    "erlang c": {
        "domain": "WFM Staffing Math",
        "day_60_learning": "Complete queueing theory & Erlang C staffing module (arrival rate, AHT, occupancy cap, shrinkage, and service-level 80/20 trade-offs).",
        "day_90_project": "Build a parallel Erlang C interval staffing workbook for one inbound queue and compare against actuals for 2 weeks.",
    },
    "workforce capacity modeling": {
        "domain": "WFM & Capacity Planning",
        "day_60_learning": "Complete multi-week capacity modeling training (headcount supply, attrition, hiring ramp, shrinkage, and FTE demand).",
        "day_90_project": "Shadow the capacity planner on a quarterly 13-week FTE capacity review and co-author the assumptions table.",
    },
    "capacity planning": {
        "domain": "WFM & Capacity Planning",
        "day_60_learning": "Complete capacity planning fundamentals (FTE demand vs. net supply, shrinkage, and hiring-class lead times).",
        "day_90_project": "Build a 13-week staffing outlook for one site or queue and review with the WFM lead.",
    },
    "demand forecasting": {
        "domain": "Forecasting & Analytics",
        "day_60_learning": "Complete time-series forecasting module (seasonality, day-of-week/interval patterns, WAPE/MAPE error tracking).",
        "day_90_project": "Produce a shadow weekly and intraday volume forecast for 4 consecutive weeks and track MAPE against actuals.",
    },
    "forecasting": {
        "domain": "Forecasting & Analytics",
        "day_60_learning": "Complete contact-volume forecasting fundamentals (historical arrival patterns, holiday adjustments, MAPE/bias).",
        "day_90_project": "Co-build the weekly interval forecast for one support channel.",
    },
    "nice iex": {
        "domain": "Contact-Center WFM Platform",
        "day_60_learning": "Complete NICE IEX administrator/scheduler training (forecaster, schedule bidding, exception codes, and intraday RTA views).",
        "day_90_project": "Shadow a WFM scheduler on NICE IEX shift generation and intraday exception management for two scheduling cycles.",
    },
    "verint": {
        "domain": "Contact-Center WFM Platform",
        "day_60_learning": "Complete Verint Workforce Management forecasting and shift-scheduling workflow training.",
        "day_90_project": "Shadow a Verint planner on weekly schedule publication and adherence tracking.",
    },
    "genesys cloud": {
        "domain": "Contact-Center WFM Platform",
        "day_60_learning": "Complete Genesys Cloud WFM module (short-term forecasting, load-based scheduling, real-time adherence).",
        "day_90_project": "Configure and monitor a test schedule in Genesys Cloud WFM alongside a senior analyst.",
    },
    "sql": {
        "domain": "Data & Analytics",
        "day_60_learning": "Complete operational SQL curriculum (SELECT, JOIN, GROUP BY, window functions, and date/interval aggregations).",
        "day_90_project": "Convert one manual spreadsheet report into a repeatable SQL query validated by the analytics team.",
    },
    "python": {
        "domain": "Data & Forecasting",
        "day_60_learning": "Complete Python for data analysis & forecasting (pandas data cleaning, time-series aggregation, automated reporting).",
        "day_90_project": "Automate one recurring data-prep or forecasting workflow in Python.",
    },
    "power bi": {
        "domain": "BI & Visualization",
        "day_60_learning": "Complete Power BI data modeling & DAX fundamentals (star schema, KPI cards, drill-through operational views).",
        "day_90_project": "Build and publish a self-service Power BI SLA/adherence tracker for team leads.",
    },
    "tableau": {
        "domain": "BI & Visualization",
        "day_60_learning": "Complete Tableau visual analytics & calculated-fields module.",
        "day_90_project": "Build an interactive Tableau operational scorecard for weekly leadership review.",
    },
    "snowflake": {
        "domain": "Cloud Data Warehouse",
        "day_60_learning": "Complete Snowflake SQL & data-pipeline querying fundamentals.",
        "day_90_project": "Query warehouse views in Snowflake to validate weekly SLA and volume metrics.",
    },
    "sap s/4hana": {
        "domain": "ERP & Enterprise Systems",
        "day_60_learning": "Complete SAP S/4HANA operational reporting & master-data navigation overview.",
        "day_90_project": "Partner with finance/supply-chain ops to reconcile one weekly SAP S/4HANA data extract.",
    },
    "p&l ownership": {
        "domain": "Financial & Operational Scope",
        "day_60_learning": "Complete operational finance & budget variance training (cost per contact, labor budget vs. actuals, unit economics).",
        "day_90_project": "Co-present the monthly labor variance and overtime cost analysis with the operations manager.",
    },
    "six sigma black belt": {
        "domain": "Process Improvement",
        "day_60_learning": "Complete Lean Six Sigma DMAIC project methodology and root-cause analysis coursework.",
        "day_90_project": "Lead a scoped DMAIC continuous-improvement project targeting shrinkage, AHT, or reporting cycle time.",
    },
    "kubernetes": {
        "domain": "Cloud & Container Infrastructure",
        "day_60_learning": "Complete container orchestration & Kubernetes fundamentals for analytics/data services.",
        "day_90_project": "Shadow the platform team on a containerized batch-job deployment.",
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


# ---------------------------------------------------------------------------
# Core Mobility & Reskilling Engine
# ---------------------------------------------------------------------------

def readiness_band_for(score: float) -> str:
    if score >= 80.0:
        return "Ready Now (Immediate Transfer / Application)"
    if score >= 65.0:
        return "Near-Ready (30-to-60-Day Bridge)"
    return "Stretch Role (90-Day Reskilling Path)"


def _lookup_catalog(req_name: str) -> Dict[str, str]:
    key = req_name.lower().strip()
    if key in RESKILLING_CATALOG:
        return RESKILLING_CATALOG[key]
    for k, val in RESKILLING_CATALOG.items():
        if k in key or key in k:
            return val
    return {
        "domain": "Core Role Competency",
        "day_60_learning": f"Complete targeted training or guided self-study on '{req_name}' using internal documentation or approved coursework.",
        "day_90_project": f"Complete a shadowed project or peer-reviewed deliverable demonstrating '{req_name}' in a live operational workflow.",
    }


def build_role_match(
    role_meta: Dict[str, Any],
    reqs: List[oaa.JDRequirement],
    align_cfg: oaa.AlignmentConfig,
    rep: oaa.AuditReport,
) -> RoleMatchRecord:
    strengths: List[str] = []
    plan: List[ReskillingStep] = []
    gap_questions: List[str] = []
    matched_count = 0
    partial_count = 0
    missing_count = 0
    immediate_gain = 0.0

    total_max = rep.total_max_item_weights if rep.total_max_item_weights > 0 else 1.0

    for row in rep.ledger:
        r_name = row.requirement.display_name
        tier = row.requirement.tier
        max_w = row.max_weight
        kw_gain = ((max_w - row.keyword_weighted_score) / total_max) * 45.0
        ctx_gain = ((max_w - row.earned_item_score) / total_max) * 20.0
        item_gain = round(kw_gain + ctx_gain, 2)

        if row.match_credit == 1.0 and row.placement_multiplier == 1.0:
            matched_count += 1
            strengths.append(f"{r_name} ({tier})")
        elif row.match_credit == 0.5:
            partial_count += 1
            immediate_gain += item_gain
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
                )
            )
        elif row.match_credit == 1.0 and row.placement_multiplier < 1.0:
            partial_count += 1
            immediate_gain += item_gain
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
                )
            )
        else:
            missing_count += 1
            cat = _lookup_catalog(row.requirement.name)
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
                    )
                )

    horizon_order = {"Days 1-30": 0, "Days 31-60": 1, "Days 61-90": 2}
    plan.sort(key=lambda s: (horizon_order.get(s.horizon, 9), -s.potential_score_gain, s.requirement))

    projected_30d = min(100.0, round(rep.composite_score + immediate_gain, 1))
    return RoleMatchRecord(
        rank=0,
        role_id=str(role_meta.get("job_id") or role_meta.get("id") or "role"),
        title=str(role_meta.get("title") or align_cfg.target_title or "Untitled Role"),
        company=str(role_meta.get("company") or "Internal"),
        location=str(role_meta.get("location") or "Not stated"),
        source=str(role_meta.get("source") or "internal"),
        url=str(role_meta.get("url") or ""),
        current_score=round(rep.composite_score, 1),
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
    )


def match_resume_to_roles(
    resume_text: str,
    role_entries: Sequence[Dict[str, Any]],
    reference_date: datetime,
    mode: str = "internal-mobility",
    top_n: Optional[int] = None,
    share_with_manager: bool = False,
) -> MobilityPortfolioReport:
    """
    Evaluate `resume_text` against a sequence of role dicts. Each dict can contain either:
      - pre-built `requirements` (list of JDRequirement dicts) and `alignment` (AlignmentConfig dict), OR
      - `description_text` and `title` (parsed via `oaa.extract_reqs_consistent`).
    """
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

        rep = oaa.run_ats_audit(resume_text, reqs, align_cfg, reference_date=reference_date)
        matches.append(build_role_match(entry, reqs, align_cfg, rep))

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
        f"- **Profile Hash (SHA-256):** `{report.resume_sha256[:16]}…`",
        f"- **Target Roles Evaluated:** `{report.roles_evaluated}` "
        f"({report.ready_now_count} Ready Now, {report.near_ready_count} Near-Ready 30–60d, {report.stretch_count} Stretch 90d)",
        "",
        "## 2. Ranked Career-Path & Role Matches",
        "",
        "| Rank | Role Title | Location | Current Score | Projected (30d Profile Alignment) | Gates | Matched / Partial / Missing | Readiness Band |",
        "| :-: | :--- | :--- | :-: | :-: | :-: | :-: | :--- |",
    ]
    for m in report.matches:
        gate_str = "PASS" if m.knockout_passed else f"UNVERIFIED ({', '.join(m.unverified_gates)})"
        counts = f"{m.matched_count} / {m.partial_count} / {m.missing_count}"
        md.append(
            f"| {m.rank} | **{m.title}** ({m.company}) | {m.location} | **{m.current_score:.1f}** | "
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
            f"- **Projected Score After Days 1–30 Profile Alignment:** `{m.projected_30d_score:.1f} / 100` *(honest Tier 1/2 alignment only; never fabricates missing skills)*",
            f"- **Verified Strengths (`1.0x`):** {', '.join(m.strengths) if m.strengths else '*(None yet at full 1.0x recent-role credit)*'}",
            "",
        ]
        if m.plan_30_60_90:
            md += [
                "#### 30 / 60 / 90-Day Action & Reskilling Roadmap",
                "",
                "| Horizon | Requirement | Gap Type | Max Score Impact | Concrete Action | Verification / Project Milestone |",
                "| :--- | :--- | :--- | :-: | :--- | :--- |",
            ]
            for s in m.plan_30_60_90:
                md.append(
                    f"| **{s.horizon}** | **{s.requirement}** ({s.tier}) | {s.gap_type} | `+{s.potential_score_gain:.2f} pts` | "
                    f"{s.action} | {s.verification_milestone} |"
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
    parser.add_argument("--output", help="Write Markdown report to this path.")
    parser.add_argument("--json-out", help="Write full JSON mobility portfolio to this path.")
    parser.add_argument("--csv-out", help="Write UTF-8 CSV role rankings & 30/60/90-day counts to this path.")

    args = parser.parse_args(argv)
    ref_dt = datetime.strptime(args.reference_date, "%Y-%m-%d")
    mode = "outplacement-pack" if args.outplacement_pack else "internal-mobility"

    if args.demo:
        resume_text, role_entries = get_demo_career_ladder()
    else:
        if not args.resume:
            parser.error("Provide --demo or --resume <path>.")
        resume_text = oaa.load_resume_file(args.resume)
        role_entries = []

        if args.reqs_dir:
            for entry in sorted(os.listdir(args.reqs_dir)):
                p = os.path.join(args.reqs_dir, entry)
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
                from_json=args.from_json,
                greenhouse=args.greenhouse,
                lever=args.lever,
                ashby=args.ashby,
                title_filter=args.title_filter,
            )
            role_entries.extend(loaded_jobs)

        if not role_entries:
            parser.error("Provide at least one requisition source (--reqs-dir, --from-json, --greenhouse, --lever, or --ashby).")

    report = match_resume_to_roles(
        resume_text=resume_text,
        role_entries=role_entries,
        reference_date=ref_dt,
        mode=mode,
        top_n=args.top,
        share_with_manager=args.share_with_manager,
    )

    md_out = render_mobility_markdown(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md_out)
    else:
        sys.stdout.write(md_out + "\n")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(report), fh, indent=2)

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
                m.role_id,
                m.title,
                m.company,
                m.location,
                f"{m.current_score:.1f}",
                f"{m.literal_score:.1f}",
                f"{m.projected_30d_score:.1f}",
                m.readiness_band,
                m.knockout_passed,
                "; ".join(m.unverified_gates),
                m.matched_count,
                m.partial_count,
                m.missing_count,
                len(d30),
                len(d60),
                len(d90),
                top_items,
            ])
        with open(args.csv_out, "w", encoding="utf-8") as fh:
            fh.write(buf.getvalue())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
