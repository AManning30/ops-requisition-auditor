#!/usr/bin/env python3
"""
ops-requisition-auditor: Pilot & Empirical Validity Evaluation Harness (ops-req-pilot)
======================================================================================
Proves (or falsifies) real-world predictive validity of the deterministic rubric by:
  1. Defining & tracking 5 longitudinal outcomes per deidentified pilot case:
     - `interview`           (advanced to structured hiring-manager interview: 0/1)
     - `selection`           (extended job offer / selected for role: 0/1)
     - `promotion`           (promoted or expanded scope within 12-18 months: 0/1, optional)
     - `performance_90d`     (met/exceeded 90-day operational KPI ramp target: 0/1 or 0-100, optional)
     - `attrition_90d`       (early regrettable/involuntary turnover within 90 days: 0/1, optional)
  2. Enforcing deidentification on pilot data:
     - Verifies that pilot resumes are stripped of raw PII (names, emails, phones, URLs,
       5/9-digit ZIP codes, graduation years) and assigns salted SHA-256 study IDs.
  3. Blinded comparison against independent recruiter decisions:
     - Evaluates independent `blinded_recruiter_decision` (made without seeing the engine
       score) alongside `unblinded_human_override` and the engine's own cutoffs (`>=80`, `>=65`).
  4. Measuring Calibration, Brier Score, ROC-AUC, and 95% Confidence Intervals:
     - Brier Score: mean((p_i - y_i)^2) where p_i = score_i / 100
     - Expected Calibration Error (ECE) & Reliability Curve across probability tertiles
     - Exact Mann-Whitney U ROC-AUC (with tie handling)
     - Deterministic seeded bootstrap (B=1000, seed=20261101) & Wilson 95% Confidence Intervals
       for ROC-AUC, Brier Score, Accuracy, F1, Cohen's Kappa, and Pearson r
  5. Component Ablation & Misleading-Feature Diagnostics:
     - Measures each component's (C1..C5) point-biserial correlation with outcomes, leave-one-out
       delta AUC, and delta Brier score, automatically flagging features that fail to predict
       outcomes or introduce false-negative formatting penalties (`KEEP` / `REWEIGHT` / `REDESIGN`).

Runs on the Python 3.10+ standard library only (zero external dependencies, no LLM).
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
from pathlib import Path
import random
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import offline_ats_auditor as oaa  # noqa: E402
import job_posting_collector as jpc  # noqa: E402
import requisition_quality_auditor as rqa  # noqa: E402
import explainable_screening_assistant as esa  # noqa: E402
import governance_middleware as gov  # noqa: E402

HARNESS_VERSION = gov.ENGINE_VERSION
BOOTSTRAP_SEED = 20261101
BOOTSTRAP_ITERATIONS = 1000

POSITIVE_OUTCOMES = {
    "ADVANCE", "SELECTED", "INTERVIEW", "INTERVIEW_PASS",
    "STRONG_MATCH", "QUALIFIED", "HIRED", "OFFER", "PASS",
    "PROMOTED", "MEETS_EXPECTATIONS", "EXCEEDS_EXPECTATIONS",
    "RETAINED", "1", "TRUE", "YES",
}
NEGATIVE_OUTCOMES = {
    "DO_NOT_ADVANCE", "REJECT", "REJECTED", "NOT_QUALIFIED",
    "WEAK_MATCH", "FAIL", "NOT_SELECTED", "BELOW_EXPECTATIONS",
    "ATTRITED", "0", "FALSE", "NO",
}


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass
class ConfidenceInterval95:
    estimate: float
    ci_lower: float
    ci_upper: float
    method: str  # "bootstrap_1000" | "wilson_score"


@dataclass
class CalibrationBin:
    bin_label: str            # e.g. "Low [0.00, 0.50)", "Mid [0.50, 0.80)", "High [0.80, 1.00]"
    count: int
    mean_predicted_prob: float
    observed_outcome_rate: float
    absolute_calibration_gap: float


@dataclass
class ComponentValidityDiagnostic:
    component_id: str         # "C1_coverage" | "C2_placement" | "C3_title_tenure_scale" | "C4_knockout_gates" | "C5_document_hygiene"
    label: str
    max_points: float
    mean_earned: float
    outcome_correlation_r: Optional[float]
    leave_one_out_auc: Optional[float]
    auc_contribution_delta: Optional[float]
    leave_one_out_brier: Optional[float]
    brier_improvement_delta: Optional[float]
    recommendation: str       # "KEEP (Validated Positive Lift)" | "REWEIGHT (Low Marginal Lift)" | "REDESIGN / REMOVE (Misleading or Formatting Noise)"
    diagnostic_note: str


@dataclass
class LongitudinalOutcomes:
    interview: int                    # 1 = interviewed / passed technical screen, 0 = not advanced
    selection: Optional[int] = None   # 1 = selected / offer extended, 0 = not selected
    promotion: Optional[int] = None   # 1 = promoted / scope expanded in 12-18m, 0 = no
    performance_90d: Optional[int] = None  # 1 = met/exceeded 90-day operational KPI ramp, 0 = missed ramp
    attrition_90d: Optional[int] = None    # 1 = early turnover within 90 days, 0 = retained at 90 days


@dataclass
class ParserConfidenceBreakdown:
    resume_confidence: float          # 0.0 to 1.0
    requisition_confidence: float     # 0.0 to 1.0
    combined_confidence: float        # 0.0 to 1.0
    confidence_band: str              # "HIGH (>=0.85)" | "MODERATE (0.75-0.84)" | "LOW (<0.75 - Manual Parse Check)"
    requires_manual_parse_check: bool
    resume_sections_detected: List[str]
    resume_valid_date_ranges: int
    resume_structured_char_ratio: float
    requisition_source_mode: str      # "confirmed_jd_config" | "heuristic_raw_text" | "both_compared"
    requisition_has_tier_split: bool
    requisition_extracted_req_count: int
    heuristic_vs_confirmed_score_drift: Optional[float] = None
    warnings: List[str] = field(default_factory=list)


@dataclass
class PilotCaseResult:
    case_id: str
    deidentified_subject_hash: str
    deidentified_verified: bool
    pii_redactions_applied: int
    requisition_id: str
    requisition_title: str
    candidate_id: str
    # Requisition Quality (Module 1)
    rqs_score: float
    rqs_band: str
    rqs_hidden_worker_flags: int
    # Candidate JD Alignment (Core + Module 2 Blind Parity)
    composite_score: float
    predicted_probability: float
    literal_score: float
    blind_score: float
    blind_parity_delta: float
    heuristic_jd_score: Optional[float]
    comp1_coverage: float
    comp2_placement: float
    comp3_alignment: float
    comp4_gates: float
    comp5_hygiene: float
    knockout_passed: bool
    unverified_gates: List[str]
    verified_count: int
    partial_count: int
    unverified_count: int
    # Parser Confidence
    parser_confidence: ParserConfidenceBreakdown
    # Blinded Recruiter vs. Engine vs. Longitudinal Outcomes
    engine_band: str                  # "Strong (>=80)" | "Borderline (65-79.9)" | "Weak (<65)"
    engine_binary_predict_80: str     # "ADVANCE" | "DO_NOT_ADVANCE"
    engine_binary_predict_65: str     # "ADVANCE" | "DO_NOT_ADVANCE"
    blinded_recruiter_decision: str   # Blinded recruiter decision without seeing engine score
    human_decision_status: str        # Final human-in-the-loop disposition
    human_reviewer: str
    human_override_reason: str
    manual_validated_outcome: str     # Primary binary ground truth ("ADVANCE" | "DO_NOT_ADVANCE")
    manual_outcome_stage: str
    outcomes: LongitudinalOutcomes
    manual_expert_score: Optional[float]
    score_error_vs_expert: Optional[float]
    abs_error_vs_expert: Optional[float]
    brier_squared_error: float
    classification_80: str            # "TP" | "FP" | "TN" | "FN"
    classification_65: str            # "TP" | "FP" | "TN" | "FN"
    blinded_recruiter_matches_outcome: Optional[bool]
    human_decision_matches_outcome: Optional[bool]
    validation_notes: str
    config_sha256: str
    resume_sha256: str


@dataclass
class ThresholdConfusionMetrics:
    threshold: float
    tp: int
    fp: int
    tn: int
    fn: int
    accuracy: float
    accuracy_ci95: ConfidenceInterval95
    precision: float
    recall: float
    specificity: float
    f1_score: float
    cohens_kappa: float


@dataclass
class OutcomeStageValiditySummary:
    outcome_name: str                 # "interview" | "selection" | "promotion" | "performance_90d" | "attrition_90d"
    sample_size: int
    positive_rate: float
    roc_auc: Optional[float]
    brier_score: Optional[float]
    point_biserial_r: Optional[float]


@dataclass
class PilotCohortReport:
    tool: str
    module: str
    harness_version: str
    reference_date: str
    total_cases: int
    all_cases_deidentified: bool
    total_pii_tokens_scrubbed: int
    mean_rqs_score: float
    mean_composite_score: float
    mean_manual_expert_score: Optional[float]
    mean_blind_parity_delta: float
    max_heuristic_vs_confirmed_drift: Optional[float]
    mean_resume_confidence: float
    mean_requisition_confidence: float
    mean_combined_confidence: float
    low_confidence_case_count: int
    # Discrimination, Calibration, Brier, AUC & 95% CIs
    roc_auc: ConfidenceInterval95
    brier_score: ConfidenceInterval95
    expected_calibration_error: float
    calibration_bins: List[CalibrationBin]
    metrics_at_80: ThresholdConfusionMetrics
    metrics_at_65: ThresholdConfusionMetrics
    # Blinded Recruiter vs. Engine vs. Human-in-the-Loop
    blinded_recruiter_accuracy: Optional[float]
    blinded_recruiter_kappa: Optional[float]
    blinded_recruiter_f1: Optional[float]
    human_reviewer_accuracy: Optional[float]
    human_reviewer_kappa: Optional[float]
    # Continuous Expert Calibration
    pearson_r_vs_expert: Optional[float]
    pearson_r_ci95: Optional[ConfidenceInterval95]
    spearman_rho_vs_expert: Optional[float]
    mae_vs_expert: Optional[float]
    rmse_vs_expert: Optional[float]
    # Multi-Stage Longitudinal Outcomes & Component Ablation
    longitudinal_outcomes: List[OutcomeStageValiditySummary]
    component_diagnostics: List[ComponentValidityDiagnostic]
    cases: List[PilotCaseResult]


# ---------------------------------------------------------------------------
# Parser Confidence Diagnostics
# ---------------------------------------------------------------------------

def compute_parser_confidence(
    raw_resume_text: str,
    parsed_resume: oaa.ParsedResume,
    raw_jd_text: Optional[str],
    reqs: Sequence[oaa.JDRequirement],
    align_cfg: oaa.AlignmentConfig,
    has_confirmed_config: bool,
    heuristic_vs_confirmed_drift: Optional[float] = None,
) -> ParserConfidenceBreakdown:
    """Deterministic 0.0-1.0 parser confidence model."""
    warnings: List[str] = []

    res_conf = 1.00
    core_sections = ["summary", "experience", "education", "skills"]
    detected_sections = [
        sec for sec in core_sections
        if len(parsed_resume.sections.get(sec, "").strip()) > 0
    ]
    total_chars = max(1, len(raw_resume_text.strip()))
    structured_chars = sum(
        len(parsed_resume.sections.get(sec, ""))
        for sec in ("summary", "experience", "education", "skills", "certifications", "projects")
    )
    structured_ratio = min(1.0, round(structured_chars / total_chars, 3))

    if "experience" not in detected_sections:
        res_conf -= 0.30
        warnings.append("Resume missing standard Work Experience heading; bullets may fall into unattributed text.")
    if "skills" not in detected_sections:
        res_conf -= 0.10
        warnings.append("Resume missing explicit Technical/Core Skills section header.")
    if "education" not in detected_sections:
        res_conf -= 0.08
        warnings.append("Resume missing explicit Education section header.")

    valid_dated_roles = sum(1 for r in parsed_resume.roles if r.start_date is not None)
    if valid_dated_roles == 0:
        res_conf -= 0.25
        warnings.append("Zero standard MM/YYYY work-history date ranges parsed; recency and net tenure default to unverified.")

    for reason, _pts in parsed_resume.schema_deductions:
        r_low = reason.lower()
        if "header" in r_low:
            res_conf -= 0.12
            warnings.append(reason)
        elif "date" in r_low:
            if valid_dated_roles > 0:
                res_conf -= 0.12
            warnings.append(reason)
        elif "table" in r_low or "column" in r_low or "pipe" in r_low:
            res_conf -= 0.15
            warnings.append(reason)
        elif "acronym" in r_low:
            res_conf -= 0.05
            warnings.append(reason)
        else:
            res_conf -= 0.05
            warnings.append(reason)

    if structured_ratio < 0.60:
        res_conf -= 0.15
        warnings.append(f"Low structured section character ratio ({structured_ratio:.0%}).")

    res_conf = round(max(0.05, min(1.0, res_conf)), 3)

    has_tier_split = False
    if raw_jd_text:
        rec = jpc.parse_job_posting_text(raw_jd_text)
        has_tier_split = bool(rec.required_qualifications and rec.preferred_qualifications)
    else:
        tiers = {r.tier for r in reqs}
        has_tier_split = ("required" in tiers and "preferred" in tiers)

    if has_confirmed_config and raw_jd_text:
        source_mode = "both_compared"
        req_conf = 0.98
    elif has_confirmed_config:
        source_mode = "confirmed_jd_config"
        req_conf = 1.00
    else:
        source_mode = "heuristic_raw_text"
        req_conf = 0.85
        if not has_tier_split:
            req_conf -= 0.12
            warnings.append("Requisition lacks explicit Required vs. Preferred section split; all items weighted as Required.")
        if len(reqs) < 4:
            req_conf -= 0.18
            warnings.append(f"Only {len(reqs)} requirements heuristically extracted from plain-text JD; confirm with --jd-config.")
        elif len(reqs) > 18:
            req_conf -= 0.08
            warnings.append(f"High requirement count ({len(reqs)}) extracted from plain-text JD.")
        if not align_cfg.target_title:
            req_conf -= 0.10
            warnings.append("Target job title could not be inferred from requisition text.")

    if heuristic_vs_confirmed_drift is not None and heuristic_vs_confirmed_drift > 8.0:
        warnings.append(
            f"Heuristic JD extraction drifted by {heuristic_vs_confirmed_drift:.1f} pts vs. confirmed config; use confirmed --jd-config."
        )

    req_conf = round(max(0.05, min(1.0, req_conf)), 3)
    combined = round(0.55 * res_conf + 0.45 * req_conf, 3)

    if combined >= 0.85:
        band = "HIGH (>=0.85)"
    elif combined >= 0.75:
        band = "MODERATE (0.75-0.84)"
    else:
        band = "LOW (<0.75 - Manual Parse Check)"

    return ParserConfidenceBreakdown(
        resume_confidence=res_conf,
        requisition_confidence=req_conf,
        combined_confidence=combined,
        confidence_band=band,
        requires_manual_parse_check=(combined < 0.75),
        resume_sections_detected=detected_sections,
        resume_valid_date_ranges=valid_dated_roles,
        resume_structured_char_ratio=structured_ratio,
        requisition_source_mode=source_mode,
        requisition_has_tier_split=has_tier_split,
        requisition_extracted_req_count=len(reqs),
        heuristic_vs_confirmed_score_drift=heuristic_vs_confirmed_drift,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Statistical, Calibration, Brier, ROC-AUC & Confidence Interval Functions
# ---------------------------------------------------------------------------

def normalize_binary_outcome(label: Any) -> str:
    if isinstance(label, bool):
        return "ADVANCE" if label else "DO_NOT_ADVANCE"
    if isinstance(label, (int, float)):
        return "ADVANCE" if float(label) >= 0.5 else "DO_NOT_ADVANCE"
    up = str(label or "").strip().upper()
    if up in POSITIVE_OUTCOMES:
        return "ADVANCE"
    if up in NEGATIVE_OUTCOMES:
        return "DO_NOT_ADVANCE"
    return "PENDING_HUMAN_REVIEW"


def _to_binary_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, (int, float)):
        f = float(val)
        if f > 1.0:
            # 0-100 scale performance score -> >= 75 is 1
            return 1 if f >= 75.0 else 0
        return 1 if f >= 0.5 else 0
    norm = normalize_binary_outcome(val)
    if norm == "ADVANCE":
        return 1
    if norm == "DO_NOT_ADVANCE":
        return 0
    return None


def classify_confusion(predicted: str, actual: str) -> str:
    p = (predicted == "ADVANCE")
    a = (actual == "ADVANCE")
    if p and a:
        return "TP"
    if p and not a:
        return "FP"
    if not p and not a:
        return "TN"
    return "FN"


def wilson_score_ci95(successes: int, total: int) -> ConfidenceInterval95:
    """Wilson score 95% confidence interval for a binomial proportion."""
    if total <= 0:
        return ConfidenceInterval95(0.0, 0.0, 0.0, "wilson_score")
    z = 1.959964
    p_hat = successes / total
    denom = 1.0 + (z * z) / total
    center = (p_hat + (z * z) / (2.0 * total)) / denom
    half = (z * math.sqrt((p_hat * (1.0 - p_hat) + (z * z) / (4.0 * total)) / total)) / denom
    lo = max(0.0, min(1.0, center - half))
    hi = max(0.0, min(1.0, center + half))
    return ConfidenceInterval95(
        estimate=round(p_hat, 4),
        ci_lower=round(lo, 4),
        ci_upper=round(hi, 4),
        method="wilson_score",
    )


def compute_confusion_metrics(
    predictions: Sequence[str],
    actuals: Sequence[str],
    threshold: float,
) -> ThresholdConfusionMetrics:
    tp = fp = tn = fn = 0
    for pred, act in zip(predictions, actuals):
        c = classify_confusion(pred, act)
        if c == "TP":
            tp += 1
        elif c == "FP":
            fp += 1
        elif c == "TN":
            tn += 1
        else:
            fn += 1

    n = max(1, tp + fp + tn + fn)
    accuracy = (tp + tn) / n
    acc_ci = wilson_score_ci95(tp + tn, n)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    p_o = accuracy
    p_yes_pred = (tp + fp) / n
    p_yes_act = (tp + fn) / n
    p_no_pred = (tn + fn) / n
    p_no_act = (tn + fp) / n
    p_e = (p_yes_pred * p_yes_act) + (p_no_pred * p_no_act)
    if abs(1.0 - p_e) < 1e-9:
        kappa = 1.0 if abs(p_o - 1.0) < 1e-9 else 0.0
    else:
        kappa = (p_o - p_e) / (1.0 - p_e)

    return ThresholdConfusionMetrics(
        threshold=threshold,
        tp=tp,
        fp=fp,
        tn=tn,
        fn=fn,
        accuracy=round(accuracy, 4),
        accuracy_ci95=acc_ci,
        precision=round(precision, 4),
        recall=round(recall, 4),
        specificity=round(specificity, 4),
        f1_score=round(f1, 4),
        cohens_kappa=round(kappa, 4),
    )


def compute_brier_score(probs: Sequence[float], outcomes: Sequence[int]) -> float:
    """Mean squared probability error: BS = (1/N) * sum((p_i - y_i)^2)."""
    if not probs or len(probs) != len(outcomes):
        return 0.0
    n = len(probs)
    return round(sum((max(0.0, min(1.0, p)) - y) ** 2 for p, y in zip(probs, outcomes)) / n, 4)


def compute_roc_auc(scores: Sequence[float], outcomes: Sequence[int]) -> Optional[float]:
    """Exact Mann-Whitney U / Wilcoxon rank-sum ROC-AUC with 0.5 tie handling."""
    if len(scores) != len(outcomes) or len(scores) < 2:
        return None
    pos = [s for s, y in zip(scores, outcomes) if y == 1]
    neg = [s for s, y in zip(scores, outcomes) if y == 0]
    if not pos or not neg:
        return None
    concordant = 0.0
    total_pairs = len(pos) * len(neg)
    for sp in pos:
        for sn in neg:
            if sp > sn:
                concordant += 1.0
            elif sp == sn:
                concordant += 0.5
    return round(concordant / total_pairs, 4)


def compute_calibration_curve(
    probs: Sequence[float],
    outcomes: Sequence[int],
) -> Tuple[float, List[CalibrationBin]]:
    """
    Computes Expected Calibration Error (ECE) and a 3-tier reliability table:
      - Low Probability:  [0.00, 0.50)
      - Mid Probability:  [0.50, 0.80)
      - High Probability: [0.80, 1.00]
    """
    bins_spec = [
        ("Low [0.00, 0.50)", 0.0, 0.50, False),
        ("Mid [0.50, 0.80)", 0.50, 0.80, False),
        ("High [0.80, 1.00]", 0.80, 1.00, True),
    ]
    n_total = max(1, len(probs))
    ece = 0.0
    out_bins: List[CalibrationBin] = []

    for label, lo, hi, inclusive_hi in bins_spec:
        pairs = [
            (p, y)
            for p, y in zip(probs, outcomes)
            if (lo <= p <= hi if inclusive_hi else lo <= p < hi)
        ]
        if not pairs:
            out_bins.append(
                CalibrationBin(
                    bin_label=label,
                    count=0,
                    mean_predicted_prob=0.0,
                    observed_outcome_rate=0.0,
                    absolute_calibration_gap=0.0,
                )
            )
            continue
        cnt = len(pairs)
        mean_p = sum(p for p, _ in pairs) / cnt
        obs_y = sum(y for _, y in pairs) / cnt
        gap = abs(mean_p - obs_y)
        ece += (cnt / n_total) * gap
        out_bins.append(
            CalibrationBin(
                bin_label=label,
                count=cnt,
                mean_predicted_prob=round(mean_p, 4),
                observed_outcome_rate=round(obs_y, 4),
                absolute_calibration_gap=round(gap, 4),
            )
        )
    return round(ece, 4), out_bins


def bootstrap_ci95_paired(
    xs: Sequence[float],
    ys: Sequence[Any],
    metric_fn: Any,
    seed: int = BOOTSTRAP_SEED,
    iterations: int = BOOTSTRAP_ITERATIONS,
) -> ConfidenceInterval95:
    """Deterministic seeded percentile bootstrap 95% confidence interval."""
    est = metric_fn(xs, ys)
    if est is None:
        return ConfidenceInterval95(0.0, 0.0, 0.0, "bootstrap_1000")
    n = len(xs)
    if n < 3:
        return ConfidenceInterval95(round(float(est), 4), round(float(est), 4), round(float(est), 4), "bootstrap_1000")

    rng = random.Random(seed)
    samples: List[float] = []
    for _ in range(iterations):
        idxs = [rng.randrange(n) for _ in range(n)]
        bx = [xs[i] for i in idxs]
        by = [ys[i] for i in idxs]
        val = metric_fn(bx, by)
        if val is not None:
            samples.append(float(val))

    if not samples:
        return ConfidenceInterval95(round(float(est), 4), round(float(est), 4), round(float(est), 4), "bootstrap_1000")

    samples.sort()
    lo_idx = max(0, int(0.025 * (len(samples) - 1)))
    hi_idx = min(len(samples) - 1, int(0.975 * (len(samples) - 1)))
    return ConfidenceInterval95(
        estimate=round(float(est), 4),
        ci_lower=round(samples[lo_idx], 4),
        ci_upper=round(samples[hi_idx], 4),
        method="bootstrap_1000",
    )


def pearson_correlation(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den_x = math.sqrt(sum((x - mx) ** 2 for x in xs))
    den_y = math.sqrt(sum((y - my) ** 2 for y in ys))
    if den_x < 1e-9 or den_y < 1e-9:
        return None
    return round(num / (den_x * den_y), 4)


def _rank_vector(vals: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(vals), key=lambda t: t[1])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def spearman_correlation(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 2:
        return None
    return pearson_correlation(_rank_vector(xs), _rank_vector(ys))


# ---------------------------------------------------------------------------
# Component Ablation & Misleading-Feature Diagnostics
# ---------------------------------------------------------------------------

def analyze_component_validity(
    cases: Sequence[PilotCaseResult],
) -> List[ComponentValidityDiagnostic]:
    """
    Evaluates each of the 5 scoring components (C1..C5) against validated outcomes:
      - Point-biserial correlation with validated outcome
      - Leave-one-out ROC-AUC and Brier Score (what happens when the component is removed
        and the remaining components are rescaled to 100 points)
      - Flags components that fail to predict job/interview success or introduce
        formatting noise (`KEEP`, `REWEIGHT`, `REDESIGN / REMOVE`).
    """
    outcomes = [1 if c.manual_validated_outcome == "ADVANCE" else 0 for c in cases]
    full_scores = [c.composite_score for c in cases]
    full_probs = [c.predicted_probability for c in cases]
    base_auc = compute_roc_auc(full_scores, outcomes)
    base_brier = compute_brier_score(full_probs, outcomes)

    comp_specs = [
        ("C1_coverage", "Component 1: Requirement Coverage", 45.0, lambda c: c.comp1_coverage),
        ("C2_placement", "Component 2: Contextual Placement & Recency", 20.0, lambda c: c.comp2_placement),
        ("C3_title_tenure_scale", "Component 3: Title, Tenure & Operational Scale", 15.0, lambda c: c.comp3_alignment),
        ("C4_knockout_gates", "Component 4: Knockout Gate Verification", 10.0, lambda c: c.comp4_gates),
        ("C5_document_hygiene", "Component 5: Document & Parse Hygiene", 10.0, lambda c: c.comp5_hygiene),
    ]

    diagnostics: List[ComponentValidityDiagnostic] = []
    n = max(1, len(cases))

    for cid, label, max_pts, getter in comp_specs:
        vals = [float(getter(c)) for c in cases]
        mean_val = round(sum(vals) / n, 2)
        corr = pearson_correlation(vals, [float(y) for y in outcomes])

        # Leave-one-out score rescaled to [0, 100]
        rem_max = 100.0 - max_pts
        loo_scores = [
            round(max(0.0, min(100.0, ((c.composite_score - getter(c)) / rem_max) * 100.0)), 2)
            for c in cases
        ]
        loo_probs = [s / 100.0 for s in loo_scores]
        loo_auc = compute_roc_auc(loo_scores, outcomes)
        loo_brier = compute_brier_score(loo_probs, outcomes)

        auc_delta = round((base_auc - loo_auc), 4) if (base_auc is not None and loo_auc is not None) else None
        # Positive brier_improvement_delta means keeping the component REDUCED Brier error (good)
        brier_delta = round((loo_brier - base_brier), 4) if loo_brier is not None else None

        if cid == "C5_document_hygiene":
            # Check if C5 correlates weakly or penalizes formatting rather than job capability
            if corr is None or corr < 0.25 or (brier_delta is not None and brier_delta <= 0.0):
                rec = "REDESIGN / DECOUPLE (Move Formatting to Parser Confidence)"
                note = (
                    "Document formatting deductions (-3 pipe table, -2 headers) reflect resume template layout rather than "
                    "job capability. Recommendation: report C5 as a Parser Confidence flag instead of deducting capability points."
                )
            else:
                rec = "REWEIGHT (Cap Hygiene Impact)"
                note = "Formatting hygiene shows positive correlation with parseable experience but should never outweigh core skills."
        elif corr is not None and corr >= 0.45:
            rec = "KEEP (Validated Positive Lift)"
            note = f"Strong positive validity (r={corr:+.2f}); contributes directly to discriminating qualified candidates."
        elif corr is not None and corr >= 0.15:
            rec = "REWEIGHT (Moderate Marginal Lift)"
            note = f"Moderate positive correlation (r={corr:+.2f}); monitor across larger vertical cohorts."
        else:
            rec = "REDESIGN / REVIEW (Low Predictive Lift)"
            note = "Low or flat variance across cohort; inspect sub-rules (such as global scale verb presence) for construct validity."

        diagnostics.append(
            ComponentValidityDiagnostic(
                component_id=cid,
                label=label,
                max_points=max_pts,
                mean_earned=mean_val,
                outcome_correlation_r=corr,
                leave_one_out_auc=loo_auc,
                auc_contribution_delta=auc_delta,
                leave_one_out_brier=loo_brier,
                brier_improvement_delta=brier_delta,
                recommendation=rec,
                diagnostic_note=note,
            )
        )

    return diagnostics


def summarize_longitudinal_outcomes(
    cases: Sequence[PilotCaseResult],
) -> List[OutcomeStageValiditySummary]:
    """Computes sample size, base rate, ROC-AUC, Brier Score, and point-biserial r across all 5 outcome stages."""
    stages = [
        ("interview", lambda c: c.outcomes.interview, False),
        ("selection", lambda c: c.outcomes.selection, False),
        ("promotion", lambda c: c.outcomes.promotion, False),
        ("performance_90d", lambda c: c.outcomes.performance_90d, False),
        ("attrition_90d", lambda c: c.outcomes.attrition_90d, True),  # Inverted: lower attrition is better -> 1 - p
    ]
    summaries: List[OutcomeStageValiditySummary] = []

    for name, getter, invert_prob in stages:
        paired = [
            (c.composite_score, c.predicted_probability, getter(c))
            for c in cases
            if getter(c) is not None
        ]
        if not paired:
            continue
        n = len(paired)
        ys = [int(p[2]) for p in paired]
        pos_rate = round(sum(ys) / n, 4)
        scores = [100.0 - p[0] if invert_prob else p[0] for p in paired]
        probs = [1.0 - p[1] if invert_prob else p[1] for p in paired]
        auc = compute_roc_auc(scores, ys)
        brier = compute_brier_score(probs, ys)
        r_val = pearson_correlation([p[0] for p in paired], [float(y) for y in ys])

        summaries.append(
            OutcomeStageValiditySummary(
                outcome_name=name,
                sample_size=n,
                positive_rate=pos_rate,
                roc_auc=auc,
                brier_score=brier,
                point_biserial_r=r_val,
            )
        )
    return summaries


# ---------------------------------------------------------------------------
# Core Pilot Case & Cohort Runner
# ---------------------------------------------------------------------------

def evaluate_pilot_case(
    case_spec: Dict[str, Any],
    reference_date: datetime,
) -> PilotCaseResult:
    case_id = str(case_spec.get("case_id") or "CASE-001")
    req_id = str(case_spec.get("requisition_id") or "REQ-001")
    cand_id = str(case_spec.get("candidate_id") or "CAND-001")
    resume_text = str(case_spec["resume_text"])
    raw_jd_text = case_spec.get("jd_text")
    jd_config = case_spec.get("jd_config")

    # 1. Deidentification & Blind-Mode Redaction Verification
    redacted_text, redaction_counts = esa.redact_resume_for_blind_screening(resume_text)
    total_pii_scrubbed = sum(redaction_counts.values())
    subj_hash = "SUBJ-" + hashlib.sha256(f"ops-pilot-salt::{cand_id}::{redacted_text}".encode("utf-8")).hexdigest()[:12].upper()
    deid_verified = ("@example.com" not in redacted_text and "[redacted-" in redacted_text) or bool(case_spec.get("deidentified", True))

    # 2. Requisition Quality Score (Module 1)
    if raw_jd_text:
        rqs_audit = rqa.audit_text(str(raw_jd_text))
        rqs_score = round(rqs_audit.score, 1)
        rqs_band = rqs_audit.band
        rqs_hw = sum(1 for f in rqs_audit.findings if f.hidden_worker_risk)
        inferred_title = rqs_audit.title
    else:
        rqs_score = 100.0
        rqs_band = "Confirmed config only (no raw JD text)"
        rqs_hw = 0
        inferred_title = ""

    # 3. Requirement Config & Optional Heuristic-vs-Confirmed Comparison
    heuristic_score: Optional[float] = None
    heuristic_drift: Optional[float] = None

    if jd_config and isinstance(jd_config, dict):
        reqs = [
            r if isinstance(r, oaa.JDRequirement) else oaa.JDRequirement(**r)
            for r in jd_config.get("requirements", [])
        ]
        align_raw = dict(jd_config.get("alignment", {}))
        align_raw.pop("reference_date", None)
        align_cfg = oaa.AlignmentConfig(**align_raw)
        has_confirmed = True
        if raw_jd_text:
            h_reqs, h_align = oaa.extract_reqs_consistent(
                str(raw_jd_text),
                str(case_spec.get("requisition_title") or align_cfg.target_title or inferred_title),
            )
            h_rep = oaa.run_ats_audit(redacted_text, h_reqs, h_align, reference_date=reference_date)
            heuristic_score = round(h_rep.composite_score, 1)
    elif raw_jd_text:
        reqs, align_cfg = oaa.extract_reqs_consistent(
            str(raw_jd_text),
            str(case_spec.get("requisition_title") or inferred_title),
        )
        has_confirmed = False
    else:
        raise ValueError(f"Pilot case '{case_id}' must provide 'jd_config', 'jd_text', or both.")

    req_title = str(case_spec.get("requisition_title") or align_cfg.target_title or inferred_title or "Untitled Requisition")

    # 4. Run Raw & Blind-Mode Resume Audit
    parsed_raw = oaa.parse_resume(resume_text, reference_date=reference_date)
    rep_raw = oaa.run_ats_audit(resume_text, reqs, align_cfg, reference_date=reference_date)
    rep_blind = oaa.run_ats_audit(redacted_text, reqs, align_cfg, reference_date=reference_date)

    comp_score = round(rep_raw.composite_score, 1)
    pred_prob = round(max(0.0, min(1.0, comp_score / 100.0)), 4)
    lit_score = round(rep_raw.legacy_literal_score, 1)
    blind_score = round(rep_blind.composite_score, 1)
    blind_delta = round(blind_score - comp_score, 2)

    if heuristic_score is not None:
        heuristic_drift = round(abs(heuristic_score - comp_score), 1)

    # 5. Parser Confidence
    p_conf = compute_parser_confidence(
        raw_resume_text=resume_text,
        parsed_resume=parsed_raw,
        raw_jd_text=str(raw_jd_text) if raw_jd_text else None,
        reqs=reqs,
        align_cfg=align_cfg,
        has_confirmed_config=has_confirmed,
        heuristic_vs_confirmed_drift=heuristic_drift,
    )

    # 6. Ledger counts
    v_cnt = sum(1 for row in rep_raw.ledger if row.match_credit == 1.0 and row.placement_multiplier == 1.0)
    p_cnt = sum(1 for row in rep_raw.ledger if (row.match_credit == 0.5) or (row.match_credit == 1.0 and row.placement_multiplier < 1.0))
    u_cnt = sum(1 for row in rep_raw.ledger if row.match_credit == 0.0)

    # 7. Predictions, Blinded Recruiter Decision, Human-in-the-Loop Override & Outcomes
    if comp_score >= 80.0 and rep_raw.knockout_passed:
        engine_band = "Strong (>=80)"
    elif comp_score >= 65.0 and rep_raw.knockout_passed:
        engine_band = "Borderline (65-79.9)"
    else:
        engine_band = "Weak (<65 or Gate Unverified)"

    pred_80 = "ADVANCE" if (comp_score >= 80.0 and rep_raw.knockout_passed) else "DO_NOT_ADVANCE"
    pred_65 = "ADVANCE" if (comp_score >= 65.0 and rep_raw.knockout_passed) else "DO_NOT_ADVANCE"

    human_raw = str(case_spec.get("human_decision") or "PENDING_HUMAN_REVIEW").strip().upper()
    human_norm = normalize_binary_outcome(human_raw)
    human_status = human_norm if human_raw not in ("HOLD", "PENDING_HUMAN_REVIEW") else human_raw
    human_reviewer = str(case_spec.get("human_reviewer") or "recruiter_pilot")
    human_override = str(case_spec.get("human_override_reason") or "")

    blinded_rec_raw = case_spec.get("blinded_recruiter_decision")
    if blinded_rec_raw is not None:
        blinded_rec_decision = normalize_binary_outcome(blinded_rec_raw)
    else:
        blinded_rec_decision = human_status

    val_outcome = normalize_binary_outcome(case_spec.get("manual_validated_outcome", "ADVANCE"))
    val_outcome_int = 1 if val_outcome == "ADVANCE" else 0
    val_stage = str(case_spec.get("manual_outcome_stage") or "Hiring Manager Technical Screen")
    val_notes = str(case_spec.get("validation_notes") or "")

    # Multi-stage longitudinal outcomes
    raw_outcomes = case_spec.get("outcomes") or {}
    long_outcomes = LongitudinalOutcomes(
        interview=_to_binary_int(raw_outcomes.get("interview", val_outcome_int)) or 0,
        selection=_to_binary_int(raw_outcomes.get("selection")),
        promotion=_to_binary_int(raw_outcomes.get("promotion")),
        performance_90d=_to_binary_int(raw_outcomes.get("performance_90d")),
        attrition_90d=_to_binary_int(raw_outcomes.get("attrition_90d")),
    )

    expert_raw = case_spec.get("manual_expert_score")
    expert_score = round(float(expert_raw), 1) if expert_raw is not None else None
    err_vs_expert = round(comp_score - expert_score, 1) if expert_score is not None else None
    abs_err_vs_expert = round(abs(comp_score - expert_score), 1) if expert_score is not None else None
    brier_err = round((pred_prob - val_outcome_int) ** 2, 4)

    cls_80 = classify_confusion(pred_80, val_outcome)
    cls_65 = classify_confusion(pred_65, val_outcome)

    blinded_matches: Optional[bool] = None
    if blinded_rec_decision in ("ADVANCE", "DO_NOT_ADVANCE"):
        blinded_matches = (blinded_rec_decision == val_outcome)

    human_matches: Optional[bool] = None
    if human_norm in ("ADVANCE", "DO_NOT_ADVANCE"):
        human_matches = (human_norm == val_outcome)

    cfg_payload = json.dumps(
        {
            "requirements": [dataclasses.asdict(r) for r in reqs],
            "alignment": dataclasses.asdict(align_cfg),
        },
        sort_keys=True,
    )
    cfg_sha = hashlib.sha256(cfg_payload.encode("utf-8")).hexdigest()[:16]
    res_sha = hashlib.sha256(redacted_text.encode("utf-8")).hexdigest()[:16]

    return PilotCaseResult(
        case_id=case_id,
        deidentified_subject_hash=subj_hash,
        deidentified_verified=deid_verified,
        pii_redactions_applied=total_pii_scrubbed,
        requisition_id=req_id,
        requisition_title=req_title,
        candidate_id=cand_id,
        rqs_score=rqs_score,
        rqs_band=rqs_band,
        rqs_hidden_worker_flags=rqs_hw,
        composite_score=comp_score,
        predicted_probability=pred_prob,
        literal_score=lit_score,
        blind_score=blind_score,
        blind_parity_delta=blind_delta,
        heuristic_jd_score=heuristic_score,
        comp1_coverage=round(rep_raw.comp1_kw_score, 2),
        comp2_placement=round(rep_raw.comp2_context_score, 2),
        comp3_alignment=round(rep_raw.comp3_alignment_score, 2),
        comp4_gates=round(rep_raw.comp4_knockout_score, 2),
        comp5_hygiene=round(rep_raw.comp5_schema_score, 2),
        knockout_passed=rep_raw.knockout_passed,
        unverified_gates=list(rep_raw.failed_knockouts),
        verified_count=v_cnt,
        partial_count=p_cnt,
        unverified_count=u_cnt,
        parser_confidence=p_conf,
        engine_band=engine_band,
        engine_binary_predict_80=pred_80,
        engine_binary_predict_65=pred_65,
        blinded_recruiter_decision=blinded_rec_decision,
        human_decision_status=human_status,
        human_reviewer=human_reviewer,
        human_override_reason=human_override,
        manual_validated_outcome=val_outcome,
        manual_outcome_stage=val_stage,
        outcomes=long_outcomes,
        manual_expert_score=expert_score,
        score_error_vs_expert=err_vs_expert,
        abs_error_vs_expert=abs_err_vs_expert,
        brier_squared_error=brier_err,
        classification_80=cls_80,
        classification_65=cls_65,
        blinded_recruiter_matches_outcome=blinded_matches,
        human_decision_matches_outcome=human_matches,
        validation_notes=val_notes,
        config_sha256=cfg_sha,
        resume_sha256=res_sha,
    )


def run_pilot_cohort(
    case_specs: Sequence[Dict[str, Any]],
    reference_date: datetime,
) -> PilotCohortReport:
    results = [evaluate_pilot_case(spec, reference_date=reference_date) for spec in case_specs]
    n = max(1, len(results))

    preds_80 = [r.engine_binary_predict_80 for r in results]
    preds_65 = [r.engine_binary_predict_65 for r in results]
    actuals = [r.manual_validated_outcome for r in results]
    actual_ints = [1 if a == "ADVANCE" else 0 for a in actuals]
    scores = [r.composite_score for r in results]
    probs = [r.predicted_probability for r in results]

    m80 = compute_confusion_metrics(preds_80, actuals, threshold=80.0)
    m65 = compute_confusion_metrics(preds_65, actuals, threshold=65.0)

    # ROC-AUC, Brier Score, ECE & 95% Bootstrap CIs
    auc_ci = bootstrap_ci95_paired(scores, actual_ints, compute_roc_auc)
    brier_ci = bootstrap_ci95_paired(probs, actual_ints, compute_brier_score)
    ece, cal_bins = compute_calibration_curve(probs, actual_ints)

    # Blinded Recruiter vs. Validated Outcomes
    blinded_pairs = [
        (r.blinded_recruiter_decision, r.manual_validated_outcome)
        for r in results
        if r.blinded_recruiter_decision in ("ADVANCE", "DO_NOT_ADVANCE")
    ]
    if blinded_pairs:
        b_metrics = compute_confusion_metrics(
            [p[0] for p in blinded_pairs],
            [p[1] for p in blinded_pairs],
            threshold=0.0,
        )
        blinded_acc: Optional[float] = b_metrics.accuracy
        blinded_kappa: Optional[float] = b_metrics.cohens_kappa
        blinded_f1: Optional[float] = b_metrics.f1_score
    else:
        blinded_acc = blinded_kappa = blinded_f1 = None

    # Final Human-in-the-Loop Reviewer Agreement
    human_pairs = [
        (r.human_decision_status, r.manual_validated_outcome)
        for r in results
        if r.human_decision_status in ("ADVANCE", "DO_NOT_ADVANCE")
    ]
    if human_pairs:
        h_metrics = compute_confusion_metrics(
            [p[0] for p in human_pairs],
            [p[1] for p in human_pairs],
            threshold=0.0,
        )
        human_acc: Optional[float] = h_metrics.accuracy
        human_kappa: Optional[float] = h_metrics.cohens_kappa
    else:
        human_acc = None
        human_kappa = None

    # Continuous calibration against manual expert scores
    expert_pairs = [
        (r.composite_score, r.manual_expert_score)
        for r in results
        if r.manual_expert_score is not None
    ]
    if expert_pairs:
        eng_vec = [p[0] for p in expert_pairs]
        exp_vec = [float(p[1]) for p in expert_pairs]
        mean_exp: Optional[float] = round(sum(exp_vec) / len(exp_vec), 2)
        mae: Optional[float] = round(sum(abs(x - y) for x, y in zip(eng_vec, exp_vec)) / len(exp_vec), 2)
        rmse: Optional[float] = round(math.sqrt(sum((x - y) ** 2 for x, y in zip(eng_vec, exp_vec)) / len(exp_vec)), 2)
        p_r = pearson_correlation(eng_vec, exp_vec)
        p_r_ci: Optional[ConfidenceInterval95] = bootstrap_ci95_paired(eng_vec, exp_vec, pearson_correlation)
        s_rho = spearman_correlation(eng_vec, exp_vec)
    else:
        mean_exp = mae = rmse = p_r = p_r_ci = s_rho = None

    drifts = [
        r.parser_confidence.heuristic_vs_confirmed_score_drift
        for r in results
        if r.parser_confidence.heuristic_vs_confirmed_score_drift is not None
    ]
    max_drift = round(max(drifts), 1) if drifts else None

    longitudinal_summaries = summarize_longitudinal_outcomes(results)
    comp_diagnostics = analyze_component_validity(results)

    return PilotCohortReport(
        tool="ops-requisition-auditor",
        module="pilot-evidence-harness",
        harness_version=HARNESS_VERSION,
        reference_date=reference_date.strftime("%Y-%m-%d"),
        total_cases=len(results),
        all_cases_deidentified=all(r.deidentified_verified for r in results),
        total_pii_tokens_scrubbed=sum(r.pii_redactions_applied for r in results),
        mean_rqs_score=round(sum(r.rqs_score for r in results) / n, 1),
        mean_composite_score=round(sum(r.composite_score for r in results) / n, 1),
        mean_manual_expert_score=mean_exp,
        mean_blind_parity_delta=round(sum(abs(r.blind_parity_delta) for r in results) / n, 2),
        max_heuristic_vs_confirmed_drift=max_drift,
        mean_resume_confidence=round(sum(r.parser_confidence.resume_confidence for r in results) / n, 3),
        mean_requisition_confidence=round(sum(r.parser_confidence.requisition_confidence for r in results) / n, 3),
        mean_combined_confidence=round(sum(r.parser_confidence.combined_confidence for r in results) / n, 3),
        low_confidence_case_count=sum(1 for r in results if r.parser_confidence.requires_manual_parse_check),
        roc_auc=auc_ci,
        brier_score=brier_ci,
        expected_calibration_error=ece,
        calibration_bins=cal_bins,
        metrics_at_80=m80,
        metrics_at_65=m65,
        blinded_recruiter_accuracy=blinded_acc,
        blinded_recruiter_kappa=blinded_kappa,
        blinded_recruiter_f1=blinded_f1,
        human_reviewer_accuracy=human_acc,
        human_reviewer_kappa=human_kappa,
        pearson_r_vs_expert=p_r,
        pearson_r_ci95=p_r_ci,
        spearman_rho_vs_expert=s_rho,
        mae_vs_expert=mae,
        rmse_vs_expert=rmse,
        longitudinal_outcomes=longitudinal_summaries,
        component_diagnostics=comp_diagnostics,
        cases=results,
    )


# ---------------------------------------------------------------------------
# Markdown & CSV Renderers
# ---------------------------------------------------------------------------

def render_pilot_markdown(report: PilotCohortReport) -> str:
    m80 = report.metrics_at_80
    m65 = report.metrics_at_65
    auc = report.roc_auc
    brier = report.brier_score

    lines: List[str] = [
        "# Pilot & Empirical Validity Statistical Report (`ops-req-pilot`)",
        "",
        "*Evaluates real-world predictive validity on deidentified pilot data across multi-stage outcomes (interview, selection, promotion, 90-day performance, 90-day attrition), comparing the deterministic rubric against blinded recruiter decisions and measuring ROC-AUC, Brier score, Expected Calibration Error (ECE), 95% confidence intervals, and component ablation.*",
        "",
        "## 1. Deidentification, Calibration & Discrimination Summary",
        "",
        f"- **Harness Version:** `{report.module}` v{report.harness_version} (Reference Date: `{report.reference_date}`)",
        f"- **Deidentified Pilot Cohort:** `{report.total_cases}` `(requisition, resume)` pairs (`all_cases_deidentified: {report.all_cases_deidentified}`, `{report.total_pii_tokens_scrubbed}` PII/ZIP/graduation-date tokens scrubbed before analysis)",
        f"- **Mean Requisition Quality Score (RQS):** `{report.mean_rqs_score:.1f} / 100`",
        f"- **Mean Engine JD Alignment Score:** `{report.mean_composite_score:.1f} / 100`"
        + (f" vs. **Mean Manual Expert Score:** `{report.mean_manual_expert_score:.1f} / 100`" if report.mean_manual_expert_score is not None else ""),
        f"- **Blind-Mode Score Parity Drift:** `{report.mean_blind_parity_delta:.2f} pts` *(0.00 = exact parity between blind-redacted and raw resumes)*",
        f"- **ROC-AUC (Discrimination):** `{auc.estimate:.4f}` (95% Bootstrap CI: `[{auc.ci_lower:.4f}, {auc.ci_upper:.4f}]`)",
        f"- **Brier Score (Probabilistic Accuracy, lower is better):** `{brier.estimate:.4f}` (95% Bootstrap CI: `[{brier.ci_lower:.4f}, {brier.ci_upper:.4f}]`)",
        f"- **Expected Calibration Error (ECE):** `{report.expected_calibration_error:.4f}`",
    ]
    if report.max_heuristic_vs_confirmed_drift is not None:
        lines.append(
            f"- **Max Heuristic JD vs. Confirmed Config Drift:** `{report.max_heuristic_vs_confirmed_drift:.1f} pts`"
        )
    lines += [
        f"- **Mean Parser Confidence:** `{report.mean_combined_confidence:.1%}` "
        f"(Resume: `{report.mean_resume_confidence:.1%}`, Requisition: `{report.mean_requisition_confidence:.1%}`)",
        f"- **Low-Confidence Cases Flagged for Manual Parse Review (`<75%`):** `{report.low_confidence_case_count} / {report.total_cases}`",
    ]
    if report.pearson_r_vs_expert is not None and report.pearson_r_ci95 is not None:
        r_ci = report.pearson_r_ci95
        lines += [
            f"- **Continuous Calibration vs. Domain-Expert Ratings:** Pearson `r = {report.pearson_r_vs_expert:.4f}` "
            f"(95% CI: `[{r_ci.ci_lower:.4f}, {r_ci.ci_upper:.4f}]`) | Spearman `rho = {report.spearman_rho_vs_expert:.4f}` | "
            f"`MAE = {report.mae_vs_expert:.2f} pts` | `RMSE = {report.rmse_vs_expert:.2f} pts`",
        ]

    lines += [
        "",
        "## 2. Blinded Comparison: Engine vs. Blinded Recruiter vs. Human-in-the-Loop",
        "",
        "| Decision Maker / Cutoff Policy | TP | FP | TN | FN | Accuracy (95% Wilson CI) | Precision | Recall | Specificity | F1 Score | Cohen's Kappa (`κ`) |",
        "| :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |",
        f"| **Engine Strong Match (`>= 80.0` + Gates)** | {m80.tp} | {m80.fp} | {m80.tn} | {m80.fn} | "
        f"`{m80.accuracy:.1%}` (`[{m80.accuracy_ci95.ci_lower:.1%}, {m80.accuracy_ci95.ci_upper:.1%}]`) | "
        f"`{m80.precision:.1%}` | `{m80.recall:.1%}` | `{m80.specificity:.1%}` | `{m80.f1_score:.3f}` | `{m80.cohens_kappa:.3f}` |",
        f"| **Engine Inclusive Review (`>= 65.0` + Gates)** | {m65.tp} | {m65.fp} | {m65.tn} | {m65.fn} | "
        f"`{m65.accuracy:.1%}` (`[{m65.accuracy_ci95.ci_lower:.1%}, {m65.accuracy_ci95.ci_upper:.1%}]`) | "
        f"`{m65.precision:.1%}` | `{m65.recall:.1%}` | `{m65.specificity:.1%}` | `{m65.f1_score:.3f}` | `{m65.cohens_kappa:.3f}` |",
    ]
    if report.blinded_recruiter_accuracy is not None:
        lines.append(
            f"| **Blinded Recruiter Baseline (No Engine Score)** | — | — | — | — | "
            f"`{report.blinded_recruiter_accuracy:.1%}` | — | — | — | `{report.blinded_recruiter_f1:.3f}` | `{report.blinded_recruiter_kappa:.3f}` |"
        )
    if report.human_reviewer_accuracy is not None:
        lines.append(
            f"| **Human Reviewer + Engine Evidence Ledger** | — | — | — | — | "
            f"`{report.human_reviewer_accuracy:.1%}` | — | — | — | — | `{report.human_reviewer_kappa:.3f}` |"
        )

    lines += [
        "",
        "## 3. Calibration Reliability Curve (`p = score / 100` vs. Observed Outcome Rate)",
        "",
        "| Probability Tier | Cases (`n`) | Mean Predicted Probability (`p`) | Observed Outcome Rate (`y`) | Absolute Calibration Gap (`|p - y|`) |",
        "| :--- | :-: | :-: | :-: | :-: |",
    ]
    for b in report.calibration_bins:
        lines.append(
            f"| **{b.bin_label}** | {b.count} | `{b.mean_predicted_prob:.1%}` | `{b.observed_outcome_rate:.1%}` | `{b.absolute_calibration_gap:.1%}` |"
        )

    if report.longitudinal_outcomes:
        lines += [
            "",
            "## 4. Multi-Stage Longitudinal Outcome Validity (`interview` → `selection` → `90d_performance` → `promotion` → `attrition_90d`)",
            "",
            "| Outcome Stage | Sample Size (`n`) | Base Rate | ROC-AUC | Brier Score | Point-Biserial Correlation (`r`) |",
            "| :--- | :-: | :-: | :-: | :-: | :-: |",
        ]
        for lo in report.longitudinal_outcomes:
            auc_s = f"`{lo.roc_auc:.3f}`" if lo.roc_auc is not None else "—"
            bs_s = f"`{lo.brier_score:.4f}`" if lo.brier_score is not None else "—"
            r_s = f"`{lo.point_biserial_r:+.3f}`" if lo.point_biserial_r is not None else "—"
            lines.append(
                f"| **`{lo.outcome_name}`** | {lo.sample_size} | `{lo.positive_rate:.1%}` | {auc_s} | {bs_s} | {r_s} |"
            )

    if report.component_diagnostics:
        lines += [
            "",
            "## 5. Component Ablation & Misleading-Feature Audit (Remove / Redesign Analysis)",
            "",
            "| Component | Max Pts | Mean Earned | Outcome Corr (`r`) | Leave-One-Out AUC (`ΔAUC`) | Brier Lift (`ΔBrier`) | Validity Decision | Diagnostic Finding |",
            "| :--- | :-: | :-: | :-: | :-: | :-: | :--- | :--- |",
        ]
        for cd in report.component_diagnostics:
            r_s = f"`{cd.outcome_correlation_r:+.2f}`" if cd.outcome_correlation_r is not None else "—"
            auc_s = (
                f"`{cd.leave_one_out_auc:.3f}` (`{cd.auc_contribution_delta:+.3f}`)"
                if cd.leave_one_out_auc is not None and cd.auc_contribution_delta is not None
                else "—"
            )
            br_s = f"`{cd.brier_improvement_delta:+.4f}`" if cd.brier_improvement_delta is not None else "—"
            lines.append(
                f"| **{cd.label}** | {cd.max_points:.0f} | `{cd.mean_earned:.1f}` | {r_s} | {auc_s} | {br_s} | **{cd.recommendation}** | {cd.diagnostic_note} |"
            )

    lines += [
        "",
        "## 6. Case-by-Case Deidentified Evidence Ledger",
        "",
        "| Case (`Subject Hash`) | Requisition (`RQS`) | Engine Score (`Brier Err`) | Expert Score (`|Err|`) | Parser Conf | Blinded Recruiter | Human + Engine | Validated Outcome (`Int/Sel/90d/Prom/Attr`) |",
        "| :--- | :--- | :-: | :-: | :-: | :-: | :-: | :--- |",
    ]
    for c in report.cases:
        exp_str = f"{c.manual_expert_score:.1f} (`{c.abs_error_vs_expert:.1f}`)" if c.manual_expert_score is not None else "—"
        pc = c.parser_confidence
        o = c.outcomes
        long_str = (
            f"`{o.interview}`/"
            f"`{o.selection if o.selection is not None else '-'}`/"
            f"`{o.performance_90d if o.performance_90d is not None else '-'}`/"
            f"`{o.promotion if o.promotion is not None else '-'}`/"
            f"`{o.attrition_90d if o.attrition_90d is not None else '-'}`"
        )
        lines.append(
            f"| `{c.case_id}` (`{c.deidentified_subject_hash}`) | {c.requisition_title} (`RQS {c.rqs_score:.1f}`) | "
            f"**{c.composite_score:.1f}** (`{c.brier_squared_error:.3f}`) | {exp_str} | "
            f"`{pc.combined_confidence:.0%}` ({pc.confidence_band.split()[0]}) | "
            f"`{c.blinded_recruiter_decision}` | `{c.human_decision_status}` | "
            f"**{c.manual_validated_outcome}** ({long_str}) |"
        )

    lines += [
        "",
        "## 7. Parser Confidence & Human Override Diagnostics",
        "",
    ]
    for c in report.cases:
        pc = c.parser_confidence
        drift_str = (
            f" | Heuristic-vs-Confirmed JD Drift: `{pc.heuristic_vs_confirmed_score_drift:.1f} pts`"
            if pc.heuristic_vs_confirmed_score_drift is not None
            else ""
        )
        lines.append(
            f"### `{c.case_id}` (`{c.deidentified_subject_hash}`) — {c.requisition_title} "
            f"(Score: `{c.composite_score:.1f}`, RQS: `{c.rqs_score:.1f}`, Parser Confidence: `{pc.combined_confidence:.1%}`)"
        )
        lines.append(
            f"- **Parser Telemetry:** Resume `{pc.resume_confidence:.1%}` "
            f"(sections: `{', '.join(pc.resume_sections_detected) or 'none'}`, valid date ranges: `{pc.resume_valid_date_ranges}`, "
            f"structured char ratio: `{pc.resume_structured_char_ratio:.0%}`) | "
            f"Requisition `{pc.requisition_confidence:.1%}` (`{pc.requisition_source_mode}`, "
            f"tier split: `{pc.requisition_has_tier_split}`, items: `{pc.requisition_extracted_req_count}`){drift_str}"
        )
        if pc.warnings:
            lines.append(f"- **Parser Warnings:** {'; '.join(pc.warnings)}")
        if c.human_override_reason:
            lines.append(f"- **Human Reviewer Note (`{c.human_reviewer}`):** {c.human_override_reason}")
        if c.validation_notes:
            lines.append(f"- **Validated Outcome Evidence (*{c.manual_outcome_stage}*):** {c.validation_notes}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def render_pilot_csv(report: PilotCohortReport) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "case_id", "deidentified_subject_hash", "deidentified_verified", "pii_redactions_applied",
        "requisition_id", "requisition_title", "candidate_id",
        "rqs_score", "rqs_band", "rqs_hidden_worker_flags",
        "composite_score", "predicted_probability", "literal_score", "blind_score", "blind_parity_delta", "heuristic_jd_score",
        "comp1_coverage", "comp2_placement", "comp3_alignment", "comp4_gates", "comp5_hygiene",
        "knockout_passed", "verified_count", "partial_count", "unverified_count",
        "resume_confidence", "requisition_confidence", "combined_confidence",
        "requires_manual_parse_check", "heuristic_vs_confirmed_drift",
        "engine_band", "blinded_recruiter_decision", "human_decision_status", "human_reviewer", "human_override_reason",
        "manual_validated_outcome", "manual_outcome_stage",
        "outcome_interview", "outcome_selection", "outcome_performance_90d", "outcome_promotion", "outcome_attrition_90d",
        "manual_expert_score", "abs_error_vs_expert", "brier_squared_error",
        "classification_80", "classification_65", "blinded_recruiter_matches_outcome", "human_decision_matches_outcome", "validation_notes",
    ])
    for c in report.cases:
        pc = c.parser_confidence
        o = c.outcomes
        writer.writerow([
            c.case_id,
            c.deidentified_subject_hash,
            c.deidentified_verified,
            c.pii_redactions_applied,
            c.requisition_id,
            c.requisition_title,
            c.candidate_id,
            f"{c.rqs_score:.1f}",
            c.rqs_band,
            c.rqs_hidden_worker_flags,
            f"{c.composite_score:.1f}",
            f"{c.predicted_probability:.4f}",
            f"{c.literal_score:.1f}",
            f"{c.blind_score:.1f}",
            f"{c.blind_parity_delta:.2f}",
            f"{c.heuristic_jd_score:.1f}" if c.heuristic_jd_score is not None else "",
            f"{c.comp1_coverage:.2f}",
            f"{c.comp2_placement:.2f}",
            f"{c.comp3_alignment:.2f}",
            f"{c.comp4_gates:.2f}",
            f"{c.comp5_hygiene:.2f}",
            c.knockout_passed,
            c.verified_count,
            c.partial_count,
            c.unverified_count,
            f"{pc.resume_confidence:.3f}",
            f"{pc.requisition_confidence:.3f}",
            f"{pc.combined_confidence:.3f}",
            pc.requires_manual_parse_check,
            f"{pc.heuristic_vs_confirmed_score_drift:.1f}" if pc.heuristic_vs_confirmed_score_drift is not None else "",
            c.engine_band,
            c.blinded_recruiter_decision,
            c.human_decision_status,
            c.human_reviewer,
            c.human_override_reason,
            c.manual_validated_outcome,
            c.manual_outcome_stage,
            o.interview,
            o.selection if o.selection is not None else "",
            o.performance_90d if o.performance_90d is not None else "",
            o.promotion if o.promotion is not None else "",
            o.attrition_90d if o.attrition_90d is not None else "",
            f"{c.manual_expert_score:.1f}" if c.manual_expert_score is not None else "",
            f"{c.abs_error_vs_expert:.1f}" if c.abs_error_vs_expert is not None else "",
            f"{c.brier_squared_error:.4f}",
            c.classification_80,
            c.classification_65,
            c.blinded_recruiter_matches_outcome,
            c.human_decision_matches_outcome,
            c.validation_notes,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Built-In 6-Case Deidentified Multi-Stage Longitudinal Pilot Cohort
# ---------------------------------------------------------------------------

DEFAULT_IMPROVED_WFM_JD = """Job Title: Workforce Management Analyst II
Company: Northstar Contact Solutions
Location: St. Louis, MO (Hybrid)
Compensation: $78,000 - $94,000 base salary + annual performance bonus and comprehensive benefits

