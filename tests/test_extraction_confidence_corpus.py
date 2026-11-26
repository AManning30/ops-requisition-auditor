#!/usr/bin/env python3
"""Unit tests for Extraction Confidence, Provenance Ledger, Ambiguity Detection, Negative Context, and Multilingual Corpus."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import job_posting_collector as jpc  # noqa: E402
import offline_ats_auditor as oaa  # noqa: E402


class TestExtractionConfidenceAndAmbiguityCorpus(unittest.TestCase):
    """Tests for requirement certainty, confirmed-vs-inferred separation, source quotes, ambiguity flags, negative examples, and multilingual JDs."""

    def setUp(self) -> None:
        corpus_path = ROOT / "examples" / "sample_ambiguity_multilingual_corpus.json"
        self.corpus = json.loads(corpus_path.read_text(encoding="utf-8"))

    def test_confirmed_config_vs_inferred_extraction_provenance(self) -> None:
        """Verifies confirmed --jd-config requirements carry CONFIRMED status while raw text requirements carry INFERRED status and quotes."""
        confirmed_reqs, _ = oaa.load_jd_config(str(ROOT / "examples" / "sample_ats_config.json"))
        for r in confirmed_reqs:
            self.assertEqual(r.extraction_status, "CONFIRMED")
            self.assertEqual(r.certainty, "CONFIRMED")
            self.assertAlmostEqual(r.confidence, 1.0)
            self.assertEqual(r.provenance_section, "confirmed_config")

        rec = jpc.parse_job_posting_text(jpc.DEMO_JOB_POSTING_TEXT)
        self.assertGreater(rec.inferred_requirements_count, 5)
        self.assertEqual(rec.confirmed_requirements_count, 0)
        self.assertGreaterEqual(rec.extraction_confidence_score, 0.80)
        self.assertFalse(rec.recommend_confirmed_jd_config)

        for item in rec.extraction_ledger:
            self.assertEqual(item["extraction_status"], "INFERRED")
            self.assertIn(item["certainty"], ("HIGH", "MODERATE", "LOW_INFERRED"))
            self.assertGreater(len(item["source_quote"]), 5)
            self.assertIn(
                item["provenance_section"],
                ("required_section", "preferred_section", "responsibilities_section", "summary_narrative"),
            )

    def test_negative_examples_and_homographs_are_excluded(self) -> None:
        """Verifies negated skills ('No SQL required'), 'Excel in a fast-paced', 'degree of ownership', and 'inventory clearance' are excluded."""
        case = next(c for c in self.corpus if c["case_id"] == "CORPUS-01-NEGATIONS-AND-HOMOGRAPHS")
        rec = jpc.parse_job_posting_text(case["raw_text"])
        extracted_names = {r["name"] for r in rec.extraction_ledger}

        for expected in case["expected_extracted_skills"]:
            self.assertIn(expected, extracted_names)

        # Negated skills & verb homographs must NOT be extracted
        self.assertNotIn("SQL", extracted_names)
        self.assertNotIn("Python", extracted_names)
        self.assertNotIn("Excel", extracted_names)
        self.assertNotIn("B.S.", extracted_names)
        self.assertEqual(rec.education_requirements, [])
        self.assertEqual(rec.security_clearance, "")

        # Verify negative exclusions ledger recorded the reasons and quotes
        excl_cats = {e["category"] for e in rec.negative_exclusions}
        self.assertIn("negated_hard_skill", excl_cats)
        self.assertIn("non_degree_idiom", excl_cats)
        self.assertIn("negated_degree", excl_cats)
        self.assertIn("non_security_clearance", excl_cats)
        self.assertIn("negated_clearance", excl_cats)

    def test_ambiguous_experience_degree_clearance_and_location_flags(self) -> None:
        """Verifies AMB-EXP-RANGE, AMB-DEG-EQUIV, AMB-CLR-CONDITIONAL, AMB-LOC-CONFLICT, and non-experience year exclusion."""
        case = next(c for c in self.corpus if c["case_id"] == "CORPUS-02-AMBIGUOUS-GATES-AND-LOCATION")
        rec = jpc.parse_job_posting_text(case["raw_text"])

        # 'For over 20 years' and '18 years of age' must be excluded; min_years_experience should come from '2-8 years' (2.0)
        self.assertEqual(rec.min_years_experience, 2.0)
        self.assertEqual(rec.max_years_experience, 8.0)

        rule_ids = {a["rule_id"] for a in rec.ambiguity_diagnostics}
        for expected_rule in case["expected_ambiguity_rules"]:
            self.assertIn(expected_rule, rule_ids)

        # Degree with equivalency clause must NOT be marked as a binary knockout gate
        bs_req = next(r for r in rec.extraction_ledger if r["name"] == "B.S.")
        self.assertFalse(bs_req["is_knockout_gate"])
        self.assertIn("degree_has_equivalent_experience_path", bs_req["ambiguity_flags"])

        # Medium ambiguity flags must trigger recommend_confirmed_jd_config = True
        self.assertTrue(rec.recommend_confirmed_jd_config)

    def test_parenthetical_acronym_and_six_sigma_belt_deduplication(self) -> None:
        """Verifies 'Average Speed of Answer (ASA)', 'Average Handle Time (AHT)', and 'Six Sigma Green Belt' are not double-counted."""
        improved_txt = (ROOT / "examples" / "sample_requisition_improved.txt").read_text(encoding="utf-8")
        rec = jpc.parse_job_posting_text(improved_txt)
        names_lower = [r["name"].lower() for r in rec.extraction_ledger]

        # Only one of ('average speed of answer', 'asa') and one of ('average handle time', 'aht') should be a requirement
        self.assertLessEqual(sum(1 for n in names_lower if n in ("average speed of answer", "asa")), 1)
        self.assertLessEqual(sum(1 for n in names_lower if n in ("average handle time", "aht")), 1)
        # 'Six Sigma Green Belt' absorbs generic 'Six Sigma'
        self.assertIn("six sigma green belt", names_lower)
        self.assertNotIn("six sigma", names_lower)

    def test_multilingual_spanish_french_and_german_extraction(self) -> None:
        """Verifies Spanish, French, and German section headers, years, alternatives, and negations."""
        for cid in ("CORPUS-03-SPANISH-WFM", "CORPUS-04-FRENCH-BI", "CORPUS-05-GERMAN-WFM"):
            case = next(c for c in self.corpus if c["case_id"] == cid)
            rec = jpc.parse_job_posting_text(case["raw_text"])
            self.assertEqual(rec.detected_language, case["expected_language"])
            self.assertGreater(len(rec.required_qualifications), 1)
            self.assertGreater(len(rec.preferred_qualifications), 0)
            self.assertGreater(len(rec.responsibilities), 0)
            self.assertIsNotNone(rec.min_years_experience)

            # Verify alternative groups ('o', 'ou', 'oder')
            for exp_alt in case["expected_alternatives"]:
                self.assertIn(exp_alt, rec.alternative_groups)

            # Verify negated skills in ES/FR/DE were excluded
            extracted_names = {r["name"] for r in rec.extraction_ledger}
            for excl in case["expected_excluded_terms"]:
                self.assertNotIn(excl, extracted_names)

            # Verify degree with ES/FR/DE equivalency is not a hard knockout gate
            bs_req = next(r for r in rec.extraction_ledger if r["name"] == "B.S.")
            self.assertFalse(bs_req["is_knockout_gate"])

            # Verify Python in Deseable / Atouts / Wünschenswert is tiered as 'preferred'
            py_req = next(r for r in rec.extraction_ledger if r["name"] == "Python")
            self.assertEqual(py_req["tier"], "preferred")
            self.assertEqual(py_req["provenance_section"], "preferred_section")


if __name__ == "__main__":
    unittest.main()
