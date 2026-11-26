import json
import unittest
from pathlib import Path

from scripts import public_trust_and_benchmarks as ptb


class TestPublicTrustAndBenchmarks(unittest.TestCase):
    def test_public_benchmark_suite_passes_all_six_domains(self) -> None:
        report = ptb.run_public_trust_benchmark_suite()
        self.assertTrue(report.all_benchmarks_passed)
        self.assertEqual(len(report.benchmark_cases), 6)
        for case in report.benchmark_cases:
            self.assertTrue(case.passed, f"Benchmark case {case.case_id} ({case.domain}) failed")

    def test_synthetic_benchmark_corpus_has_zero_real_pii(self) -> None:
        corpus = ptb.build_public_synthetic_benchmark_corpus()
        self.assertTrue(ptb.verify_zero_real_pii_in_corpus(corpus))
        corpus_path = Path(__file__).resolve().parent.parent / "examples" / "public_synthetic_benchmark_corpus.json"
        if corpus_path.exists():
            loaded = json.loads(corpus_path.read_text(encoding="utf-8"))
            self.assertTrue(ptb.verify_zero_real_pii_in_corpus(loaded))

    def test_known_failure_modes_and_independent_reviews_complete(self) -> None:
        self.assertEqual(len(ptb.KNOWN_FAILURE_MODES), 10)
        self.assertEqual(len(ptb.INDEPENDENT_ETHICS_AND_HR_REVIEWS), 3)
        report = ptb.run_public_trust_benchmark_suite()
        self.assertTrue(report.governance_docs_verified)
        for k, passed in report.governance_checks.items():
            self.assertTrue(passed, f"Governance document check failed: {k}")


if __name__ == "__main__":
    unittest.main()