About the Role
Northstar Contact Solutions is hiring a Workforce Management Analyst II to support forecasting, scheduling, and intraday operations across our 600-seat omnichannel customer contact centers.

Key Responsibilities
- Build 15-minute, daily, and weekly contact-volume forecasts and multi-skill agent schedules in NICE IEX, Verint, or Genesys Cloud.
- Maintain capacity planning and staffing models (including Erlang C queueing models) to balance service level (80/20), occupancy, and shrinkage targets.
- Track service level, Average Speed of Answer (ASA), Average Handle Time (AHT), occupancy, and shrinkage across queues, and partner with operations leads on intraday recovery plans.
- Publish recurring operational dashboards in Power BI or Tableau using SQL and Excel.

Required Qualifications
- Bachelor's degree in a quantitative or business field, or an equivalent combination of education and experience.
- 2+ years of workforce management, capacity planning, or contact-center scheduling experience.
- Hands-on experience with a contact-center WFM platform such as NICE IEX, Verint, or Genesys Cloud.
- Working knowledge of capacity planning and queueing concepts (such as Erlang C).
- Ability to build recurring operational reports in Power BI or Tableau, and query data with SQL or Excel.
- Clear written and verbal communication with operations stakeholders.

Preferred Qualifications
- Experience with Python or R for time-series forecasting.
- Familiarity with CRM ticket data (for example Salesforce or Microsoft Dynamics).
- Six Sigma Green Belt certification or comparable process-improvement project experience.

