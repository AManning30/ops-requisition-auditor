"""Tests for the Explainable Screening Assistant (Employer Edition, Module 2). No network access required."""
from datetime import datetime
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import offline_ats_auditor as oaa  # noqa: E402
import explainable_screening_assistant as esa  # noqa: E402


class TestExplainableScreeningAssistant(unittest.TestCase):
    def test_blind_mode_redacts_pii_zip_and_grad_years_without_distorting_score(self):
        raw_resume = (
            "JANE DOE\n"
            "Chicago, IL 60601-2345 | jane.doe@example.com | (312) 555-0199 | https://linkedin.com/in/janedoe\n\n"
            "Professional Summary\n"
            "Senior Analytics Manager with 8 years of experience.\n\n"
            "Work Experience\n"
            "Senior Analytics Manager — Acme Corp (01/2020 - Present)\n"
            "- Spearheaded enterprise demand forecasting models in Python and SQL across 420 distribution centers.\n\n"
            "Education\n"
            "B.S. in Industrial Engineering — University of Illinois (05/2016), Class of 2016\n\n"
            "Technical & Core Skills\n"
            "Python, SQL, Demand Forecasting\n"
        )
        redacted, counts = esa.redact_resume_for_blind_screening(raw_resume)
        self.assertNotIn("JANE DOE", redacted)
        self.assertNotIn("jane.doe@example.com", redacted)
        self.assertNotIn("555-0199", redacted)
        self.assertNotIn("linkedin.com", redacted)
        self.assertNotIn("60601", redacted)
        self.assertNotIn("2016", redacted)
        # Work experience date range must remain intact for tenure math
        self.assertIn("01/2020 - Present", redacted)
        self.assertGreaterEqual(counts["name_header"], 1)
        self.assertGreaterEqual(counts["email"], 1)
        self.assertGreaterEqual(counts["phone"], 1)
        self.assertGreaterEqual(counts["url"], 1)
        self.assertGreaterEqual(counts["zip_code"], 1)
        self.assertGreaterEqual(counts["education_grad_date"], 1)

        # Verify score parity between blind mode and non-blind mode on the canonical fixtures
        cfg_path = os.path.join(ROOT_DIR, "examples", "sample_ats_config.json")
        reqs, align_cfg = oaa.load_jd_config(cfg_path)
        base_txt = oaa.load_resume_file(os.path.join(ROOT_DIR, "examples", "sample_resume.txt"))
        opt_txt = oaa.load_resume_file(os.path.join(ROOT_DIR, "examples", "sample_resume_optimized.txt"))
        ref_dt = datetime(2026, 11, 1)

        batch_blind = esa.screen_batch(
            [("OPT", "opt.txt", opt_txt), ("BASE", "base.txt", base_txt)],
            reqs, align_cfg, ref_dt, blind_mode=True,
        )
        batch_raw = esa.screen_batch(
            [("OPT", "opt.txt", opt_txt), ("BASE", "base.txt", base_txt)],
            reqs, align_cfg, ref_dt, blind_mode=False,
        )
        self.assertEqual(batch_blind.candidates[0].composite_score, 100.0)
        self.assertEqual(batch_blind.candidates[1].composite_score, 88.3)
        self.assertEqual(batch_blind.candidates[0].composite_score, batch_raw.candidates[0].composite_score)
        self.assertEqual(batch_blind.candidates[1].composite_score, batch_raw.candidates[1].composite_score)

    def test_never_auto_rejects_and_preserves_human_override_audit_trail(self):
        candidates, reqs, align_cfg, decisions, demographics = esa.get_demo_batch()
        batch = esa.screen_batch(
            candidates, reqs, align_cfg, datetime(2026, 11, 1),
            blind_mode=True, decisions_map=decisions, demographics_map=demographics,
        )
        self.assertEqual(batch.total_candidates, 4)
        # Candidate 4 scored below threshold and is missing the B.S. gate, but is NEVER auto-rejected
        c4 = next(c for c in batch.candidates if c.candidate_id == "CAND-004")
        self.assertLess(c4.composite_score, 65.0)
        self.assertFalse(c4.knockout_passed)
        self.assertEqual(c4.decision.status, "PENDING_HUMAN_REVIEW")
        self.assertIn("human review required", c4.recommendation_band)

        # Candidate 3 is borderline (74.3) with a recorded human ADVANCE override
        c3 = next(c for c in batch.candidates if c.candidate_id == "CAND-003")
        self.assertEqual(c3.decision.status, "ADVANCE")
        self.assertEqual(c3.decision.reviewer, "recruiter_j_smith")
        self.assertIn("Human override", c3.decision.override_reason)

    def test_colorado_nyc_candidate_factor_disclosure_notice(self):
        candidates, reqs, align_cfg, decisions, _ = esa.get_demo_batch()
        batch = esa.screen_batch(candidates, reqs, align_cfg, datetime(2026, 11, 1), decisions_map=decisions)
        c2 = next(c for c in batch.candidates if c.candidate_id == "CAND-002")
        notice = esa.render_candidate_notice(batch, c2)
        self.assertIn("# Candidate Factor & Gap Disclosure Notice: `CAND-002`", notice)
        self.assertIn("## 1. Automated Assessment & Human Oversight Disclosure", notice)
        self.assertIn("## 2. Principal Factors & Component Breakdown", notice)
        self.assertIn("## 3. Qualifications Verified at Full Credit", notice)
        self.assertIn("## 4. Qualifications Partially Verified", notice)
        self.assertIn("## 5. Qualifications Not Verified on Submitted Application (Right to Clarify / Correct)", notice)
        self.assertIn("Six Sigma Black Belt", notice)
        self.assertIn("not legal advice", notice)

    def test_four_fifths_selection_rate_and_impact_ratio_table(self):
        # Create 10 synthetic candidate records (5 Group A: 4 selected = 80%; 5 Group B: 2 selected = 40% -> 0.50 ratio -> FLAG)
        records = []
        demographics = {}
        for i in range(1, 6):
            cid = f"A-{i}"
            score = 85.0 if i <= 4 else 55.0
            records.append(esa.CandidateScreeningRecord(
                candidate_id=cid, source_file=cid, blind_mode=True, raw_sha256="x", scored_sha256="y",
                redactions_applied={}, rank=i, composite_score=score, literal_score=score,
                recommendation_band="", knockout_passed=True, unverified_gates=[],
                comp1_kw_score=0, comp2_context_score=0, comp3_alignment_score=0,
                comp4_knockout_score=10, comp5_schema_score=10,
                verified_requirements=[], partial_requirements=[], unverified_requirements=[],
            ))
            demographics[cid] = {"cohort": "Group A"}
        for i in range(1, 6):
            cid = f"B-{i}"
            score = 85.0 if i <= 2 else 55.0
            records.append(esa.CandidateScreeningRecord(
                candidate_id=cid, source_file=cid, blind_mode=True, raw_sha256="x", scored_sha256="y",
                redactions_applied={}, rank=i + 5, composite_score=score, literal_score=score,
                recommendation_band="", knockout_passed=True, unverified_gates=[],
                comp1_kw_score=0, comp2_context_score=0, comp3_alignment_score=0,
                comp4_knockout_score=10, comp5_schema_score=10,
                verified_requirements=[], partial_requirements=[], unverified_requirements=[],
            ))
            demographics[cid] = {"cohort": "Group B"}

        rows = esa.compute_selection_rate_tables(records, demographics, threshold=80.0)
        self.assertEqual(len(rows), 2)
        row_a = next(r for r in rows if r.group == "Group A")
        row_b = next(r for r in rows if r.group == "Group B")
        self.assertAlmostEqual(row_a.selection_rate, 0.8)
        self.assertAlmostEqual(row_a.impact_ratio, 1.0)
        self.assertEqual(row_a.four_fifths_status, "PASS (>=0.80)")
        self.assertAlmostEqual(row_b.selection_rate, 0.4)
        self.assertAlmostEqual(row_b.impact_ratio, 0.5)
        self.assertIn("FLAG (<0.80", row_b.four_fifths_status)

    def test_cli_demo_and_directory_screening(self):
        script = os.path.join(SCRIPTS_DIR, "explainable_screening_assistant.py")
        demo_proc = subprocess.run(
            [sys.executable, "-B", script, "--demo"],
            capture_output=True, text=True, encoding="utf-8", check=True,
        )
        self.assertIn("# Explainable Screening Shortlist & Audit Report: Senior Analytics Manager", demo_proc.stdout)
        self.assertIn("CAND-001", demo_proc.stdout)
        self.assertIn("Adverse-Impact & Four-Fifths", demo_proc.stdout)

        with tempfile.TemporaryDirectory() as tmp:
            res_dir = os.path.join(tmp, "resumes")
            os.makedirs(res_dir)
            for name, src in [
                ("cand_alpha.txt", os.path.join(ROOT_DIR, "examples", "sample_resume.txt")),
                ("cand_beta.txt", os.path.join(ROOT_DIR, "examples", "sample_resume_optimized.txt")),
            ]:
                with open(src, "r", encoding="utf-8") as f_in, open(os.path.join(res_dir, name), "w", encoding="utf-8") as f_out:
                    f_out.write(f_in.read())

            out_md = os.path.join(tmp, "batch.md")
            out_json = os.path.join(tmp, "batch.json")
            out_csv = os.path.join(tmp, "batch.csv")
            notices_dir = os.path.join(tmp, "notices")
            rc = esa.main([
                "--resumes-dir", res_dir,
                "--jd-config", os.path.join(ROOT_DIR, "examples", "sample_ats_config.json"),
                "--reference-date", "2026-11-01",
                "--notices-dir", notices_dir,
                "--output", out_md,
                "--json-out", out_json,
                "--csv-out", out_csv,
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(out_md))
            self.assertTrue(os.path.isfile(out_json))
            self.assertTrue(os.path.isfile(out_csv))
            with open(out_csv, "r", encoding="utf-8") as fh:
                csv_txt = fh.read()
            self.assertIn("candidate_id,composite_score", csv_txt)
            self.assertIn("CAND_BETA,100.00", csv_txt)
            self.assertTrue(os.path.isfile(os.path.join(notices_dir, "CAND_ALPHA_disclosure_notice.md")))
            self.assertTrue(os.path.isfile(os.path.join(notices_dir, "CAND_BETA_disclosure_notice.md")))
            with open(out_json, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["total_candidates"], 2)
            self.assertEqual(data["candidates"][0]["composite_score"], 100.0)
            self.assertEqual(data["candidates"][1]["composite_score"], 88.3)


if __name__ == "__main__":
    unittest.main()
