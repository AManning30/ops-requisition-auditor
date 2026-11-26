#!/usr/bin/env python3
"""
Unit & deterministic fuzz tests for Resume Parser Coverage, DOCX/PDF/Table/OCR Loaders,
Multilingual Dates & Section Headers, Career-Gap Exclusion, and Transferred Skills vs.
Competing Products (`offline_ats_auditor.py` & `parser_benchmark_suite.py`).
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import offline_ats_auditor as oaa  # noqa: E402
import parser_benchmark_suite as pbs  # noqa: E402


class TestParserCorpusAndFuzzSuite(unittest.TestCase):
    def test_eleven_case_multiformat_multilingual_corpus_passes_100_percent(self) -> None:
        rep = pbs.run_full_parser_benchmark(
            reference_date="2026-11-01",
            fuzz_iterations=60,
            fuzz_seed=20261101,
        )
        self.assertEqual(rep.total_corpus_cases, 11)
        for r in rep.case_results:
            self.assertTrue(r.passed, f"Corpus case {r.case_id} failed: {r.failure_reasons}")
        self.assertEqual(rep.passed_corpus_cases, 11)
        self.assertEqual(rep.corpus_pass_rate, 1.0)
        self.assertTrue(rep.fuzz_summary.all_passed)
        self.assertEqual(rep.fuzz_summary.crashes, 0)

    def test_docx_headers_tables_textboxes_and_pdf_ocr_fallbacks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. DOCX with header, 2-column table, textbox, and footer
            docx_bytes = pbs.build_synthetic_docx_bytes(
                header_text="ELENA VANCE | elena@example.com | (314) 555-0199",
                body_paragraphs=["Career Profile", "Senior WFM Lead."],
                table_rows=[
                    ["Professional Background", "Senior WFM Lead — Acme (01/2021 - Present)"],
                    ["", "- Built Demand Forecasting models in SQL and Python."],
                    ["Academic Credentials", "B.S. in Industrial Engineering"],
                    ["Technical Proficiencies", "SQL, Python, Demand Forecasting"],
                ],
                textbox_text="Six Sigma Black Belt",
                footer_text="Confidential Resume",
            )
            docx_path = os.path.join(tmpdir, "test_resume.docx")
            with open(docx_path, "wb") as fh:
                fh.write(docx_bytes)

            txt_docx, diag_docx = oaa.extract_resume_with_ocr_fallback(docx_path)
            self.assertTrue(diag_docx["docx_header_footer_extracted"])
            self.assertEqual(diag_docx["docx_tables_extracted"], 1)
            self.assertGreaterEqual(diag_docx["docx_textboxes_extracted"], 1)
            self.assertIn("ELENA VANCE", txt_docx)
            self.assertIn("Six Sigma Black Belt", txt_docx)

            parsed_docx = oaa.parse_resume(txt_docx, reference_date=datetime(2026, 11, 1))
            self.assertIn("Demand Forecasting", parsed_docx.sections["experience"])
            self.assertIn("B.S.", parsed_docx.sections["education"])
            self.assertIn("SQL, Python", parsed_docx.sections["skills"])

            # 2. FlateDecode PDF decoded via stdlib stream fallback
            pdf_bytes = pbs.build_synthetic_pdf_bytes(
                [
                    "JORDAN LEE",
                    "Work History",
                    "WFM Analyst - Apex (01/2022 - Present)",
                    "- Built SQL and Python forecasting models.",
                    "Education",
                    "B.S. in Mathematics",
                    "Skills",
                    "SQL, Python",
                ],
                compress_flate=True,
                scanned_image_only=False,
            )
            pdf_path = os.path.join(tmpdir, "flate_resume.pdf")
            with open(pdf_path, "wb") as fh:
                fh.write(pdf_bytes)
            txt_pdf, diag_pdf = oaa.extract_resume_with_ocr_fallback(pdf_path)
            self.assertIn("JORDAN LEE", txt_pdf)
            self.assertIn("WFM Analyst", txt_pdf)
            self.assertFalse(diag_pdf["is_scanned_document"])

            # 3. Scanned image-only PDF without sidecar -> OCR_REQUIRED_SCANNED_PDF
            scan_bytes = pbs.build_synthetic_pdf_bytes([], scanned_image_only=True)
            scan_path = os.path.join(tmpdir, "scanned.pdf")
            with open(scan_path, "wb") as fh:
                fh.write(scan_bytes)
            txt_scan, diag_scan = oaa.extract_resume_with_ocr_fallback(scan_path)
            self.assertTrue(diag_scan["is_scanned_document"])
            self.assertEqual(diag_scan["ocr_fallback_status"], "OCR_REQUIRED_SCANNED_PDF")
            self.assertIn("SCANNED_DOCUMENT_OCR_REQUIRED", txt_scan)

            # 4. Scanned image-only PDF with sidecar .ocr.txt -> RECOVERED_VIA_SIDECAR_OCR_TXT
            with open(f"{scan_path}.ocr.txt", "w", encoding="utf-8") as fh:
                fh.write("Work Experience\nWFM Analyst (01/2021 - Present)\n- SQL and Python models.\n")
            txt_rec, diag_rec = oaa.extract_resume_with_ocr_fallback(scan_path)
            self.assertTrue(diag_rec["ocr_fallback_used"])
            self.assertEqual(diag_rec["ocr_fallback_status"], "RECOVERED_VIA_SIDECAR_OCR_TXT")
            self.assertIn("SQL and Python models", txt_rec)

    def test_overlapping_role_deduplication_and_career_gap_exclusion(self) -> None:
        resume_txt = (
            "TAYLOR BROOKS\n"
            "Work Experience\n"
            "Senior WFM Analyst — Alpha (01/2023 - 01/2026)\n"
            "- Led Demand Forecasting.\n"
            "Planned Career Break — Parental Leave & Family Caregiving (01/2022 - 01/2023)\n"
            "- Full-time parental leave.\n"
            "WFM Analyst — Beta (01/2020 - 01/2022)\n"
            "- Built SQL reports.\n"
            "Concurrent WFM Consultant — Gamma (06/2020 - 06/2021)\n"
            "- Overlapping part-time consulting.\n"
        )
        parsed = oaa.parse_resume(resume_txt, reference_date=datetime(2026, 1, 1))
        # Net work tenure is (2023..2026 = 3.0y) + (2020..2022 = 2.0y) = 5.0y
        # The 1.0y concurrent overlap (06/2020..06/2021) is deduplicated, and the 1.0y Parental Leave is excluded!
        self.assertAlmostEqual(parsed.total_relevant_tenure_years, 5.0, delta=0.1)
        self.assertAlmostEqual(parsed.overlapping_years_deduplicated, 1.0, delta=0.1)
        self.assertEqual(len(parsed.career_gaps), 1)
        self.assertTrue(parsed.career_gaps[0].is_career_gap)

    def test_transferred_skills_vs_competing_products_classification(self) -> None:
        resume_txt = (
            "CASEY SMITH\n"
            "Work Experience\n"
            "WFM Planner — Apex (01/2021 - Present)\n"
            "- Built capacity planning models using NICE IEX and Tableau.\n"
            "Education\n"
            "B.S. in Statistics\n"
            "Skills\n"
            "NICE IEX, Tableau, capacity planning\n"
        )
        reqs = [
            oaa.JDRequirement(name="WFM Platform", category="hard_skill", tier="required"),
            oaa.JDRequirement(name="Verint", category="hard_skill", tier="required"),
            oaa.JDRequirement(name="Power BI", category="hard_skill", tier="required"),
            oaa.JDRequirement(
                name="Workforce Capacity Modeling",
                category="hard_skill",
                tier="required",
                synonyms=["capacity planning"],
            ),
            oaa.JDRequirement(name="Kubernetes", category="hard_skill", tier="preferred"),
        ]
        align_cfg = oaa.AlignmentConfig(target_title="WFM Planner", min_years_required=3.0)
        rep = oaa.run_ats_audit(resume_txt, reqs, align_cfg, reference_date=datetime(2026, 11, 1))
        by_name = {row.requirement.name: row for row in rep.ledger}

        # 1. Generic 'WFM Platform' is satisfied at full 1.0x credit by specific 'NICE IEX'
        self.assertEqual(by_name["WFM Platform"].match_credit, 1.0)
        self.assertEqual(by_name["WFM Platform"].matched_term, "NICE IEX")
        self.assertEqual(by_name["WFM Platform"].transferability_class, "GENERIC_CATEGORY_SATISFIED_BY_SPECIFIC_TOOL")

        # 2. Specific 'Verint' earns 0.0x credit (competing product 'NICE IEX' is flagged but never given synonym credit)
        self.assertEqual(by_name["Verint"].match_credit, 0.0)
        self.assertEqual(by_name["Verint"].transferability_class, "COMPETING_PRODUCT_NO_CREDIT")
        self.assertEqual(by_name["Verint"].adjacent_competing_product, "NICE IEX")

        # 3. Specific 'Power BI' earns 0.0x credit (competing product 'Tableau' is flagged)
        self.assertEqual(by_name["Power BI"].match_credit, 0.0)
        self.assertEqual(by_name["Power BI"].transferability_class, "COMPETING_PRODUCT_NO_CREDIT")
        self.assertEqual(by_name["Power BI"].adjacent_competing_product, "Tableau")

        # 4. Same-capability synonym 'capacity planning' earns 0.5x credit for 'Workforce Capacity Modeling'
        self.assertEqual(by_name["Workforce Capacity Modeling"].match_credit, 0.5)
        self.assertEqual(by_name["Workforce Capacity Modeling"].transferability_class, "TRANSFERRED_METHODOLOGY_SYNONYM")

        # 5. Unrelated missing tool 'Kubernetes' is classified as UNVERIFIED_TRUE_GAP
        self.assertEqual(by_name["Kubernetes"].match_credit, 0.0)
        self.assertEqual(by_name["Kubernetes"].transferability_class, "UNVERIFIED_TRUE_GAP")
        self.assertEqual(by_name["Kubernetes"].adjacent_competing_product, "")

    def test_cli_export_and_csv_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            corpus_path = os.path.join(tmpdir, "corpus.json")
            md_path = os.path.join(tmpdir, "bench.md")
            json_path = os.path.join(tmpdir, "bench.json")
            csv_path = os.path.join(tmpdir, "bench.csv")

            self.assertEqual(pbs.main(["--export-default-corpus", corpus_path]), 0)
            self.assertTrue(os.path.isfile(corpus_path))

            rc = pbs.main([
                "--corpus", corpus_path,
                "--fuzz-iterations", "40",
                "--output", md_path,
                "--json-out", json_path,
                "--csv-out", csv_path,
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.isfile(md_path))
            self.assertTrue(os.path.isfile(json_path))
            self.assertTrue(os.path.isfile(csv_path))
            with open(json_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.assertEqual(data["passed_corpus_cases"], 11)

    def test_zip_and_pdf_decompression_bomb_protection(self) -> None:
        """Verifies that oversized .docx ZIP members and .pdf FlateDecode streams are blocked before OOM."""
        import zipfile
        import zlib

        with tempfile.TemporaryDirectory() as tmpdir:
            docx_path = os.path.join(tmpdir, "test.docx")
            with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("word/document.xml", b"<w:document><w:body>" + (b"A" * 2048) + b"</w:body></w:document>")
            with zipfile.ZipFile(docx_path, "r") as zf:
                with self.assertRaises(ValueError):
                    oaa._safe_zip_read(zf, "word/document.xml", max_bytes=512)

        # Oversized PDF stream exceeding MAX_UNCOMPRESSED_DOC_BYTES must raise ValueError
        orig_cap = oaa.MAX_UNCOMPRESSED_DOC_BYTES
        try:
            oaa.MAX_UNCOMPRESSED_DOC_BYTES = 512
            big_stream = zlib.compress(b"BT (" + (b"X" * 2048) + b") Tj ET")
            pdf_bytes = b"%PDF-1.4\n1 0 obj\n<< /Length " + str(len(big_stream)).encode() + b" >>\nstream\n" + big_stream + b"\nendstream\nendobj\n%%EOF"
            with self.assertRaises(ValueError):
                oaa._extract_pdf_streams_stdlib(pdf_bytes)
        finally:
            oaa.MAX_UNCOMPRESSED_DOC_BYTES = orig_cap


if __name__ == "__main__":
    unittest.main()