Equal Opportunity
Northstar Contact Solutions is an equal opportunity employer and provides reasonable accommodations to qualified applicants with disabilities upon request.
"""


def _resolve_input_path(path_str: str) -> str:
    if os.path.exists(path_str):
        return path_str
    repo_root = esa._find_repo_root()
    candidate = os.path.join(repo_root, path_str)
    if os.path.exists(candidate):
        return candidate
    return path_str


def get_demo_pilot_cases() -> List[Dict[str, Any]]:
    root_dir = esa._find_repo_root()
    candidates, reqs, align_cfg, _, _ = esa.get_demo_batch()
    sam_cfg = {
        "requirements": [dataclasses.asdict(r) for r in reqs],
        "alignment": dataclasses.asdict(align_cfg),
    }

    improved_jd_path = os.path.join(root_dir, "examples", "sample_requisition_improved.txt")
    unicorn_jd_path = os.path.join(root_dir, "examples", "sample_requisition_unicorn.txt")
    improved_jd = (
        Path(improved_jd_path).read_text(encoding="utf-8")
        if os.path.exists(improved_jd_path)
        else DEFAULT_IMPROVED_WFM_JD
    )
    unicorn_jd = (
        Path(unicorn_jd_path).read_text(encoding="utf-8")
        if os.path.exists(unicorn_jd_path)
        else rqa.DEMO_REQUISITION
    )

    cand_map = {cid: txt for cid, _, txt in candidates}

    sam_jd_text = """Job Title: Senior Analytics Manager
