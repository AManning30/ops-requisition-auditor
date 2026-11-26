#!/usr/bin/env python3
"""
Unit tests for the Fairness, Bias & Adverse-Impact Evaluation Engine (`fairness_adverse_impact_auditor.py`).
"""
from __future__ import annotations

import copy
from datetime import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import fairness_adverse_impact_auditor as faia  # noqa: E402


class TestFairnessAdverseImpactAuditor(unittest.TestCase):
    def test_six_dimensions_evaluated_with_zero_blind_parity_drift(self) -> None:
        cases, reqs, align_cfg = faia.get_demo_fairness_cohort()
        rep = faia.evaluate_fairness_cohort(
            raw_cases=cases,
            reqs=reqs,
            align_cfg=align_cfg,
            reference_date=datetime(2026, 11, 1),
            shortlist_threshold=80.0,
        )
        self.assertEqual(rep.total_cases, 12)
        self.assertEqual(tuple(rep.dimensions_evaluated), faia.EVALUATED_DIMENSIONS)
        self.assertEqual(rep.overall_blind_parity_max_drift, 0.0)
        self.assertEqual(rep.overall_human_accuracy, 1.0)
        self.assertEqual(len(rep.report_sha256), 64)

        dims_present = {r.dimension for r in rep.subgroup_rows}
        self.assertEqual(
            dims_present,
            {
                "race_ethnicity",
                "sex",
                "age_proxy_band",
                "disability_status",
                "resume_language",
                "intersectional_race_sex",
            },
        )
        # Check that multilingual resumes (en, es, fr, de) all achieved 0% parser extraction error
        lang_rows = [r for r in rep.subgroup_rows if r.dimension == "resume_language"]
        self.assertEqual({r.subgroup for r in lang_rows}, {"en", "es", "fr", "de"})
        for r in lang_rows:
            self.assertEqual(r.mean_parser_error_rate, 0.0)
            self.assertGreaterEqual(r.mean_parser_confidence, 0.80)

    def test_small_sample_warnings_wilson_ci_and_fisher_exact_p_value(self) -> None:
        # Wilson 95% CI bounds check
        ci_low, ci_high = faia.wilson_95_ci(2, 3)
        self.assertGreater(ci_low, 0.15)
        self.assertLess(ci_high, 0.98)

        # Exact Fisher test check: identical proportions -> p = 1.0; extreme split -> p < 0.05
        self.assertEqual(faia.fisher_exact_two_sided(2, 1, 2, 1), 1.0)
        p_extreme = faia.fisher_exact_two_sided(0, 10, 9, 1)
        self.assertLess(p_extreme, 0.001)

        # 3-Tier sample size warning classification
        tier_small, warn_small = faia.classify_sample_size_warning(3, 12)
        self.assertIn("CRITICAL_SMALL_SAMPLE", tier_small)
        self.assertIn("SMALL_SAMPLE_CAUTION", warn_small)

        tier_mod, warn_mod = faia.classify_sample_size_warning(12, 1000)
        self.assertIn("MODERATE_SAMPLE", tier_mod)
        self.assertIn("[NYC LL144 <2% Pool Share]", warn_mod)

        tier_pow, _ = faia.classify_sample_size_warning(45, 100)
        self.assertIn("POWERED_SAMPLE", tier_pow)

    def test_parser_and_override_disparity_alerts_trigger_on_biased_inputs(self) -> None:
        cases, reqs, align_cfg = faia.get_demo_fairness_cohort()
        biased_cases = copy.deepcopy(cases)

        # Simulate a parser failure on the French resume (extracting only 2 of 8 ground-truth requirements)
        # and an inaccurate downward human override onFAIR-01
        for c in biased_cases:
            if c["case_id"] == "FAIR-06":
                c["parser_extracted_req_count"] = 2
            if c["case_id"] == "FAIR-01":
                c["human_reviewer_decision"] = "DO_NOT_ADVANCE"
                c["validated_outcome"] = "ADVANCE"

        rep = faia.evaluate_fairness_cohort(
            raw_cases=biased_cases,
            reqs=reqs,
            align_cfg=align_cfg,
            reference_date=datetime(2026, 11, 1),
            shortlist_threshold=80.0,
        )
        self.assertGreaterEqual(len(rep.parser_disparity_alerts), 1)
        self.assertTrue(any("resume_language='fr'" in a for a in rep.parser_disparity_alerts))
        self.assertGreaterEqual(len(rep.override_disparity_alerts), 1)

    def test_reproducible_cli_markdown_json_and_csv_exports(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = os.path.join(tmpdir, "manifest.json")
            md_path = os.path.join(tmpdir, "report.md")
            json_path = os.path.join(tmpdir, "report.json")
            csv_path = os.path.join(tmpdir, "report.csv")

            rc_exp = faia.main(["--export-demo-manifest", manifest_path])
            self.assertEqual(rc_exp, 0)
            self.assertTrue(os.path.isfile(manifest_path))

            rc_run = faia.main([
                "--manifest", manifest_path,
                "--output", md_path,
                "--json-out", json_path,
                "--csv-out", csv_path,
            ])
            self.assertEqual(rc_run, 0)
            self.assertTrue(os.path.isfile(md_path))
            self.assertTrue(os.path.isfile(json_path))
            self.assertTrue(os.path.isfile(csv_path))

            with open(json_path, "r", encoding="utf-8") as fh:
                j_data = json.load(fh)
            self.assertEqual(j_data["total_cases"], 12)
            self.assertEqual(len(j_data["report_sha256"]), 64)

            with open(csv_path, "r", encoding="utf-8") as fh:
                csv_txt = fh.read()
            self.assertIn("dimension,subgroup,n_applicants,pool_share_pct,sample_size_tier", csv_txt)
            self.assertIn("disability_status,Disclosed Disability / Accommodation", csv_txt)
            self.assertIn("age_proxy_band,40 and Over (>=40)", csv_txt)


if __name__ == "__main__":
    unittest.main()
