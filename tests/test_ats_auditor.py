import json
import os
from pathlib import Path
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
            encoding="utf-8",
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



    def test_tos_compliance_router_and_parsers(self):
        import tos_compliant_collector as tcc
        self.assertEqual(set(jpc.RESTRICTED_DOMAINS.keys()), {
            "linkedin.com", "indeed.com", "simplyhired.com", "ziprecruiter.com",
            "glassdoor.com", "monster.com", "careerbuilder.com", "dice.com",
            "builtin.com", "wellfound.com", "angel.co",
        })
        for domain in jpc.RESTRICTED_DOMAINS:
            with self.assertRaises(jpc.ComplianceBlockError):
                jpc.enforce_domain_compliance(f"https://www.{domain}/job/12345")
            with self.assertRaises(jpc.ComplianceBlockError):
                jpc.collect_from_url(f"https://www.{domain}/job/12345")
            fetch_res = jpc.fetch_job_source(f"https://{domain}/viewjob?jk=abc")
            self.assertFalse(fetch_res.get("ok"))
            self.assertIn(domain, fetch_res.get("error", ""))

        proc = subprocess.run(
            [sys.executable, os.path.join(ROOT_DIR, "scripts", "tos_compliant_collector.py"), "--url", "https://www.indeed.com/viewjob?jk=123"],
            cwd=ROOT_DIR, capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("[COMPLIANCE GATE]", proc.stderr)

        raw_gh = "&amp;lt;p&amp;gt;Role Overview&amp;lt;/p&amp;gt;&amp;lt;ul&amp;gt;&amp;lt;li&amp;gt;Python &amp;amp; SQL&amp;lt;/li&amp;gt;&amp;lt;li&amp;gt;Snowflake&amp;lt;/li&amp;gt;&amp;lt;/ul&amp;gt;"
        cleaned = jpc.html_to_plain_text(raw_gh)
        self.assertIn("Role Overview", cleaned)
        self.assertIn("- Python & SQL", cleaned)
        self.assertIn("- Snowflake", cleaned)

        lever_job = tcc._parse_lever_posting("acme", {
            "id": "lev-01",
            "text": "Workforce Management Analyst",
            "categories": {"location": "Remote, US"},
            "workplaceType": "remote",
            "descriptionPlain": "Lead NICE IEX forecasting.",
            "lists": [{"text": "Requirements", "content": "<li>SQL</li><li>Power BI</li>"}],
            "salaryRange": {"min": 95000, "max": 120000, "currency": "USD", "interval": "per-year"},
            "hostedUrl": "https://jobs.lever.co/acme/lev-01",
            "createdAt": 1762000000000,
        })
        self.assertEqual(lever_job.source, "lever")
        self.assertIn("USD 95000-120000", lever_job.compensation)
        self.assertIn("- SQL", lever_job.description_text)
        rec = jpc.normalized_job_to_record(lever_job)
        self.assertEqual(rec.company_name, "acme")
        self.assertIn("SQL", rec.hard_skills)

        manual_job = tcc.load_manual_jd("Required: Python, SQL, Workforce Management")
        self.assertEqual(manual_job.source, "manual")
        self.assertIn("Workforce Management", manual_job.description_text)

    def test_load_jds_for_audit_offline_and_filtering(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jd_txt = Path(tmpdir) / "senior_wfm_analyst.txt"
            jd_txt.write_text("Job Title: Senior WFM Analyst\n- NICE IEX\n- SQL\n", encoding="utf-8")

            jobs_from_file = oaa.load_jds_for_audit(jd_file=str(jd_txt))
            self.assertEqual(len(jobs_from_file), 1)
            self.assertEqual(jobs_from_file[0]["source"], "local_file")
            self.assertEqual(jobs_from_file[0]["job_id"], "senior_wfm_analyst")
            self.assertIn("NICE IEX", jobs_from_file[0]["description_text"])

            snap_json = Path(tmpdir) / "cached_board.json"
            sample_board = [
                {"source": "greenhouse", "job_id": "1", "title": "Senior Workforce Analyst", "description_text": "- SQL"},
                {"source": "greenhouse", "job_id": "2", "title": "Frontend Engineer", "description_text": "- React"},
            ]
            snap_json.write_text(json.dumps(sample_board), encoding="utf-8")

            filtered = oaa.load_jds_for_audit(from_json=str(snap_json), title_filter=r"workforce|wfm")
            self.assertEqual(len(filtered), 1)
            self.assertEqual(filtered[0]["job_id"], "1")

            collector_mod = oaa._import_collector()
            self.assertTrue(hasattr(collector_mod, "collect_from_url"))
            self.assertTrue(hasattr(collector_mod, "fetch_greenhouse"))

            # Verify CLI loop prints header/url/attribution, scores only description_text + title, and supports descending sort + --top N
            usajobs_snap = Path(tmpdir) / "usajobs_snap.json"
            usajobs_snap.write_text(json.dumps([
                {
                    "source": "usajobs",
                    "job_id": "USA-LOW",
                    "company": "Department of Veterans Affairs",
                    "title": "Unrelated Rust Firmware Role",
                    "location": "Orlando, FL",
                    "compensation": "$88,000 - $114,000",
                    "url": "https://www.usajobs.gov/job/000001",
                    "description_text": "Required:\n- Rust\n- Embedded C\n- FPGA\n",
                    "attribution": "Data source: USAJOBS. Not endorsed by OPM.",
                },
                {
                    "source": "usajobs",
                    "job_id": "USA-HIGH",
                    "company": "Department of Veterans Affairs",
                    "title": "Senior Analytics Manager",
                    "location": "Orlando, FL",
                    "compensation": "$110,000 - $140,000",
                    "url": "https://www.usajobs.gov/job/123456",
                    "description_text": "Required:\n- Python\n- SQL\n- Snowflake\n- Power BI\n",
                    "attribution": "Data source: USAJOBS. Not endorsed by OPM.",
                },
            ]), encoding="utf-8")
            json_out_path = Path(tmpdir) / "multi_out.json"
            cli_res = subprocess.run(
                [
                    sys.executable,
                    os.path.join(ROOT_DIR, "scripts", "offline_ats_auditor.py"),
                    "--resume", os.path.join(ROOT_DIR, "examples", "sample_resume.txt"),
                    "--from-json", str(usajobs_snap),
                    "--top", "1",
                    "--json-out", str(json_out_path),
                ],
                cwd=ROOT_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            self.assertIn("## Senior Analytics Manager — Department of Veterans Affairs (`usajobs`)", cli_res.stdout)
            self.assertIn("**Source URL:** https://www.usajobs.gov/job/123456", cli_res.stdout)
            self.assertIn("> Data source: USAJOBS. Not endorsed by OPM.", cli_res.stdout)
            self.assertNotIn("Unrelated Rust Firmware Role", cli_res.stdout)
            self.assertNotIn("OPM", cli_res.stdout.split("# JD Alignment Scorecard")[1])
            saved_json = json.loads(json_out_path.read_text(encoding="utf-8"))
            self.assertEqual(len(saved_json), 1)
            self.assertEqual(saved_json[0]["job_meta"]["job_id"], "USA-HIGH")

    def test_ashby_url_collection_and_collector_file_flags(self):
        sample_ashby_payload = json.dumps({
            "jobs": [
                {
                    "id": "ashby-job-99",
                    "title": "Staff Workforce Data Scientist",
                    "location": "Remote - US",
                    "workplaceType": "Remote",
                    "publishedAt": "2026-11-01",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-job-99",
                    "applyUrl": "https://jobs.ashbyhq.com/acme/ashby-job-99/application",
                    "descriptionPlain": "Required:\n- Python\n- SQL\n- Snowflake\n- Capacity Planning\n",
                    "compensation": {
                        "scrapeableCompensationSalarySummary": "$160,000 - $190,000"
                    }
                }
            ]
        })
        from unittest import mock
        with mock.patch.object(jpc, "_http_get", return_value=(200, sample_ashby_payload)):
            fetch_res = jpc.fetch_job_source("https://jobs.ashbyhq.com/acme/ashby-job-99")
            rec = jpc.parse_from_fetch_result(fetch_res)
            self.assertEqual(rec.source_platform, "Ashby ATS")
            self.assertEqual(rec.job_id, "ashby-job-99")
            self.assertEqual(rec.job_title, "Staff Workforce Data Scientist")
            self.assertEqual(rec.company_name, "acme")
            self.assertEqual(rec.workplace_type, "Remote")
            self.assertIn("$160,000 - $190,000", rec.salary.raw_text)

        with mock.patch.object(jpc, "_http_get_json", return_value=json.loads(sample_ashby_payload)):
            norm_jobs = jpc.collect_from_url("https://jobs.ashbyhq.com/acme/ashby-job-99")
            self.assertEqual(len(norm_jobs), 1)
            self.assertEqual(norm_jobs[0].source, "ashby")
            self.assertEqual(norm_jobs[0].job_id, "ashby-job-99")

        with tempfile.TemporaryDirectory() as tmpdir:
            jd_path = Path(tmpdir) / "sample_jd.txt"
            jd_path.write_text("Job Title: Analytics Engineer\nCompany: Acme Corp\nRequired:\n- SQL\n- Python\n", encoding="utf-8")
            for flag in ("--input-file", "--jd-file", "--jd-text"):
                proc = subprocess.run(
                    [sys.executable, "-B", os.path.join(ROOT_DIR, "scripts", "job_posting_collector.py"), flag, str(jd_path)],
                    cwd=ROOT_DIR,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=True,
                )
                self.assertIn("Analytics Engineer", proc.stdout)


if __name__ == "__main__":
    unittest.main()
