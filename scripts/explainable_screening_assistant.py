#!/usr/bin/env python3
"""
ops-requisition-auditor: Explainable Screening Assistant (Employer Edition, Module 2)
=====================================================================================
Ranks a batch of candidate resumes against a recruiter-confirmed requirement configuration
using the deterministic 100-point JD Alignment engine in `offline_ats_auditor.py`.

Core governance & compliance design:
  1. Never auto-rejects: every candidate is marked PENDING_HUMAN_REVIEW until a human
     reviewer records a decision (with reviewer ID and override reason if applicable).
  2. Blind mode (on by default): strips candidate name header, street/ZIP code (Illinois
     HB 3773), email, phone, personal URLs, and education graduation years (ADEA age-proxy
     mitigation) before scoring, while preserving work-experience date ranges for tenure.
  3. Immutable audit record: records engine version, config SHA-256, raw and blind-redacted
     resume SHA-256, 5-component breakdown, quoted evidence ledger, and reviewer sign-off.
  4. Candidate factor & gap disclosure notices: generates plain-language per-candidate
     notices (Colorado SB 24-205 / NYC LL144 / Illinois HB 3773 style) listing principal
     scoring factors, verified qualifications, and unverified items with a correction path.
  5. Adverse-impact selection-rate tables: computes per-group selection rates and EEOC
     four-fifths (80% / impact ratio) tables from separately stored self-ID data.

Disclaimer: This tool is a transparent heuristic for assisting human recruiters. It is not
legal advice and does not replace an independent bias audit where required (e.g., NYC LL144).
Runs on the Python 3 standard library only.
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
MODULE_NAME = "explainable-screening"

DISCLAIMER = (
    "*A deterministic, human-in-the-loop screening aid. It never auto-rejects candidates, "
    "is not legal advice, and does not replace an independent bias audit where required by law.*"
)

EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\b\d{3})[-.\s]\d{3}[-.\s]\d{4}\b")
URL_RE = re.compile(r"\b(?:https?://|www\.|linkedin\.com/in/|github\.com/)\S+", re.IGNORECASE)
# US 5-digit or 9-digit ZIP codes following a state abbreviation or 'ZIP' label
STATE_ZIP_RE = re.compile(
    r"\b(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)"
    r"(\s+\d{5}(?:-\d{4})?)\b"
)
GRAD_DATE_RE = re.compile(
    r"\(\s*(?:(?:0?[1-9]|1[0-2])[/.-])?(?:19\d{2}|20\d{2})\s*\)"
    r"|\b(?:class\s+of|graduated\s*:?)\s*(?:19\d{2}|20\d{2})\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ReviewerDecision:
    status: str = "PENDING_HUMAN_REVIEW"  # "PENDING_HUMAN_REVIEW" | "ADVANCE" | "HOLD" | "DECLINE"
    reviewer: str = ""
    timestamp: str = ""
    override_reason: str = ""


@dataclass
class CandidateScreeningRecord:
    candidate_id: str
    source_file: str
    blind_mode: bool
    raw_sha256: str
    scored_sha256: str
    redactions_applied: Dict[str, int]
    rank: int
    composite_score: float
    literal_score: float
    recommendation_band: str
    knockout_passed: bool
    unverified_gates: List[str]
    comp1_kw_score: float
    comp2_context_score: float
    comp3_alignment_score: float
    comp4_knockout_score: float
    comp5_schema_score: float
    verified_requirements: List[Dict[str, Any]]
    partial_requirements: List[Dict[str, Any]]
    unverified_requirements: List[Dict[str, Any]]
    decision: ReviewerDecision = field(default_factory=ReviewerDecision)


@dataclass
class SelectionRateRow:
    dimension: str
    group: str
    applicants: int
    selected: int
    selection_rate: float
    impact_ratio: Optional[float]
    four_fifths_status: str


@dataclass
class ScreeningBatchAudit:
    tool: str
    module: str
    engine_version: str
    reference_date: str
    requisition_title: str
    config_sha256: str
    blind_mode: bool
    shortlist_threshold: float
    total_candidates: int
    recommended_count: int
    borderline_count: int
    below_threshold_count: int
    candidates: List[CandidateScreeningRecord]
    selection_rate_tables: List[SelectionRateRow] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Blind-Mode Redactor
# ---------------------------------------------------------------------------

def redact_resume_for_blind_screening(raw_text: str) -> Tuple[str, Dict[str, int]]:
    """
    Strip identifying PII and demographic proxies before scoring:
      - Candidate name line at the top of the resume
      - Email addresses, phone numbers, LinkedIn/GitHub/personal URLs
      - 5/9-digit ZIP codes (Illinois HB 3773 proxy protection)
      - Graduation dates/years inside the Education section (ADEA age-proxy mitigation)
    Work experience date ranges (e.g. '01/2020 - Present') are preserved for tenure math.
    """
    counts = {
        "name_header": 0,
        "email": 0,
        "phone": 0,
        "url": 0,
        "zip_code": 0,
        "education_grad_date": 0,
    }
    lines = raw_text.splitlines()
    out_lines: List[str] = []
    in_education = False
    name_redacted = False

    for idx, line in enumerate(lines):
        stripped = line.strip()
        clean_hdr = re.sub(r"[^a-zA-Z\s&/|]", "", stripped).strip().lower()
        sec = oaa._classify_standard_header(clean_hdr)
        if sec is not None:
            in_education = (sec == "education")

        # Redact top candidate name line (first non-empty line before any section header if it looks like a person's name)
        if not name_redacted and idx <= 2 and stripped and sec is None:
            words = stripped.split()
            if 2 <= len(words) <= 4 and all(re.match(r"^[A-Za-z.'-]+$", w) for w in words):
                if not re.search(r"\b(?:resume|curriculum|vitae|summary|analyst|manager|engineer|director|specialist)\b", stripped, re.IGNORECASE):
                    out_lines.append("[redacted-name]")
                    counts["name_header"] += 1
                    name_redacted = True
                    continue

        updated = line
        updated, n_email = EMAIL_RE.subn("redacted@redacted.invalid", updated)
        counts["email"] += n_email

        updated, n_phone = PHONE_RE.subn("[redacted-phone]", updated)
        counts["phone"] += n_phone

        updated, n_url = URL_RE.subn("[redacted-url]", updated)
        counts["url"] += n_url

        def _zip_repl(m: re.Match[str]) -> str:
            counts["zip_code"] += 1
            return f"{m.group(1)} [redacted-zip]"

        updated = STATE_ZIP_RE.sub(_zip_repl, updated)

        if in_education and not oaa.DATE_RANGE_REGEX.search(updated):
            updated, n_grad = GRAD_DATE_RE.subn("([redacted-grad-date])", updated)
            counts["education_grad_date"] += n_grad

        out_lines.append(updated)

    return "\n".join(out_lines), counts


# ---------------------------------------------------------------------------
# Screening & Governance Engine
# ---------------------------------------------------------------------------

def recommendation_band_for(score: float, threshold: float = 80.0) -> str:
    if score >= threshold:
        return "Recommended for human interview review"
    if score >= 65.0:
        return "Borderline — human review recommended"
    return "Below threshold — human review required before disposition"


def _candidate_record_from_report(
    candidate_id: str,
    source_file: str,
    raw_text: str,
    scored_text: str,
    blind_mode: bool,
    redactions: Dict[str, int],
    rep: oaa.AuditReport,
    threshold: float,
    decision: Optional[ReviewerDecision] = None,
) -> CandidateScreeningRecord:
    verified: List[Dict[str, Any]] = []
    partial: List[Dict[str, Any]] = []
    unverified: List[Dict[str, Any]] = []

    for row in rep.ledger:
        entry = {
            "requirement": row.requirement.display_name,
            "tier": row.requirement.tier,
            "category": row.requirement.category,
            "is_knockout_gate": row.requirement.is_knockout_gate,
            "max_weight": row.max_weight,
            "match_credit": row.match_credit,
            "placement_multiplier": row.placement_multiplier,
            "earned_weight": round(row.earned_item_score, 2),
            "matched_term": row.matched_term,
            "placement_zone": row.placement_zone,
            "quote": row.exact_quote,
        }
        if row.match_credit == 1.0 and row.placement_multiplier == 1.0:
            verified.append(entry)
        elif row.match_credit > 0.0:
            partial.append(entry)
        else:
            unverified.append(entry)

    return CandidateScreeningRecord(
        candidate_id=candidate_id,
        source_file=os.path.basename(source_file) if source_file else candidate_id,
        blind_mode=blind_mode,
        raw_sha256=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        scored_sha256=hashlib.sha256(scored_text.encode("utf-8")).hexdigest(),
        redactions_applied=redactions,
        rank=0,
        composite_score=round(rep.composite_score, 1),
        literal_score=round(rep.legacy_literal_score, 1),
        recommendation_band=recommendation_band_for(rep.composite_score, threshold),
        knockout_passed=rep.knockout_passed,
        unverified_gates=list(rep.failed_knockouts),
        comp1_kw_score=round(rep.comp1_kw_score, 2),
        comp2_context_score=round(rep.comp2_context_score, 2),
        comp3_alignment_score=round(rep.comp3_alignment_score, 2),
        comp4_knockout_score=round(rep.comp4_knockout_score, 2),
        comp5_schema_score=round(rep.comp5_schema_score, 2),
        verified_requirements=verified,
        partial_requirements=partial,
        unverified_requirements=unverified,
        decision=decision or ReviewerDecision(),
    )


def compute_selection_rate_tables(
    candidates: Sequence[CandidateScreeningRecord],
    demographics: Dict[str, Dict[str, str]],
    threshold: float = 80.0,
) -> List[SelectionRateRow]:
    """
    Compute NYC LL144 / EEOC Uniform Guidelines four-fifths (80%) selection-rate tables
    from separately stored voluntary self-ID demographic data.
    A candidate counts as 'selected' if their human decision is 'ADVANCE', or (while pending
    human review) if their score meets `threshold` and all required knockout gates are verified.
    """
    if not demographics:
        return []

    by_dim: Dict[str, Dict[str, List[bool]]] = {}
    for c in candidates:
        demo = demographics.get(c.candidate_id, {})
        if not demo:
            continue
        if c.decision.status in ("ADVANCE", "DECLINE", "HOLD"):
            is_selected = (c.decision.status == "ADVANCE")
        else:
            is_selected = (c.composite_score >= threshold and c.knockout_passed)
        for dim, group in sorted(demo.items()):
            if not group or group.lower() in ("unknown", "decline_to_state", "undisclosed"):
                continue
            by_dim.setdefault(dim, {}).setdefault(group, []).append(is_selected)

    rows: List[SelectionRateRow] = []
    for dim, groups in sorted(by_dim.items()):
        rates = {
            grp: (sum(1 for x in outcomes if x) / len(outcomes)) if outcomes else 0.0
            for grp, outcomes in groups.items()
        }
        max_rate = max(rates.values()) if rates else 0.0
        for grp, outcomes in sorted(groups.items()):
            n_app = len(outcomes)
            n_sel = sum(1 for x in outcomes if x)
            rate = rates[grp]
            ratio = round(rate / max_rate, 3) if max_rate > 0 else None
            if ratio is None:
                status = "NO_SELECTIONS"
            elif n_app < 5:
                status = f"{'PASS' if ratio >= 0.80 else 'FLAG (<0.80)'} (small sample n={n_app})"
            elif ratio >= 0.80:
                status = "PASS (>=0.80)"
            else:
                status = "FLAG (<0.80 — review required)"
            rows.append(
                SelectionRateRow(
                    dimension=dim,
                    group=grp,
                    applicants=n_app,
                    selected=n_sel,
                    selection_rate=round(rate, 3),
                    impact_ratio=ratio,
                    four_fifths_status=status,
                )
            )
    return rows


def screen_batch(
    candidate_inputs: Sequence[Tuple[str, str, str]],
    reqs: List[oaa.JDRequirement],
    align_cfg: oaa.AlignmentConfig,
    reference_date: datetime,
    blind_mode: bool = True,
    shortlist_threshold: float = 80.0,
    decisions_map: Optional[Dict[str, ReviewerDecision]] = None,
    demographics_map: Optional[Dict[str, Dict[str, str]]] = None,
) -> ScreeningBatchAudit:
    """
    Score and rank a batch of `(candidate_id, source_file, raw_resume_text)` tuples against
    `reqs` and `align_cfg`. Never auto-rejects any candidate.
    """
    decisions_map = decisions_map or {}
    cfg_payload = json.dumps(
        {
            "target_title": align_cfg.target_title,
            "min_years_required": align_cfg.min_years_required,
            "requirements": [dataclasses.asdict(r) for r in reqs],
        },
        sort_keys=True,
    )
    config_sha256 = hashlib.sha256(cfg_payload.encode("utf-8")).hexdigest()

    records: List[CandidateScreeningRecord] = []
    for cid, src_file, raw_text in candidate_inputs:
        if blind_mode:
            scored_text, redactions = redact_resume_for_blind_screening(raw_text)
        else:
            scored_text = raw_text
            redactions = {
                "name_header": 0, "email": 0, "phone": 0,
                "url": 0, "zip_code": 0, "education_grad_date": 0,
            }
        rep = oaa.run_ats_audit(scored_text, reqs, align_cfg, reference_date=reference_date)
        rec = _candidate_record_from_report(
            candidate_id=cid,
            source_file=src_file,
            raw_text=raw_text,
            scored_text=scored_text,
            blind_mode=blind_mode,
            redactions=redactions,
            rep=rep,
            threshold=shortlist_threshold,
            decision=decisions_map.get(cid),
        )
        records.append(rec)

    records.sort(key=lambda r: (-r.composite_score, -r.literal_score, r.candidate_id))
    for i, r in enumerate(records, start=1):
        r.rank = i

    recommended_count = sum(1 for r in records if r.composite_score >= shortlist_threshold)
    borderline_count = sum(1 for r in records if 65.0 <= r.composite_score < shortlist_threshold)
    below_count = sum(1 for r in records if r.composite_score < 65.0)

    sel_tables = compute_selection_rate_tables(records, demographics_map or {}, threshold=shortlist_threshold)

    return ScreeningBatchAudit(
        tool="ops-requisition-auditor",
        module=MODULE_NAME,
        engine_version=ENGINE_VERSION,
        reference_date=reference_date.strftime("%Y-%m-%d"),
        requisition_title=align_cfg.target_title or "Untitled Requisition",
        config_sha256=config_sha256,
        blind_mode=blind_mode,
        shortlist_threshold=shortlist_threshold,
        total_candidates=len(records),
        recommended_count=recommended_count,
        borderline_count=borderline_count,
        below_threshold_count=below_count,
        candidates=records,
        selection_rate_tables=sel_tables,
    )


# ---------------------------------------------------------------------------
# Rendering: Candidate Disclosure Notices & Batch Portfolio Report
# ---------------------------------------------------------------------------

def render_candidate_notice(batch: ScreeningBatchAudit, cand: CandidateScreeningRecord) -> str:
    """
    Render a plain-language Candidate Factor & Gap Disclosure Notice supporting
    Colorado SB 24-205, NYC Local Law 144, and Illinois HB 3773 transparency workflows.
    """
    lines: List[str] = [
        f"# Candidate Factor & Gap Disclosure Notice: `{cand.candidate_id}`",
        "",
        DISCLAIMER,
        "",
        "## 1. Automated Assessment & Human Oversight Disclosure",
        "",
        f"- **Requisition:** {batch.requisition_title}",
        f"- **Tool & Version:** `{batch.tool}` (`{batch.module}` v{batch.engine_version}, deterministic rule engine — no LLM or generative AI)",
        f"- **Reference Date:** `{batch.reference_date}`",
        f"- **Configuration Hash (SHA-256):** `{batch.config_sha256[:16]}…`",
        f"- **Application Hash (SHA-256):** `{cand.raw_sha256[:16]}…`",
        f"- **Blind Mode Applied:** `{'Yes (name, email, phone, ZIP code, URLs, and education graduation years stripped before scoring)' if cand.blind_mode else 'No'}`",
        f"- **JD Alignment Score:** `{cand.composite_score:.1f} / 100` (Literal-only view: `{cand.literal_score:.1f}%`)",
        f"- **Screening Band:** **{cand.recommendation_band}**",
        f"- **Human Reviewer Status:** `{cand.decision.status}`"
        + (f" (Reviewer: `{cand.decision.reviewer}`)" if cand.decision.reviewer else " — **No automated rejection is ever performed.**"),
        "",
        "## 2. Principal Factors & Component Breakdown",
        "",
        "| Factor | Earned | Max | What This Measures |",
        "| :--- | :-: | :-: | :--- |",
        f"| **1. Requirement Coverage** | {cand.comp1_kw_score:.2f} | 45.00 | Presence of job-related skills, tools, methods, and qualifications from the requisition |",
        f"| **2. Contextual Placement & Recency** | {cand.comp2_context_score:.2f} | 20.00 | Whether matched skills are demonstrated in recent work experience (<=5 yrs: 1.0x) vs. skills list only (0.4x) |",
        f"| **3. Title, Tenure & Operational Scale** | {cand.comp3_alignment_score:.2f} | 15.00 | Target title alignment, net relevant tenure, and quantified operational scope |",
        f"| **4. Knockout Gate Verification** | {cand.comp4_knockout_score:.2f} | 10.00 | Verification of required degrees, licenses, or certifications on the submitted text |",
        f"| **5. Document & Parse Hygiene** | {cand.comp5_schema_score:.2f} | 10.00 | Standard section headings and parseable work-history date ranges |",
        f"| **Total JD Alignment Score** | **{cand.composite_score:.1f}** | **100.0** | Deterministic sum of Components 1–5 |",
        "",
        "## 3. Qualifications Verified at Full Credit (`1.0x` match in recent work experience)",
        "",
    ]
    if cand.verified_requirements:
        for v in cand.verified_requirements:
            lines.append(f"- **{v['requirement']}** ({v['tier']}): matched `{v['matched_term']}` in *{v['placement_zone']}* — Quote: \"{v['quote']}\"")
    else:
        lines.append("- *(None verified at full 1.0x recent-experience credit)*")

    lines += ["", "## 4. Qualifications Partially Verified (Synonym Match or Skills-List Placement)", ""]
    if cand.partial_requirements:
        for p in cand.partial_requirements:
            reason = (
                f"same-capability synonym `{p['matched_term']}` (0.5x)"
                if p["match_credit"] == 0.5
                else f"listed in *{p['placement_zone']}* ({p['placement_multiplier']}x)"
            )
            lines.append(f"- **{p['requirement']}** ({p['tier']}): {reason} — Quote: \"{p['quote']}\"")
    else:
        lines.append("- *(None)*")

    lines += [
        "",
        "## 5. Qualifications Not Verified on Submitted Application (Right to Clarify / Correct)",
        "",
    ]
    if cand.unverified_requirements:
        lines.append(
            "The following job requirements were **not detected** on your submitted resume. "
            "If you hold any of these qualifications under a different term or omitted them from your resume, "
            "you may submit a clarification or updated resume to the recruiting team to trigger a deterministic re-score:"
        )
        lines.append("")
        for u in cand.unverified_requirements:
            gate_tag = " **[Required Knockout Gate]**" if u["is_knockout_gate"] else ""
            lines.append(f"- **{u['requirement']}** ({u['tier']}){gate_tag}: `NOT VERIFIED` on submitted text")
    else:
        lines.append("- All requisition requirements were verified on your submitted application.")

    return "\n".join(lines) + "\n"


def render_batch_markdown(batch: ScreeningBatchAudit, include_notices: bool = True) -> str:
    md: List[str] = [
        f"# Explainable Screening Shortlist & Audit Report: {batch.requisition_title}",
        "",
        DISCLAIMER,
        "",
        "## 1. Governance & Run Summary",
        "",
        f"- **Module:** `{batch.module}` (Engine v{batch.engine_version})",
        f"- **Reference Date:** `{batch.reference_date}`",
        f"- **Requirement Config SHA-256:** `{batch.config_sha256}`",
        f"- **Blind Mode Redaction:** `{'ENABLED (PII, ZIP codes, URLs, and education graduation years stripped before scoring)' if batch.blind_mode else 'DISABLED'}`",
        f"- **Shortlist Review Threshold:** `{batch.shortlist_threshold:.1f} / 100` *(advisory only; no candidate is auto-rejected)*",
        f"- **Candidates Evaluated:** `{batch.total_candidates}` "
        f"({batch.recommended_count} recommended for interview review, {batch.borderline_count} borderline, {batch.below_threshold_count} below threshold)",
        "",
        "## 2. Ranked Candidate Shortlist (Human-in-the-Loop)",
        "",
        "| Rank | Candidate ID | Score | Literal | Gates | Verified / Partial / Unverified | Recommendation Band | Human Reviewer Decision |",
        "| :-: | :--- | :-: | :-: | :-: | :-: | :--- | :--- |",
    ]
    for c in batch.candidates:
        gate_str = "PASS" if c.knockout_passed else f"NOT VERIFIED ({', '.join(c.unverified_gates)})"
        counts_str = f"{len(c.verified_requirements)} / {len(c.partial_requirements)} / {len(c.unverified_requirements)}"
        dec_str = c.decision.status
        if c.decision.reviewer:
            dec_str += f" ({c.decision.reviewer})"
        if c.decision.override_reason:
            dec_str += f" — *{c.decision.override_reason}*"
        md.append(
            f"| {c.rank} | `{c.candidate_id}` | **{c.composite_score:.1f}** | {c.literal_score:.1f}% | "
            f"{gate_str} | {counts_str} | {c.recommendation_band} | {dec_str} |"
        )

    if batch.selection_rate_tables:
        md += [
            "",
            "## 3. Adverse-Impact & Four-Fifths (`80%`) Selection-Rate Table",
            "",
            "*Computed post-scoring from separately stored voluntary self-identification records. Demographic fields are never passed to the scoring engine.*",
            "",
            "| Dimension | Group | Applicants | Selected | Selection Rate | Impact Ratio | Four-Fifths (`0.80`) Status |",
            "| :--- | :--- | :-: | :-: | :-: | :-: | :--- |",
        ]
        for row in batch.selection_rate_tables:
            ratio_str = f"{row.impact_ratio:.2f}" if row.impact_ratio is not None else "—"
            md.append(
                f"| `{row.dimension}` | {row.group} | {row.applicants} | {row.selected} | "
                f"{row.selection_rate * 100:.1f}% | {ratio_str} | {row.four_fifths_status} |"
            )

    if include_notices:
        md += ["", "## 4. Per-Candidate Factor & Gap Disclosure Notices", ""]
        for c in batch.candidates:
            md.append("---")
            md.append("")
            md.append(render_candidate_notice(batch, c))

    return "\n".join(md)


# ---------------------------------------------------------------------------
# Built-In Synthetic Demo Batch (6 Candidates)
# ---------------------------------------------------------------------------

def get_demo_batch() -> Tuple[
    List[Tuple[str, str, str]],
    List[oaa.JDRequirement],
    oaa.AlignmentConfig,
    Dict[str, ReviewerDecision],
    Dict[str, Dict[str, str]],
]:
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg_path = os.path.join(root_dir, "examples", "sample_ats_config.json")
    base_resume_path = os.path.join(root_dir, "examples", "sample_resume.txt")
    opt_path = os.path.join(root_dir, "examples", "sample_resume_optimized.txt")
    if os.path.exists(cfg_path) and os.path.exists(base_resume_path):
        reqs, align_cfg = oaa.load_jd_config(cfg_path)
        base_resume = oaa.load_resume_file(base_resume_path)
    else:
        base_resume, reqs, align_cfg = oaa.get_demo_fixture()
    if os.path.exists(opt_path):
        with open(opt_path, "r", encoding="utf-8") as fh:
            opt_resume = fh.read()
    else:
        opt_resume = base_resume

    cand3_borderline = """MORGAN CHEN
