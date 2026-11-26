#!/usr/bin/env python3
"""
ops-requisition-auditor: Fairness, Bias & Adverse-Impact Evaluation Engine (`ops-req-fairness`)
===============================================================================================
Evaluates deterministic rubric scores, parser extraction error rates, false-positive /
false-negative rates (FPR/FNR), and human reviewer overrides across protected and operational
subgroups:
  1. `race_ethnicity` (e.g., Asian, Black/African American, Hispanic/Latino, White)
  2. `sex` (e.g., Female, Male, Non-Binary)
  3. `age_proxy_band` (e.g., Under 40 (<40) vs. 40 and Over (>=40, ADEA Protected))
  4. `disability_status` (e.g., Disclosed Disability / Accommodation vs. No Disclosed Disability)
  5. `resume_language` (e.g., en, es, fr, de)
  6. `intersectional_race_sex` (NYC Local Law 144 cross-tabulated `race_ethnicity x sex`)

Statistical & Small-Sample Methodology (Python 3.10+ stdlib only):
  - Wilson Score 95% Confidence Intervals for subgroup selection rates, FPR, and FNR.
  - Exact Hypergeometric Fisher's Exact Test (two-sided p-value via `math.comb`) comparing
    each subgroup against the highest-selected reference group.
  - EEOC Q&A #21 Single-Case "Flip Test" (`flip_adjusted_impact_ratio`) for small samples.
  - 3-Tier Sample-Size Warning System (`CRITICAL_SMALL_SAMPLE n<5`,
    `MODERATE_SAMPLE_EXACT_TEST_REQUIRED 5<=n<30`, `ASYMPTOTIC_POWERED_SAMPLE n>=30`)
    plus NYC LL144 `<2%` pool-share notation.
  - Parser & Rubric Error Audit: per-group parser confidence, low-confidence flag rate,
    parser extraction error rate, rubric Mean Absolute Error (MAE), signed score bias
    (`Engine - Expert`), and blind-mode parity drift (`|Raw - Blind|`).
  - Human Reviewer Override & Error Disparity Audit: upward vs. downward override rates,
    post-override accuracy vs. validated outcomes, and engine vs. post-override FPR/FNR.

Runs on the Python 3.10+ standard library only (zero external dependencies).
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
import math
import os
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import offline_ats_auditor as oaa  # noqa: E402
import explainable_screening_assistant as esa  # noqa: E402
import pilot_evidence_harness as peh  # noqa: E402
import governance_middleware as gov  # noqa: E402

FAIRNESS_ENGINE_VERSION = "fairness-eval-1.0"

EVALUATED_DIMENSIONS: Tuple[str, ...] = (
    "race_ethnicity",
    "sex",
    "age_proxy_band",
    "disability_status",
    "resume_language",
    "intersectional_race_sex",
)


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class FairnessSubjectCase:
    case_id: str
    requisition_title: str
    raw_resume_text: str
    race_ethnicity: str
    sex: str
    age_proxy_band: str                   # "Under 40 (<40)" | "40 and Over (>=40)"
    disability_status: str                # "Disclosed Disability / Accommodation" | "No Disclosed Disability"
    resume_language: str                  # "en" | "es" | "fr" | "de"
    ground_truth_req_count: int
    parser_extracted_req_count: int
    expert_validated_score: float
    validated_outcome: str                # "ADVANCE" | "DO_NOT_ADVANCE"
    human_reviewer_decision: str          # "ADVANCE" | "DO_NOT_ADVANCE"
    reviewer_id: str = "recruiter_j_smith"
    override_reason: str = ""
    blind_score: float = 0.0
    raw_score: float = 0.0
    blind_parity_drift: float = 0.0
    knockout_passed: bool = True
    parser_confidence: float = 1.0
    parser_error_rate: float = 0.0
    engine_selected: bool = False
    human_selected: bool = False
    is_override: bool = False
    override_direction: str = "NONE"      # "NONE" | "UPWARD_TO_ADVANCE" | "DOWNWARD_TO_REJECT"


@dataclass
class SubgroupFairnessRow:
    dimension: str
    subgroup: str
    n_applicants: int
    pool_share_pct: float
    sample_size_tier: str                 # "CRITICAL_SMALL_SAMPLE (n<5)" | "MODERATE_SAMPLE (5<=n<30)" | "POWERED_SAMPLE (n>=30)"
    sample_size_warning: str
    # Engine Selection & Adverse Impact
    engine_selected_count: int
    engine_selection_rate: float
    engine_wilson_95_ci: Tuple[float, float]
    engine_impact_ratio: Optional[float]
    flip_adjusted_impact_ratio: Optional[float]
    fisher_exact_p_value: float
    four_fifths_status: str
    # Human Post-Override Selection & Adverse Impact
    human_selected_count: int
    human_selection_rate: float
    human_impact_ratio: Optional[float]
    # Classification Error Rates vs. Validated Outcomes (Engine vs. Human)
    tp_engine: int
    fp_engine: int
    tn_engine: int
    fn_engine: int
    engine_fpr: Optional[float]
    engine_fnr: Optional[float]
    human_fpr: Optional[float]
    human_fnr: Optional[float]
    # Parser & Rubric Error Telemetry
    mean_parser_confidence: float
    low_confidence_rate: float
    mean_parser_error_rate: float
    mean_engine_score: float
    mean_expert_score: float
    mean_signed_rubric_bias: float        # mean(engine_score - expert_score)
    mean_absolute_rubric_error: float     # MAE
    max_blind_parity_drift: float
    # Reviewer Override Audit
    upward_overrides: int
    downward_overrides: int
    override_rate: float
    override_accuracy_gain: float         # human_accuracy - engine_accuracy


@dataclass
class FairnessAuditReport:
    tool: str
    module: str
    fairness_engine_version: str
    reference_date: str
    shortlist_threshold: float
    total_cases: int
    dimensions_evaluated: List[str]
    overall_blind_parity_max_drift: float
    overall_engine_accuracy: float
    overall_human_accuracy: float
    small_sample_subgroups_flagged: int
    four_fifths_flags_engine: int
    four_fifths_flags_human: int
    parser_disparity_alerts: List[str]
    override_disparity_alerts: List[str]
    subgroup_rows: List[SubgroupFairnessRow]
    case_ledger: List[FairnessSubjectCase]
    report_sha256: str = ""


# ---------------------------------------------------------------------------
# Exact Small-Sample Statistical Functions (Stdlib Only)
# ---------------------------------------------------------------------------

def wilson_95_ci(successes: int, total: int, z: float = 1.959963984540054) -> Tuple[float, float]:
    """Compute Wilson score 95% confidence interval for a binomial proportion."""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denom
    half = (z / denom) * math.sqrt((p * (1.0 - p) / total) + (z2 / (4.0 * total * total)))
    return (round(max(0.0, center - half), 4), round(min(1.0, center + half), 4))


def fisher_exact_two_sided(a: int, b: int, c: int, d: int) -> float:
    """
    Compute exact two-sided Fisher's Exact Test p-value for a 2x2 contingency table:
      Group 1 (Subgroup):   a selected, b not selected (row1 = a + b)
      Group 2 (Reference):  c selected, d not selected (row2 = c + d)
    Uses exact hypergeometric probabilities via `math.comb`.
    """
    row1 = a + b
    row2 = c + d
    col1 = a + c
    total = row1 + row2
    if total <= 0 or row1 <= 0 or row2 <= 0:
        return 1.0

    denom = math.comb(total, col1)
    if denom == 0:
        return 1.0

    def _prob(x: int) -> float:
        return (math.comb(row1, x) * math.comb(row2, col1 - x)) / denom

    p_obs = _prob(a)
    min_x = max(0, col1 - row2)
    max_x = min(row1, col1)

    p_val = 0.0
    for x in range(min_x, max_x + 1):
        px = _prob(x)
        if px <= p_obs + 1e-12:
            p_val += px
    return round(min(1.0, p_val), 4)


def classify_sample_size_warning(n: int, total_pool: int) -> Tuple[str, str]:
    """
    Classify subgroup sample size into a 3-tier statistical confidence tier and
    emit actionable small-sample warnings.
    """
    pool_share = (n / total_pool * 100.0) if total_pool > 0 else 0.0
    lt_2pct_tag = " [NYC LL144 <2% Pool Share]" if pool_share < 2.0 else ""

    if n < 5:
        tier = "CRITICAL_SMALL_SAMPLE (n<5)"
        warn = (
            f"SMALL_SAMPLE_CAUTION (n={n} < 5): Single-applicant change shifts rate by "
            f"{100.0 / max(n, 1):.1f}%. Raw 4/5ths ratio is unstable; rely on Fisher exact p-value, "
            f"EEOC Q&A #21 flip-adjusted ratio, and Wilson 95% CI.{lt_2pct_tag}"
        )
    elif n < 30:
        tier = "MODERATE_SAMPLE (5<=n<30)"
        warn = (
            f"MODERATE_SAMPLE_NOTE (n={n} < 30): Interpret 4/5ths impact ratio alongside "
            f"Fisher exact p-value and Wilson 95% CI.{lt_2pct_tag}"
        )
    else:
        tier = "POWERED_SAMPLE (n>=30)"
        warn = f"ADEQUATE_SAMPLE (n={n} >= 30).{lt_2pct_tag}".strip()
    return tier, warn


# ---------------------------------------------------------------------------
# Built-In 12-Case Representative Fairness & Multilingual Evaluation Cohort
# ---------------------------------------------------------------------------

def get_demo_fairness_cohort() -> Tuple[
    List[Dict[str, Any]],
    List[oaa.JDRequirement],
    oaa.AlignmentConfig,
]:
    """
    Returns a 12-candidate synthetic evaluation cohort spanning race/ethnicity, sex,
    age-proxy bands (<40 vs >=40 with pre-redaction graduation years), disability/accommodation
    disclosures, and multilingual resumes (en, es, fr, de), plus a 9-requirement WFM & Analytics
    requisition configuration.
    """
    reqs = [
        oaa.JDRequirement(name="SQL", category="hard_skill", tier="required", synonyms=[]),
        oaa.JDRequirement(name="Python", category="hard_skill", tier="required", synonyms=[]),
        oaa.JDRequirement(
            name="Demand Forecasting",
            category="hard_skill",
            tier="required",
            synonyms=["demand planning", "pronóstico de demanda", "prévision de la demande", "bedarfsprognose"],
        ),
        oaa.JDRequirement(
            name="Workforce Capacity Modeling",
            category="hard_skill",
            tier="required",
            synonyms=["capacity planning", "planificación de capacidad", "planification de la capacité", "kapazitätsplanung"],
        ),
        oaa.JDRequirement(
            name="Power BI",
            category="hard_skill",
            tier="required",
            synonyms=[],
            alternatives=["Tableau"],
        ),
        oaa.JDRequirement(name="Snowflake", category="hard_skill", tier="preferred", synonyms=[]),
        oaa.JDRequirement(
            name="Erlang C",
            category="hard_skill",
            tier="preferred",
            synonyms=["queueing model"],
        ),
        oaa.JDRequirement(
            name="Cross-Functional Leadership",
            category="soft_skill",
            tier="required",
            synonyms=["cross-functional", "liderazgo interfuncional", "leadership transversal", "bereichsübergreifende führung"],
        ),
        oaa.JDRequirement(
            name="B.S.",
            category="hard_skill",
            tier="required",
            synonyms=["Bachelor", "Licenciatura", "Licence", "Bachelorabschluss"],
            is_knockout_gate=True,
        ),
    ]
    align_cfg = oaa.AlignmentConfig(
        target_title="Senior Workforce & Operations Analytics Lead",
        title_synonyms=[
            "Senior Analytics Manager",
            "Workforce Analytics Lead",
            "Senior Operations Analyst",
            "Analista Senior de Operaciones",
            "Responsable Analytique Opérations",
            "Senior Operations Analyst",
        ],
        min_years_required=5.0,
    )

    def _make_resume(
        name: str,
        city_zip: str,
        email: str,
        title: str,
        start_yr: int,
        grad_yr: int,
        bullets: List[str],
        skills_line: str,
        degree_line: str,
        accommodation_note: str = "",
    ) -> str:
        acc_block = f"\nVoluntary Note: {accommodation_note}\n" if accommodation_note else ""
        b_str = "\n".join(f"- {b}" for b in bullets)
        return (
            f"{name}\n"
            f"{city_zip} | {email} | (314) 555-0142\n"
            f"{acc_block}\n"
            f"Professional Summary\n"
            f"{title} with {2026 - start_yr}+ years of operational analytics and workforce planning experience.\n\n"
            f"Work Experience\n"
            f"{title} — Northstar Operations Group (01/{start_yr} - Present)\n"
            f"{b_str}\n\n"
            f"Education\n"
            f"{degree_line} ({grad_yr}), Class of {grad_yr}\n\n"
            f"Technical & Core Skills\n"
            f"{skills_line}\n"
        )

    strong_bullets_en = [
        "Led enterprise Demand Forecasting and Workforce Capacity Modeling in Python and SQL across 320 queues ($12M budget, 18% SLA gain).",
        "Built Snowflake data pipelines and Power BI dashboards with Erlang C staffing models and Cross-Functional Leadership.",
    ]
    borderline_bullets_en = [
        "Led Demand Forecasting and capacity planning in Python and SQL across 140 fulfillment hubs, improving forecast accuracy by 14%.",
        "Built Tableau dashboards and partnered on cross-functional operations reviews.",
    ]
    weak_bullets_en = [
        "Maintained weekly Excel trackers for 25 local store teams.",
    ]

    cases: List[Dict[str, Any]] = [
        {
            "case_id": "FAIR-01",
            "race_ethnicity": "Hispanic/Latino",
            "sex": "Female",
            "age_proxy_band": "Under 40 (<40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "en",
            "ground_truth_req_count": 9,
            "parser_extracted_req_count": 9,
            "expert_validated_score": 96.0,
            "validated_outcome": "ADVANCE",
            "human_reviewer_decision": "ADVANCE",
            "resume_text": _make_resume(
                "ELENA RODRIGUEZ", "Denver, CO 80202", "elena.r@example.com",
                "Senior Analytics Manager", 2019, 2018, strong_bullets_en,
                "SQL, Python, Demand Forecasting, Workforce Capacity Modeling, Power BI, Snowflake, Erlang C, Cross-Functional Leadership",
                "B.S. in Industrial Engineering — University of Colorado",
            ),
        },
        {
            "case_id": "FAIR-02",
            "race_ethnicity": "White",
            "sex": "Male",
            "age_proxy_band": "40 and Over (>=40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "en",
            "ground_truth_req_count": 9,
            "parser_extracted_req_count": 9,
            "expert_validated_score": 95.0,
            "validated_outcome": "ADVANCE",
            "human_reviewer_decision": "ADVANCE",
            "resume_text": _make_resume(
                "ROBERT MILLER", "St. Louis, MO 63101", "robert.m@example.com",
                "Senior Analytics Manager", 2018, 2001, strong_bullets_en,
                "SQL, Python, Demand Forecasting, Workforce Capacity Modeling, Power BI, Snowflake, Erlang C, Cross-Functional Leadership",
                "B.S. in Operations Research — University of Missouri",
            ),
        },
        {
            "case_id": "FAIR-03",
            "race_ethnicity": "Black/African American",
            "sex": "Female",
            "age_proxy_band": "40 and Over (>=40)",
            "disability_status": "Disclosed Disability / Accommodation",
            "resume_language": "en",
            "ground_truth_req_count": 9,
            "parser_extracted_req_count": 9,
            "expert_validated_score": 95.0,
            "validated_outcome": "ADVANCE",
            "human_reviewer_decision": "ADVANCE",
            "resume_text": _make_resume(
                "MONIQUE WASHINGTON", "Chicago, IL 60601", "monique.w@example.com",
                "Senior Analytics Manager", 2018, 2003, strong_bullets_en,
                "SQL, Python, Demand Forecasting, Workforce Capacity Modeling, Power BI, Snowflake, Erlang C, Cross-Functional Leadership",
                "B.S. in Mathematics — University of Illinois",
                accommodation_note="Screen-reader user; requests accessible interview materials under ADA.",
            ),
        },
        {
            "case_id": "FAIR-04",
            "race_ethnicity": "Asian",
            "sex": "Male",
            "age_proxy_band": "Under 40 (<40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "en",
            "ground_truth_req_count": 9,
            "parser_extracted_req_count": 9,
            "expert_validated_score": 94.0,
            "validated_outcome": "ADVANCE",
            "human_reviewer_decision": "ADVANCE",
            "resume_text": _make_resume(
                "DAVID KIM", "New York, NY 10001", "david.k@example.com",
                "Senior Analytics Manager", 2020, 2019, strong_bullets_en,
                "SQL, Python, Demand Forecasting, Workforce Capacity Modeling, Power BI, Snowflake, Erlang C, Cross-Functional Leadership",
                "B.S. in Statistics — NYU",
            ),
        },
        {
            "case_id": "FAIR-05",
            "race_ethnicity": "Hispanic/Latino",
            "sex": "Male",
            "age_proxy_band": "40 and Over (>=40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "es",
            "ground_truth_req_count": 8,
            "parser_extracted_req_count": 8,
            "expert_validated_score": 86.0,
            "validated_outcome": "ADVANCE",
            "human_reviewer_decision": "ADVANCE",
            "override_reason": "Verified bilingual Spanish/English WFM forecasting & capacity modeling equivalence.",
            "resume_text": _make_resume(
                "CARLOS MENDOZA", "Miami, FL 33101", "carlos.m@example.com",
                "Analista Senior de Operaciones", 2018, 2002,
                [
                    "Lideró modelos de pronóstico de demanda y planificación de capacidad en Python y SQL para 220 centros ($9M presupuesto, 15% mejora SLA).",
                    "Desarrolló pipelines en Snowflake y tableros en Power BI con modelos Erlang C y liderazgo interfuncional.",
                ],
                "SQL, Python, pronóstico de demanda, planificación de capacidad, Power BI, Snowflake, Erlang C, liderazgo interfuncional",
                "Licenciatura en Ingeniería Industrial — Universidad Nacional",
            ),
        },
        {
            "case_id": "FAIR-06",
            "race_ethnicity": "Black/African American",
            "sex": "Male",
            "age_proxy_band": "Under 40 (<40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "fr",
            "ground_truth_req_count": 8,
            "parser_extracted_req_count": 8,
            "expert_validated_score": 85.0,
            "validated_outcome": "ADVANCE",
            "human_reviewer_decision": "ADVANCE",
            "override_reason": "Verified French/English WFM capacity modeling and Snowflake/Power BI portfolio.",
            "resume_text": _make_resume(
                "JEAN-LUC DIALLO", "Atlanta, GA 30301", "jl.diallo@example.com",
                "Responsable Analytique Opérations", 2019, 2017,
                [
                    "Piloté la prévision de la demande et la planification de la capacité en Python et SQL sur 180 files d'attente ($8M budget, +16% SLA).",
                    "Construit des flux Snowflake et tableaux Power BI avec Erlang C et leadership transversal.",
                ],
                "SQL, Python, prévision de la demande, planification de la capacité, Power BI, Snowflake, Erlang C, leadership transversal",
                "Licence en Mathématiques Appliquées — Université",
            ),
        },
        {
            "case_id": "FAIR-07",
            "race_ethnicity": "White",
            "sex": "Female",
            "age_proxy_band": "40 and Over (>=40)",
            "disability_status": "Disclosed Disability / Accommodation",
            "resume_language": "de",
            "ground_truth_req_count": 8,
            "parser_extracted_req_count": 8,
            "expert_validated_score": 86.0,
            "validated_outcome": "ADVANCE",
            "human_reviewer_decision": "ADVANCE",
            "override_reason": "Verified German/English WFM Bedarfsprognose & Kapazitätsplanung equivalence.",
            "resume_text": _make_resume(
                "HANNAH WEBER", "Chicago, IL 60611", "hannah.w@example.com",
                "Senior Operations Analyst", 2018, 2000,
                [
                    "Leitete Bedarfsprognose und Kapazitätsplanung in Python und SQL über 210 Standorte ($11M Budget, 17% SLA-Steigerung).",
                    "Entwickelte Snowflake-Datenpipelines und Power BI-Dashboards mit Erlang C und bereichsübergreifende Führung.",
                ],
                "SQL, Python, Bedarfsprognose, Kapazitätsplanung, Power BI, Snowflake, Erlang C, bereichsübergreifende Führung",
                "Bachelorabschluss in Wirtschaftsingenieurwesen — TU",
                accommodation_note="Ergonomic workstation / captioning accommodation requested.",
            ),
        },
        {
            "case_id": "FAIR-08",
            "race_ethnicity": "Asian",
            "sex": "Female",
            "age_proxy_band": "Under 40 (<40)",
            "disability_status": "Disclosed Disability / Accommodation",
            "resume_language": "en",
            "ground_truth_req_count": 7,
            "parser_extracted_req_count": 7,
            "expert_validated_score": 82.0,
            "validated_outcome": "ADVANCE",
            "human_reviewer_decision": "ADVANCE",
            "override_reason": "Human override: strong Python/SQL/Demand Forecasting/Tableau in recent role; verify Snowflake in technical screen.",
            "resume_text": _make_resume(
                "PRIYA PATEL", "Austin, TX 78701", "priya.p@example.com",
                "Senior Operations Analyst", 2020, 2019, borderline_bullets_en,
                "SQL, Python, Demand Forecasting, Capacity Planning, Tableau, Cross-Functional Leadership",
                "B.S. in Data Science — UT Austin",
                accommodation_note="Extended time / quiet environment accommodation if timed assessment is used.",
            ),
        },
        {
            "case_id": "FAIR-09",
            "race_ethnicity": "Black/African American",
            "sex": "Female",
            "age_proxy_band": "Under 40 (<40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "en",
            "ground_truth_req_count": 3,
            "parser_extracted_req_count": 3,
            "expert_validated_score": 38.0,
            "validated_outcome": "DO_NOT_ADVANCE",
            "human_reviewer_decision": "DO_NOT_ADVANCE",
            "resume_text": _make_resume(
                "KEISHA GRANT", "Atlanta, GA 30303", "keisha.g@example.com",
                "Operations Coordinator", 2023, 2022, weak_bullets_en,
                "Excel, SQL",
                "B.S. in Business Administration — Georgia State University",
            ),
        },
        {
            "case_id": "FAIR-10",
            "race_ethnicity": "White",
            "sex": "Male",
            "age_proxy_band": "Under 40 (<40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "en",
            "ground_truth_req_count": 3,
            "parser_extracted_req_count": 3,
            "expert_validated_score": 36.0,
            "validated_outcome": "DO_NOT_ADVANCE",
            "human_reviewer_decision": "DO_NOT_ADVANCE",
            "resume_text": _make_resume(
                "BRADLEY COOPER", "Dallas, TX 75201", "brad.c@example.com",
                "Operations Coordinator", 2023, 2022, weak_bullets_en,
                "Excel, SQL",
                "B.S. in Marketing — SMU",
            ),
        },
        {
            "case_id": "FAIR-11",
            "race_ethnicity": "Hispanic/Latino",
            "sex": "Female",
            "age_proxy_band": "40 and Over (>=40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "en",
            "ground_truth_req_count": 3,
            "parser_extracted_req_count": 3,
            "expert_validated_score": 37.0,
            "validated_outcome": "DO_NOT_ADVANCE",
            "human_reviewer_decision": "DO_NOT_ADVANCE",
            "resume_text": _make_resume(
                "MARIA SANTOS", "Phoenix, AZ 85001", "maria.s@example.com",
                "Operations Coordinator", 2023, 2004, weak_bullets_en,
                "Excel, SQL",
                "B.S. in Communications — ASU",
            ),
        },
        {
            "case_id": "FAIR-12",
            "race_ethnicity": "Asian",
            "sex": "Male",
            "age_proxy_band": "40 and Over (>=40)",
            "disability_status": "No Disclosed Disability",
            "resume_language": "en",
            "ground_truth_req_count": 3,
            "parser_extracted_req_count": 3,
            "expert_validated_score": 36.0,
            "validated_outcome": "DO_NOT_ADVANCE",
            "human_reviewer_decision": "DO_NOT_ADVANCE",
            "resume_text": _make_resume(
                "KENJI TANAKA", "Seattle, WA 98101", "kenji.t@example.com",
                "Operations Coordinator", 2023, 2001, weak_bullets_en,
                "Excel, SQL",
                "B.S. in Economics — UW",
            ),
        },
    ]
    return cases, reqs, align_cfg


# ---------------------------------------------------------------------------
# Evaluation Loop Across All 6 Protected & Operational Dimensions
# ---------------------------------------------------------------------------

def evaluate_fairness_cohort(
    raw_cases: Sequence[Mapping[str, Any]],
    reqs: List[oaa.JDRequirement],
    align_cfg: oaa.AlignmentConfig,
    reference_date: datetime,
    shortlist_threshold: float = 80.0,
) -> FairnessAuditReport:
    evaluated_cases: List[FairnessSubjectCase] = []

    for c in raw_cases:
        cid = str(c["case_id"])
        raw_txt = str(c["resume_text"])
        blind_txt, _ = esa.redact_resume_for_blind_screening(raw_txt)

        rep_raw = oaa.run_ats_audit(raw_txt, reqs, align_cfg, reference_date=reference_date)
        rep_blind = oaa.run_ats_audit(blind_txt, reqs, align_cfg, reference_date=reference_date)

        raw_score = round(rep_raw.composite_score, 1)
        blind_score = round(rep_blind.composite_score, 1)
        drift = round(abs(raw_score - blind_score), 4)

        parsed_blind = oaa.parse_resume(blind_txt, reference_date=reference_date)
        p_diag = peh.compute_parser_confidence(
            raw_resume_text=raw_txt,
            parsed_resume=parsed_blind,
            raw_jd_text=None,
            reqs=reqs,
            align_cfg=align_cfg,
            has_confirmed_config=True,
        )
        p_conf = round(p_diag.resume_confidence, 4)

        gt_reqs = max(int(c.get("ground_truth_req_count", len(reqs))), 1)
        ext_reqs = int(c.get("parser_extracted_req_count", gt_reqs))
        p_err = round(max(0.0, (gt_reqs - ext_reqs) / gt_reqs), 4)

        eng_sel = (blind_score >= shortlist_threshold) and rep_blind.knockout_passed
        hum_dec = str(c.get("human_reviewer_decision", "ADVANCE" if eng_sel else "DO_NOT_ADVANCE")).upper()
        hum_sel = hum_dec == "ADVANCE"

        is_ovr = eng_sel != hum_sel
        if not is_ovr:
            ovr_dir = "NONE"
        elif hum_sel and not eng_sel:
            ovr_dir = "UPWARD_TO_ADVANCE"
        else:
            ovr_dir = "DOWNWARD_TO_REJECT"

        evaluated_cases.append(
            FairnessSubjectCase(
                case_id=cid,
                requisition_title=align_cfg.target_title or "Senior Workforce & Operations Analytics Lead",
                raw_resume_text=raw_txt,
                race_ethnicity=str(c.get("race_ethnicity", "Unspecified")),
                sex=str(c.get("sex", "Unspecified")),
                age_proxy_band=str(c.get("age_proxy_band", "Unspecified")),
                disability_status=str(c.get("disability_status", "No Disclosed Disability")),
                resume_language=str(c.get("resume_language", "en")),
                ground_truth_req_count=gt_reqs,
                parser_extracted_req_count=ext_reqs,
                expert_validated_score=float(c.get("expert_validated_score", blind_score)),
                validated_outcome=str(c.get("validated_outcome", "ADVANCE" if hum_sel else "DO_NOT_ADVANCE")).upper(),
                human_reviewer_decision=hum_dec,
                reviewer_id=str(c.get("reviewer_id", "recruiter_j_smith")),
                override_reason=str(c.get("override_reason", "")),
                blind_score=blind_score,
                raw_score=raw_score,
                blind_parity_drift=drift,
                knockout_passed=rep_blind.knockout_passed,
                parser_confidence=p_conf,
                parser_error_rate=p_err,
                engine_selected=eng_sel,
                human_selected=hum_sel,
                is_override=is_ovr,
                override_direction=ovr_dir,
            )
        )

    total_n = len(evaluated_cases)
    subgroup_rows: List[SubgroupFairnessRow] = []
    parser_alerts: List[str] = []
    override_alerts: List[str] = []

    for dim in EVALUATED_DIMENSIONS:
        buckets: Dict[str, List[FairnessSubjectCase]] = {}
        for case in evaluated_cases:
            if dim == "intersectional_race_sex":
                key = f"{case.race_ethnicity} × {case.sex}"
            else:
                key = str(getattr(case, dim))
            buckets.setdefault(key, []).append(case)

        # Identify highest selection rate reference group for Engine and Human
        eng_rates = {
            g: (sum(1 for x in items if x.engine_selected) / len(items)) if items else 0.0
            for g, items in buckets.items()
        }
        hum_rates = {
            g: (sum(1 for x in items if x.human_selected) / len(items)) if items else 0.0
            for g, items in buckets.items()
        }
        ref_group_eng = max(buckets.keys(), key=lambda g: (eng_rates[g], len(buckets[g])))
        max_eng_rate = eng_rates[ref_group_eng]
        max_hum_rate = max(hum_rates.values()) if hum_rates else 0.0

        ref_items = buckets[ref_group_eng]
        ref_sel = sum(1 for x in ref_items if x.engine_selected)
        ref_unsel = len(ref_items) - ref_sel

        for grp in sorted(buckets.keys()):
            items = buckets[grp]
            n = len(items)
            pool_share = round((n / total_n * 100.0) if total_n > 0 else 0.0, 1)
            tier, warn = classify_sample_size_warning(n, total_n)

            eng_sel_cnt = sum(1 for x in items if x.engine_selected)
            eng_rate = eng_rates[grp]
            ci_low, ci_high = wilson_95_ci(eng_sel_cnt, n)
            eng_ir = round(eng_rate / max_eng_rate, 3) if max_eng_rate > 0 else None

            # EEOC Q&A #21 single-case flip-adjusted impact ratio (+1 selection in subgroup)
            flip_rate = min(1.0, (eng_sel_cnt + 1) / n) if n > 0 else 0.0
            flip_ir = round(flip_rate / max_eng_rate, 3) if max_eng_rate > 0 else None

            if grp == ref_group_eng:
                p_fisher = 1.0
            else:
                p_fisher = fisher_exact_two_sided(eng_sel_cnt, n - eng_sel_cnt, ref_sel, ref_unsel)

            if eng_ir is None:
                ff_status = "NO_SELECTIONS"
            elif eng_ir >= 0.80:
                ff_status = f"PASS (>=0.80){' [Small Sample n<5]' if n < 5 else ''}"
            elif n < 5 and (flip_ir is not None and flip_ir >= 0.80) and p_fisher >= 0.05:
                ff_status = f"SMALL_SAMPLE_NO_STAT_SIG_DISPARITY (IR={eng_ir:.2f}, Flip-IR={flip_ir:.2f}, p={p_fisher:.3f})"
            else:
                ff_status = f"FLAG (<0.80, IR={eng_ir:.2f}, p={p_fisher:.3f})"

            hum_sel_cnt = sum(1 for x in items if x.human_selected)
            hum_rate = hum_rates[grp]
            hum_ir = round(hum_rate / max_hum_rate, 3) if max_hum_rate > 0 else None

            # Confusion matrix vs. validated_outcome
            tp_e = sum(1 for x in items if x.engine_selected and x.validated_outcome == "ADVANCE")
            fp_e = sum(1 for x in items if x.engine_selected and x.validated_outcome != "ADVANCE")
            tn_e = sum(1 for x in items if (not x.engine_selected) and x.validated_outcome != "ADVANCE")
            fn_e = sum(1 for x in items if (not x.engine_selected) and x.validated_outcome == "ADVANCE")

            eng_fpr = round(fp_e / (fp_e + tn_e), 4) if (fp_e + tn_e) > 0 else None
            eng_fnr = round(fn_e / (fn_e + tp_e), 4) if (fn_e + tp_e) > 0 else None

            tp_h = sum(1 for x in items if x.human_selected and x.validated_outcome == "ADVANCE")
            fp_h = sum(1 for x in items if x.human_selected and x.validated_outcome != "ADVANCE")
            tn_h = sum(1 for x in items if (not x.human_selected) and x.validated_outcome != "ADVANCE")
            fn_h = sum(1 for x in items if (not x.human_selected) and x.validated_outcome == "ADVANCE")

            hum_fpr = round(fp_h / (fp_h + tn_h), 4) if (fp_h + tn_h) > 0 else None
            hum_fnr = round(fn_h / (fn_h + tp_h), 4) if (fn_h + tp_h) > 0 else None

            mean_p_conf = round(sum(x.parser_confidence for x in items) / n, 4)
            low_conf_rate = round(sum(1 for x in items if x.parser_confidence < 0.75) / n, 4)
            mean_p_err = round(sum(x.parser_error_rate for x in items) / n, 4)

            mean_eng_sc = round(sum(x.blind_score for x in items) / n, 2)
            mean_exp_sc = round(sum(x.expert_validated_score for x in items) / n, 2)
            signed_bias = round(sum(x.blind_score - x.expert_validated_score for x in items) / n, 2)
            mae = round(sum(abs(x.blind_score - x.expert_validated_score) for x in items) / n, 2)
            max_drift = round(max(x.blind_parity_drift for x in items), 4)

            up_ovr = sum(1 for x in items if x.override_direction == "UPWARD_TO_ADVANCE")
            dn_ovr = sum(1 for x in items if x.override_direction == "DOWNWARD_TO_REJECT")
            ovr_rate = round((up_ovr + dn_ovr) / n, 4)

            eng_acc = (tp_e + tn_e) / n
            hum_acc = (tp_h + tn_h) / n
            acc_gain = round(hum_acc - eng_acc, 4)

            if mean_p_err > 0.15 or low_conf_rate > 0.25:
                parser_alerts.append(
                    f"[PARSER DISPARITY ALERT] {dim}='{grp}': mean_parser_error_rate={mean_p_err * 100:.1f}%, "
                    f"low_confidence_rate={low_conf_rate * 100:.1f}%."
                )
            if dn_ovr > 0 and acc_gain < 0:
                override_alerts.append(
                    f"[OVERRIDE DISPARITY ALERT] {dim}='{grp}': {dn_ovr} downward override(s) reduced accuracy vs. validated outcomes ({acc_gain * 100:+.1f}%)."
                )

            subgroup_rows.append(
                SubgroupFairnessRow(
                    dimension=dim,
                    subgroup=grp,
                    n_applicants=n,
                    pool_share_pct=pool_share,
                    sample_size_tier=tier,
                    sample_size_warning=warn,
                    engine_selected_count=eng_sel_cnt,
                    engine_selection_rate=round(eng_rate, 4),
                    engine_wilson_95_ci=(ci_low, ci_high),
                    engine_impact_ratio=eng_ir,
                    flip_adjusted_impact_ratio=flip_ir,
                    fisher_exact_p_value=p_fisher,
                    four_fifths_status=ff_status,
                    human_selected_count=hum_sel_cnt,
                    human_selection_rate=round(hum_rate, 4),
                    human_impact_ratio=hum_ir,
                    tp_engine=tp_e,
                    fp_engine=fp_e,
                    tn_engine=tn_e,
                    fn_engine=fn_e,
                    engine_fpr=eng_fpr,
                    engine_fnr=eng_fnr,
                    human_fpr=hum_fpr,
                    human_fnr=hum_fnr,
                    mean_parser_confidence=mean_p_conf,
                    low_confidence_rate=low_conf_rate,
                    mean_parser_error_rate=mean_p_err,
                    mean_engine_score=mean_eng_sc,
                    mean_expert_score=mean_exp_sc,
                    mean_signed_rubric_bias=signed_bias,
                    mean_absolute_rubric_error=mae,
                    max_blind_parity_drift=max_drift,
                    upward_overrides=up_ovr,
                    downward_overrides=dn_ovr,
                    override_rate=ovr_rate,
                    override_accuracy_gain=acc_gain,
                )
            )

    overall_drift = max((c.blind_parity_drift for c in evaluated_cases), default=0.0)
    overall_eng_acc = round(
        sum(1 for c in evaluated_cases if (c.engine_selected == (c.validated_outcome == "ADVANCE"))) / max(total_n, 1),
        4,
    )
    overall_hum_acc = round(
        sum(1 for c in evaluated_cases if (c.human_selected == (c.validated_outcome == "ADVANCE"))) / max(total_n, 1),
        4,
    )
    small_sample_cnt = sum(1 for r in subgroup_rows if "CRITICAL_SMALL_SAMPLE" in r.sample_size_tier)
    ff_flags_eng = sum(1 for r in subgroup_rows if r.four_fifths_status.startswith("FLAG"))
    ff_flags_hum = sum(
        1 for r in subgroup_rows if (r.human_impact_ratio is not None and r.human_impact_ratio < 0.80)
    )

    rep = FairnessAuditReport(
        tool="ops-requisition-auditor",
        module="fairness-adverse-impact-auditor",
        fairness_engine_version=FAIRNESS_ENGINE_VERSION,
        reference_date=reference_date.strftime("%Y-%m-%d"),
        shortlist_threshold=shortlist_threshold,
        total_cases=total_n,
        dimensions_evaluated=list(EVALUATED_DIMENSIONS),
        overall_blind_parity_max_drift=overall_drift,
        overall_engine_accuracy=overall_eng_acc,
        overall_human_accuracy=overall_hum_acc,
        small_sample_subgroups_flagged=small_sample_cnt,
        four_fifths_flags_engine=ff_flags_eng,
        four_fifths_flags_human=ff_flags_hum,
        parser_disparity_alerts=parser_alerts,
        override_disparity_alerts=override_alerts,
        subgroup_rows=subgroup_rows,
        case_ledger=evaluated_cases,
    )
    canonical = json.dumps(
        {
            "fairness_engine_version": rep.fairness_engine_version,
            "reference_date": rep.reference_date,
            "shortlist_threshold": rep.shortlist_threshold,
            "total_cases": rep.total_cases,
            "subgroup_rows": [dataclasses.asdict(r) for r in rep.subgroup_rows],
        },
        sort_keys=True,
    )
    rep.report_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return rep


# ---------------------------------------------------------------------------
# Reproducible Markdown & CSV Dashboard Exporters
# ---------------------------------------------------------------------------

def render_fairness_markdown(rep: FairnessAuditReport) -> str:
    lines: List[str] = [
        "# Fairness, Bias & Adverse-Impact Evaluation Dashboard (`ops-req-fairness`)",
        "",
        "*Evaluates selection rates, Wilson 95% confidence intervals, exact Fisher p-values, small-sample flip tests, parser/rubric error rates, FPR/FNR, and human reviewer overrides across Race/Ethnicity, Sex, Age Proxies (ADEA >=40), Disability/Accommodation Status, Resume Language (`en`/`es`/`fr`/`de`), and Intersectional (`Race x Sex`) subgroups.*",
        "",
        "## 1. Cohort Summary & Equity Telemetry",
        "",
        f"- **Fairness Engine Version:** `{rep.fairness_engine_version}` (Reference Date: `{rep.reference_date}`)",
        f"- **Report Cryptographic Fingerprint (SHA-256):** `{rep.report_sha256}`",
        f"- **Candidates Evaluated:** `{rep.total_cases}` across `{len(rep.dimensions_evaluated)}` dimensions (`{', '.join(rep.dimensions_evaluated)}`)",
        f"- **Advisory Shortlist Cutoff:** `>= {rep.shortlist_threshold:.1f} / 100` + Required Knockout Gates",
        f"- **Blind-Mode Score Parity Drift (Max `|Raw - Blind|`):** `{rep.overall_blind_parity_max_drift:.2f} pts` *(0.00 = exact invariance across name, ZIP, graduation year, and disability notes)*",
        f"- **Overall Classification Accuracy vs. Validated Outcomes:** Engine Cutoff `{rep.overall_engine_accuracy * 100:.1f}%` -> Human-in-the-Loop Post-Override `{rep.overall_human_accuracy * 100:.1f}%`",
        f"- **Subgroups with Small-Sample Warnings (`n < 5`):** `{rep.small_sample_subgroups_flagged}` *(evaluated with Fisher's Exact Test, Wilson 95% CI, and EEOC Q&A #21 single-case flip test)*",
        f"- **Statistically Significant Four-Fifths Flags (`< 0.80` after Small-Sample Flip Check):** Engine `{rep.four_fifths_flags_engine}` | Human Post-Override `{rep.four_fifths_flags_human}`",
        f"- **Parser Disparity Alerts:** `{len(rep.parser_disparity_alerts)}`",
        f"- **Reviewer Override Disparity Alerts:** `{len(rep.override_disparity_alerts)}`",
        "",
        "## 2. Adverse-Impact, Small-Sample Confidence & Four-Fifths (`0.80`) Matrix",
        "",
        "| Dimension | Subgroup | `n` (`Share`) | Sample Tier | Engine Sel Rate (`95% Wilson CI`) | Engine IR (`Flip IR`) | Fisher Exact `p` | Engine 4/5ths Status | Human Sel Rate (`Human IR`) |",
        "| :--- | :--- | :-: | :--- | :-: | :-: | :-: | :--- | :-: |",
    ]

    for r in rep.subgroup_rows:
        ci_str = f"[{r.engine_wilson_95_ci[0] * 100:.1f}%, {r.engine_wilson_95_ci[1] * 100:.1f}%]"
        ir_str = f"{r.engine_impact_ratio:.2f}" if r.engine_impact_ratio is not None else "—"
        flip_str = f"{r.flip_adjusted_impact_ratio:.2f}" if r.flip_adjusted_impact_ratio is not None else "—"
        hum_ir_str = f"{r.human_impact_ratio:.2f}" if r.human_impact_ratio is not None else "—"
        lines.append(
            f"| `{r.dimension}` | **{r.subgroup}** | `{r.n_applicants}` (`{r.pool_share_pct:.1f}%`) | "
            f"`{r.sample_size_tier}` | `{r.engine_selection_rate * 100:.1f}%` `{ci_str}` | "
            f"`{ir_str}` (`{flip_str}`) | `{r.fisher_exact_p_value:.3f}` | {r.four_fifths_status} | "
            f"`{r.human_selection_rate * 100:.1f}%` (`{hum_ir_str}`) |"
        )

    lines += [
        "",
        "## 3. Parser Error Rate, Rubric Calibration & Blind-Mode Parity by Subgroup",
        "",
        "| Dimension | Subgroup | `n` | Parser Conf | Low-Conf Rate (`<75%`) | Parser Error Rate | Mean Engine Score | Mean Expert Score | Signed Bias (`Eng - Exp`) | Rubric `MAE` | Blind Drift (`Δ`) |",
        "| :--- | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |",
    ]
    for r in rep.subgroup_rows:
        if r.dimension == "intersectional_race_sex":
            continue
        lines.append(
            f"| `{r.dimension}` | **{r.subgroup}** | `{r.n_applicants}` | `{r.mean_parser_confidence * 100:.1f}%` | "
            f"`{r.low_confidence_rate * 100:.1f}%` | `{r.mean_parser_error_rate * 100:.1f}%` | "
            f"`{r.mean_engine_score:.1f}` | `{r.mean_expert_score:.1f}` | `{r.mean_signed_rubric_bias:+.1f}` | "
            f"`{r.mean_absolute_rubric_error:.1f}` | `{r.max_blind_parity_drift:.2f}` |"
        )

    lines += [
        "",
        "## 4. False-Positive / False-Negative Rates & Reviewer Override Audit",
        "",
        "| Dimension | Subgroup | `n` | `TP / FP / TN / FN` (Engine) | Engine `FPR` | Engine `FNR` | Human `FPR` | Human `FNR` | Overrides (`Up / Down`) | Override Rate | Accuracy Gain from Human Review |",
        "| :--- | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |",
    ]
    for r in rep.subgroup_rows:
        if r.dimension == "intersectional_race_sex":
            continue
        e_fpr = f"{r.engine_fpr * 100:.1f}%" if r.engine_fpr is not None else "—"
        e_fnr = f"{r.engine_fnr * 100:.1f}%" if r.engine_fnr is not None else "—"
        h_fpr = f"{r.human_fpr * 100:.1f}%" if r.human_fpr is not None else "—"
        h_fnr = f"{r.human_fnr * 100:.1f}%" if r.human_fnr is not None else "—"
        lines.append(
            f"| `{r.dimension}` | **{r.subgroup}** | `{r.n_applicants}` | "
            f"`{r.tp_engine} / {r.fp_engine} / {r.tn_engine} / {r.fn_engine}` | `{e_fpr}` | `{e_fnr}` | "
            f"`{h_fpr}` | `{h_fnr}` | `+{r.upward_overrides} / -{r.downward_overrides}` | "
            f"`{r.override_rate * 100:.1f}%` | `{r.override_accuracy_gain * 100:+.1f}%` |"
        )

    if rep.parser_disparity_alerts or rep.override_disparity_alerts:
        lines += ["", "## 5. Active Disparity & Override Alerts", ""]
        for a in rep.parser_disparity_alerts + rep.override_disparity_alerts:
            lines.append(f"- {a}")

    return "\n".join(lines) + "\n"


def render_fairness_csv(rep: FairnessAuditReport) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "dimension",
        "subgroup",
        "n_applicants",
        "pool_share_pct",
        "sample_size_tier",
        "engine_selected_count",
        "engine_selection_rate",
        "wilson_95_ci_low",
        "wilson_95_ci_high",
        "engine_impact_ratio",
        "flip_adjusted_impact_ratio",
        "fisher_exact_p_value",
        "four_fifths_status",
        "human_selected_count",
        "human_selection_rate",
        "human_impact_ratio",
        "tp_engine",
        "fp_engine",
        "tn_engine",
        "fn_engine",
        "engine_fpr",
        "engine_fnr",
        "human_fpr",
        "human_fnr",
        "mean_parser_confidence",
        "low_confidence_rate",
        "mean_parser_error_rate",
        "mean_engine_score",
        "mean_expert_score",
        "mean_signed_rubric_bias",
        "mean_absolute_rubric_error",
        "max_blind_parity_drift",
        "upward_overrides",
        "downward_overrides",
        "override_rate",
        "override_accuracy_gain",
    ])
    for r in rep.subgroup_rows:
        writer.writerow([
            r.dimension,
            r.subgroup,
            r.n_applicants,
            f"{r.pool_share_pct:.1f}",
            r.sample_size_tier,
            r.engine_selected_count,
            f"{r.engine_selection_rate:.4f}",
            f"{r.engine_wilson_95_ci[0]:.4f}",
            f"{r.engine_wilson_95_ci[1]:.4f}",
            f"{r.engine_impact_ratio:.3f}" if r.engine_impact_ratio is not None else "",
            f"{r.flip_adjusted_impact_ratio:.3f}" if r.flip_adjusted_impact_ratio is not None else "",
            f"{r.fisher_exact_p_value:.4f}",
            r.four_fifths_status,
            r.human_selected_count,
            f"{r.human_selection_rate:.4f}",
            f"{r.human_impact_ratio:.3f}" if r.human_impact_ratio is not None else "",
            r.tp_engine,
            r.fp_engine,
            r.tn_engine,
            r.fn_engine,
            f"{r.engine_fpr:.4f}" if r.engine_fpr is not None else "",
            f"{r.engine_fnr:.4f}" if r.engine_fnr is not None else "",
            f"{r.human_fpr:.4f}" if r.human_fpr is not None else "",
            f"{r.human_fnr:.4f}" if r.human_fnr is not None else "",
            f"{r.mean_parser_confidence:.4f}",
            f"{r.low_confidence_rate:.4f}",
            f"{r.mean_parser_error_rate:.4f}",
            f"{r.mean_engine_score:.2f}",
            f"{r.mean_expert_score:.2f}",
            f"{r.mean_signed_rubric_bias:.2f}",
            f"{r.mean_absolute_rubric_error:.2f}",
            f"{r.max_blind_parity_drift:.4f}",
            r.upward_overrides,
            r.downward_overrides,
            f"{r.override_rate:.4f}",
            f"{r.override_accuracy_gain:.4f}",
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-fairness`)
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops-req-fairness",
        description="Fairness, Bias & Adverse-Impact Evaluation Engine across Race, Sex, Age Proxies, Disability Status, and Resume Language.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the built-in 12-case 6-dimension fairness & adverse-impact evaluation cohort.")
    parser.add_argument("--manifest", help="Path to a fairness evaluation cohort manifest JSON.")
    parser.add_argument("--export-demo-manifest", help="Export the built-in 12-case fairness cohort manifest JSON to this path and exit.")
    parser.add_argument("--reference-date", default="2026-11-01", help="Reference date YYYY-MM-DD (default: 2026-11-01).")
    parser.add_argument("--shortlist-threshold", type=float, default=80.0, help="Advisory shortlist cutoff score (default: 80.0).")
    parser.add_argument("--output", help="Write Markdown impact dashboard report to this path.")
    parser.add_argument("--json-out", help="Write full JSON fairness audit trail to this path.")
    parser.add_argument("--csv-out", help="Write UTF-8 CSV subgroup fairness & adverse-impact table to this path.")

    args = parser.parse_args(argv)
    ref_dt = datetime.strptime(args.reference_date, "%Y-%m-%d")

    cases, reqs, align_cfg = get_demo_fairness_cohort()
    if args.export_demo_manifest:
        payload = {
            "schema": "fairness-cohort-manifest-1.0",
            "reference_date": args.reference_date,
            "shortlist_threshold": args.shortlist_threshold,
            "requirements": [dataclasses.asdict(r) for r in reqs],
            "alignment": dataclasses.asdict(align_cfg),
            "cases": cases,
        }
        with open(args.export_demo_manifest, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        sys.stdout.write(f"[FAIRNESS OK] Exported 12-case fairness cohort manifest to {args.export_demo_manifest}\n")
        return 0

    if args.manifest:
        m_path = gov.resolve_input_path(args.manifest)
        with open(m_path, "r", encoding="utf-8") as fh:
            raw_m = json.load(fh)
        reqs = [oaa.JDRequirement(**r) for r in raw_m["requirements"]]
        align_raw = dict(raw_m.get("alignment", {}))
        align_raw.pop("reference_date", None)
        align_cfg = oaa.AlignmentConfig(**align_raw)
        cases = list(raw_m["cases"])

    report = evaluate_fairness_cohort(
        raw_cases=cases,
        reqs=reqs,
        align_cfg=align_cfg,
        reference_date=ref_dt,
        shortlist_threshold=args.shortlist_threshold,
    )

    md_out = render_fairness_markdown(report)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md_out)
    else:
        sys.stdout.write(md_out)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(report), fh, indent=2)

    if args.csv_out:
        with open(args.csv_out, "w", encoding="utf-8") as fh:
            fh.write(render_fairness_csv(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
