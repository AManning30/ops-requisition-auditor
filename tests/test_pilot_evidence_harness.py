#!/usr/bin/env python3
"""Unit tests for the Pilot & Empirical Validity Evaluation Harness (ops-req-pilot)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pilot_evidence_harness as peh  # noqa: E402


class TestPilotEvidenceHarness(unittest.TestCase):
    """Tests for deidentification, longitudinal outcomes, Brier/AUC/ECE/CIs, blinded recruiter comparison, and component ablation."""

    def setUp(self) -> None:
        self.ref_date = datetime(2026, 11, 1)
        self.demo_cases = peh.get_demo_pilot_cases()

    def test_deidentification_and_blind_parity(self) -> None:
        """Verifies all pilot cases are deidentified with salted SUBJ- hashes and 0.00 blind drift."""
        report = peh.run_pilot_cohort(self.demo_cases, reference_date=self.ref_date)
        self.assertEqual(report.total_cases, 6)
        self.assertTrue(report.all_cases_deidentified)
        self.assertGreater(report.total_pii_tokens_scrubbed, 15)
        self.assertEqual(report.mean_blind_parity_delta, 0.0)
        for c in report.cases:
            self.assertTrue(c.deidentified_subject_hash.startswith("SUBJ-"))
            self.assertTrue(c.deidentified_verified)

    def test_calibration_brier_auc_ece_and_confidence_intervals(self) -> None:
        """Verifies ROC-AUC, Brier Score, ECE calibration bins, and 95% bootstrap/Wilson CIs."""
        report = peh.run_pilot_cohort(self.demo_cases, reference_date=self.ref_date)
        # ROC-AUC and 95% Bootstrap CI
        self.assertAlmostEqual(report.roc_auc.estimate, 1.0, places=3)
        self.assertLessEqual(report.roc_auc.ci_lower, report.roc_auc.estimate)
        self.assertGreaterEqual(report.roc_auc.ci_upper, report.roc_auc.estimate)

        # Brier Score (< 0.05 on calibrated cohort) and 95% Bootstrap CI
        self.assertLess(report.brier_score.estimate, 0.05)
        self.assertLessEqual(report.brier_score.ci_lower, report.brier_score.estimate)
        self.assertGreaterEqual(report.brier_score.ci_upper, report.brier_score.estimate)

        # Expected Calibration Error & 3 probability bins
        self.assertLess(report.expected_calibration_error, 0.20)
        self.assertEqual(len(report.calibration_bins), 3)
        self.assertEqual(sum(b.count for b in report.calibration_bins), 6)

        # Wilson 95% CI on classification accuracy
        self.assertAlmostEqual(report.metrics_at_65.accuracy, 1.0)
        self.assertGreater(report.metrics_at_65.accuracy_ci95.ci_lower, 0.55)
        self.assertAlmostEqual(report.metrics_at_65.accuracy_ci95.ci_upper, 1.0)

        # Continuous calibration vs. domain expert scores
        self.assertGreater(report.pearson_r_vs_expert, 0.95)
        self.assertIsNotNone(report.pearson_r_ci95)
        self.assertGreater(report.pearson_r_ci95.ci_lower, 0.85)
        self.assertGreater(report.spearman_rho_vs_expert, 0.95)

    def test_blinded_recruiter_comparison_and_longitudinal_outcomes(self) -> None:
        """Verifies blinded recruiter baseline vs. human+engine lift and all 5 longitudinal outcome stages."""
        report = peh.run_pilot_cohort(self.demo_cases, reference_date=self.ref_date)
        # Blinded recruiter missed PILOT-03 (5/6 = 83.3% accuracy, kappa = 0.667), while Human + Engine achieved 100%
        self.assertAlmostEqual(report.blinded_recruiter_accuracy, 5 / 6, places=3)
        self.assertAlmostEqual(report.blinded_recruiter_kappa, 0.6667, places=3)
        self.assertAlmostEqual(report.human_reviewer_accuracy, 1.0)
        self.assertAlmostEqual(report.human_reviewer_kappa, 1.0)

        # Verify all 5 longitudinal outcomes are tracked and summarized
        stage_names = [s.outcome_name for s in report.longitudinal_outcomes]
        self.assertEqual(
            stage_names,
            ["interview", "selection", "promotion", "performance_90d", "attrition_90d"],
        )
        interview_stage = next(s for s in report.longitudinal_outcomes if s.outcome_name == "interview")
        self.assertEqual(interview_stage.sample_size, 6)
        self.assertAlmostEqual(interview_stage.roc_auc, 1.0)
        attrition_stage = next(s for s in report.longitudinal_outcomes if s.outcome_name == "attrition_90d")
        self.assertLess(attrition_stage.point_biserial_r, -0.80)

    def test_component_ablation_and_misleading_feature_diagnostics(self) -> None:
        """Verifies 5-component ablation table and C5 formatting hygiene reweight/redesign guidance."""
        report = peh.run_pilot_cohort(self.demo_cases, reference_date=self.ref_date)
        self.assertEqual(len(report.component_diagnostics), 5)
        c1_diag = next(d for d in report.component_diagnostics if d.component_id == "C1_coverage")
        self.assertIn("KEEP", c1_diag.recommendation)
        self.assertGreater(c1_diag.outcome_correlation_r, 0.80)

        c5_diag = next(d for d in report.component_diagnostics if d.component_id == "C5_document_hygiene")
        self.assertTrue(
            "REWEIGHT" in c5_diag.recommendation or "REDESIGN" in c5_diag.recommendation
        )

    def test_parser_confidence_and_cli_exports(self) -> None:
        """Verifies parser confidence flags and CLI --manifest / single-case exports."""
        report = peh.run_pilot_cohort(self.demo_cases, reference_date=self.ref_date)
        c1 = next(c for c in report.cases if c.case_id == "PILOT-01")
        self.assertGreaterEqual(c1.parser_confidence.combined_confidence, 0.95)
        self.assertFalse(c1.parser_confidence.requires_manual_parse_check)

        c6 = next(c for c in report.cases if c.case_id == "PILOT-06")
        self.assertLess(c6.parser_confidence.combined_confidence, 0.75)
        self.assertTrue(c6.parser_confidence.requires_manual_parse_check)

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            manifest_path = ROOT / "examples" / "sample_pilot_manifest.json"
            md_out = tmp / "pilot.md"
            json_out = tmp / "pilot.json"
            csv_out = tmp / "pilot.csv"

            rc = peh.main(
                [
                    "--manifest",
                    str(manifest_path),
                    "--reference-date",
                    "2026-11-01",
                    "--output",
                    str(md_out),
                    "--json-out",
                    str(json_out),
                    "--csv-out",
                    str(csv_out),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(md_out.exists())
            self.assertTrue(json_out.exists())
            self.assertTrue(csv_out.exists())
            csv_text = csv_out.read_text(encoding="utf-8")
            self.assertIn("deidentified_subject_hash", csv_text)
            self.assertIn("brier_squared_error", csv_text)
            self.assertIn("outcome_performance_90d", csv_text)


if __name__ == "__main__":
    unittest.main()