Chicago, IL 60601 | morgan.chen@example.com | (312) 555-0192 | https://linkedin.com/in/morganchen

Professional Summary
Senior Operations & Workforce Analyst with 6 years of experience in demand forecasting and supply chain analytics.

Work Experience
Senior Analytics Specialist — Midwest Logistics Group (03/2021 - Present)
- Led enterprise demand forecasting models in Python and SQL across 180 fulfillment centers, improving inventory accuracy by 14%.
- Built Snowflake data pipelines and Tableau executive dashboards for capacity planning and operating budget reviews.
- Partnered with cross-functional leadership to streamline weekly staffing schedules.

Analytics Associate — Great Lakes Retail (06/2019 - 02/2021)
- Maintained SQL reporting tables and weekly SLA trackers across 45 stores.

Education
B.S. in Industrial Engineering — University of Illinois (05/2019)

Technical & Core Skills
Python, SQL, Snowflake, Power BI, Tableau, Capacity Planning, Cross-Functional Leadership
"""

    cand4_career_changer = """TAYLOR BROOKS
Denver, CO 80202 | taylor.brooks@example.com | (303) 555-0144

Professional Summary
Operations Analyst with 4 years of experience building SQL and Power BI reports and supporting workforce scheduling.

Work Experience
Operations Analyst — Rocky Mountain Support Services (01/2022 - Present)
- Built Power BI and SQL dashboards to monitor supplier compliance and weekly staffing across 85 support queues.
- Assisted leadership with capacity planning and cross-functional process improvement initiatives.

