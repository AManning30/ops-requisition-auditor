#!/usr/bin/env python3
"""
Unit tests for Transparent Uncertainty, Plausible Score Intervals, Three-Pillar Separation
(Score vs. Confidence vs. Evidence Completeness), Side-by-Side Semantic vs. Literal View,
Explicit Missing Evidence, and Complete Point-Deduction Explanations (`offline_ats_auditor.py`).
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import offline_ats_auditor as oaa  # noqa: E402


class TestUncertaintyAndScoreExplanations(unittest.TestCase):
    def test_baseline_88_3_three_pillars_and_plausible_interval(self) -> None:
        resume_txt, reqs, align_cfg = oaa.get_demo_fixture()
        rep = oaa.run_ats_audit(resume_txt, reqs, align_cfg, reference_date=datetime(2026, 11, 1))

        self.assertEqual(rep.composite_score, 88.3)
        self.assertEqual(rep.legacy_literal_score, 82.9)

        unc = rep.uncertainty_and_explanations
        self.assertIn("epistemic_notice", unc)
        pillars = unc["three_pillar_distinction"]

        # Pillar 1: Heuristic Alignment Score & Plausible Band [82.9, 96.8]
        p1 = pillars["1_alignment_score"]
        self.assertEqual(p1["point_estimate"], 88.3)
        self.assertEqual(p1["literal_only_score"], 82.9)
        self.assertEqual(p1["plausible_score_interval"], [82.9, 96.8])
        self.assertAlmostEqual(p1["interval_width_pts"], 13.9, places=1)
        self.assertIn("MODERATE_INTERPRETATION_SPREAD", p1["uncertainty_band"])

        # Pillar 2: Measurement & Parser Confidence (High, distinct from score)
        p2 = pillars["2_measurement_confidence"]
        self.assertGreaterEqual(p2["combined_confidence"], 0.90)
        self.assertIn("HIGH_MEASUREMENT_CONFIDENCE", p2["confidence_band"])

        # Pillar 3: Evidence Completeness (6 full 1.0x, 5 partial, 1 missing = 71.7% weighted completeness)
        p3 = pillars["3_evidence_completeness"]
        self.assertEqual(p3["fully_verified_1x_count"], 6)
        self.assertEqual(p3["partially_verified_count"], 5)
        self.assertEqual(p3["unverified_missing_count"], 1)
        self.assertEqual(p3["total_requirements_count"], 12)
        self.assertAlmostEqual(p3["weighted_evidence_completeness_pct"], 71.7, places=1)
        self.assertIn("PARTIAL_EVIDENCE", p3["completeness_band"])

    def test_side_by_side_semantic_vs_literal_and_missing_evidence_ledger(self) -> None:
        resume_txt, reqs, align_cfg = oaa.get_demo_fixture()
        rep = oaa.run_ats_audit(resume_txt, reqs, align_cfg, reference_date=datetime(2026, 11, 1))
        unc = rep.uncertainty_and_explanations

        svl = unc["semantic_vs_literal_comparison"]
        self.assertEqual(svl["semantic_score"], 88.3)
        self.assertEqual(svl["literal_score"], 82.9)
        self.assertAlmostEqual(svl["semantic_minus_literal_delta"], 5.4, places=1)
        self.assertEqual(len(svl["component_comparison"]), 5)
        self.assertEqual(len(svl["divergent_requirements"]), 2)
        div_names = {d["requirement"] for d in svl["divergent_requirements"]}
        self.assertEqual(div_names, {"Workforce Capacity Modeling", "P&L Ownership"})

        # Explicit missing evidence (Six Sigma Black Belt)
        missing = unc["missing_evidence_items"]
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["requirement"], "Six Sigma Black Belt")
        self.assertAlmostEqual(missing[0]["points_at_stake"], 3.25, places=2)

        # Partial evidence (5 items: 2 synonyms + 3 skills-section-only)
        partial = unc["partial_evidence_items"]
        self.assertEqual(len(partial), 5)

    def test_complete_point_deduction_reconciliation_sums_to_100(self) -> None:
        resume_txt, reqs, align_cfg = oaa.get_demo_fixture()
        rep = oaa.run_ats_audit(resume_txt, reqs, align_cfg, reference_date=datetime(2026, 11, 1))
        unc = rep.uncertainty_and_explanations

        deds = unc["point_deductions_ledger"]
        self.assertEqual(len(deds), 6)
        sum_deducted = sum(float(d["points_deducted"]) for d in deds)
        self.assertAlmostEqual(sum_deducted, 11.67, places=2)
        self.assertAlmostEqual(rep.composite_score + sum_deducted, 100.0, delta=0.05)

        # Also test a weak resume with C3, C4, and C5 deductions to verify all 5 components reconcile to 100.0
        weak_resume = (
            "TAYLOR SMITH\n"
            "Where I've Been\n"
            "Junior Coordinator (2023 - 2024)\n"
            "- Helped with SQL tables.\n"
        )
        rep_weak = oaa.run_ats_audit(weak_resume, reqs, align_cfg, reference_date=datetime(2026, 11, 1))
        deds_weak = rep_weak.uncertainty_and_explanations["point_deductions_ledger"]
        comp_types = {d["component"] for d in deds_weak}
        self.assertIn("C1_C2_Requirement_Alignment", comp_types)
        self.assertIn("C3_Title_Tenure_Scale", comp_types)
        self.assertIn("C4_Knockout_Gate", comp_types)
        self.assertIn("C5_Document_Hygiene", comp_types)
        sum_weak_ded = sum(float(d["points_deducted"]) for d in deds_weak)
        self.assertAlmostEqual(rep_weak.composite_score + sum_weak_ded, 100.0, delta=0.15)

    def test_enhanced_markdown_report_contains_all_new_sections(self) -> None:
        resume_txt, reqs, align_cfg = oaa.get_demo_fixture()
        rep = oaa.run_ats_audit(resume_txt, reqs, align_cfg, reference_date=datetime(2026, 11, 1))
        md = oaa.render_markdown_report(rep)

        self.assertIn("## 5. Transparent Uncertainty: Score vs. Confidence vs. Evidence Completeness", md)
        self.assertIn("1. Heuristic Alignment Score & Plausible Band", md)
        self.assertIn("[82.9, 96.8]", md)
        self.assertIn("2. Measurement & Parser Confidence", md)
        self.assertIn("3. Evidence Completeness", md)
        self.assertIn("### Side-by-Side Semantic vs. Literal View Comparison", md)
        self.assertIn("### Explicit Missing & Partially Verified Evidence", md)
        self.assertIn("## 6. Complete Point-Deduction Explanations (Reconciling `100.0` -> `88.3`)", md)


if __name__ == "__main__":
    unittest.main()
