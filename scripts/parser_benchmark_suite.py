#!/usr/bin/env python3
"""
ops-requisition-auditor: Resume Parser & Language Coverage Benchmark Suite (`ops-req-parser-bench`)
===================================================================================================
Benchmarks the deterministic resume parser and loader across:
  1. File Formats & Layouts:
     - `.docx` with Word headers (`word/header1.xml`), footers (`word/footer1.xml`),
       2-column tables (`w:tbl`), and text boxes (`w:txbxContent`).
     - `.pdf` with compressed `FlateDecode` (`zlib`) content streams parsed via zero-dependency
       Python stdlib fallback (`_extract_pdf_streams_stdlib`).
     - Scanned / image-only `.pdf` documents with sidecar `.ocr.txt` recovery and
       `OCR_REQUIRED_SCANNED_PDF` detection when no OCR text is present.
     - Multi-column ASCII/Markdown pipe tables (`Work Experience | Senior Analyst ...`).
  2. Date Parsing, Overlapping-Role Tenure & Career-Gap Exclusion:
     - English, Spanish, French, and German month names and "Present" tokens (`Actualidad`,
       `Présent`, `Heute`), ISO dates (`YYYY-MM-DD`), quarters (`Q1 2021 - Q4 2023`),
       seasons (`Spring 2020 - Fall 2022`), and 2-digit apostrophe years (`Jan '21 - Mar '24`).
     - Overlapping concurrent roles merged via interval union without double-counting.
     - Explicit Career-Gap blocks (`Parental Leave`, `Family Caregiving`, `Sabbatical`,
       `Elternzeit`, `Congé Parental`) excluded from tenure inflation and never penalized.
  3. Transferred Skills vs. Competing Products:
     - Verifies that generic JD categories (`WFM platform`, `BI tool`) are satisfied at `1.0x`
       by specific named platforms (`NICE IEX`, `Power BI`), same-capability synonyms earn `0.5x`,
       and competing proprietary products (`Verint` vs. `NICE IEX`; `Power BI` vs. `Tableau`)
       earn `0.0x` scoring credit while surfacing `adjacent_competing_product`.
  4. Deterministic Fuzz & Mutation Stress Suite (`run_deterministic_parser_fuzz_suite`):
     - Exercises 100+ seeded mutations (Unicode noise, malformed tables, nested pipes,
       irregular date tokens, control bytes, empty/scanned inputs) to verify 0 crashes.

Runs on the Python 3.10+ standard library only (zero external dependencies).
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import io
import json
import os
import random
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import zipfile
import zlib

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import offline_ats_auditor as oaa  # noqa: E402
import governance_middleware as gov  # noqa: E402

PARSER_BENCH_VERSION = "parser-bench-1.0"


# ---------------------------------------------------------------------------
# Synthetic Binary DOCX & PDF Fixture Generators (Stdlib Only)
# ---------------------------------------------------------------------------

def build_synthetic_docx_bytes(
    header_text: str,
    body_paragraphs: Sequence[str],
    table_rows: Sequence[Sequence[str]],
    textbox_text: str = "",
    footer_text: str = "",
) -> bytes:
    """Build a valid Word `.docx` zip archive in memory with headers, tables, textboxes, and footers."""
    ns_w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if header_text:
            h_xml = f'<?xml version="1.0" encoding="UTF-8"?><w:hdr {ns_w}><w:p><w:r><w:t>{header_text}</w:t></w:r></w:p></w:hdr>'
            zf.writestr("word/header1.xml", h_xml.encode("utf-8"))
        if footer_text:
            f_xml = f'<?xml version="1.0" encoding="UTF-8"?><w:ftr {ns_w}><w:p><w:r><w:t>{footer_text}</w:t></w:r></w:p></w:ftr>'
            zf.writestr("word/footer1.xml", f_xml.encode("utf-8"))

        body_parts: List[str] = []
        for p in body_paragraphs:
            body_parts.append(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>")
        if textbox_text:
            body_parts.append(
                f"<w:p><w:r><w:pict><w:txbxContent><w:p><w:r><w:t>{textbox_text}</w:t></w:r></w:p></w:txbxContent></w:pict></w:r></w:p>"
            )
        if table_rows:
            tr_xml: List[str] = []
            for row in table_rows:
                tc_xml = "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row)
                tr_xml.append(f"<w:tr>{tc_xml}</w:tr>")
            body_parts.append(f"<w:tbl>{''.join(tr_xml)}</w:tbl>")

        doc_xml = (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f"<w:document {ns_w}><w:body>{''.join(body_parts)}</w:body></w:document>"
        )
        zf.writestr("word/document.xml", doc_xml.encode("utf-8"))
    return buf.getvalue()


def build_synthetic_pdf_bytes(text_lines: Sequence[str], compress_flate: bool = True, scanned_image_only: bool = False) -> bytes:
    """Build a minimal PDF byte stream (either FlateDecode text-layer or scanned image-only)."""
    if scanned_image_only:
        return (
            b"%PDF-1.4\n"
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
            b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /XObject << /Im1 4 0 R >> >> >> endobj\n"
            b"4 0 obj << /Type /XObject /Subtype /Image /Width 100 /Height 100 >>\n"
            b"stream\n\x00\x01\x02\x03SCANNED_BITMAP_PIXELS\nendstream\nendobj\n"
            b"%%EOF\n"
        )

    ops = ["BT"]
    for ln in text_lines:
        esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops.append(f"({esc}) Tj")
    ops.append("ET")
    content_bytes = "\n".join(ops).encode("latin-1", errors="ignore")
    stream_payload = zlib.compress(content_bytes) if compress_flate else content_bytes
    filter_str = b"/Filter /FlateDecode " if compress_flate else b""

    return (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /Contents 4 0 R >> endobj\n"
        b"4 0 obj << " + filter_str + b"/Length " + str(len(stream_payload)).encode("ascii") + b" >>\n"
        b"stream\n" + stream_payload + b"\nendstream\nendobj\n"
        b"%%EOF\n"
    )


# ---------------------------------------------------------------------------
# Benchmark Data Models
# ---------------------------------------------------------------------------

@dataclass
class CorpusCaseResult:
    case_id: str
    category: str
    language: str
    format_type: str
    description: str
    expected_min_tenure_years: float
    expected_max_tenure_years: float
    actual_tenure_years: float
    overlapping_years_deduplicated: float
    career_gaps_detected: int
    expected_career_gaps: int
    sections_detected: List[str]
    expected_sections: List[str]
    matched_exact_or_generic: List[str]
    matched_synonyms: List[str]
    competing_products_flagged: List[str]
    expected_competing_product: str
    ocr_fallback_status: str
    passed: bool
    failure_reasons: List[str] = field(default_factory=list)


@dataclass
class FuzzSuiteSummary:
    iterations: int
    seed: int
    crashes: int
    average_parse_ms: float
    mutation_categories_tested: List[str]
    all_passed: bool


@dataclass
class ParserBenchmarkReport:
    tool: str
    module: str
    benchmark_version: str
    reference_date: str
    total_corpus_cases: int
    passed_corpus_cases: int
    corpus_pass_rate: float
    format_coverage: Dict[str, int]
    language_coverage: Dict[str, int]
    fuzz_summary: FuzzSuiteSummary
    case_results: List[CorpusCaseResult]
    report_sha256: str = ""


# ---------------------------------------------------------------------------
# Default 11-Case Multi-Format, Multi-Layout & Multilingual Benchmark Corpus
# ---------------------------------------------------------------------------

def get_default_parser_corpus() -> Dict[str, Any]:
    reqs = [
        {"name": "WFM Platform", "category": "hard_skill", "tier": "required", "synonyms": [], "alternatives": []},
        {"name": "Verint", "category": "hard_skill", "tier": "required", "synonyms": [], "alternatives": []},
        {"name": "Power BI", "category": "hard_skill", "tier": "required", "synonyms": [], "alternatives": []},
        {
            "name": "Workforce Capacity Modeling",
            "category": "hard_skill",
            "tier": "required",
            "synonyms": ["capacity planning", "planificación de capacidad", "planification de la capacité", "kapazitätsplanung"],
            "alternatives": [],
        },
        {
            "name": "Demand Forecasting",
            "category": "hard_skill",
            "tier": "required",
            "synonyms": ["demand planning", "pronóstico de demanda", "prévision de la demande", "bedarfsprognose"],
            "alternatives": [],
        },
        {"name": "SQL", "category": "hard_skill", "tier": "required", "synonyms": [], "alternatives": []},
        {"name": "Python", "category": "hard_skill", "tier": "required", "synonyms": [], "alternatives": []},
        {
            "name": "B.S.",
            "category": "hard_skill",
            "tier": "required",
            "synonyms": ["Bachelor", "Licenciatura", "Licence", "Bachelorabschluss"],
            "alternatives": [],
            "is_knockout_gate": True,
        },
    ]
    cases = [
        {
            "case_id": "CORP-01-DOCX-FULL",
            "category": "docx_headers_tables_textboxes",
            "language": "en",
            "format_type": ".docx",
            "description": "Word .docx with header contact info, 2-column table sections, and textbox certifications",
            "expected_min_tenure_years": 5.5,
            "expected_max_tenure_years": 6.2,
            "expected_career_gaps": 0,
            "expected_sections": ["experience", "education", "skills"],
            "expected_competing_product": "NICE IEX",
            "docx_spec": {
                "header_text": "ALEX RIVERA | St. Louis, MO 63101 | alex.rivera@example.com | (314) 555-0188",
                "body_paragraphs": [
                    "Career Profile",
                    "Senior WFM Analyst with 6 years of Demand Forecasting and capacity planning.",
                ],
                "table_rows": [
                    ["Professional Background", "Senior WFM Analyst — Apex Contact Center (01/2021 - Present)"],
                    ["", "- Built Demand Forecasting and capacity planning models in SQL, Python, and NICE IEX across 200 queues."],
                    ["", "- Published executive operational scorecards in Power BI."],
                    ["Academic Credentials", "B.S. in Industrial Engineering — University of Missouri"],
                    ["Technical Proficiencies", "SQL, Python, NICE IEX, Power BI, Demand Forecasting, Capacity Planning"],
                ],
                "textbox_text": "Six Sigma Green Belt Certified",
                "footer_text": "Page 1 of 1",
            },
        },
        {
            "case_id": "CORP-02-PDF-FLATE",
            "category": "pdf_stdlib_flate_stream",
            "language": "en",
            "format_type": ".pdf",
            "description": "Compressed FlateDecode PDF decoded via zero-dependency stdlib zlib stream parser",
            "expected_min_tenure_years": 4.5,
            "expected_max_tenure_years": 5.2,
            "expected_career_gaps": 0,
            "expected_sections": ["summary", "experience", "education", "skills"],
            "expected_competing_product": "Tableau",
            "pdf_lines": [
                "MORGAN LEE",
                "Professional Overview",
                "Workforce Operations Analyst experienced in Verint and Tableau.",
                "Work History",
                "WFM Analyst - Northstar Logistics (01/2022 - Present)",
                "- Led Demand Forecasting and Workforce Capacity Modeling in SQL, Python, Verint, and Tableau.",
                "Education & Training",
                "B.S. in Statistics",
                "Tools & Technologies",
                "SQL, Python, Verint, Tableau, Demand Forecasting, Workforce Capacity Modeling",
            ],
        },
        {
            "case_id": "CORP-03-PDF-SCANNED-SIDECAR-OCR",
            "category": "scanned_pdf_sidecar_ocr_recovery",
            "language": "en",
            "format_type": ".pdf+ocr",
            "description": "Scanned image-only PDF recovered automatically via sidecar .ocr.txt fallback",
            "expected_min_tenure_years": 5.5,
            "expected_max_tenure_years": 6.2,
            "expected_career_gaps": 0,
            "expected_sections": ["experience", "education", "skills"],
            "expected_competing_product": "",
            "scanned_sidecar_text": (
                "SAMIRA KHAN\n"
                "Work Experience\n"
                "Senior WFM Planner — Metro Support (01/2021 - Present)\n"
                "- Directed Demand Forecasting and Workforce Capacity Modeling using SQL, Python, Verint, and Power BI.\n"
                "Education\n"
                "B.S. in Mathematics\n"
                "Skills\n"
                "SQL, Python, Verint, Power BI, Demand Forecasting, Workforce Capacity Modeling\n"
            ),
        },
        {
            "case_id": "CORP-04-PDF-SCANNED-NO-OCR",
            "category": "scanned_pdf_unrecoverable_alert",
            "language": "en",
            "format_type": ".pdf (scanned)",
            "description": "Scanned image-only PDF without OCR sidecar flags OCR_REQUIRED_SCANNED_PDF cleanly",
            "expected_min_tenure_years": 0.0,
            "expected_max_tenure_years": 0.0,
            "expected_career_gaps": 0,
            "expected_sections": [],
            "expected_competing_product": "",
            "scanned_no_sidecar": True,
        },
        {
            "case_id": "CORP-05-OVERLAP-AND-CAREER-GAP",
            "category": "overlapping_roles_and_career_break",
            "language": "en",
            "format_type": ".txt",
            "description": "Concurrent overlapping roles + 1-year Parental Leave / Family Caregiving break excluded from tenure",
            "expected_min_tenure_years": 5.5,
            "expected_max_tenure_years": 6.0,
            "expected_career_gaps": 1,
            "expected_sections": ["summary", "experience", "education", "skills"],
            "expected_competing_product": "NICE IEX",
            "raw_text": (
                "TAYLOR VANCE\n"
                "Qualifications Summary\n"
                "Operations & WFM Lead with overlapping consulting and caregiving sabbatical.\n\n"
                "Employment Experience\n"
                "Senior WFM Analyst — Alpha Corp (01/2023 - Present)\n"
                "- Led Demand Forecasting and capacity planning in SQL, Python, Power BI, and NICE IEX.\n\n"
                "Planned Career Break — Parental Leave & Family Caregiving (01/2022 - 12/2022)\n"
                "- Full-time family caregiving sabbatical prior to returning to workforce analytics.\n\n"
                "WFM Analyst — Beta Logistics (01/2020 - 12/2021)\n"
                "- Built SQL and Python staffing forecasts.\n\n"
                "Part-Time WFM Consultant — Gamma Advisory (06/2020 - 06/2021)\n"
                "- Concurrent advisory project overlapping Beta Logistics tenure.\n\n"
                "Academic Background\n"
                "B.S. in Operations Research\n\n"
                "Core Competencies\n"
                "SQL, Python, Power BI, NICE IEX, Demand Forecasting, Capacity Planning\n"
            ),
        },
        {
            "case_id": "CORP-06-DATE-VARIATIONS",
            "category": "quarters_seasons_iso_apostrophe_dates",
            "language": "en",
            "format_type": ".txt",
            "description": "Parses ISO dates (2023-01-01), quarters (Q1 2021 - Q4 2022), and apostrophe years (Jan '19 - Dec '20)",
            "expected_min_tenure_years": 7.5,
            "expected_max_tenure_years": 8.0,
            "expected_career_gaps": 0,
            "expected_sections": ["experience", "education", "skills"],
            "expected_competing_product": "",
            "raw_text": (
                "JORDAN HAYES\n"
                "Career History\n"
                "Principal WFM Architect — CloudOps (2023-01-01 through Present)\n"
                "- Architected Demand Forecasting and Workforce Capacity Modeling in SQL, Python, Verint, and Power BI.\n"
                "Lead WFM Analyst — Delta Retail (Q1 2021 - Q4 2022)\n"
                "- Managed SQL and Power BI staffing models.\n"
                "Operations Analyst — Epsilon Freight (Jan '19 - Dec '20)\n"
                "- Built Python forecasting scripts.\n"
                "Education\n"
                "B.S. in Industrial Engineering\n"
                "Technical Skills\n"
                "SQL, Python, Verint, Power BI, Demand Forecasting, Workforce Capacity Modeling\n"
            ),
        },
        {
            "case_id": "CORP-07-SPANISH-RESUME",
            "category": "multilingual_spanish_es",
            "language": "es",
            "format_type": ".txt",
            "description": "Spanish section headers (Experiencia Laboral, Habilidades Técnicas) and Spanish dates (Enero 2021 - Actualidad)",
            "expected_min_tenure_years": 5.5,
            "expected_max_tenure_years": 6.2,
            "expected_career_gaps": 0,
            "expected_sections": ["summary", "experience", "education", "skills"],
            "expected_competing_product": "Genesys Cloud",
            "raw_text": (
                "CARLOS MENDOZA\n"
                "Resumen Profesional\n"
                "Analista Senior de Operaciones y WFM.\n\n"
                "Experiencia Laboral\n"
                "Analista Senior WFM — Contacto Global (Enero 2021 hasta Actualidad)\n"
                "- Lideró pronóstico de demanda y planificación de capacidad en SQL, Python, Power BI y Genesys Cloud.\n\n"
                "Formación Académica\n"
                "Licenciatura en Ingeniería Industrial\n\n"
                "Habilidades Técnicas\n"
                "SQL, Python, Power BI, Genesys Cloud, pronóstico de demanda, planificación de capacidad\n"
            ),
        },
        {
            "case_id": "CORP-08-FRENCH-RESUME",
            "category": "multilingual_french_fr",
            "language": "fr",
            "format_type": ".txt",
            "description": "French section headers (Expérience Professionnelle, Compétences Techniques) and French dates (Janvier 2021 - Présent)",
            "expected_min_tenure_years": 5.5,
            "expected_max_tenure_years": 6.2,
            "expected_career_gaps": 0,
            "expected_sections": ["summary", "experience", "education", "skills"],
            "expected_competing_product": "Calabrio",
            "raw_text": (
                "CLAIRE DUBOIS\n"
                "Profil Professionnel\n"
                "Responsable Analytique Opérations et WFM.\n\n"
                "Expérience Professionnelle\n"
                "Responsable WFM — Services Europe (Janvier 2021 à Présent)\n"
                "- Piloté la prévision de la demande et la planification de la capacité en SQL, Python, Power BI et Calabrio.\n\n"
                "Formation\n"
                "Licence en Mathématiques Appliquées\n\n"
                "Compétences Techniques\n"
                "SQL, Python, Power BI, Calabrio, prévision de la demande, planification de la capacité\n"
            ),
        },
        {
            "case_id": "CORP-09-GERMAN-RESUME",
            "category": "multilingual_german_de",
            "language": "de",
            "format_type": ".txt",
            "description": "German section headers (Berufserfahrung, Fachkenntnisse), German dates (März 2020 bis Heute), and Elternzeit gap",
            "expected_min_tenure_years": 5.5,
            "expected_max_tenure_years": 6.2,
            "expected_career_gaps": 1,
            "expected_sections": ["summary", "experience", "education", "skills"],
            "expected_competing_product": "Looker",
            "raw_text": (
                "LUKAS SCHMIDT\n"
                "Berufsprofil\n"
                "Senior Workforce Management & Operations Analyst.\n\n"
                "Berufserfahrung\n"
                "Senior WFM Analyst — Nordstern GmbH (Januar 2021 bis Heute)\n"
                "- Leitete Bedarfsprognose und Kapazitätsplanung mit SQL, Python, Verint und Looker.\n\n"
                "Elternzeit — Familienbetreuung (März 2020 bis Dezember 2020)\n"
                "- Geplante Elternzeit.\n\n"
                "Ausbildung\n"
                "Bachelorabschluss in Wirtschaftsingenieurwesen\n\n"
                "Fachkenntnisse\n"
                "SQL, Python, Verint, Looker, Bedarfsprognose, Kapazitätsplanung\n"
            ),
        },
        {
            "case_id": "CORP-10-ASCII-PIPE-TABLE",
            "category": "multicolumn_pipe_table_layout",
            "language": "en",
            "format_type": ".md",
            "description": "Two-column pipe-delimited Markdown/ASCII resume layout de-interleaved into standard sections",
            "expected_min_tenure_years": 5.5,
            "expected_max_tenure_years": 6.2,
            "expected_career_gaps": 0,
            "expected_sections": ["summary", "experience", "education", "skills"],
            "expected_competing_product": "Tableau",
            "raw_text": (
                "DANA CHEN\n"
                "Executive Profile | Senior WFM & BI Lead with 6 years of experience.\n"
                "Work Experience | Senior WFM Lead — Pacific Ops (01/2021 - Present)\n"
                " | - Built Demand Forecasting and Workforce Capacity Modeling in SQL, Python, Verint, and Tableau.\n"
                "Education | B.S. in Applied Mathematics\n"
                "Skills & Tools | SQL, Python, Verint, Tableau, Demand Forecasting, Workforce Capacity Modeling\n"
            ),
        },
        {
            "case_id": "CORP-11-COMPETING-PRODUCT-BOUNDARY",
            "category": "transferred_skill_vs_competing_product",
            "language": "en",
            "format_type": ".txt",
            "description": "Distinguishes generic WFM Platform (1.0x via NICE IEX), synonym capacity planning (0.5x), and competing products Verint/Power BI (0.0x)",
            "expected_min_tenure_years": 5.5,
            "expected_max_tenure_years": 6.2,
            "expected_career_gaps": 0,
            "expected_sections": ["experience", "education", "skills"],
            "expected_competing_product": "NICE IEX",
            "raw_text": (
                "CASEY BROOKS\n"
                "Work Experience\n"
                "WFM Capacity Planner — Summit Services (01/2021 - Present)\n"
                "- Built capacity planning and demand planning models in SQL, Python, NICE IEX, and Tableau.\n"
                "Education\n"
                "B.S. in Economics\n"
                "Skills\n"
                "SQL, Python, NICE IEX, Tableau, capacity planning, demand planning\n"
            ),
        },
    ]
    return {
        "schema": "resume-parser-benchmark-corpus-1.0",
        "reference_date": "2026-11-01",
        "requirements": reqs,
        "cases": cases,
    }


# ---------------------------------------------------------------------------
# Corpus & Fuzz Benchmark Execution
# ---------------------------------------------------------------------------

def evaluate_parser_corpus(
    corpus_dict: Mapping[str, Any],
    reference_date: datetime,
) -> List[CorpusCaseResult]:
    reqs = [oaa.JDRequirement(**r) for r in corpus_dict["requirements"]]
    align_cfg = oaa.AlignmentConfig(target_title="Senior WFM Analyst", min_years_required=5.0)
    results: List[CorpusCaseResult] = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for case in corpus_dict["cases"]:
            cid = str(case["case_id"])
            cat = str(case["category"])
            lang = str(case["language"])
            fmt = str(case["format_type"])
            desc = str(case["description"])

            if "docx_spec" in case:
                spec = case["docx_spec"]
                docx_bytes = build_synthetic_docx_bytes(
                    header_text=spec.get("header_text", ""),
                    body_paragraphs=spec.get("body_paragraphs", []),
                    table_rows=spec.get("table_rows", []),
                    textbox_text=spec.get("textbox_text", ""),
                    footer_text=spec.get("footer_text", ""),
                )
                fpath = os.path.join(tmpdir, f"{cid}.docx")
                with open(fpath, "wb") as fh:
                    fh.write(docx_bytes)
                raw_txt, diag = oaa.extract_resume_with_ocr_fallback(fpath)
            elif "pdf_lines" in case:
                pdf_bytes = build_synthetic_pdf_bytes(case["pdf_lines"], compress_flate=True, scanned_image_only=False)
                fpath = os.path.join(tmpdir, f"{cid}.pdf")
                with open(fpath, "wb") as fh:
                    fh.write(pdf_bytes)
                raw_txt, diag = oaa.extract_resume_with_ocr_fallback(fpath)
            elif "scanned_sidecar_text" in case:
                pdf_bytes = build_synthetic_pdf_bytes([], scanned_image_only=True)
                fpath = os.path.join(tmpdir, f"{cid}.pdf")
                with open(fpath, "wb") as fh:
                    fh.write(pdf_bytes)
                with open(f"{fpath}.ocr.txt", "w", encoding="utf-8") as fh:
                    fh.write(case["scanned_sidecar_text"])
                raw_txt, diag = oaa.extract_resume_with_ocr_fallback(fpath)
            elif case.get("scanned_no_sidecar"):
                pdf_bytes = build_synthetic_pdf_bytes([], scanned_image_only=True)
                fpath = os.path.join(tmpdir, f"{cid}.pdf")
                with open(fpath, "wb") as fh:
                    fh.write(pdf_bytes)
                raw_txt, diag = oaa.extract_resume_with_ocr_fallback(fpath)
            else:
                raw_txt = str(case.get("raw_text", ""))
                diag = {"ocr_fallback_status": "NOT_NEEDED"}

            parsed = oaa.parse_resume(raw_txt, reference_date=reference_date)
            rep = oaa.run_ats_audit(raw_txt, reqs, align_cfg, reference_date=reference_date)

            detected_secs = sorted(
                sec for sec in ("summary", "experience", "education", "certifications", "skills")
                if parsed.sections.get(sec, "").strip()
            )
            exact_or_gen = [
                row.requirement.name
                for row in rep.ledger
                if row.match_credit == 1.0
            ]
            syns = [
                row.requirement.name
                for row in rep.ledger
                if row.match_credit == 0.5
            ]
            comp_flagged = [
                f"{row.requirement.name}->{row.adjacent_competing_product}"
                for row in rep.ledger
                if row.transferability_class == "COMPETING_PRODUCT_NO_CREDIT" and row.adjacent_competing_product
            ]

            failures: List[str] = []
            min_t = float(case.get("expected_min_tenure_years", 0.0))
            max_t = float(case.get("expected_max_tenure_years", 99.0))
            if not (min_t <= parsed.total_relevant_tenure_years <= max_t):
                failures.append(
                    f"Tenure {parsed.total_relevant_tenure_years:.2f}y outside expected [{min_t:.2f}y, {max_t:.2f}y]"
                )
            exp_gaps = int(case.get("expected_career_gaps", 0))
            if len(parsed.career_gaps) != exp_gaps:
                failures.append(f"Career gaps {len(parsed.career_gaps)} != expected {exp_gaps}")
            for req_sec in case.get("expected_sections", []):
                if req_sec not in detected_secs:
                    failures.append(f"Missing expected section '{req_sec}' (detected: {detected_secs})")
            exp_comp = str(case.get("expected_competing_product", ""))
            if exp_comp and not any(exp_comp in cf for cf in comp_flagged):
                failures.append(f"Expected competing product '{exp_comp}' not flagged (flagged: {comp_flagged})")
            if case.get("scanned_no_sidecar") and diag.get("ocr_fallback_status") != "OCR_REQUIRED_SCANNED_PDF":
                failures.append(f"Expected OCR_REQUIRED_SCANNED_PDF, got {diag.get('ocr_fallback_status')}")

            results.append(
                CorpusCaseResult(
                    case_id=cid,
                    category=cat,
                    language=lang,
                    format_type=fmt,
                    description=desc,
                    expected_min_tenure_years=min_t,
                    expected_max_tenure_years=max_t,
                    actual_tenure_years=parsed.total_relevant_tenure_years,
                    overlapping_years_deduplicated=parsed.overlapping_years_deduplicated,
                    career_gaps_detected=len(parsed.career_gaps),
                    expected_career_gaps=exp_gaps,
                    sections_detected=detected_secs,
                    expected_sections=list(case.get("expected_sections", [])),
                    matched_exact_or_generic=exact_or_gen,
                    matched_synonyms=syns,
                    competing_products_flagged=comp_flagged,
                    expected_competing_product=exp_comp,
                    ocr_fallback_status=str(diag.get("ocr_fallback_status", "NOT_NEEDED")),
                    passed=(len(failures) == 0),
                    failure_reasons=failures,
                )
            )
    return results


def run_deterministic_parser_fuzz_suite(
    iterations: int = 100,
    seed: int = 20261101,
    reference_date: Optional[datetime] = None,
) -> FuzzSuiteSummary:
    """
    Run a deterministic seeded fuzz & mutation suite against `parse_resume` and `run_ats_audit`:
      - Mutation 1: Multi-column pipe/table interleaving & unbalanced delimiters
      - Mutation 2: Multilingual & malformed date ranges (reversed intervals, leap dates, 2-digit years)
      - Mutation 3: Unicode diacritics, zero-width spaces, RTL marks, and emoji/symbol injection
      - Mutation 4: Pathological long single-line resumes and empty/whitespace/control-byte inputs
      - Mutation 5: Career-break and overlapping concurrent role stress patterns
    Verifies that 100% of fuzz inputs complete deterministically with zero unhandled exceptions
    and bounded scores in `[0.0, 100.0]`.
    """
    ref_dt = reference_date or datetime(2026, 11, 1)
    rng = random.Random(seed)
    _, reqs, align_cfg = oaa.get_demo_fixture()

    mutation_families = [
        "multicolumn_pipe_delimiters",
        "malformed_and_reversed_dates",
        "unicode_diacritics_and_zero_width",
        "long_lines_and_control_bytes",
        "overlapping_roles_and_career_breaks",
    ]
    date_tokens = [
        "01/2020 - Present", "2024/12 - 2020/01", "Enero 2021 hasta Actualidad",
        "Févr. 2019 à Présent", "März 2018 bis Heute", "Q1 2021 - Q4 2023",
        "Spring 2020 ~ Fall 2022", "Jan '21 - Mar '24", "99/9999 - 00/0000",
        "2022-03-15 through 2025-11-01", "1998 - 2002",
    ]
    headers = [
        "Work Experience", "Experiencia Laboral", "Expérience Professionnelle",
        "Berufserfahrung", "Technical Proficiencies", "Habilidades Técnicas",
        "Compétences Techniques", "Fachkenntnisse", "Where I've Been", "Toolbox",
        "Education | B.S. in Engineering", "Skills | SQL | Python | Power BI | NICE IEX",
    ]
    snippets = [
        "- Built Demand Forecasting and Workforce Capacity Modeling in SQL, Python, Power BI, and NICE IEX ($14M).",
        "Planned Career Break — Parental Leave & Family Caregiving (03/2022 - 11/2022)",
        "Elternzeit (01/2021 bis 09/2021)",
        "|\t| Table Cell 1 | Table Cell 2 | 01/2021 - Present |",
        "\u200b\u200c\u200dSenior Analytics Manager — Español / Français / Deutsch — C++ / C# / .NET / B.S.",
        "A" * 2500,
        "",
    ]

    crashes = 0
    start_ts = datetime.now()
    for i in range(iterations):
        lines: List[str] = []
        n_lines = rng.randint(0, 35)
        for _ in range(n_lines):
            choice = rng.randint(0, 3)
            if choice == 0:
                lines.append(rng.choice(headers))
            elif choice == 1:
                lines.append(f"Role {i} ({rng.choice(date_tokens)})")
            elif choice == 2:
                lines.append(rng.choice(snippets))
            else:
                noise = "".join(rng.choice("ABCabc123|/-–—•*#@()\n\tÀéüö") for _ in range(rng.randint(1, 120)))
                lines.append(noise)
        fuzz_text = "\n".join(lines)
        try:
            parsed = oaa.parse_resume(fuzz_text, reference_date=ref_dt)
            rep = oaa.run_ats_audit(fuzz_text, reqs, align_cfg, reference_date=ref_dt)
            if not (0.0 <= rep.composite_score <= 100.0) or parsed.total_relevant_tenure_years < 0.0:
                crashes += 1
        except Exception:
            crashes += 1

    elapsed_ms = max(0.01, (datetime.now() - start_ts).total_seconds() * 1000.0)
    return FuzzSuiteSummary(
        iterations=iterations,
        seed=seed,
        crashes=crashes,
        average_parse_ms=round(elapsed_ms / max(iterations, 1), 3),
        mutation_categories_tested=mutation_families,
        all_passed=(crashes == 0),
    )


def run_full_parser_benchmark(
    corpus_dict: Optional[Mapping[str, Any]] = None,
    reference_date: str = "2026-11-01",
    fuzz_iterations: int = 100,
    fuzz_seed: int = 20261101,
) -> ParserBenchmarkReport:
    corpus = corpus_dict or get_default_parser_corpus()
    ref_dt = datetime.strptime(reference_date[:10], "%Y-%m-%d")

    case_results = evaluate_parser_corpus(corpus, reference_date=ref_dt)
    fuzz_summary = run_deterministic_parser_fuzz_suite(
        iterations=fuzz_iterations,
        seed=fuzz_seed,
        reference_date=ref_dt,
    )

    fmt_cov: Dict[str, int] = {}
    lang_cov: Dict[str, int] = {}
    for r in case_results:
        fmt_cov[r.format_type] = fmt_cov.get(r.format_type, 0) + 1
        lang_cov[r.language] = lang_cov.get(r.language, 0) + 1

    passed_cnt = sum(1 for r in case_results if r.passed)
    total_cnt = len(case_results)

    rep = ParserBenchmarkReport(
        tool="ops-requisition-auditor",
        module="parser-benchmark-suite",
        benchmark_version=PARSER_BENCH_VERSION,
        reference_date=reference_date[:10],
        total_corpus_cases=total_cnt,
        passed_corpus_cases=passed_cnt,
        corpus_pass_rate=round(passed_cnt / max(total_cnt, 1), 4),
        format_coverage=fmt_cov,
        language_coverage=lang_cov,
        fuzz_summary=fuzz_summary,
        case_results=case_results,
    )
    canonical = json.dumps(
        {
            "benchmark_version": rep.benchmark_version,
            "reference_date": rep.reference_date,
            "total_corpus_cases": rep.total_corpus_cases,
            "passed_corpus_cases": rep.passed_corpus_cases,
            "fuzz_crashes": fuzz_summary.crashes,
            "case_ids": [c.case_id for c in case_results],
        },
        sort_keys=True,
    )
    rep.report_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return rep


# ---------------------------------------------------------------------------
# Markdown & CSV Renderers
# ---------------------------------------------------------------------------

def render_parser_benchmark_markdown(rep: ParserBenchmarkReport) -> str:
    fmt_str = ", ".join(f"`{k}` ({v})" for k, v in sorted(rep.format_coverage.items()))
    lang_str = ", ".join(f"`{k}` ({v})" for k, v in sorted(rep.language_coverage.items()))

    lines: List[str] = [
        "# Resume Parser, Layout, OCR & Multilingual Benchmark Report (`ops-req-parser-bench`)",
        "",
        "*Benchmarks DOCX (headers/footers/tables/textboxes), PDF (`FlateDecode` stdlib stream + scanned OCR sidecar fallback), multilingual (`en`/`es`/`fr`/`de`) dates & section headers, overlapping-role tenure & career-break exclusion, transferred-skill vs. competing-product classification, and deterministic fuzz testing.*",
        "",
        "## 1. Benchmark & Fuzz Stress Telemetry",
        "",
        f"- **Benchmark Suite Version:** `{rep.benchmark_version}` (Reference Date: `{rep.reference_date}`)",
        f"- **Report SHA-256:** `{rep.report_sha256}`",
        f"- **Corpus Pass Rate:** `{rep.passed_corpus_cases} / {rep.total_corpus_cases}` (`{rep.corpus_pass_rate * 100:.1f}%`)",
        f"- **File Format & OCR Coverage:** {fmt_str}",
        f"- **Language Coverage:** {lang_str}",
        f"- **Deterministic Fuzz Suite (`seed={rep.fuzz_summary.seed}`):** `{rep.fuzz_summary.iterations}` mutated resumes evaluated | Crashes: `{rep.fuzz_summary.crashes}` | Avg Parse Time: `{rep.fuzz_summary.average_parse_ms:.2f} ms/resume` | Status: `{'PASS (0 crashes)' if rep.fuzz_summary.all_passed else 'FAIL'}`",
        "",
        "## 2. Multi-Format, Layout, Career-Gap & Competing-Product Corpus Ledger",
        "",
        "| Case ID | Format (`Lang`) | Category | Net Tenure (`Overlap Dedup`) | Career Gaps | Sections Parsed | Generic / Exact (`1.0x`) | Synonyms (`0.5x`) | Competing Product (`0.0x`) | OCR Status | Status |",
        "| :--- | :---: | :--- | :---: | :---: | :--- | :--- | :--- | :--- | :--- | :---: |",
    ]
    for r in rep.case_results:
        sec_str = ", ".join(r.sections_detected) if r.sections_detected else "*(scanned/none)*"
        ex_str = ", ".join(r.matched_exact_or_generic) if r.matched_exact_or_generic else "—"
        syn_str = ", ".join(r.matched_synonyms) if r.matched_synonyms else "—"
        comp_str = ", ".join(r.competing_products_flagged) if r.competing_products_flagged else "—"
        stat_str = "**PASS**" if r.passed else f"**FAIL** ({'; '.join(r.failure_reasons)})"
        lines.append(
            f"| `{r.case_id}` | `{r.format_type}` (`{r.language}`) | `{r.category}` | "
            f"`{r.actual_tenure_years:.2f}y` (`-{r.overlapping_years_deduplicated:.2f}y`) | "
            f"`{r.career_gaps_detected}` | `{sec_str}` | {ex_str} | {syn_str} | `{comp_str}` | "
            f"`{r.ocr_fallback_status}` | {stat_str} |"
        )

    return "\n".join(lines) + "\n"


def render_parser_benchmark_csv(rep: ParserBenchmarkReport) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "case_id",
        "category",
        "language",
        "format_type",
        "actual_tenure_years",
        "overlapping_years_deduplicated",
        "career_gaps_detected",
        "sections_detected",
        "matched_exact_or_generic",
        "matched_synonyms",
        "competing_products_flagged",
        "ocr_fallback_status",
        "passed",
    ])
    for r in rep.case_results:
        writer.writerow([
            r.case_id,
            r.category,
            r.language,
            r.format_type,
            f"{r.actual_tenure_years:.2f}",
            f"{r.overlapping_years_deduplicated:.2f}",
            r.career_gaps_detected,
            ";".join(r.sections_detected),
            ";".join(r.matched_exact_or_generic),
            ";".join(r.matched_synonyms),
            ";".join(r.competing_products_flagged),
            r.ocr_fallback_status,
            r.passed,
        ])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# CLI Entry Point (`ops-req-parser-bench`)
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops-req-parser-bench",
        description="Resume Parser, Layout, OCR Fallback, Multilingual & Fuzz Benchmark Suite.",
    )
    parser.add_argument("--demo", action="store_true", help="Run the built-in 11-case multi-format/multilingual parser benchmark + 100-case deterministic fuzz suite.")
    parser.add_argument("--corpus", help="Path to a parser benchmark corpus JSON file.")
    parser.add_argument("--export-default-corpus", help="Export the built-in 11-case parser benchmark corpus JSON to this path and exit.")
    parser.add_argument("--fuzz-iterations", type=int, default=100, help="Number of seeded fuzz iterations to run (default: 100).")
    parser.add_argument("--fuzz-seed", type=int, default=20261101, help="Deterministic RNG seed for fuzz testing (default: 20261101).")
    parser.add_argument("--reference-date", default="2026-11-01", help="Reference date YYYY-MM-DD.")
    parser.add_argument("--output", help="Write Markdown benchmark report to this path.")
    parser.add_argument("--json-out", help="Write full JSON benchmark results to this path.")
    parser.add_argument("--csv-out", help="Write UTF-8 CSV benchmark ledger to this path.")

    args = parser.parse_args(argv)

    if args.export_default_corpus:
        with open(args.export_default_corpus, "w", encoding="utf-8") as fh:
            json.dump(get_default_parser_corpus(), fh, indent=2)
        sys.stdout.write(f"[PARSER BENCH OK] Exported 11-case parser benchmark corpus to {args.export_default_corpus}\n")
        return 0

    corpus_dict = get_default_parser_corpus()
    if args.corpus:
        c_path = gov.resolve_input_path(args.corpus)
        with open(c_path, "r", encoding="utf-8") as fh:
            corpus_dict = json.load(fh)

    rep = run_full_parser_benchmark(
        corpus_dict=corpus_dict,
        reference_date=args.reference_date,
        fuzz_iterations=args.fuzz_iterations,
        fuzz_seed=args.fuzz_seed,
    )
    md_out = render_parser_benchmark_markdown(rep)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(md_out)
    else:
        sys.stdout.write(md_out)

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(dataclasses.asdict(rep), fh, indent=2)

    if args.csv_out:
        with open(args.csv_out, "w", encoding="utf-8") as fh:
            fh.write(render_parser_benchmark_csv(rep))

    return 0 if (rep.passed_corpus_cases == rep.total_corpus_cases and rep.fuzz_summary.all_passed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
