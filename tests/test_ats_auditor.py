import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT_DIR, "scripts"))

import offline_ats_auditor as oaa
import job_posting_collector as jpc


class TestEnterpriseATSAuditor(unittest.TestCase):
    def test_baseline_demo_score_parity(self):
        resume_txt, reqs, align_cfg = oaa.get_demo_fixture()
        rep = oaa.run_ats_audit(resume_txt, reqs, align_cfg, reference_date=oaa.datetime(2026, 11, 1))
        self.assertAlmostEqual(rep.composite_score, 88.3, places=1)
        self.assertAlmostEqual(rep.legacy_literal_score, 82.9, places=1)
        self.assertEqual(rep.verdict, "Strong match")
        self.assertEqual(len(rep.ledger), 12)
        self.assertEqual(len(rep.recovery_plan), 6)

    def test_optimized_resume_reaches_100(self):
        with open(os.path.join(ROOT_DIR, "examples", "sample_resume_optimized.txt"), "r", encoding="utf-8") as f:
            opt_txt = f.read()
        _, reqs, align_cfg = oaa.get_demo_fixture()
        rep = oaa.run_ats_audit(opt_txt, reqs, align_cfg, reference_date=oaa.datetime(2026, 11, 1))
        self.assertAlmostEqual(rep.composite_score, 100.0, places=1)
        self.assertAlmostEqual(rep.legacy_literal_score, 100.0, places=1)
        self.assertEqual(len(rep.recovery_plan), 0)

    def test_sample_ats_config_and_replacements_parity(self):
        cfg_path = os.path.join(ROOT_DIR, "examples", "sample_ats_config.json")
        reqs, align_cfg = oaa.load_jd_config(cfg_path)
        self.assertEqual(len(reqs), 12)
        self.assertAlmostEqual(sum(r.max_item_weight for r in reqs), 60.0, places=2)

        base_resume = oaa.load_resume_file(os.path.join(ROOT_DIR, "examples", "sample_resume.txt"))
        rep_base = oaa.run_ats_audit(base_resume, reqs, align_cfg, reference_date=oaa.datetime(2026, 11, 1))
        self.assertAlmostEqual(rep_base.composite_score, 88.3, places=1)

        with open(os.path.join(ROOT_DIR, "examples", "sample_replacements.json"), "r", encoding="utf-8") as rf:
            rep_map = json.load(rf)
        rewritten = base_resume
        for old_s, new_s in rep_map.items():
            rewritten = rewritten.replace(old_s, new_s)
        rep_opt = oaa.run_ats_audit(rewritten, reqs, align_cfg, reference_date=oaa.datetime(2026, 11, 1))
        self.assertAlmostEqual(rep_opt.composite_score, 100.0, places=1)
        self.assertAlmostEqual(rep_opt.legacy_literal_score, 100.0, places=1)

    def test_combined_headers_and_wfm_acronyms(self):
        lines = [
            "JANE DOE",
            "Orlando, FL | (555) 234-5678 | jane.doe@example.com",
            "SENIOR WORKFORCE MANAGEMENT ANALYST",
            "Results-driven Workforce Analyst with 7+ years optimizing NICE IEX scheduling, SLA/ASA, and AHT across enterprise healthcare operations.",
            "CORE COMPETENCIES & TECHNICAL SKILLS",
            "Workforce Management: NICE IEX, Genesys Cloud, Real-Time Adherence, Shrinkage, SQL, Power BI",
            "PROFESSIONAL EXPERIENCE",
            "IMG Health Clinic | Workforce Analyst | 01/2020 - Present",
            "- Improved schedule adherence by 14% across 450 contact center agents using NICE IEX and SQL.",
            "EDUCATION",
            "Bachelor of Science (B.S.) in Business Analytics",
        ]
        sample_wfm = chr(10).join(lines)
        parsed = oaa.parse_resume(sample_wfm, reference_date=oaa.datetime(2026, 11, 1))
        self.assertGreater(len(parsed.sections["summary"]), 40)
        self.assertGreater(len(parsed.sections["skills"]), 30)
        self.assertEqual(parsed.schema_score, 10.0)
        self.assertEqual(parsed.schema_deductions, [])

    def test_docx_load_and_export_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src_docx = os.path.join(tmpdir, "sample.docx")
            dst_docx = os.path.join(tmpdir, "updated.docx")
            xml_content = (
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                '<w:body>'
                '<w:p><w:r><w:t>Designed models for capacity planning across 10 sites.</w:t></w:r></w:p>'
                '</w:body></w:document>'
            )
            with zipfile.ZipFile(src_docx, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("word/document.xml", xml_content.encode("utf-8"))

            loaded = oaa.load_resume_file(src_docx)
            self.assertIn("capacity planning", loaded)

            count = oaa.export_updated_docx(
                src_docx, dst_docx, {"capacity planning": "Workforce Capacity Modeling"}
            )
            self.assertEqual(count, 1)
            reloaded = oaa.load_resume_file(dst_docx)
            self.assertIn("Workforce Capacity Modeling", reloaded)

    def test_job_posting_collector_demo_and_schema(self):
        res = subprocess.run(
            [sys.executable, os.path.join(ROOT_DIR, "scripts", "job_posting_collector.py"), "--demo"],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Senior Analytics Manager", res.stdout)
        with open(os.path.join(ROOT_DIR, "examples", "sample_job_record.json"), "r", encoding="utf-8") as jf:
            rec_data = json.load(jf)
        self.assertIn("ats_config", rec_data)
        self.assertGreater(len(rec_data["ats_config"]["requirements"]), 5)

    def test_interactive_workbench_html_integrity(self):
        demo_path = os.path.join(ROOT_DIR, "demo.html")
        docs_path = os.path.join(ROOT_DIR, "docs", "index.html")
        with open(demo_path, "r", encoding="utf-8") as f1, open(docs_path, "r", encoding="utf-8") as f2:
            demo_html = f1.read()
            docs_html = f2.read()
        self.assertEqual(demo_html, docs_html)
        self.assertTrue(demo_html.strip().endswith("</html>"))
        for required_fn in ("function renderAtsTable()", "function toggleAtsItem(", "function applyAllAtsFixes()", "function resetAtsSim()"):
            self.assertIn(required_fn, demo_html)
        self.assertNotIn("C:/Users/", demo_html)


if __name__ == "__main__":
    unittest.main()