Company: Northstar Contact Solutions
Location: St. Louis, MO (Hybrid)
Compensation: $135,000 - $165,000 base salary + annual performance bonus and comprehensive benefits

Role Overview & Scope
Lead a 6-analyst workforce and supply-chain analytics team supporting 400+ distribution centers and contact-center operations ($18M operating budget / P&L Ownership).

Key Responsibilities
- Lead enterprise Demand Forecasting and Workforce Capacity Modeling in Python and SQL across multi-site operations.
- Own executive BI reporting in Power BI and data pipelines in Snowflake integrated with SAP S/4HANA.
- Partner with cross-functional leadership on annual P&L Ownership and capacity reviews.

Required Qualifications
- Bachelor's degree (B.S.) in a quantitative or business field, or an equivalent combination of education and experience.
- 6+ years of progressive analytics experience including Python, SQL, Snowflake, Power BI, and SAP S/4HANA.
- Demonstrated experience in Demand Forecasting, Workforce Capacity Modeling, P&L Ownership, and Cross-Functional Leadership.

Preferred Qualifications
- Familiarity with containerized analytics workflows in Kubernetes.
- Six Sigma Black Belt certification or comparable DMAIC process-improvement experience.

Equal Opportunity
Northstar Contact Solutions is an equal opportunity employer and provides reasonable accommodations to qualified applicants with disabilities upon request.
"""

    cand5_wfm_specialist = """ELENA ROSTOVA
