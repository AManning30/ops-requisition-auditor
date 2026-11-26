import json
import unittest
from pathlib import Path

from scripts import workbench_parity_and_a11y as wpa


class TestWorkbenchParityAndAccessibility(unittest.TestCase):
    def test_shared_scoring_test_vectors_match_canonical_benchmarks(self) -> None:
        vectors = wpa.generate_shared_scoring_test_vectors()
        self.assertEqual(vectors["vector_contract_version"], "shared-vectors-1.0")
        self.assertEqual(vectors["candidate_jd_alignment"]["baseline_composite_score"], 88.3)
        self.assertEqual(vectors["candidate_jd_alignment"]["baseline_literal_score"], 82.9)
        self.assertEqual(vectors["candidate_jd_alignment"]["plausible_band_low"], 82.9)
        self.assertEqual(vectors["candidate_jd_alignment"]["plausible_band_high"], 96.8)
        self.assertEqual(vectors["candidate_jd_alignment"]["optimized_composite_score"], 100.0)
        self.assertEqual(vectors["employer_requisition_quality"]["unicorn_rqs_score"], 36.0)
        self.assertEqual(vectors["employer_requisition_quality"]["unicorn_projected_auto_edit_score"], 56.0)
        self.assertEqual(vectors["employer_requisition_quality"]["improved_rqs_score"], 100.0)
        self.assertTrue(vectors["explainable_screening"]["all_blind_parity_verified"])

    def test_workbench_html_parity_and_wcag_accessibility_checks(self) -> None:
        report = wpa.verify_workbench_parity_and_accessibility(sync_docs=False)
        self.assertTrue(report.html_byte_parity, "demo.html and docs/index.html must be byte-identical")
        self.assertTrue(report.all_parity_checks_passed)
        self.assertTrue(report.all_accessibility_checks_passed)
        self.assertEqual(len(report.parity_checks), 7)
        self.assertEqual(len(report.accessibility_checks), 7)

    def test_exported_shared_vectors_file_exists_and_matches(self) -> None:
        vec_path = Path(__file__).resolve().parent.parent / "examples" / "shared_scoring_test_vectors.json"
        self.assertTrue(vec_path.exists())
        loaded = json.loads(vec_path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["vector_contract_version"], "shared-vectors-1.0")
        self.assertEqual(loaded["candidate_jd_alignment"]["baseline_composite_score"], 88.3)


if __name__ == "__main__":
    unittest.main()
