"""Tests for the Requisition Quality Audit (Employer Edition, Module 1). No network access required."""
import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(ROOT_DIR, "scripts")
sys.path.insert(0, SCRIPTS_DIR)

import job_posting_collector as jpc  # noqa: E402
import requisition_quality_auditor as rqa  # noqa: E402

UNICORN = os.path.join(ROOT_DIR, "examples", "sample_requisition_unicorn.txt")
IMPROVED = os.path.join(ROOT_DIR, "examples", "sample_requisition_improved.txt")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _rules(audit):
    return {f.rule_id for f in audit.findings}


class TestRequisitionQualityAuditor(unittest.TestCase):
    def test_unicorn_requisition_scores_low_with_expected_findings(self):
        audit = rqa.audit_text(_read(UNICORN))
        self.assertLess(audit.score, 50.0)
        self.assertEqual(audit.band, "High hidden-worker risk")
        for rule in ("G1", "G3", "G4", "G6", "L1", "L2", "L3", "C2", "T2", "P1", "P3", "P4"):
            self.assertIn(rule, _rules(audit), f"expected rule {rule}")
        self.assertGreaterEqual(sum(1 for f in audit.findings if f.rule_id == "L3"), 2)
        # Every scored finding carries a quote and a suggestion.
        for f in audit.findings:
            self.assertTrue(f.quote)
            self.assertTrue(f.suggestion)

    def test_improved_requisition_scores_high(self):
        audit = rqa.audit_text(_read(IMPROVED))
        self.assertGreaterEqual(audit.score, 90.0)
        self.assertEqual(audit.band, "Clear & inclusive")
        for rule in ("G1", "G3", "G6", "L3", "P1", "P4"):
            self.assertNotIn(rule, _rules(audit))

    def test_component_math_sums_to_score(self):
        audit = rqa.audit_text(_read(UNICORN))
        self.assertAlmostEqual(sum(c.earned for c in audit.components), audit.score, places=1)
        for c in audit.components:
            self.assertLessEqual(c.deducted, c.max_points)
            self.assertGreaterEqual(c.earned, 0.0)

    def test_deterministic_output(self):
        text = _read(UNICORN)
        a1 = json.dumps(rqa.audit_to_dict(rqa.audit_text(text)), sort_keys=True, default=str)
        a2 = json.dumps(rqa.audit_to_dict(rqa.audit_text(text)), sort_keys=True, default=str)
        self.assertEqual(a1, a2)

    def test_equivalency_clause_clears_degree_gate(self):
        base = ("Job Title: Data Analyst\nRequired Qualifications\n- Bachelor's degree in Statistics required.\n"
                "- 2+ years of SQL experience.\n")
        self.assertIn("G1", _rules(rqa.audit_text(base)))
        fixed = base.replace("required.", "or an equivalent combination of education and experience.")
        self.assertNotIn("G1", _rules(rqa.audit_text(fixed)))

    def test_alternatives_are_not_flagged_as_competing_products(self):
        both = "Job Title: BI Analyst\nRequired Qualifications\n- Advanced Power BI and Tableau development.\n"
        either = "Job Title: BI Analyst\nRequired Qualifications\n- Advanced Power BI or Tableau development.\n"
        self.assertIn("L3", _rules(rqa.audit_text(both)))
        self.assertNotIn("L3", _rules(rqa.audit_text(either)))

    def test_contained_product_names_are_not_double_counted(self):
        found = rqa.find_products("Experience with NICE IEX and SAP S/4HANA.")
        self.assertEqual(found.get("Contact-center WFM platform"), ["NICE IEX"])
        self.assertEqual(found.get("ERP"), ["SAP S/4HANA"])

    # --- Regression tests from the field test on 46 public requisitions (rubric rqs-0.2) ---

    def test_degree_of_ownership_is_not_a_degree_gate(self):
        text = ("Job Title: Operations Analyst\nRequired Qualifications\n"
                "- Operate with a high degree of ownership and urgency to hit deadlines.\n- 2+ years of SQL experience.\n")
        self.assertNotIn("G1", _rules(rqa.audit_text(text)))

    def test_student_enrolment_is_not_a_degree_gate(self):
        text = "Job Title: MBA Intern\nRequired Qualifications\n- Currently enrolled in a full-time MBA program.\n"
        self.assertNotIn("G1", _rules(rqa.audit_text(text)))

    def test_optional_advanced_degree_is_not_flagged(self):
        text = ("Job Title: Analytics Lead\nRequired Qualifications\n"
                "- 6+ years of experience and a degree (Masters or PhD is a plus) in a quantitative field.\n")
        rules = _rules(rqa.audit_text(text))
        self.assertIn("G1", rules)
        self.assertNotIn("G2", rules)

    def test_soft_year_wording_is_not_a_gate(self):
        text = ("Job Title: Operations Analyst II\nRequired Qualifications\n"
                "- Ideally 8+ years of experience in operations analytics.\n")
        self.assertNotIn("G3", _rules(rqa.audit_text(text)))

    def test_ambiguous_level_years_are_low_severity(self):
        text = "Job Title: Regulatory Operations Lead\nRequired Qualifications\n- 10+ years of experience in regulatory operations.\n"
        g3 = [f for f in rqa.audit_text(text).findings if f.rule_id == "G3"]
        self.assertEqual(len(g3), 1)
        self.assertEqual(g3[0].severity, "low")
        self.assertEqual(g3[0].points, 3.0)

    def test_non_usd_pay_ranges_are_detected(self):
        for pay in ("\u20ac114.000\u2014\u20ac142.000 EUR", "$105,400\u2014$155,000 USD", "GBP 94,000 - 108,000"):
            text = f"Job Title: Analyst\nPay Range\n{pay}\nRequired Qualifications\n- SQL.\n"
            self.assertNotIn("P1", _rules(rqa.audit_text(text)), pay)

    def test_compound_requirement_threshold(self):
        long_line = "- " + " ".join(["word"] * 45)
        text = f"Job Title: Analyst\nRequired Qualifications\n{long_line}\n- SQL.\n"
        self.assertIn("C3", _rules(rqa.audit_text(text)))

    def test_board_audits_do_not_penalise_missing_eeo_text(self):
        job = jpc.NormalizedJob("greenhouse", "9", "Acme", "Operations Analyst", "Austin, TX", "", "", "", "",
                                "Required Qualifications\n- 2+ years of SQL experience.\n")
        p4 = [f for f in rqa.audit_normalized_job(job).findings if f.rule_id == "P4"]
        self.assertTrue(p4)
        self.assertEqual(p4[0].points, 0.0)

    def test_labor_certification_wording_is_info_only(self):
        text = ("Job Title: Data Analyst\nRequired Qualifications\n"
                "- Minimum Requirements for Position: Bachelor's degree or foreign equivalent in Computer Science.\n")
        rules = _rules(rqa.audit_text(text))
        self.assertIn("G1P", rules)
        self.assertNotIn("G1", rules)

    def test_long_list_is_not_double_penalised(self):
        items_10 = "\n".join(f"- Requirement number {i} for this role." for i in range(10))
        self.assertNotIn("L1", _rules(rqa.audit_text(f"Job Title: Analyst\nRequired Qualifications\n{items_10}\n")))
        items_12 = "\n".join(f"- Requirement number {i} for this role." for i in range(12))
        rules = [f.rule_id for f in rqa.audit_text(f"Job Title: Analyst\nRequired Qualifications\n{items_12}\n").findings]
        self.assertIn("L1", rules)
        self.assertNotIn("L4", rules)

    def test_specialist_domain_allowlist_exempts_accounting_and_security_certs(self):
        sec_text = (
            "Job Title: Security Operations Analyst\nRequired Qualifications\n"
            "- Experience with SIEM, SOAR, EDR, DLP, CSPM, NIST, CISSP, CISM, GSEC, and SOC operations.\n"
        )
        self.assertNotIn("P2", _rules(rqa.audit_text(sec_text)))
        fin_text = (
            "Job Title: Accounting & Payment Compliance Manager\nRequired Qualifications\n"
            "- Experience with CPA, SOX, COSO, FCPA, GAAP, IFRS, FINRA, AML, KYC, BSA, ACH, and NACHA.\n"
        )
        self.assertNotIn("P2", _rules(rqa.audit_text(fin_text)))

    def test_age_and_company_history_years_are_not_experience_gates(self):
        text = (
            "Job Title: Assistant Restaurant Manager\n"
            "For nearly 20 years, we've built the leading fast-casual restaurant brand.\n"
            "Required Qualifications\n"
            "- Must be 18 years of age or older.\n"
            "- 2+ years of restaurant supervision experience.\n"
        )
        audit = rqa.audit_text(text)
        self.assertNotIn("G3", _rules(audit))
        self.assertEqual(audit.stats["required_years"], 2.0)

    def test_parenthetical_and_conversational_headers_are_recognised(self):
        self.assertEqual(jpc.classify_section_header("About this roll* (Responsibilities)"), "responsibilities")
        self.assertEqual(jpc.classify_section_header("Must-have pantry staples (Minimum Qualifications)"), "required")
        self.assertEqual(jpc.classify_section_header("Optional garnishes (Preferred Qualifications)"), "preferred")
        self.assertEqual(jpc.classify_section_header("What will I be doing?"), "responsibilities")
        self.assertEqual(jpc.classify_section_header("What skills do I need?"), "required")

    def test_curly_apostrophe_and_common_headers_are_recognised(self):
        self.assertEqual(jpc.classify_section_header("What You\u2019ll Do"), "responsibilities")
        self.assertEqual(jpc.classify_section_header("Your Expertise:"), "required")
        self.assertEqual(jpc.classify_section_header("A Typical Day:"), "responsibilities")
        self.assertEqual(jpc.classify_section_header("We\u2019re excited about you because\u2026"), "required")
        self.assertEqual(jpc.classify_section_header("Compensation"), "benefits")
        self.assertEqual(jpc.classify_section_header("Where You'll Be"), "other")

    def test_level_detection_and_benchmark_override(self):
        self.assertEqual(rqa.detect_level("Workforce Management Analyst II"), "level_2")
        self.assertEqual(rqa.detect_level("Senior Manager, Capacity Planning"), "senior_manager")
        self.assertEqual(rqa.detect_level("Junior Scheduler"), "entry")
        text = _read(UNICORN)
        self.assertIn("G3", _rules(rqa.audit_text(text)))
        self.assertNotIn("G3", _rules(rqa.audit_text(text, benchmarks={"level_2": 8})))

    def test_projection_applies_only_concrete_edits(self):
        audit = rqa.audit_text(_read(UNICORN))
        self.assertIsNotNone(audit.projected_score)
        self.assertGreater(audit.projected_score, audit.score)
        self.assertIn("equivalent combination of education and experience", audit.improved_text)
        self.assertNotIn("no gaps in employment", audit.improved_text.lower())
        self.assertIn("equal opportunity employer", audit.improved_text)
        self.assertNotIn("[INSERT", audit.improved_text)  # placeholders are never auto-inserted

    def test_markdown_report_structure(self):
        md = rqa.render_markdown(rqa.audit_text(_read(UNICORN)))
        self.assertTrue(md.startswith("# Requisition Quality Report: Workforce Management Analyst II"))
        for heading in ("## 1. Summary", "## 2. Component Breakdown", "## 3. Findings Ledger",
                        "## 4. Automatic Edits", "## 5. Needs a Human Decision", "## 6. Draft With Automatic Edits Applied"):
            self.assertIn(heading, md)
        self.assertIn("not legal advice", md)

    def test_render_rqs_csv_includes_level_detected_and_sha(self):
        import csv
        import io
        audit = rqa.audit_text(_read(UNICORN))
        csv_text = rqa.render_rqs_csv([audit])
        rows = list(csv.DictReader(io.StringIO(csv_text)))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["input_sha256"], audit.input_sha256[:16])
        self.assertEqual(rows[0]["level_detected"], "level II")
        self.assertEqual(rows[0]["title"], "Workforce Management Analyst II - WFM Ops - L4")

    def test_portfolio_audit_with_stubbed_board(self):
        def fake_greenhouse(token, job_id=None):
            return [
                jpc.NormalizedJob("greenhouse", "1", "Northstar", "Workforce Management Analyst II", "St. Louis, MO",
                                  "Hybrid", "", "https://example.invalid/1", "2026-11-01", _read(UNICORN)),
                jpc.NormalizedJob("greenhouse", "2", "Northstar", "Workforce Management Analyst II", "St. Louis, MO",
                                  "Hybrid", "$62,000 - $74,000 per year", "https://example.invalid/2", "2026-11-01",
                                  _read(IMPROVED)),
            ]

        original = jpc.fetch_greenhouse
        jpc.fetch_greenhouse = fake_greenhouse
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out_md = os.path.join(tmp, "portfolio.md")
                out_json = os.path.join(tmp, "portfolio.json")
                rc = rqa.main(["--greenhouse", "northstar", "--output", out_md, "--json-out", out_json])
                self.assertEqual(rc, 0)
                md = _read(out_md)
                data = json.loads(_read(out_json))
        finally:
            jpc.fetch_greenhouse = original
        self.assertIn("# Requisition Portfolio Audit: Greenhouse board 'northstar'", md)
        self.assertIn("**Requisitions audited:** 2", md)
        scores = [a["score"] for a in data["audits"]]
        self.assertEqual(len(scores), 2)
        self.assertLess(min(scores), 50.0)

    def test_cli_demo_and_compliance_gate(self):
        script = os.path.join(SCRIPTS_DIR, "requisition_quality_auditor.py")
        demo = subprocess.run([sys.executable, "-B", script, "--demo"], capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(demo.returncode, 0, demo.stderr)
        self.assertIn("Requisition Quality Score (RQS)", demo.stdout)
        blocked = subprocess.run([sys.executable, "-B", script, "--url", "https://www.linkedin.com/jobs/view/1"],
                                 capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(blocked.returncode, 2)
        self.assertIn("[COMPLIANCE GATE]", blocked.stderr)


if __name__ == "__main__":
    unittest.main()