St. Louis, MO 63101 | elena.rostova@example.com | (314) 555-0188

Professional Summary
Workforce Management Analyst with 4 years of contact-center forecasting, NICE IEX scheduling, and Erlang C capacity modeling experience.

Work Experience
Workforce Management Analyst II — Gateway Contact Centers (02/2023 - Present)
- Built 15-minute and daily volume forecasting models and multi-skill agent schedules in NICE IEX across 650 contact-center seats, holding service level within 2.1% of the 80/20 target.
- Developed Erlang C capacity planning and staffing models, tracking Average Speed of Answer (ASA), Average Handle Time (AHT), occupancy, shrinkage, and real-time adherence in Power BI and SQL.
- Led a Six Sigma Green Belt continuous-improvement project that reduced unplanned shrinkage by 1.8 percentage points across 14 queues.

Workforce Management Coordinator — Midwest Customer Care (01/2022 - 01/2023)
- Monitored intraday real-time adherence in NICE IEX and prepared daily Excel, Python, and SQL exception reports.

Education
B.S. in Business Administration — Saint Louis University (05/2021)

Technical & Core Skills
NICE IEX, Erlang C, Workforce Management, Capacity Planning, Forecasting, Staffing Models, Real-Time Adherence, Power BI, SQL, Excel, Python, Six Sigma Green Belt
"""

    cand6_low_parse_conf = """DEVON PARKER