Junior Reporting Analyst — Mile High Logistics (01/2020 - 12/2021)
- Prepared weekly Excel and SQL operational reports for regional managers.

Education
Completed 90 credits toward Business Analytics curriculum; 6 years of hands-on operations and reporting experience.

Technical & Core Skills
SQL, Power BI, Excel, Capacity Planning, Cross-Functional Leadership
"""

    candidates = [
        ("CAND-001", "cand_001_optimized.txt", opt_resume),
        ("CAND-002", "cand_002_baseline.txt", base_resume),
        ("CAND-003", "cand_003_borderline.txt", cand3_borderline),
        ("CAND-004", "cand_004_non_degreed.txt", cand4_career_changer),
    ]
    decisions = {
        "CAND-001": ReviewerDecision("ADVANCE", "recruiter_j_smith", "2026-11-15T09:00:00Z", ""),
        "CAND-002": ReviewerDecision("ADVANCE", "recruiter_j_smith", "2026-11-15T09:05:00Z", ""),
        "CAND-003": ReviewerDecision(
            "ADVANCE",
            "recruiter_j_smith",
            "2026-11-15T09:12:00Z",
            "Human override: strong Python/SQL/Snowflake/Demand Forecasting in recent role; verify Power BI and SAP in phone screen.",
        ),
        "CAND-004": ReviewerDecision("PENDING_HUMAN_REVIEW", "", "", ""),
    }
    demographics = {
        "CAND-001": {"gender": "Female", "race_ethnicity": "Hispanic/Latino"},
        "CAND-002": {"gender": "Male", "race_ethnicity": "White"},
        "CAND-003": {"gender": "Female", "race_ethnicity": "Asian"},
        "CAND-004": {"gender": "Male", "race_ethnicity": "Black/African American"},
    }
    return candidates, reqs, align_cfg, decisions, demographics


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-screen`)
# ---------------------------------------------------------------------------

def _load_decisions(path: Optional[str]) -> Dict[str, ReviewerDecision]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    out: Dict[str, ReviewerDecision] = {}
    for cid, d in raw.items():
        if isinstance(d, dict):
            out[cid] = ReviewerDecision(
                status=str(d.get("status", "PENDING_HUMAN_REVIEW")),
                reviewer=str(d.get("reviewer", "")),
                timestamp=str(d.get("timestamp", "")),
                override_reason=str(d.get("override_reason", "")),
            )
    return out


def _load_demographics(path: Optional[str]) -> Dict[str, Dict[str, str]]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops-req-screen",
        description="Explainable Screening Assistant (Employer Edition, Module 2): human-in-the-loop batch screening, blind redaction, Colorado/NYC candidate factor notices, and four-fifths selection-rate tables.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the built-in 4-candidate synthetic screening demo.")
    parser.add_argument("--resumes-dir", help="Directory containing candidate resumes (.txt, .md, .docx, .pdf).")
    parser.add_argument("--resumes", nargs="+", help="Explicit list of candidate resume file paths.")
    parser.add_argument("--jd-config", help="Recruiter-confirmed requirement configuration JSON.")
    parser.add_argument("--jd-file", help="Plain-text or HTML job requisition file.")
    parser.add_argument("--reference-date", default="2026-11-01", help="Reference date YYYY-MM-DD for reproducible tenure math.")
    parser.add_argument("--shortlist-threshold", type=float, default=80.0, help="Advisory shortlist score threshold (default: 80.0).")
    parser.add_argument("--no-blind-mode", action="store_true", help="Disable blind-mode PII/ZIP/graduation-year redaction.")
    parser.add_argument("--decisions-json", help="Optional JSON mapping candidate_id to human reviewer decision records.")
    parser.add_argument("--demographics-json", help="Optional separate JSON mapping candidate_id to voluntary self-ID groups for 4/5ths selection-rate monitoring.")
    parser.add_argument("--notices-dir", help="Optional directory to write individual candidate disclosure notices (.md).")
    parser.add_argument("--output", help="Write Markdown batch report to this path.")
    parser.add_argument("--json-out", help="Write full JSON audit trail to this path.")
    parser.add_argument("--csv-out", help="Write UTF-8 CSV shortlist & component scorecard to this path.")

    args = parser.parse_args(argv)
    ref_dt = datetime.strptime(args.reference_date, "%Y-%m-%d")
    blind_mode = not args.no_blind_mode

    if args.demo:
        candidate_inputs, reqs, align_cfg, decisions_map, demographics_map = get_demo_batch()
    else:
        resume_paths: List[str] = []
        if args.resumes_dir:
            for entry in sorted(os.listdir(args.resumes_dir)):
                if os.path.splitext(entry)[1].lower() in (".txt", ".md", ".docx", ".pdf"):
                    resume_paths.append(os.path.join(args.resumes_dir, entry))
        if args.resumes:
            resume_paths.extend(args.resumes)
        if not resume_paths:
            parser.error("Provide --demo, --resumes-dir, or --resumes.")

        if args.jd_config:
            reqs, align_cfg = oaa.load_jd_config(args.jd_config)
        elif args.jd_file:
            with open(args.jd_file, "r", encoding="utf-8") as fh:
                jd_text = fh.read()
            reqs, align_cfg = oaa.extract_reqs_consistent(jd_text)
        else:
            parser.error("Provide --jd-config or --jd-file when screening resumes.")

        candidate_inputs = []
        for idx, p in enumerate(resume_paths, start=1):
            stem = Path(p).stem
            cid = stem.upper() if stem else f"CAND-{idx:03d}"
            candidate_inputs.append((cid, p, oaa.load_resume_file(p)))

        decisions_map = _load_decisions(args.decisions_json)
        demographics_map = _load_demographics(args.demographics_json)

    batch = screen_batch(
        candidate_inputs=candidate_inputs,
        reqs=reqs,
        align_cfg=align_cfg,
        reference_date=ref_dt,
        blind_mode=blind_mode,
        shortlist_threshold=args.shortlist_threshold,
        decisions_map=decisions_map,
        demographics_map=demographics_map,
    )

    md_report = render_batch_markdown(batch, include_notices=True)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md_report)
    else:
        sys.stdout.write(md_report + "\n")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(batch), fh, indent=2)

    if args.csv_out:
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")
        writer.writerow([
            "rank", "candidate_id", "composite_score", "literal_score",
            "comp1_coverage", "comp2_placement", "comp3_alignment", "comp4_gates", "comp5_hygiene",
            "knockout_passed", "unverified_gates", "verified_count", "partial_count", "unverified_count",
            "recommendation_band", "decision_status", "reviewer", "override_reason",
            "blind_mode", "raw_sha256", "scored_sha256",
        ])
        for c in batch.candidates:
            writer.writerow([
                c.rank,
                c.candidate_id,
                f"{c.composite_score:.2f}",
                f"{c.literal_score:.2f}",
                f"{c.comp1_kw_score:.2f}",
                f"{c.comp2_context_score:.2f}",
                f"{c.comp3_alignment_score:.2f}",
                f"{c.comp4_knockout_score:.2f}",
                f"{c.comp5_schema_score:.2f}",
                c.knockout_passed,
                "; ".join(c.unverified_gates),
                len(c.verified_requirements),
                len(c.partial_requirements),
                len(c.unverified_requirements),
                c.recommendation_band,
                c.decision.status,
                c.decision.reviewer,
                c.decision.override_reason,
                c.blind_mode,
                c.raw_sha256,
                c.scored_sha256,
            ])
        with open(args.csv_out, "w", encoding="utf-8") as fh:
            fh.write(buf.getvalue())

    if args.notices_dir:
        os.makedirs(args.notices_dir, exist_ok=True)
        for c in batch.candidates:
            notice_path = os.path.join(args.notices_dir, f"{c.candidate_id}_disclosure_notice.md")
            with open(notice_path, "w", encoding="utf-8") as fh:
                fh.write(render_candidate_notice(batch, c))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