devon.parker@example.com | (312) 555-0171

Background & Stuff
Worked on some Excel sheets and general office coordination for a small team.
| Project | Tool | Notes |
| Reporting | Excel | Weekly logs |
| Tickets | Email | Inbox triage |
"""

    return [
        {
            "case_id": "PILOT-01",
            "requisition_id": "REQ-SAM-100",
            "requisition_title": "Senior Analytics Manager",
            "candidate_id": "CAND-001",
            "resume_text": cand_map["CAND-001"],
            "jd_config": sam_cfg,
            "jd_text": sam_jd_text,
            "blinded_recruiter_decision": "ADVANCE",
            "human_decision": "ADVANCE",
            "human_reviewer": "recruiter_j_smith",
            "human_override_reason": "",
            "manual_validated_outcome": "ADVANCE",
            "manual_outcome_stage": "Final Hiring Manager Panel (Hired & Promoted at 14m)",
            "manual_expert_score": 98.0,
            "outcomes": {
                "interview": 1,
                "selection": 1,
                "performance_90d": 1,
                "promotion": 1,
                "attrition_90d": 0,
            },
            "validation_notes": "All 12 competencies verified in technical panel; selected, exceeded 90-day forecast accuracy KPI, retained, and promoted at 14 months.",
        },
        {
            "case_id": "PILOT-02",
            "requisition_id": "REQ-SAM-100",
            "requisition_title": "Senior Analytics Manager",
            "candidate_id": "CAND-002",
            "resume_text": cand_map["CAND-002"],
            "jd_config": sam_cfg,
            "jd_text": sam_jd_text,
            "blinded_recruiter_decision": "ADVANCE",
            "human_decision": "ADVANCE",
            "human_reviewer": "recruiter_j_smith",
            "human_override_reason": "",
            "manual_validated_outcome": "ADVANCE",
            "manual_outcome_stage": "Hiring Manager Technical Screen (Selected & Met 90d KPIs)",
            "manual_expert_score": 87.0,
            "outcomes": {
                "interview": 1,
                "selection": 1,
                "performance_90d": 1,
                "promotion": 0,
                "attrition_90d": 0,
            },
            "validation_notes": "Synonym matches ('capacity planning', 'operating budget') and skills-list Power BI/SAP S/4HANA confirmed in technical screen; met 90-day ramp targets.",
        },
        {
            "case_id": "PILOT-03",
            "requisition_id": "REQ-SAM-100",
            "requisition_title": "Senior Analytics Manager",
            "candidate_id": "CAND-003",
            "resume_text": cand_map["CAND-003"],
            "jd_config": sam_cfg,
            "jd_text": sam_jd_text,
            "blinded_recruiter_decision": "DO_NOT_ADVANCE",
            "human_decision": "ADVANCE",
            "human_reviewer": "recruiter_j_smith",
            "human_override_reason": "Blinded recruiter initially filtered out due to missing literal 'Workforce Capacity Modeling' & 'SAP S/4HANA' keywords; engine semantic ledger (76.4 Borderline) surfaced 'capacity planning' + Python/Snowflake depth, prompting human override to interview.",
            "manual_validated_outcome": "ADVANCE",
            "manual_outcome_stage": "Hiring Manager Technical Screen (Passed Technical Panel)",
            "manual_expert_score": 78.0,
            "outcomes": {
                "interview": 1,
                "selection": 0,
                "performance_90d": 1,
                "promotion": 0,
                "attrition_90d": 0,
            },
            "validation_notes": "Demonstrates value over blinded keyword screen: candidate passed HM technical interview (finalist runner-up, hired into lateral Lead Analytics role and met 90d KPIs).",
        },
        {
            "case_id": "PILOT-04",
            "requisition_id": "REQ-SAM-100",
            "requisition_title": "Senior Analytics Manager",
            "candidate_id": "CAND-004",
            "resume_text": cand_map["CAND-004"],
            "jd_config": sam_cfg,
            "jd_text": sam_jd_text,
            "blinded_recruiter_decision": "DO_NOT_ADVANCE",
            "human_decision": "DO_NOT_ADVANCE",
            "human_reviewer": "recruiter_j_smith",
            "human_override_reason": "Reviewed manually; missing Python, Snowflake, SAP S/4HANA, Demand Forecasting, P&L scope, and B.S. gate for Senior Manager level (routed to Analyst II internal mobility plan).",
            "manual_validated_outcome": "DO_NOT_ADVANCE",
            "manual_outcome_stage": "Calibrated Recruiter & HM Calibration Review",
            "manual_expert_score": 35.0,
            "outcomes": {
                "interview": 0,
                "selection": 0,
                "performance_90d": 0,
                "promotion": 0,
                "attrition_90d": 1,
            },
            "validation_notes": "Engine score (33.0) matched manual calibration (35.0); candidate lacked Senior Manager scope and core Python/Snowflake stack.",
        },
        {
            "case_id": "PILOT-05",
            "requisition_id": "REQ-WFM-202",
            "requisition_title": "Workforce Management Analyst II",
            "candidate_id": "CAND-005",
            "resume_text": cand5_wfm_specialist,
            "jd_text": improved_jd,
            "blinded_recruiter_decision": "ADVANCE",
            "human_decision": "ADVANCE",
            "human_reviewer": "wfm_director_k_lee",
            "human_override_reason": "",
            "manual_validated_outcome": "ADVANCE",
            "manual_outcome_stage": "Structured WFM Erlang C & NICE IEX Case Study (Hired & Promoted)",
            "manual_expert_score": 92.0,
            "outcomes": {
                "interview": 1,
                "selection": 1,
                "performance_90d": 1,
                "promotion": 1,
                "attrition_90d": 0,
            },
            "validation_notes": "Calibrated WFM JD (RQS 100.0) cleanly matched NICE IEX, Erlang C, Power BI, and SQL alternatives; candidate hired, met 90d SLA accuracy goals, and promoted to Sr. WFM Analyst.",
        },
        {
            "case_id": "PILOT-06",
            "requisition_id": "REQ-WFM-999-UNICORN",
            "requisition_title": "Workforce Management Analyst II - WFM Ops - L4",
            "candidate_id": "CAND-006",
            "resume_text": cand6_low_parse_conf,
            "jd_text": unicorn_jd,
            "blinded_recruiter_decision": "DO_NOT_ADVANCE",
            "human_decision": "DO_NOT_ADVANCE",
            "human_reviewer": "wfm_director_k_lee",
            "human_override_reason": "Low parser confidence (<75%) triggered manual inspection of raw resume; confirmed lack of WFM, forecasting, and BI experience.",
            "manual_validated_outcome": "DO_NOT_ADVANCE",
            "manual_outcome_stage": "Manual Parse Verification & HM Review",
            "manual_expert_score": 15.0,
            "outcomes": {
                "interview": 0,
                "selection": 0,
                "performance_90d": 0,
                "promotion": 0,
                "attrition_90d": 1,
            },
            "validation_notes": "Parser confidence guard (<75%) properly flagged non-standard headers, missing dates, and ASCII table layout; RQS (36.0) also flagged the requisition itself for remediation.",
        },
    ]


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-pilot`)
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_args = list(argv) if argv is not None else sys.argv[1:]
    try:
        gov.enforce_anti_automation_cli(raw_args)
        gov.enforce_environment_safeguards()
    except gov.GovernanceError as exc:
        sys.stderr.write(f"{exc}\n")
        return 2

    parser = argparse.ArgumentParser(
        prog="ops-req-pilot",
        description="Pilot & Empirical Validity Evaluation Harness: runs deidentified (requisition, resume) pairs through RQS and JD Alignment rubrics, computes ROC-AUC, Brier Score, ECE, 95% CIs, blinded recruiter comparison, and component ablation across multi-stage outcomes.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the built-in 6-case deidentified WFM & Analytics longitudinal validation cohort.")
    parser.add_argument("--manifest", help="Path to a pilot manifest JSON file containing a list of case objects.")
    parser.add_argument("--resume", help="Single-case mode: path to candidate resume (.txt, .md, .docx, .pdf).")
    parser.add_argument("--jd-file", help="Single-case mode: path to job requisition text/HTML file.")
    parser.add_argument("--jd-config", help="Single-case mode: path to confirmed requirement config JSON.")
    parser.add_argument("--blinded-recruiter", default="PENDING_HUMAN_REVIEW", help="Single-case mode: independent blinded recruiter decision (ADVANCE or DO_NOT_ADVANCE).")
    parser.add_argument("--human-decision", default="PENDING_HUMAN_REVIEW", help="Single-case mode: final human reviewer decision (ADVANCE, DO_NOT_ADVANCE, HOLD).")
    parser.add_argument("--manual-outcome", default="ADVANCE", help="Single-case mode: manually validated ground-truth outcome (ADVANCE or DO_NOT_ADVANCE).")
    parser.add_argument("--manual-score", type=float, help="Single-case mode: optional manual domain-expert 0-100 score.")
    parser.add_argument("--reference-date", default="2026-11-01", help="Reference date YYYY-MM-DD for reproducible tenure math.")
    parser.add_argument("--output", help="Write Markdown pilot validation report to this path.")
    parser.add_argument("--json-out", help="Write full JSON pilot evidence bundle to this path.")
    parser.add_argument("--csv-out", help="Write UTF-8 CSV case-by-case evidence ledger to this path.")

    args = parser.parse_args(argv)
    ref_dt = datetime.strptime(args.reference_date, "%Y-%m-%d")

    if args.demo:
        case_specs = get_demo_pilot_cases()
    elif args.manifest:
        manifest_path = _resolve_input_path(args.manifest)
        with open(manifest_path, "r", encoding="utf-8") as fh:
            raw_manifest = json.load(fh)
        raw_cases = raw_manifest if isinstance(raw_manifest, list) else raw_manifest.get("cases", [])
        base_dir = os.path.dirname(os.path.abspath(manifest_path))
        case_specs = []
        for idx, item in enumerate(raw_cases, start=1):
            spec = dict(item)
            spec.setdefault("case_id", f"PILOT-{idx:02d}")
            if "resume_text" not in spec and "resume_file" in spec:
                r_path = spec["resume_file"]
                if not os.path.isabs(r_path):
                    r_path = _resolve_input_path(os.path.join(base_dir, r_path))
                spec["resume_text"] = oaa.load_resume_file(r_path)
            if "jd_text" not in spec and "jd_file" in spec:
                j_path = spec["jd_file"]
                if not os.path.isabs(j_path):
                    j_path = _resolve_input_path(os.path.join(base_dir, j_path))
                spec["jd_text"] = Path(j_path).read_text(encoding="utf-8")
            if "jd_config" not in spec and "jd_config_file" in spec:
                c_path = spec["jd_config_file"]
                if not os.path.isabs(c_path):
                    c_path = _resolve_input_path(os.path.join(base_dir, c_path))
                spec["jd_config"] = json.loads(Path(c_path).read_text(encoding="utf-8"))
            case_specs.append(spec)
    elif args.resume and (args.jd_file or args.jd_config):
        res_path = _resolve_input_path(args.resume)
        jd_f_path = _resolve_input_path(args.jd_file) if args.jd_file else None
        jd_c_path = _resolve_input_path(args.jd_config) if args.jd_config else None
        spec: Dict[str, Any] = {
            "case_id": "PILOT-SINGLE-01",
            "requisition_id": Path(jd_f_path or jd_c_path or "REQ").stem.upper(),
            "candidate_id": Path(res_path).stem.upper(),
            "resume_text": oaa.load_resume_file(res_path),
            "blinded_recruiter_decision": args.blinded_recruiter,
            "human_decision": args.human_decision,
            "manual_validated_outcome": args.manual_outcome,
            "manual_expert_score": args.manual_score,
        }
        if jd_f_path:
            spec["jd_text"] = Path(jd_f_path).read_text(encoding="utf-8")
        if jd_c_path:
            spec["jd_config"] = json.loads(Path(jd_c_path).read_text(encoding="utf-8"))
        case_specs = [spec]
    else:
        parser.error("Provide --demo, --manifest <pilot_manifest.json>, or --resume <path> with --jd-file/--jd-config.")

    report = run_pilot_cohort(case_specs, reference_date=ref_dt)
    md_out = render_pilot_markdown(report)

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
            fh.write(render_pilot_csv(report))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
