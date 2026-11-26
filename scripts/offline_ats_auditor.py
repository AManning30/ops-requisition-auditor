#!/usr/bin/env python3
"""
ops-requisition-auditor: Deterministic JD Alignment Scoring Engine
==================================================================
A transparent, repeatable JD-alignment heuristic inspired by common ATS parsing behavior.
Weights are this project's own, not any vendor's algorithm; use it to find fixable gaps.

Implements the 100-point JD Alignment Scorecard:
  - Component 1: Weighted Keyword & Taxonomy Score (Max 45 pts)
  - Component 2: Contextual Placement & Recency Score (Max 20 pts)
  - Component 3: Title, Seniority & Calculated Tenure Alignment (Max 15 pts)
  - Component 4: Hard Knockout / Minimum Gate Verification (Max 10 pts)
  - Component 5: HR-XML Parseability & Schema Integrity (Max 10 pts)

Usage:
  1. Run built-in verification demo:
     python3 offline_ats_auditor.py --demo

  2. Audit a resume text/markdown file against a structured JD JSON config:
     python3 offline_ats_auditor.py --resume my_resume.txt --jd-config jd_rules.json --output audit_report.md

  3. Audit a resume file against a plain-text JD file (heuristic entity extraction):
     python3 offline_ats_auditor.py --resume my_resume.txt --jd-text job_description.txt --output audit_report.md
"""

from __future__ import annotations

import argparse
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
import zipfile
import zlib


# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

CATEGORY_WEIGHTS: Dict[str, int] = {
    "hard_skill": 3,      # Hard Skills, Domain Methodologies, Tools/ERP/Software, Certifications
    "title_scope": 2,     # Target Job Titles & Functional Scope (P&L, team size, compliance)
    "soft_skill": 1,      # Soft Skills / Core Competencies
}

TIER_MULTIPLIERS: Dict[str, float] = {
    "required": 2.0,      # Explicitly marked Required / Minimum / Must-Have
    "preferred": 1.0,     # Marked Preferred / Desired / Nice-to-Have
}


@dataclass
class JDRequirement:
    name: str
    category: str = "hard_skill"          # "hard_skill" (3), "title_scope" (2), "soft_skill" (1)
    tier: str = "required"                # "required" (2.0x), "preferred" (1.0x)
    synonyms: List[str] = field(default_factory=list)
    is_knockout_gate: bool = False        # Mandatory degree, license, clearance, work auth
    alternatives: List[str] = field(default_factory=list)  # Options the JD itself offers ("NICE, Verint, or Genesys"): each earns full 1.0 credit
    confidence: float = 1.0               # 0.0 to 1.0 extraction certainty score
    certainty: str = "CONFIRMED"          # "CONFIRMED" | "HIGH" | "MODERATE" | "LOW_INFERRED"
    extraction_status: str = "CONFIRMED"  # "CONFIRMED" | "INFERRED"
    source_quote: str = ""                # Exact requisition line from which requirement was extracted
    provenance_section: str = "confirmed_config"  # "confirmed_config" | "required_section" | "preferred_section" | "responsibilities_section" | "summary_narrative"
    provenance_rule: str = "manual_config"        # "manual_config" | "taxonomy_bullet_match" | "alternative_chain_or" | "degree_gate_regex" | "certification_match" | "soft_skill_match" | "heuristic_phrase_fallback"
    ambiguity_flags: List[str] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return f"{self.name} (or {', '.join(self.alternatives)})" if self.alternatives else self.name

    @property
    def base_weight(self) -> int:
        return CATEGORY_WEIGHTS.get(self.category, 3)

    @property
    def req_multiplier(self) -> float:
        return TIER_MULTIPLIERS.get(self.tier, 1.0)

    @property
    def max_item_weight(self) -> float:
        return float(self.base_weight) * self.req_multiplier


@dataclass
class AuditLedgerRow:
    requirement: JDRequirement
    match_credit: float                   # 1.0 (Exact), 0.5 (Semantic), 0.0 (Missing)
    placement_multiplier: float           # 1.0 (Recent Exp), 0.8 (Historical >5y), 0.4 (Skills-Only), 0.0 (Missing)
    matched_term: str
    placement_zone: str                   # "Recent Work Experience (<=5 yrs)", "Historical (>5 yrs)", "Skills Section Only", "MISSING"
    exact_quote: str
    context_credit_factor: Optional[float] = None  # Effective (credit * placement) when a synonym in recent exp outscores skills-only literal in Comp 2
    literal_placement_multiplier: Optional[float] = None  # Best placement multiplier specifically for the 1.0x literal term
    transferability_class: str = ""       # "EXACT_OR_JD_ALTERNATIVE" | "GENERIC_CATEGORY_SATISFIED_BY_SPECIFIC_TOOL" | "TRANSFERRED_METHODOLOGY_SYNONYM" | "COMPETING_PRODUCT_NO_CREDIT" | "UNVERIFIED_TRUE_GAP"
    adjacent_competing_product: str = ""  # Populated when match_credit == 0.0 but candidate holds a competing product in the same tool family

    @property
    def max_weight(self) -> float:
        return self.requirement.max_item_weight

    @property
    def keyword_weighted_score(self) -> float:
        return self.max_weight * self.match_credit

    @property
    def literal_only_keyword_score(self) -> float:
        return self.max_weight * (1.0 if self.match_credit == 1.0 else 0.0)

    @property
    def earned_item_score(self) -> float:
        factor = self.context_credit_factor if self.context_credit_factor is not None else (self.match_credit * self.placement_multiplier)
        return self.max_weight * factor

    @property
    def literal_earned_item_score(self) -> float:
        if self.match_credit < 1.0:
            return 0.0
        lit_mult = self.literal_placement_multiplier if self.literal_placement_multiplier is not None else self.placement_multiplier
        return self.max_weight * lit_mult

    @property
    def raw_weight_lost_kw(self) -> float:
        return self.max_weight - self.keyword_weighted_score

    @property
    def raw_weight_lost_context(self) -> float:
        return self.max_weight - self.earned_item_score


@dataclass
class RoleBlock:
    header: str
    start_date: Optional[datetime]
    end_date: Optional[datetime]
    is_recent: bool
    duration_years: float
    text: str
    lines: List[str]
    is_career_gap: bool = False


@dataclass
class ParsedResume:
    raw_text: str
    sections: Dict[str, str]
    roles: List[RoleBlock]
    total_relevant_tenure_years: float
    schema_deductions: List[Tuple[str, float]]
    schema_score: float
    career_gaps: List[RoleBlock] = field(default_factory=list)
    gross_unmerged_tenure_years: float = 0.0
    overlapping_years_deduplicated: float = 0.0


@dataclass
class AlignmentConfig:
    target_title: str = ""
    title_synonyms: List[str] = field(default_factory=list)
    min_years_required: float = 5.0
    target_seniority_keywords: List[str] = field(
        default_factory=lambda: ["architected", "spearheaded", "governed", "led", "managed", "directed", "scaled", "owned"]
    )


@dataclass
class AuditReport:
    composite_score: float
    modern_semantic_score: float
    legacy_literal_score: float
    knockout_passed: bool
    failed_knockouts: List[str]
    verdict: str
    primary_bottleneck: str
    comp1_kw_score: float
    comp1_literal_score: float
    comp2_context_score: float
    comp3_alignment_score: float
    comp3_breakdown: Dict[str, float]
    comp4_knockout_score: float
    comp5_schema_score: float
    comp5_deductions: List[Tuple[str, float]]
    total_max_item_weights: float
    sum_kw_matched_weights: float
    sum_literal_matched_weights: float
    sum_earned_item_scores: float
    ledger: List[AuditLedgerRow]
    recovery_plan: List[Dict[str, object]]
    effective_org_config: Dict[str, Any] = field(default_factory=dict)
    uncertainty_and_explanations: Dict[str, Any] = field(default_factory=dict)

    @property
    def overall_score(self) -> float:
        return self.composite_score


# ---------------------------------------------------------------------------
# Resume Parser & HR-XML Schema Auditor
# ---------------------------------------------------------------------------

STANDARD_SECTION_HEADERS = {
    # Summary / Profile (EN + ES + FR + DE)
    "professional summary": "summary",
    "executive summary": "summary",
    "career summary": "summary",
    "summary of qualifications": "summary",
    "qualifications summary": "summary",
    "career profile": "summary",
    "executive profile": "summary",
    "professional overview": "summary",
    "career highlights": "summary",
    "summary": "summary",
    "profile": "summary",
    "professional profile": "summary",
    "objective": "summary",
    "career objective": "summary",
    "resumen profesional": "summary",
    "perfil profesional": "summary",
    "resumen ejecutivo": "summary",
    "profil professionnel": "summary",
    "résumé professionnel": "summary",
    "synthèse professionnelle": "summary",
    "berufsprofil": "summary",
    "zusammenfassung": "summary",
    "kurzprofil": "summary",
    # Work Experience (EN + ES + FR + DE)
    "work experience": "experience",
    "professional experience": "experience",
    "relevant experience": "experience",
    "experience": "experience",
    "employment history": "experience",
    "career history": "experience",
    "work history": "experience",
    "professional background": "experience",
    "employment experience": "experience",
    "industry experience": "experience",
    "leadership experience": "experience",
    "operational experience": "experience",
    "military experience": "experience",
    "projects & experience": "experience",
    "experiencia laboral": "experience",
    "experiencia profesional": "experience",
    "historial laboral": "experience",
    "trayectoria profesional": "experience",
    "expérience professionnelle": "experience",
    "parcours professionnel": "experience",
    "expériences professionnelles": "experience",
    "berufserfahrung": "experience",
    "beruflicher werdegang": "experience",
    "arbeitserfahrung": "experience",
    # Education (EN + ES + FR + DE)
    "education": "education",
    "education & certifications": "education",
    "education and certifications": "education",
    "education & training": "education",
    "education and training": "education",
    "academic background": "education",
    "academic credentials": "education",
    "educación": "education",
    "educacion": "education",
    "formación académica": "education",
    "formacion academica": "education",
    "formation": "education",
    "formation académique": "education",
    "études": "education",
    "ausbildung": "education",
    "studium": "education",
    "bildung": "education",
    # Certifications (EN + ES + FR + DE)
    "certifications": "certifications",
    "licenses & certifications": "certifications",
    "licenses and certifications": "certifications",
    "professional certifications": "certifications",
    "credentials & licenses": "certifications",
    "certificaciones": "certifications",
    "certifications et licences": "certifications",
    "zertifizierungen": "certifications",
    # Skills (EN + ES + FR + DE)
    "technical & core skills": "skills",
    "core competencies & technical skills": "skills",
    "core competencies and technical skills": "skills",
    "technical skills & core competencies": "skills",
    "skills & core competencies": "skills",
    "technical skills": "skills",
    "core competencies": "skills",
    "areas of expertise": "skills",
    "key skills": "skills",
    "skills & tools": "skills",
    "skills": "skills",
    "technical proficiencies": "skills",
    "tools & technologies": "skills",
    "tools and technologies": "skills",
    "software & systems": "skills",
    "competencies": "skills",
    "habilidades": "skills",
    "habilidades técnicas": "skills",
    "competencias técnicas": "skills",
    "competencias": "skills",
    "compétences": "skills",
    "compétences techniques": "skills",
    "fähigkeiten": "skills",
    "kenntnisse": "skills",
    "fachkenntnisse": "skills",
    "edv-kenntnisse": "skills",
}


def _classify_standard_header(clean_header: str) -> Optional[str]:
    if clean_header in STANDARD_SECTION_HEADERS:
        return STANDARD_SECTION_HEADERS[clean_header]
    if len(clean_header) <= 65:
        if re.match(
            r"^(?:core\s+competencies|technical\s+skills|key\s+skills|areas\s+of\s+expertise|technical\s+proficiencies|tools\s*(?:&|and)\s*technologies|habilidades(?:\s+t[eé]cnicas)?|comp[eé]tences(?:\s+techniques)?|f[aä]higkeiten|fachkenntnisse)"
            r"(?:\s*(?:&|and|/|\||et|y|und)\s*(?:core\s+competencies|technical\s+skills|tools|proficiencies|skills|technologies|systems))?$",
            clean_header,
        ):
            return "skills"
        if re.match(
            r"^(?:professional|work|relevant|career|employment|industry|leadership|operational|military)\s+(?:experience|history|background)$"
            r"|^(?:experiencia\s+(?:laboral|profesional)|exp[eé]rience\s+professionnelle|berufserfahrung)$",
            clean_header,
        ):
            return "experience"
        if re.match(
            r"^(?:professional|executive|career|qualifications)\s+(?:summary|profile|overview|highlights)$"
            r"|^(?:resumen\s+profesional|profil\s+professionnel|berufsprofil)$",
            clean_header,
        ):
            return "summary"
        if re.match(
            r"^(?:education|academic\s+background|formaci[oó]n\s+acad[eé]mica|formation|ausbildung)"
            r"(?:\s*(?:&|and|/|y|et|und)\s*(?:certifications|licenses|training|credentials))?$",
            clean_header,
        ):
            return "education"
        if re.match(r"^(?:professional\s+)?(?:certifications|licenses|credentials|certificaciones|zertifizierungen)$", clean_header):
            return "certifications"
    return None


MAX_UNCOMPRESSED_DOC_BYTES = 15 * 1024 * 1024  # 15 MB decompression bomb protection cap


def _safe_zip_read(zf: zipfile.ZipFile, member_name: str, max_bytes: int = MAX_UNCOMPRESSED_DOC_BYTES) -> bytes:
    """Read a member from a ZipFile while enforcing uncompressed byte-size and compression-ratio limits to block XML/ZIP bombs."""
    info = zf.getinfo(member_name)
    if info.file_size > max_bytes:
        raise ValueError(
            f"[SECURITY GATE] Archive member '{member_name}' uncompressed size ({info.file_size} bytes) "
            f"exceeds {max_bytes} byte limit (potential ZIP/XML bomb)."
        )
    if info.compress_size > 0 and info.file_size > 1024 * 1024 and (info.file_size / info.compress_size) > 200.0:
        raise ValueError(
            f"[SECURITY GATE] Archive member '{member_name}' compression ratio ({info.file_size / info.compress_size:.1f}x) "
            "exceeds 200x safety threshold (potential ZIP/XML bomb)."
        )
    data = zf.read(member_name)
    if len(data) > max_bytes:
        raise ValueError(
            f"[SECURITY GATE] Archive member '{member_name}' read size ({len(data)} bytes) "
            f"exceeds {max_bytes} byte limit."
        )
    return data


def _extract_pdf_streams_stdlib(pdf_bytes: bytes) -> Tuple[str, Dict[str, Any]]:
    """
    Zero-dependency stdlib PDF text extractor for uncompressed and FlateDecode (zlib)
    content streams with a 15 MB per-stream decompression cap. Also detects scanned/image-only
    PDFs (`is_scanned_image_pdf`).
    """
    page_count = max(1, len(re.findall(rb"/Type\s*/Page\b", pdf_bytes)))
    image_count = len(re.findall(rb"/Subtype\s*/Image\b", pdf_bytes))
    extracted_chunks: List[str] = []

    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf_bytes, re.DOTALL):
        raw_stream = m.group(1)
        decoded_bytes = raw_stream
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                decomp = zlib.decompressobj(wbits)
                candidate_bytes = decomp.decompress(raw_stream, MAX_UNCOMPRESSED_DOC_BYTES + 1)
                if len(candidate_bytes) > MAX_UNCOMPRESSED_DOC_BYTES or decomp.unconsumed_tail:
                    raise ValueError(
                        f"[SECURITY GATE] PDF FlateDecode stream exceeds {MAX_UNCOMPRESSED_DOC_BYTES} byte decompression cap."
                    )
                decoded_bytes = candidate_bytes
                break
            except ValueError:
                raise
            except Exception:
                continue
        text_view = decoded_bytes.decode("latin-1", errors="ignore")
        if "BT" not in text_view and "Tj" not in text_view and "TJ" not in text_view:
            continue
        for lit_m in re.finditer(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)\s*Tj", text_view):
            s = lit_m.group(1).replace(r"\(", "(").replace(r"\)", ")").replace(r"\n", "\n").replace(r"\r", " ")
            if s.strip():
                extracted_chunks.append(s)
        for arr_m in re.finditer(r"\[(.*?)\]\s*TJ", text_view, re.DOTALL):
            arr_body = arr_m.group(1)
            parts = [
                p.group(1).replace(r"\(", "(").replace(r"\)", ")")
                for p in re.finditer(r"\(([^()\\]*(?:\\.[^()\\]*)*)\)", arr_body)
            ]
            if parts:
                extracted_chunks.append("".join(parts))

    full_text = "\n".join(extracted_chunks).strip()
    printable_chars = len(re.sub(r"\s+", "", full_text))
    is_scanned = (printable_chars < 40) and (image_count > 0 or b"%PDF" in pdf_bytes[:16])
    return full_text, {
        "page_count": page_count,
        "image_count": image_count,
        "printable_chars": printable_chars,
        "is_scanned_image_pdf": is_scanned,
    }


def _resolve_input_path(path: str) -> str:
    """Resolve relative paths (e.g. 'examples/sample_resume.txt') against CWD or repository root."""
    if not path or os.path.exists(path):
        return path
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(repo_root, path)
    if os.path.exists(candidate):
        return candidate
    home_candidate = os.path.join(os.path.expanduser("~"), "ops-requisition-auditor", path)
    if os.path.exists(home_candidate):
        return home_candidate
    return path


def extract_resume_with_ocr_fallback(path: str) -> Tuple[str, Dict[str, Any]]:
    """
    Extract resume text from `.txt`, `.md`, `.docx` (including Word headers, footers,
    tables, and text boxes), or `.pdf` with a 4-tier fallback chain:
      1. `pypdf` / `fitz` (PyMuPDF) native text extraction
      2. Zero-dependency stdlib PDF `FlateDecode` stream extraction (`_extract_pdf_streams_stdlib`)
      3. Sidecar OCR file (`<path>.ocr.txt` or `<stem>.ocr.txt`) if staged by DMS/ATS pipeline
      4. Local `tesseract` CLI binary (`shutil.which("tesseract")`) if installed on host
    If a PDF is a scanned image with zero text layer and no OCR text available, marks
    `ocr_fallback_status = "OCR_REQUIRED_SCANNED_PDF"` and embeds `[SCANNED_DOCUMENT_OCR_REQUIRED]`.
    """
    path = _resolve_input_path(path)
    ext = os.path.splitext(path)[1].lower()
    diag: Dict[str, Any] = {
        "format": ext or ".txt",
        "extraction_method": "plain_text",
        "docx_header_footer_extracted": False,
        "docx_tables_extracted": 0,
        "docx_textboxes_extracted": 0,
        "is_scanned_document": False,
        "ocr_fallback_used": False,
        "ocr_fallback_status": "NOT_NEEDED",
    }

    if ext == ".docx":
        diag["extraction_method"] = "docx_xml_stdlib"
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            lines: List[str] = []

            # 1. Extract Word header XMLs (where candidates often put name/email/phone)
            for h_name in sorted(n for n in names if re.match(r"^word/header\d*\.xml$", n)):
                h_root = ET.fromstring(_safe_zip_read(zf, h_name))
                h_texts = [" ".join(t.text.strip() for t in p.findall(".//w:t", ns) if t.text and t.text.strip()) for p in h_root.findall(".//w:p", ns)]
                h_texts = [t for t in h_texts if t]
                if h_texts:
                    diag["docx_header_footer_extracted"] = True
                    lines.extend(h_texts)

            # 2. Extract main document body (paragraphs, tables, and text boxes)
            xml_bytes = _safe_zip_read(zf, "word/document.xml")
            root = ET.fromstring(xml_bytes)
            body = root.find("w:body", ns)
            if body is None:
                body = root
            for elem in body:
                tag = elem.tag.split("}")[-1]
                if tag == "p":
                    txbx_nodes = elem.findall(".//w:txbxContent", ns)
                    if txbx_nodes:
                        diag["docx_textboxes_extracted"] += len(txbx_nodes)
                    texts = [t.text for t in elem.findall(".//w:t", ns) if t.text]
                    if texts:
                        lines.append("".join(texts))
                elif tag == "tbl":
                    diag["docx_tables_extracted"] += 1
                    for row in elem.findall(".//w:tr", ns):
                        cell_texts: List[str] = []
                        for cell in row.findall(".//w:tc", ns):
                            c_txt = " ".join(
                                "".join(t.text for t in p.findall(".//w:t", ns) if t.text).strip()
                                for p in cell.findall(".//w:p", ns)
                            ).strip()
                            if c_txt:
                                cell_texts.append(c_txt)
                        if len(cell_texts) > 1:
                            lines.append(" | ".join(cell_texts))
                        elif len(cell_texts) == 1:
                            lines.append(cell_texts[0])

            # 3. Extract Word footer XMLs
            for f_name in sorted(n for n in names if re.match(r"^word/footer\d*\.xml$", n)):
                f_root = ET.fromstring(_safe_zip_read(zf, f_name))
                f_texts = [" ".join(t.text.strip() for t in p.findall(".//w:t", ns) if t.text and t.text.strip()) for p in f_root.findall(".//w:p", ns)]
                f_texts = [t for t in f_texts if t]
                if f_texts:
                    diag["docx_header_footer_extracted"] = True
                    lines.extend(f_texts)

        return "\n".join(lines), diag

    if ext == ".pdf":
        extracted_text = ""
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(path)
            extracted_text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
            diag["extraction_method"] = "pypdf_text_layer"
        except Exception:
            try:
                import fitz  # type: ignore
                doc = fitz.open(path)
                extracted_text = "\n".join(page.get_text() for page in doc).strip()
                diag["extraction_method"] = "pymupdf_text_layer"
            except Exception:
                pass

        # Tier 1.5: Poppler pdftotext CLI fallback for CIDFont / ToUnicode digital PDFs
        if len(re.sub(r"\s+", "", extracted_text)) < 40:
            pdftotext_bin = shutil.which("pdftotext")
            if pdftotext_bin:
                try:
                    proc = subprocess.run(
                        [pdftotext_bin, "-layout", path, "-"],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        timeout=15,
                        check=False,
                    )
                    if proc.returncode == 0 and len(re.sub(r"\s+", "", proc.stdout)) >= 40:
                        extracted_text = proc.stdout.strip()
                        diag["extraction_method"] = "poppler_pdftotext_cli"
                except Exception:
                    pass

        with open(path, "rb") as fh:
            pdf_bytes = fh.read()

        if len(re.sub(r"\s+", "", extracted_text)) < 40:
            stdlib_txt, pdf_meta = _extract_pdf_streams_stdlib(pdf_bytes)
            diag.update(pdf_meta)
            if len(re.sub(r"\s+", "", stdlib_txt)) >= 40:
                extracted_text = stdlib_txt
                diag["extraction_method"] = "stdlib_pdf_flate_stream"

        if len(re.sub(r"\s+", "", extracted_text)) < 40:
            diag["is_scanned_document"] = True
            # Fallback 1: check for sidecar .ocr.txt
            sidecar_candidates = [f"{path}.ocr.txt", f"{os.path.splitext(path)[0]}.ocr.txt"]
            for sc in sidecar_candidates:
                if os.path.isfile(sc):
                    with open(sc, "r", encoding="utf-8", errors="replace") as sc_fh:
                        extracted_text = sc_fh.read().strip()
                    diag["ocr_fallback_used"] = True
                    diag["ocr_fallback_status"] = "RECOVERED_VIA_SIDECAR_OCR_TXT"
                    diag["extraction_method"] = "sidecar_ocr_txt"
                    break

            # Fallback 2: check for local tesseract CLI
            if len(re.sub(r"\s+", "", extracted_text)) < 40:
                tess_bin = shutil.which("tesseract")
                if tess_bin:
                    try:
                        proc = subprocess.run(
                            [tess_bin, path, "stdout"],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            timeout=20,
                            check=False,
                        )
                        if proc.returncode == 0 and len(proc.stdout.strip()) >= 40:
                            extracted_text = proc.stdout.strip()
                            diag["ocr_fallback_used"] = True
                            diag["ocr_fallback_status"] = "RECOVERED_VIA_TESSERACT_OCR"
                            diag["extraction_method"] = "tesseract_cli_ocr"
                    except Exception:
                        pass

            if len(re.sub(r"\s+", "", extracted_text)) < 40:
                diag["ocr_fallback_status"] = "OCR_REQUIRED_SCANNED_PDF"
                diag["extraction_method"] = "scanned_pdf_no_text_layer"
                if not extracted_text:
                    extracted_text = "[SCANNED_DOCUMENT_OCR_REQUIRED: PDF contains image/scan pages without an extractable text layer. Provide OCR text or a .docx/.txt resume.]"

        return extracted_text, diag

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        raw_txt = f.read()
    if "[SCANNED_DOCUMENT_OCR_REQUIRED" in raw_txt or len(re.sub(r"\s+", "", raw_txt)) < 25:
        diag["is_scanned_document"] = True
        diag["ocr_fallback_status"] = "OCR_REQUIRED_LOW_TEXT_DENSITY"
    return raw_txt, diag


def load_resume_file(path: str) -> str:
    """Load resume text from .txt, .md, .docx (zero-dependency XML), or .pdf (with stdlib stream & OCR fallbacks)."""
    text, _ = extract_resume_with_ocr_fallback(path)
    return text

NON_STANDARD_HEADERS = [
    "where i've been",
    "my journey",
    "what i do",
    "career story",
    "about me",
    "toolbox",
    "superpowers",
]

_MONTH_TOKEN = (
    r"(?:"
    r"Jan(?:uary|uar|vier|v)?|Ene(?:ro)?|"
    r"Feb(?:ruary|ruar|rero)?|F[eé]vr(?:ier)?|"
    r"Mar(?:ch|zo|s)?|M[aä]r(?:z)?|Maerz|Mrz|"
    r"Apr(?:il)?|Abr(?:il)?|Avr(?:il)?|"
    r"May(?:o)?|Mai|"
    r"Jun(?:e|io|i)?|Juin|"
    r"Jul(?:y|io|i)?|Juil(?:let)?|"
    r"Aug(?:ust)?|Ago(?:sto)?|Ao[uû]t|"
    r"Sep(?:t(?:ember|iembre|embre)?)?|Set(?:iembre)?|"
    r"Oct(?:ober|ubre|obre)?|Okt(?:ober)?|"
    r"Nov(?:ember|iembre|embre)?|"
    r"Dec(?:ember)?|Dic(?:iembre)?|D[eé]c(?:embre)?|Dez(?:ember)?"
    r")\.?\s+(?:'?\d{2}|\d{4})"
)
_QUARTER_OR_SEASON_TOKEN = r"(?:(?:Q[1-4]|Spring|Summer|Fall|Autumn|Winter)\s+\d{4})"
_ISO_FULL_DATE_TOKEN = r"(?:\d{4}[/.-](?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])|(?:0?[1-9]|1[0-2])[/.-](?:0?[1-9]|[12]\d|3[01])[/.-]\d{4})"
_NUM_DATE_TOKEN = r"(?:(?:0?[1-9]|1[0-2])[/.-]\d{4}|\d{4}[/.-](?:0?[1-9]|1[0-2]))"
_SINGLE_DATE_PATTERN = rf"(?:{_ISO_FULL_DATE_TOKEN}|{_NUM_DATE_TOKEN}|{_MONTH_TOKEN}|{_QUARTER_OR_SEASON_TOKEN})"

_PRESENT_TOKEN_PATTERN = (
    r"(?:Present|Current|Now|Ongoing|To\s+Date|Actualidad|Actual|Presente|Hoy|"
    r"Pr[eé]sent|Actuel|Aujourd'hui|Heute|Aktuell|Gegenwart|Laufend)"
)

DATE_RANGE_REGEX = re.compile(
    rf"(?P<start>{_SINGLE_DATE_PATTERN})"
    r"\s*(?:[-–—―~]|to|through|thru|until|hasta|au|à|bis)\s*"
    rf"(?P<end>{_SINGLE_DATE_PATTERN}|{_PRESENT_TOKEN_PATTERN})",
    re.IGNORECASE,
)

YEAR_ONLY_RANGE_REGEX = re.compile(
    rf"\b(19\d{{2}}|20\d{{2}})\s*(?:[-–—―~]|to|bis|au|hasta)\s*(19\d{{2}}|20\d{{2}}|{_PRESENT_TOKEN_PATTERN})\b",
    re.IGNORECASE,
)

CAREER_GAP_HEADER_RE = re.compile(
    r"\b(?:career\s+break|parental\s+leave|maternity\s+leave|paternity\s+leave|family\s+caregiving|"
    r"caregiving\s+sabbatical|medical\s+leave|sabbatical|planned\s+sabbatical|full[-\s]time\s+caregiver|"
    r"pausa\s+profesional|licencia\s+parental|cong[eé]\s+parental|cong[eé]\s+sabbatique|elternzeit|sabbatjahr)\b",
    re.IGNORECASE,
)

_MULTILINGUAL_MONTH_MAP: Dict[str, int] = {
    "jan": 1, "january": 1, "januar": 1, "janvier": 1, "janv": 1, "ene": 1, "enero": 1,
    "feb": 2, "february": 2, "februar": 2, "febrero": 2, "fevr": 2, "févr": 2, "fevrier": 2, "février": 2,
    "mar": 3, "march": 3, "marzo": 3, "mars": 3, "mär": 3, "märz": 3, "maerz": 3, "mrz": 3,
    "apr": 4, "april": 4, "abr": 4, "abril": 4, "avr": 4, "avril": 4,
    "may": 5, "mayo": 5, "mai": 5,
    "jun": 6, "june": 6, "junio": 6, "juni": 6, "juin": 6,
    "jul": 7, "july": 7, "julio": 7, "juli": 7, "juil": 7, "juillet": 7,
    "aug": 8, "august": 8, "ago": 8, "agosto": 8, "aout": 8, "août": 8,
    "sep": 9, "sept": 9, "september": 9, "septiembre": 9, "set": 9, "setiembre": 9, "septembre": 9,
    "oct": 10, "october": 10, "octubre": 10, "octobre": 10, "okt": 10, "oktober": 10,
    "nov": 11, "november": 11, "noviembre": 11, "novembre": 11,
    "dec": 12, "december": 12, "dic": 12, "diciembre": 12, "déc": 12, "décembre": 12, "decembre": 12, "dez": 12, "dezember": 12,
    "q1": 1, "spring": 3,
    "q2": 4, "summer": 6,
    "q3": 7, "fall": 9, "autumn": 9,
    "q4": 10, "winter": 12,
}

COMMON_ACRONYM_EXEMPTIONS = {
    "SQL", "AWS", "GCP", "API", "REST", "JSON", "HTML", "CSS", "XML", "YAML", "CSV", "PDF",
    "SDK", "CLI", "GUI", "IDE", "URL", "HTTP", "HTTPS", "SSH", "TLS", "SSL", "DNS", "TCP",
    "UDP", "VPN", "VPC", "IAM", "RBAC", "SSO", "MFA", "JWT", "CICD", "TDD", "BDD", "OOP",
    "ORM", "MVC", "SPA", "SSR", "PWA", "GPU", "CPU", "RAM", "SSD", "IOS", "UNIX", "USA",
    "USD", "EUR", "GBP", "MBA", "CPA", "CFA", "PMP", "SHRM", "PHR", "CISSP", "CISA", "CISM",
    "CCNA", "CCNP", "ITIL", "HIPAA", "GDPR", "SOC", "PCI", "DSS", "SOX", "GAAP", "IFRS",
    "FINRA", "SEC", "FDA", "OSHA", "EPA", "FERPA", "CCPA", "NIST", "ISO", "ERP", "CRM",
    "HCM", "SCM", "WMS", "TMS", "MES", "PLM", "QMS", "CMS", "LMS", "DMS", "ATS", "HRIS",
    "SLA", "SLO", "SLI", "KPI", "OKR", "ROI", "ROAS", "ARR", "MRR", "CAC", "LTV", "EBITDA",
    "CAGR", "YOY", "QOQ", "MOM", "COGS", "CAPEX", "OPEX", "B2B", "B2C", "DTC", "GTM", "RFP",
    "RFQ", "RFI", "SOW", "MSA", "NDA", "POA", "POC", "MVP", "UAT", "SDLC", "SAAS", "PAAS",
    "IAAS", "DAAS", "ETL", "ELT", "OLAP", "OLTP", "RDBMS", "NOSQL", "LLM", "NLP", "NLU",
    "OCR", "RAG", "MLOPS", "DEVOPS", "SECOPS", "FINOPS", "GITOPS", "SAP", "HANA", "ABAP",
    "SFDC", "CEO", "CFO", "CTO", "COO", "CIO", "CISO", "CMO", "CRO", "CHRO", "CPO", "SVP",
    "EVP", "EKS", "ECS", "RDS", "S3", "EC2", "VBA", "DAX", "SSIS", "SSRS", "SSAS", "DBMS",
    "WFM", "NICE", "IEX", "ASA", "AHT", "FCR", "ACD", "IVR", "BPO", "UHG", "EMR", "EHR",
    "EPIC", "HEDIS", "NCQA", "ASQ", "DMAIC", "SIPOC", "KANBAN", "SCRUM", "UKG",
}


def _parse_date_token(token: str, reference_date: datetime) -> Optional[datetime]:
    token = token.strip()
    if re.match(rf"^{_PRESENT_TOKEN_PATTERN}$", token, re.IGNORECASE):
        return reference_date

    # Check multilingual month / quarter / season + 2/4-digit year (e.g. "Enero 2021", "Févr. 2020", "Q2 2022", "Jan '21")
    m_word = re.match(r"^([A-Za-zÀ-ÿ0-9]+)\.?\s+'?(\d{2}|\d{4})$", token)
    if m_word:
        m_key = m_word.group(1).lower()
        yr_str = m_word.group(2)
        if m_key in _MULTILINGUAL_MONTH_MAP:
            yr = int(yr_str)
            if yr < 100:
                yr = (2000 + yr) if yr <= 60 else (1900 + yr)
            return datetime(yr, _MULTILINGUAL_MONTH_MAP[m_key], 1)

    normalized = token.replace(".", "/").replace("-", "/")
    for fmt in ("%m/%Y", "%Y/%m", "%Y/%m/%d", "%m/%d/%Y", "%B %Y", "%b %Y"):
        try:
            dt = datetime.strptime(normalized, fmt)
            return datetime(dt.year, dt.month, 1)
        except ValueError:
            continue
    return None


def _merge_intervals(intervals: List[Tuple[datetime, datetime]]) -> float:
    """Merge overlapping (start, end) date intervals and return total net years."""
    valid = [(s, e) for s, e in intervals if s is not None and e is not None and e >= s]
    if not valid:
        return 0.0
    valid.sort(key=lambda x: x[0])
    merged: List[Tuple[datetime, datetime]] = [valid[0]]
    for start, end in valid[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    total_days = sum((e - s).days for s, e in merged)
    return max(0.0, total_days / 365.25)


def _is_standalone_header_line(line: str) -> bool:
    """Return True if a line looks like a standalone company/title header preceding a date line."""
    s = line.strip()
    if not s or len(s) > 90:
        return False
    if re.match(r"^[-*•]|^\d+[.)]\s", s):
        return False
    if s.endswith("."):
        return False
    return True


def parse_resume(raw_text: str, reference_date: Optional[datetime] = None) -> ParsedResume:
    if reference_date is None:
        reference_date = datetime.now()

    lines = raw_text.splitlines()
    sections: Dict[str, List[str]] = {
        "header": [],
        "summary": [],
        "experience": [],
        "education": [],
        "certifications": [],
        "skills": [],
    }
    current_section = "header"
    deductions: List[Tuple[str, float]] = []

    found_non_standard_header = False
    has_table_or_columns = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Detect ASCII/Markdown tables or multi-column pipe layouts (excluding contact header line)
        if stripped.count("|") >= 2 and not re.search(r"\b(?:19|20)\d{2}\b", stripped) and "@" not in stripped:
            has_table_or_columns = True

        clean_header = stripped.lstrip("#*- \t").rstrip("#*:_ \t").lower()
        matched_sec = _classify_standard_header(clean_header)
        if matched_sec:
            current_section = matched_sec
            continue
        elif clean_header in NON_STANDARD_HEADERS:
            found_non_standard_header = True
            if "been" in clean_header or "journey" in clean_header:
                current_section = "experience"
            elif "toolbox" in clean_header or "superpowers" in clean_header:
                current_section = "skills"
            else:
                current_section = "summary"
            continue

        # Handle 2-column / table rows where the left cell is a section header (e.g. "Work Experience | Senior Analyst (01/2021 - Present)")
        if "|" in stripped and "@" not in stripped:
            parts = [p.strip() for p in stripped.split("|") if p.strip()]
            if len(parts) >= 2:
                left_clean = parts[0].lstrip("#*- \t").rstrip("#*:_ \t").lower()
                left_sec = _classify_standard_header(left_clean)
                if left_sec:
                    current_section = left_sec
                    remainder = " — ".join(parts[1:])
                    if remainder:
                        sections[current_section].append(remainder)
                    continue

        sections[current_section].append(stripped)

    # Promote unlabeled summary/headline lines from header into summary if no explicit Summary header was used
    if not sections["summary"] and len(sections["header"]) > 2:
        kept_header: List[str] = []
        for idx, hline in enumerate(sections["header"]):
            is_contact = bool(re.search(r"(@|\b\d{3}[-.)\s]\d{3}[-.\s]\d{4}|linkedin\.com|/in/)", hline, re.IGNORECASE))
            if idx == 0 or is_contact:
                kept_header.append(hline)
            else:
                sections["summary"].append(hline)
        sections["header"] = kept_header

    if found_non_standard_header:
        deductions.append(("Non-standard section headers detected (-2.0 pts)", 2.0))
    if not sections["experience"]:
        deductions.append(("Missing standard 'Work Experience' section header (-2.0 pts)", 2.0))

    if has_table_or_columns:
        deductions.append(("Multi-column table / pipe layout detected (-3.0 pts)", 3.0))

    # Parse Role Blocks inside Experience section
    exp_lines = sections["experience"] if sections["experience"] else lines
    roles: List[RoleBlock] = []
    current_role_lines: List[str] = []
    current_header = "General Experience"
    current_start: Optional[datetime] = None
    current_end: Optional[datetime] = None
    pending_pre_date_headers: List[str] = []

    for line in exp_lines:
        m = DATE_RANGE_REGEX.search(line)
        if m:
            carried_header_lines: List[str] = []
            if current_start is None and current_end is None:
                carried_header_lines = list(pending_pre_date_headers)
                pending_pre_date_headers.clear()
            elif len(current_role_lines) > 1 and _is_standalone_header_line(current_role_lines[-1]):
                carried_header_lines = [current_role_lines.pop()]

            if current_role_lines and (current_start is not None or current_end is not None):
                if current_start and current_end:
                    dur = max(0.0, (current_end - current_start).days / 365.25)
                    is_recent = ((reference_date - current_end).days / 365.25) <= 5.0
                else:
                    dur = 0.0
                    is_recent = False
                roles.append(
                    RoleBlock(
                        header=current_header,
                        start_date=current_start,
                        end_date=current_end,
                        is_recent=is_recent,
                        duration_years=dur,
                        text="\n".join(current_role_lines),
                        lines=list(current_role_lines),
                        is_career_gap=bool(CAREER_GAP_HEADER_RE.search(current_header)),
                    )
                )

            header_parts = carried_header_lines + [line]
            current_header = " — ".join(header_parts)
            current_start = _parse_date_token(m.group("start"), reference_date)
            current_end = _parse_date_token(m.group("end"), reference_date)
            current_role_lines = list(header_parts)
        else:
            if current_start is None and current_end is None and _is_standalone_header_line(line):
                pending_pre_date_headers.append(line)
            current_role_lines.append(line)

    if current_role_lines:
        if current_start and current_end:
            dur = max(0.0, (current_end - current_start).days / 365.25)
            is_recent = ((reference_date - current_end).days / 365.25) <= 5.0
        else:
            dur = 0.0
            is_recent = False
        roles.append(
            RoleBlock(
                header=current_header,
                start_date=current_start,
                end_date=current_end,
                is_recent=is_recent,
                duration_years=dur,
                text="\n".join(current_role_lines),
                lines=list(current_role_lines),
                is_career_gap=bool(CAREER_GAP_HEADER_RE.search(current_header)),
            )
        )

    # Check date hygiene
    has_valid_dates = any(r.start_date is not None and r.end_date is not None for r in roles)
    has_year_only_dates = bool(YEAR_ONLY_RANGE_REGEX.search("\n".join(exp_lines))) and not has_valid_dates
    if not has_valid_dates or has_year_only_dates:
        deductions.append(("Missing or non-standard employment dates (require MM/YYYY or Month YYYY) (-3.0 pts)", 3.0))

    # Check obscure unexpanded acronyms inside summary/experience/skills only (excluding candidate name/header, role headers & all-caps lines)
    role_header_tokens = {
        tok for r in roles for tok in re.findall(r"\b[A-Z]{3,6}\b", r.header)
    }
    body_lines = [
        ln for sec in ("summary", "experience", "skills")
        for ln in sections.get(sec, [])
        if not ln.isupper()
    ]
    body_text = "\n".join(body_lines)
    acronyms_found = set(re.findall(r"\b[A-Z]{3,6}\b", body_text))
    obscure_unexpanded = [
        a for a in acronyms_found
        if a not in COMMON_ACRONYM_EXEMPTIONS and a not in role_header_tokens and f"({a})" not in raw_text
    ]
    if len(obscure_unexpanded) >= 3:
        deductions.append(
            (f"Unexpanded acronyms without full spelling ({', '.join(sorted(obscure_unexpanded)[:4])}) (-2.0 pts)", 2.0)
        )

    total_deduction = sum(d[1] for d in deductions)
    schema_score = max(0.0, 10.0 - total_deduction)
    work_roles = [r for r in roles if not r.is_career_gap]
    career_gaps = [r for r in roles if r.is_career_gap]
    dated_work_intervals = [
        (r.start_date, r.end_date)
        for r in work_roles
        if r.start_date is not None and r.end_date is not None
    ]
    gross_tenure = sum(max(0.0, (e - s).days / 365.25) for s, e in dated_work_intervals)
    total_tenure = _merge_intervals(dated_work_intervals)
    overlap_dedup = max(0.0, round(gross_tenure - total_tenure, 2))

    joined_sections = {k: "\n".join(v) for k, v in sections.items()}
    return ParsedResume(
        raw_text=raw_text,
        sections=joined_sections,
        roles=work_roles if work_roles else roles,
        total_relevant_tenure_years=round(total_tenure, 2),
        schema_deductions=deductions,
        schema_score=schema_score,
        career_gaps=career_gaps,
        gross_unmerged_tenure_years=round(gross_tenure, 2),
        overlapping_years_deduplicated=overlap_dedup,
    )


# ---------------------------------------------------------------------------
# Deterministic Matching & Scoring Engine
# ---------------------------------------------------------------------------

def _build_term_regex(term: str) -> re.Pattern:
    """Build a punctuation-safe boundary regex (handles B.S., C++, C#, .NET, Node.js, etc.)."""
    cleaned = term.strip()
    # Allow optional trailing period if the term ends with a period (e.g. 'B.S.' matches 'B.S.' or 'B.S')
    if cleaned.endswith("."):
        core = re.escape(cleaned[:-1]) + r"\.?"
    else:
        core = re.escape(cleaned)

    left_bound = r"(?<!\w)" if (cleaned and (cleaned[0].isalnum() or cleaned[0] == "_")) else r"(?:^|(?<=[\s,;/(\[\x22']))"
    right_Char = cleaned[-1] if not cleaned.endswith(".") else (cleaned[-2] if len(cleaned) >= 2 else cleaned[-1])
    right_bound = r"(?!\w)" if (right_Char.isalnum() or right_Char == "_") else r"(?:$|(?=[\s,;/.)\]\x22':]))"
    return re.compile(rf"{left_bound}{core}{right_bound}", re.IGNORECASE)


def _term_in_text(term: str, text: str) -> Optional[str]:
    """Return the first line containing `term` (boundary-aware), or None."""
    if not term or not text:
        return None
    pattern = _build_term_regex(term)
    for line in text.splitlines():
        if pattern.search(line):
            return line.strip()
    return None


# ---------------------------------------------------------------------------
# Transferred Skills vs. Competing Products Taxonomy
# ---------------------------------------------------------------------------

COMPETING_PRODUCT_FAMILIES: Dict[str, Dict[str, List[str]]] = {
    "contact_center_wfm_platform": {
        "generic_labels": [
            "wfm platform",
            "wfm software",
            "wfm system",
            "wfm tool",
            "workforce management platform",
            "workforce management software",
            "workforce management system",
        ],
        "competing_products": [
            "NICE IEX",
            "NICE WFM",
            "Verint",
            "Genesys Cloud",
            "Aspect WFM",
            "Alvaria",
            "Calabrio",
            "Assembled",
            "Injixo",
            "Playvox",
        ],
    },
    "labor_timekeeping_platform": {
        "generic_labels": [
            "timekeeping system",
            "labor management system",
            "time and attendance system",
        ],
        "competing_products": [
            "UKG Dimensions",
            "UKG Pro",
            "Kronos",
            "ADP Workforce Now",
            "Dayforce",
            "Workday Time Tracking",
        ],
    },
    "bi_visualization_platform": {
        "generic_labels": [
            "bi tool",
            "bi platform",
            "business intelligence tool",
            "business intelligence platform",
            "data visualization tool",
            "dashboarding tool",
        ],
        "competing_products": [
            "Power BI",
            "Tableau",
            "Looker",
            "Qlik Sense",
            "QlikView",
            "MicroStrategy",
            "Domo",
            "Superset",
        ],
    },
    "cloud_data_warehouse": {
        "generic_labels": [
            "cloud data warehouse",
            "data warehouse platform",
            "mpp data warehouse",
        ],
        "competing_products": [
            "Snowflake",
            "BigQuery",
            "Amazon Redshift",
            "Redshift",
            "Databricks",
            "Azure Synapse",
            "Teradata",
        ],
    },
    "erp_enterprise_platform": {
        "generic_labels": [
            "erp system",
            "erp platform",
            "enterprise resource planning system",
        ],
        "competing_products": [
            "SAP S/4HANA",
            "SAP ECC",
            "Oracle NetSuite",
            "Oracle ERP Cloud",
            "Microsoft Dynamics 365",
            "Workday Financials",
        ],
    },
    "statistical_programming_language": {
        "generic_labels": [
            "statistical programming language",
            "data science language",
        ],
        "competing_products": [
            "Python",
            "R",
            "SAS",
            "MATLAB",
            "Julia",
        ],
    },
}


def classify_skill_transferability(
    req: JDRequirement,
    resume_text: str,
    match_credit: float,
    matched_term: str,
) -> Tuple[str, str]:
    """
    Distinguish:
      1. `EXACT_OR_JD_ALTERNATIVE` (`1.0x`): exact requirement name or JD-offered alternative matched.
      2. `GENERIC_CATEGORY_SATISFIED_BY_SPECIFIC_TOOL` (`1.0x`): JD asked for a generic tool category
         (e.g., 'WFM platform', 'BI tool') and candidate holds a specific named platform.
      3. `TRANSFERRED_METHODOLOGY_SYNONYM` (`0.5x`): same capability worded differently
         (e.g., 'capacity planning' <-> 'Workforce Capacity Modeling').
      4. `COMPETING_PRODUCT_NO_CREDIT` (`0.0x`): JD asked for a specific proprietary product
         (e.g., 'Verint', 'Power BI') without alternatives, and candidate holds a competing product
         in the same family (e.g., 'NICE IEX', 'Tableau'). Earns 0.0x scoring credit (never swapped),
         while surfacing the adjacent product for human reviewer awareness.
      5. `UNVERIFIED_TRUE_GAP` (`0.0x`): neither the tool nor a competing product in the family is present.
    """
    req_low = req.name.strip().lower()
    if match_credit == 1.0:
        for fam in COMPETING_PRODUCT_FAMILIES.values():
            if req_low in fam["generic_labels"] and matched_term.strip().lower() != req_low:
                return ("GENERIC_CATEGORY_SATISFIED_BY_SPECIFIC_TOOL", "")
        return ("EXACT_OR_JD_ALTERNATIVE", "")
    if match_credit == 0.5:
        return ("TRANSFERRED_METHODOLOGY_SYNONYM", "")

    # match_credit == 0.0: check if req.name is a specific product in a family and resume has a competing product
    allowed_set = {req_low} | {a.strip().lower() for a in req.alternatives} | {s.strip().lower() for s in req.synonyms}
    for fam in COMPETING_PRODUCT_FAMILIES.values():
        fam_products_low = {p.lower(): p for p in fam["competing_products"]}
        if req_low in fam_products_low:
            for prod_low, prod_disp in fam_products_low.items():
                if prod_low in allowed_set:
                    continue
                if _term_in_text(prod_disp, resume_text):
                    return ("COMPETING_PRODUCT_NO_CREDIT", prod_disp)
    return ("UNVERIFIED_TRUE_GAP", "")


def evaluate_requirement(req: JDRequirement, resume: ParsedResume) -> AuditLedgerRow:
    """
    Evaluate a single JD requirement for Match Credit and Placement/Recency Multiplier.

    Selects the primary match that maximizes composite ATS points: `credit * (45 + 20 * mult)`,
    ensuring an exact literal match (1.0 credit) on the resume is never erased by a 0.5x synonym,
    while also tracking the highest contextual factor `max(credit * mult)` for Component 2.
    Enforces the asymmetrical generic-to-specific rule: if the JD requirement is a generic tool
    category (e.g., 'WFM platform' or 'BI tool'), any specific platform in that family satisfies
    it at 1.0x credit; if the JD requires a specific product, competing products earn 0.0x credit.
    """
    generic_specifics: List[str] = []
    req_low = req.name.strip().lower()
    for fam in COMPETING_PRODUCT_FAMILIES.values():
        if req_low in fam["generic_labels"]:
            generic_specifics.extend(fam["competing_products"])

    candidates: List[Tuple[str, float]] = (
        [(req.name, 1.0)]
        + [(alt, 1.0) for alt in req.alternatives]
        + [(spec, 1.0) for spec in generic_specifics if spec.lower() not in {req_low} | {a.lower() for a in req.alternatives}]
        + [(syn, 0.5) for syn in req.synonyms]
    )

    best_credit = 0.0
    best_multiplier = 0.0
    best_term = ""
    best_zone = "MISSING"
    best_quote = "MISSING"
    best_composite_val = 0.0

    max_context_factor = 0.0
    best_literal_mult: Optional[float] = None

    def _record_candidate(term: str, credit: float, mult: float, zone: str, quote: str) -> None:
        nonlocal best_credit, best_multiplier, best_term, best_zone, best_quote, best_composite_val
        nonlocal max_context_factor, best_literal_mult

        ctx_factor = credit * mult
        if ctx_factor > max_context_factor:
            max_context_factor = ctx_factor
        if credit == 1.0 and (best_literal_mult is None or mult > best_literal_mult):
            best_literal_mult = mult

        comp_val = credit * (45.0 + 20.0 * mult)
        if comp_val > best_composite_val:
            best_composite_val = comp_val
            best_credit = credit
            best_multiplier = mult
            best_term = term
            best_zone = zone
            best_quote = quote

    for term, credit in candidates:
        # 1. Check Recent Work Experience (<= 5 years)
        for role in resume.roles:
            if role.is_recent:
                line = _term_in_text(term, role.text)
                if line:
                    _record_candidate(term, credit, 1.0, "Recent Work Experience (<=5 yrs)", line)

        # 2. Check Historical Work Experience (> 5 years)
        for role in resume.roles:
            if not role.is_recent:
                line = _term_in_text(term, role.text)
                if line:
                    _record_candidate(term, credit, 0.8, "Historical Experience (>5 yrs)", line)

        # 3. Check Education / Certifications / Summary
        for sec_name in ("certifications", "education", "summary"):
            line = _term_in_text(term, resume.sections.get(sec_name, ""))
            if line:
                mult = 1.0 if (req.is_knockout_gate or sec_name in ("certifications", "education")) else 0.4
                _record_candidate(term, credit, mult, f"{sec_name.title()} Section", line)

        # 4. Check Standalone Skills Section (0.4x unverified penalty if not in experience)
        line = _term_in_text(term, resume.sections.get("skills", ""))
        if line:
            _record_candidate(term, credit, 0.4, "Skills Section Only (Unverified 0.4x)", line)

        # 5. Fallback scan across full text if sections were unstructured
        if best_credit == 0.0:
            line = _term_in_text(term, resume.raw_text)
            if line:
                _record_candidate(term, credit, 0.4, "Unstructured Text (0.4x)", line)

    t_class, adj_prod = classify_skill_transferability(req, resume.raw_text, best_credit, best_term)
    return AuditLedgerRow(
        requirement=req,
        match_credit=best_credit,
        placement_multiplier=best_multiplier,
        matched_term=best_term,
        placement_zone=best_zone,
        exact_quote=best_quote,
        context_credit_factor=best_credit * best_multiplier,
        literal_placement_multiplier=best_literal_mult,
        transferability_class=t_class,
        adjacent_competing_product=adj_prod,
    )


def _score_title_alignment(alignment: AlignmentConfig, resume: ParsedResume) -> float:
    """
    Component 3a: Title Taxonomy Match (Max 5.0 pts).
      - 5.0 pts: Exact target_title in recent role headers (or summary/experience if no roles parsed)
      - 3.0 pts: Exact target_title in historical roles, OR title_synonym in recent/any role
      - 1.0 pt : Partial title token overlap in role headers/summary
      - 0.0 pts: Unrelated / no title configured
    """
    if not alignment.target_title and not alignment.title_synonyms:
        return 0.0

    recent_headers = "\n".join(r.header for r in resume.roles if r.is_recent)
    all_headers = "\n".join(r.header for r in resume.roles) or resume.raw_text
    summary_and_headers = f"{recent_headers}\n{resume.sections.get('summary', '')}"

    if alignment.target_title:
        if _term_in_text(alignment.target_title, summary_and_headers):
            return 5.0
        if _term_in_text(alignment.target_title, resume.raw_text):
            return 3.0

    for syn in alignment.title_synonyms:
        if _term_in_text(syn, all_headers) or _term_in_text(syn, resume.raw_text):
            return 3.0

    # Check partial token overlap (tokens >= 4 chars)
    stop_words = {"senior", "junior", "lead", "principal", "staff", "associate", "with", "from"}
    title_tokens = [
        t.lower() for t in re.findall(r"[A-Za-z]{4,}", alignment.target_title)
        if t.lower() not in stop_words
    ]
    if title_tokens and any(_term_in_text(tok, all_headers) for tok in title_tokens):
        return 1.0

    return 0.0


def run_ats_audit(
    resume_text: str,
    requirements: List[JDRequirement],
    alignment: AlignmentConfig,
    reference_date: Optional[datetime] = None,
    org_profile: Optional[Any] = None,
) -> AuditReport:
    resume = parse_resume(resume_text, reference_date=reference_date)

    # Extract organization-native weights, thresholds, and equivalency policies if provided
    c1_max = 45.0
    c2_max = 20.0
    c3_max = 15.0
    c4_max = 10.0
    c5_max = 10.0
    strong_thresh = 80.0
    border_thresh = 65.0
    eff_cfg_summary: Dict[str, Any] = {
        "org_id": "default-baseline-org",
        "org_name": "Baseline Reference Configuration (Standard 100-Point Heuristic)",
        "config_version": "1.5.0",
        "approval_status": "APPROVED",
        "comp1_keyword_max": 45.0,
        "comp2_context_max": 20.0,
        "comp3_alignment_max": 15.0,
        "comp4_knockout_max": 10.0,
        "comp5_schema_max": 10.0,
        "jd_strong_match_threshold": 80.0,
        "jd_borderline_threshold": 65.0,
        "require_degree_equivalency_by_default": True,
        "bachelor_equivalency_years": 4.0,
    }
    if org_profile is not None:
        st = getattr(org_profile, "scoring_and_thresholds", None)
        ge = getattr(org_profile, "gate_and_equivalency_policy", None)
        aw = getattr(org_profile, "approval_workflow", None)
        if st is not None:
            c1_max = float(st.comp1_keyword_max)
            c2_max = float(st.comp2_context_max)
            c3_max = float(st.comp3_alignment_max)
            c4_max = float(st.comp4_knockout_max)
            c5_max = float(st.comp5_schema_max)
            strong_thresh = float(st.jd_strong_match_threshold)
            border_thresh = float(st.jd_borderline_threshold)
        eff_cfg_summary = {
            "org_id": getattr(org_profile, "org_id", "custom-org"),
            "org_name": getattr(org_profile, "org_name", "Custom Organization Profile"),
            "config_version": getattr(org_profile, "config_version", "1.0.0"),
            "config_sha256": getattr(org_profile, "config_sha256", ""),
            "approval_status": getattr(aw, "approval_status", "APPROVED") if aw else "APPROVED",
            "ta_or_comp_approver_id": getattr(aw, "ta_or_comp_approver_id", "") if aw else "",
            "compliance_or_legal_approver_id": getattr(aw, "compliance_or_legal_approver_id", "") if aw else "",
            "comp1_keyword_max": c1_max,
            "comp2_context_max": c2_max,
            "comp3_alignment_max": c3_max,
            "comp4_knockout_max": c4_max,
            "comp5_schema_max": c5_max,
            "jd_strong_match_threshold": strong_thresh,
            "jd_borderline_threshold": border_thresh,
            "require_degree_equivalency_by_default": getattr(ge, "require_degree_equivalency_by_default", True) if ge else True,
            "bachelor_equivalency_years": getattr(ge, "bachelor_equivalency_years", 4.0) if ge else 4.0,
        }

    ledger: List[AuditLedgerRow] = [evaluate_requirement(req, resume) for req in requirements]

    total_max_weights = sum(r.max_weight for r in ledger) or 1.0
    sum_kw_matched = sum(r.keyword_weighted_score for r in ledger)
    sum_literal_matched = sum(r.literal_only_keyword_score for r in ledger)
    sum_earned_context = sum(r.earned_item_score for r in ledger)

    # Component 1: Weighted Keyword & Taxonomy Score
    comp1_kw_score = c1_max * (sum_kw_matched / total_max_weights)
    comp1_literal_score = c1_max * (sum_literal_matched / total_max_weights)

    # Component 2: Contextual Placement & Recency Score
    comp2_context_score = c2_max * (sum_earned_context / total_max_weights)

    # Component 3: Title, Seniority & Calculated Tenure Alignment
    title_pts = _score_title_alignment(alignment, resume)

    if resume.total_relevant_tenure_years >= alignment.min_years_required:
        tenure_pts = 5.0
    elif resume.total_relevant_tenure_years >= max(0.0, alignment.min_years_required - 1.0):
        tenure_pts = 3.0
    else:
        tenure_pts = 0.0

    has_quantified_metrics = bool(
        re.search(
            r"(\$[\d,.]+[KMB]?|\d+(?:\.\d+)?%|\b\d{2,}\+?\s*(?:users|clients|engineers|employees|projects|markets|accounts|vendors|centers|hours))",
            resume.raw_text,
            re.IGNORECASE,
        )
    )
    has_ownership_verbs = any(
        re.search(rf"\b{re.escape(v)}\b", resume.raw_text, re.IGNORECASE)
        for v in alignment.target_seniority_keywords
    )
    if has_quantified_metrics and has_ownership_verbs:
        seniority_pts = 5.0
    elif has_quantified_metrics or has_ownership_verbs:
        seniority_pts = 2.5
    else:
        seniority_pts = 0.0

    raw_comp3 = title_pts + tenure_pts + seniority_pts
    comp3_alignment_score = (raw_comp3 / 15.0) * c3_max
    comp3_breakdown = {
        "Title Taxonomy Match": title_pts,
        f"Calculated Domain Tenure ({resume.total_relevant_tenure_years:.1f} yrs vs {alignment.min_years_required:.1f} yrs req)": tenure_pts,
        "Seniority & Quantified Enterprise Scale": seniority_pts,
    }

    # Component 4: Hard Knockout / Minimum Gate Verification
    knockout_rows = [r for r in ledger if r.requirement.is_knockout_gate]
    failed_knockouts = [r.requirement.name for r in knockout_rows if r.match_credit == 0.0]
    knockout_passed = len(failed_knockouts) == 0
    comp4_knockout_score = c4_max if knockout_passed else 0.0

    # Component 5: HR-XML Parseability & Schema Integrity
    st_obj = getattr(org_profile, "scoring_and_thresholds", None) if org_profile is not None else None
    decouple_c5 = bool(getattr(st_obj, "decouple_formatting_from_capability_score", False)) if st_obj is not None else False
    if decouple_c5:
        comp5_schema_score = c5_max
    else:
        comp5_schema_score = (resume.schema_score / 10.0) * c5_max

    composite_score = comp1_kw_score + comp2_context_score + comp3_alignment_score + comp4_knockout_score + comp5_schema_score
    comp2_literal_context = c2_max * (sum(r.literal_earned_item_score for r in ledger) / total_max_weights)
    legacy_literal_score = comp1_literal_score + comp2_literal_context + comp3_alignment_score + comp4_knockout_score + comp5_schema_score

    # Verdict & Primary Bottleneck
    if not knockout_passed:
        verdict = "Gate Risk (knockout gate not verified)"
        primary_bottleneck = f"Mandatory gate(s) not found on the resume: {', '.join(failed_knockouts)}. Confirm with the candidate; 'or equivalent' wording may apply."
    elif composite_score >= strong_thresh:
        verdict = "Strong match"
        primary_bottleneck = "High alignment across taxonomy and context; minor lexical polish remaining."
    elif composite_score >= border_thresh:
        verdict = "Borderline"
        primary_bottleneck = "Semantic synonym dilution (0.5x) and skills trapped in bottom Skills section (0.4x) suppressing rank."
    else:
        verdict = "Weak match"
        primary_bottleneck = "High-weight Required (2.0x) hard skills missing or unverified in recent Work Experience."

    # Build Highest-ROI Point Recovery Plan
    recovery_plan: List[Dict[str, object]] = []
    for r in ledger:
        if r.match_credit == 1.0 and r.placement_multiplier == 1.0:
            continue
        kw_pts_lost = 45.0 * ((r.max_weight - r.keyword_weighted_score) / total_max_weights)
        ctx_pts_lost = 20.0 * ((r.max_weight - r.earned_item_score) / total_max_weights)
        total_pts_recoverable = round(kw_pts_lost + ctx_pts_lost, 2)

        if r.match_credit == 0.5 and r.placement_multiplier == 1.0:
            fix_tier = "Tier 1: Synonym-to-Literal Swap (0.5x -> 1.0x Match Credit)"
            action = f"If '{r.matched_term}' and '{r.requirement.name}' name the same capability, use the JD's exact term; if they are different tools or methods, do not swap - ask the candidate."
        elif r.match_credit == 0.5 and r.placement_multiplier < 1.0:
            fix_tier = f"Tier 1 + Tier 2: Synonym Swap & Contextual Elevation ({r.placement_multiplier:.1f}x -> 1.0x)"
            action = f"If '{r.matched_term}' and '{r.requirement.name}' name the same capability, use the JD's exact term inside a recent Work Experience bullet with action + metric; if they are different tools or methods, ask the candidate instead."
        elif r.match_credit == 1.0 and r.placement_multiplier < 1.0:
            fix_tier = f"Tier 2: Contextual Elevation ({r.placement_multiplier:.1f}x -> 1.0x Placement)"
            action = f"Move '{r.requirement.name}' from {r.placement_zone} into a recent Work Experience bullet with action + metric."
        elif r.requirement.is_knockout_gate:
            fix_tier = "Gate check: not found on resume (0.0x)"
            action = f"Confirm whether the candidate holds '{r.requirement.name}' or an accepted equivalent; list it only if true."
        else:
            fix_tier = "Tier 3: True Gap Interrogation (0.0x Missing Requirement)"
            action = f"Ask whether the candidate has hands-on experience with '{r.requirement.display_name}'; write a bullet only from what they confirm."

        recovery_plan.append(
            {
                "requirement": r.requirement.display_name,
                "tier_label": f"{r.requirement.tier.title()} ({r.requirement.req_multiplier:.1f}x)",
                "max_item_weight": r.max_weight,
                "fix_tier": fix_tier,
                "points_recoverable": total_pts_recoverable,
                "action": action,
            }
        )

    recovery_plan.sort(key=lambda x: float(x["points_recoverable"]), reverse=True)

    # -----------------------------------------------------------------------
    # Build Transparent Uncertainty, 3-Pillar Metrics, Side-by-Side Comparison
    # & Complete Point-Deduction Explanation Ledger
    # -----------------------------------------------------------------------
    # 1. Plausible Score Interval [lower_bound, upper_bound] without fabricating 0.0x missing items
    #    - Lower bound: strict literal-only view minus any unconfirmed-requirement extraction penalty
    #    - Upper bound: credits already-present resume evidence (0.5x synonyms & 0.4x/0.8x skills/historical items) at 1.0x,
    #      while keeping 0.0x missing requirements at 0.0x.
    present_max_kw = sum(r.max_weight for r in ledger if r.match_credit > 0.0)
    upper_c1 = c1_max * (present_max_kw / total_max_weights)
    upper_c2 = c2_max * (present_max_kw / total_max_weights)
    upper_bound_score = min(
        100.0,
        round(upper_c1 + upper_c2 + comp3_alignment_score + comp4_knockout_score + comp5_schema_score, 1),
    )
    inferred_reqs = [r for r in ledger if getattr(r.requirement, "extraction_status", "CONFIRMED") == "INFERRED"]
    extraction_margin = round(min(4.0, len(inferred_reqs) * 0.5), 1)
    lower_bound_score = max(0.0, round(min(composite_score, legacy_literal_score) - extraction_margin, 1))
    interval_width = round(max(0.0, upper_bound_score - lower_bound_score), 1)

    if interval_width <= 3.0:
        uncertainty_band = "HIGH_SCORE_STABILITY (Narrow Interval <= 3.0 pts)"
    elif interval_width <= 15.0:
        uncertainty_band = "MODERATE_INTERPRETATION_SPREAD (Synonym / Placement Sensitivity)"
    else:
        uncertainty_band = "WIDE_INTERPRETATION_SPREAD (High Synonym / Placement / Extraction Sensitivity)"

    # 2. Measurement & Parser Confidence (distinct from candidate score!)
    core_secs = [s for s in ("summary", "experience", "education", "skills") if resume.sections.get(s, "").strip()]
    dated_roles_cnt = sum(1 for r in resume.roles if r.start_date is not None and r.end_date is not None)
    res_parse_conf = 1.0
    if "experience" not in core_secs:
        res_parse_conf -= 0.25
    if "skills" not in core_secs:
        res_parse_conf -= 0.08
    if "education" not in core_secs:
        res_parse_conf -= 0.05
    if dated_roles_cnt == 0:
        res_parse_conf -= 0.25
    res_parse_conf -= min(0.25, (10.0 - resume.schema_score) * 0.025)
    res_parse_conf = round(max(0.10, min(1.0, res_parse_conf)), 3)

    req_conf_vals = [float(getattr(r.requirement, "confidence", 1.0)) for r in ledger] or [1.0]
    req_ext_conf = round(sum(req_conf_vals) / len(req_conf_vals), 3)
    combined_meas_conf = round(0.55 * res_parse_conf + 0.45 * req_ext_conf, 3)
    if combined_meas_conf >= 0.85:
        meas_conf_band = "HIGH_MEASUREMENT_CONFIDENCE (>=85%)"
    elif combined_meas_conf >= 0.70:
        meas_conf_band = "MODERATE_MEASUREMENT_CONFIDENCE (70%-84%)"
    else:
        meas_conf_band = "LOW_MEASUREMENT_CONFIDENCE (<70% — Manual Parse Review Required)"

    # 3. Evidence Completeness (distinct from both score and parser confidence!)
    full_1x_items = [r for r in ledger if r.match_credit == 1.0 and r.placement_multiplier == 1.0]
    partial_items = [r for r in ledger if r.match_credit > 0.0 and not (r.match_credit == 1.0 and r.placement_multiplier == 1.0)]
    missing_items = [r for r in ledger if r.match_credit == 0.0]
    total_reqs_cnt = max(len(ledger), 1)
    weighted_completeness_pct = round((sum_earned_context / total_max_weights) * 100.0, 1)
    item_verification_pct = round(((len(full_1x_items) + 0.5 * len(partial_items)) / total_reqs_cnt) * 100.0, 1)

    if weighted_completeness_pct >= 85.0 and len(missing_items) == 0:
        completeness_band = "COMPLETE_EVIDENCE (>=85% Weighted Contextual Verification)"
    elif weighted_completeness_pct >= 65.0:
        completeness_band = "PARTIAL_EVIDENCE (65%-84% Weighted Contextual Verification — Clarify Gaps)"
    else:
        completeness_band = "SPARSE_EVIDENCE (<65% Weighted Contextual Verification)"

    # 4. Side-by-Side Semantic vs. Literal View Comparison
    divergent_reqs: List[Dict[str, Any]] = []
    for r in ledger:
        sem_c1 = c1_max * (r.keyword_weighted_score / total_max_weights)
        lit_c1 = c1_max * (r.literal_only_keyword_score / total_max_weights)
        sem_c2 = c2_max * (r.earned_item_score / total_max_weights)
        lit_c2 = c2_max * (r.literal_earned_item_score / total_max_weights)
        diff_pts = round((sem_c1 + sem_c2) - (lit_c1 + lit_c2), 2)
        if abs(diff_pts) >= 0.01:
            divergent_reqs.append({
                "requirement": r.requirement.display_name,
                "matched_term": r.matched_term,
                "placement_zone": r.placement_zone,
                "semantic_points": round(sem_c1 + sem_c2, 2),
                "literal_points": round(lit_c1 + lit_c2, 2),
                "semantic_minus_literal_delta": diff_pts,
                "reason": (
                    f"Matched same-capability synonym '{r.matched_term}' (0.5x semantic credit in {r.placement_zone}; "
                    "0.0x in strict literal filter)."
                ),
            })

    side_by_side_components = [
        {
            "component": "1. Requirement Coverage",
            "max_points": round(c1_max, 2),
            "semantic_earned": round(comp1_kw_score, 2),
            "literal_earned": round(comp1_literal_score, 2),
            "delta": round(comp1_kw_score - comp1_literal_score, 2),
            "explanation": "Semantic view credits verified same-capability synonyms at 0.5x; Literal view requires exact JD terms or JD-offered alternatives.",
        },
        {
            "component": "2. Contextual Placement & Recency",
            "max_points": round(c2_max, 2),
            "semantic_earned": round(comp2_context_score, 2),
            "literal_earned": round(comp2_literal_context, 2),
            "delta": round(comp2_context_score - comp2_literal_context, 2),
            "explanation": "Measures whether matched terms appear in recent work experience (1.0x), older roles (0.8x), or skills list only (0.4x).",
        },
        {
            "component": "3. Title, Tenure & Operational Scale",
            "max_points": round(c3_max, 2),
            "semantic_earned": round(comp3_alignment_score, 2),
            "literal_earned": round(comp3_alignment_score, 2),
            "delta": 0.0,
            "explanation": "Deterministic title alignment, merged net tenure vs. required years, and quantified operational scope.",
        },
        {
            "component": "4. Knockout Gate Verification",
            "max_points": round(c4_max, 2),
            "semantic_earned": round(comp4_knockout_score, 2),
            "literal_earned": round(comp4_knockout_score, 2),
            "delta": 0.0,
            "explanation": "Binary verification of required degree, certification, license, or clearance gates on submitted text.",
        },
        {
            "component": "5. Document & Parse Hygiene",
            "max_points": round(c5_max, 2),
            "semantic_earned": round(comp5_schema_score, 2),
            "literal_earned": round(comp5_schema_score, 2),
            "delta": 0.0,
            "explanation": "HR-XML section header standard compliance, date format parseability, and acronym expansion.",
        },
    ]

    # 5. Explicit Missing & Partial Evidence Lists
    missing_evidence_list: List[Dict[str, Any]] = []
    for r in missing_items:
        c1_loss = c1_max * (r.max_weight / total_max_weights)
        c2_loss = c2_max * (r.max_weight / total_max_weights)
        comp_note = (
            f"Candidate holds adjacent competing product '{r.adjacent_competing_product}' (0.0x credit; do not swap, but note transferable ecosystem)."
            if r.adjacent_competing_product
            else "No evidence or synonym detected anywhere in submitted resume text."
        )
        missing_evidence_list.append({
            "requirement": r.requirement.display_name,
            "tier": r.requirement.tier,
            "max_weight": r.max_weight,
            "is_knockout_gate": r.requirement.is_knockout_gate,
            "transferability_class": r.transferability_class,
            "adjacent_competing_product": r.adjacent_competing_product,
            "points_at_stake": round(c1_loss + c2_loss, 2),
            "diagnostic_note": comp_note,
            "candidate_verification_question": (
                f"Do you hold hands-on experience or credentials in '{r.requirement.display_name}' that were omitted from your resume?"
            ),
        })

    partial_evidence_list: List[Dict[str, Any]] = []
    for r in partial_items:
        c1_loss = c1_max * ((r.max_weight - r.keyword_weighted_score) / total_max_weights)
        c2_loss = c2_max * ((r.max_weight - r.earned_item_score) / total_max_weights)
        partial_evidence_list.append({
            "requirement": r.requirement.display_name,
            "tier": r.requirement.tier,
            "matched_term": r.matched_term,
            "match_credit": r.match_credit,
            "placement_multiplier": r.placement_multiplier,
            "placement_zone": r.placement_zone,
            "quote": r.exact_quote,
            "points_deducted": round(c1_loss + c2_loss, 2),
            "what_is_missing_for_full_credit": (
                f"Matched synonym '{r.matched_term}' (0.5x) instead of exact JD term '{r.requirement.name}' (1.0x)."
                if r.match_credit == 0.5 and r.placement_multiplier == 1.0
                else (
                    f"Term '{r.matched_term}' appears only in {r.placement_zone} ({r.placement_multiplier:.1f}x); "
                    "not yet demonstrated in a recent Work Experience bullet (<=5 yrs, 1.0x)."
                )
            ),
        })

    # 6. Complete Point-Deduction Explanations (Reconciling 100.0 -> composite_score)
    point_deductions_ledger: List[Dict[str, Any]] = []
    for r in ledger:
        if r.match_credit == 1.0 and r.placement_multiplier == 1.0:
            continue
        c1_loss = c1_max * ((r.max_weight - r.keyword_weighted_score) / total_max_weights)
        c2_loss = c2_max * ((r.max_weight - r.earned_item_score) / total_max_weights)
        tot_loss = round(c1_loss + c2_loss, 2)
        if r.match_credit == 0.0:
            dtype = "MISSING_REQUIREMENT_0.0X"
            expl = (
                f"'{r.requirement.display_name}' ({r.requirement.tier}, {r.max_weight:g}w) was not found on the resume "
                f"(0.0x credit): -{c1_loss:.2f} pts in C1 Coverage and -{c2_loss:.2f} pts in C2 Placement."
            )
            if r.adjacent_competing_product:
                expl += f" Adjacent competing product '{r.adjacent_competing_product}' was detected but earns 0.0x credit."
        elif r.match_credit == 0.5 and r.placement_multiplier == 1.0:
            dtype = "SYNONYM_DILUTION_0.5X"
            expl = (
                f"'{r.requirement.display_name}' ({r.requirement.tier}, {r.max_weight:g}w) matched via same-capability synonym "
                f"'{r.matched_term}' (0.5x credit in {r.placement_zone}): lost -{c1_loss:.2f} pts in C1 Coverage and -{c2_loss:.2f} pts in C2 Placement."
            )
        elif r.match_credit == 1.0 and r.placement_multiplier < 1.0:
            dtype = "PLACEMENT_PENALTY_SKILLS_OR_HISTORICAL"
            expl = (
                f"'{r.requirement.display_name}' ({r.requirement.tier}, {r.max_weight:g}w) earned full C1 Coverage (1.0x), "
                f"but appears only in {r.placement_zone} ({r.placement_multiplier:.1f}x multiplier): lost -{c2_loss:.2f} pts in C2 Placement."
            )
        else:
            dtype = "SYNONYM_AND_PLACEMENT_PENALTY"
            expl = (
                f"'{r.requirement.display_name}' ({r.requirement.tier}, {r.max_weight:g}w) matched via synonym '{r.matched_term}' (0.5x) "
                f"in {r.placement_zone} ({r.placement_multiplier:.1f}x): lost -{c1_loss:.2f} pts in C1 and -{c2_loss:.2f} pts in C2."
            )
        point_deductions_ledger.append({
            "component": "C1_C2_Requirement_Alignment",
            "target": r.requirement.display_name,
            "deduction_type": dtype,
            "comp1_points_lost": round(c1_loss, 2),
            "comp2_points_lost": round(c2_loss, 2),
            "other_component_points_lost": 0.0,
            "points_deducted": tot_loss,
            "explanation": expl,
        })

    # Component 3 deductions (if comp3_alignment_score < c3_max)
    c3_scale = c3_max / 15.0
    if title_pts < 5.0:
        lost_t = round((5.0 - title_pts) * c3_scale, 2)
        point_deductions_ledger.append({
            "component": "C3_Title_Tenure_Scale",
            "target": f"Target Title ('{alignment.target_title or 'N/A'}')",
            "deduction_type": "TITLE_ALIGNMENT_GAP",
            "comp1_points_lost": 0.0,
            "comp2_points_lost": 0.0,
            "other_component_points_lost": lost_t,
            "points_deducted": lost_t,
            "explanation": f"Target title '{alignment.target_title}' earned {title_pts:.1f}/5.0 raw pts (-{lost_t:.2f} scaled C3 pts).",
        })
    if tenure_pts < 5.0:
        lost_ten = round((5.0 - tenure_pts) * c3_scale, 2)
        point_deductions_ledger.append({
            "component": "C3_Title_Tenure_Scale",
            "target": f"Minimum Tenure ({alignment.min_years_required:.1f} yrs)",
            "deduction_type": "TENURE_SHORTFALL",
            "comp1_points_lost": 0.0,
            "comp2_points_lost": 0.0,
            "other_component_points_lost": lost_ten,
            "points_deducted": lost_ten,
            "explanation": (
                f"Calculated net relevant tenure ({resume.total_relevant_tenure_years:.2f} yrs) is below requisition target "
                f"({alignment.min_years_required:.1f} yrs): lost -{lost_ten:.2f} pts in C3."
            ),
        })
    if seniority_pts < 5.0:
        lost_sen = round((5.0 - seniority_pts) * c3_scale, 2)
        point_deductions_ledger.append({
            "component": "C3_Title_Tenure_Scale",
            "target": "Operational Scale & Ownership Verbs",
            "deduction_type": "SCALE_OR_SENIORITY_UNVERIFIED",
            "comp1_points_lost": 0.0,
            "comp2_points_lost": 0.0,
            "other_component_points_lost": lost_sen,
            "points_deducted": lost_sen,
            "explanation": f"Quantified operational metrics or seniority ownership verbs partially/not detected (-{lost_sen:.2f} pts in C3).",
        })

    # Component 4 deductions (if knockout gate unverified)
    if not knockout_passed:
        point_deductions_ledger.append({
            "component": "C4_Knockout_Gate",
            "target": ", ".join(failed_knockouts),
            "deduction_type": "KNOCKOUT_GATE_UNVERIFIED",
            "comp1_points_lost": 0.0,
            "comp2_points_lost": 0.0,
            "other_component_points_lost": round(c4_max, 2),
            "points_deducted": round(c4_max, 2),
            "explanation": (
                f"Required knockout gate(s) ({', '.join(failed_knockouts)}) were not verified on the submitted resume text "
                f"(-{c4_max:.2f} pts in C4; confirm whether candidate holds gate or qualifies via equivalency)."
            ),
        })

    # Component 5 deductions (document & parse hygiene)
    c5_scale = c5_max / 10.0
    for reason_str, raw_ded in resume.schema_deductions:
        scaled_ded = round(raw_ded * c5_scale, 2)
        point_deductions_ledger.append({
            "component": "C5_Document_Hygiene",
            "target": "HR-XML Document & Parse Hygiene",
            "deduction_type": "SCHEMA_HYGIENE_DEFECT",
            "comp1_points_lost": 0.0,
            "comp2_points_lost": 0.0,
            "other_component_points_lost": scaled_ded,
            "points_deducted": scaled_ded,
            "explanation": f"{reason_str} (-{scaled_ded:.2f} pts in C5).",
        })

    point_deductions_ledger.sort(key=lambda d: (-float(d["points_deducted"]), str(d["target"])))
    total_pts_deducted = round(100.0 - composite_score, 2)

    uncertainty_payload: Dict[str, Any] = {
        "epistemic_notice": (
            "The JD Alignment Score is a deterministic text-alignment heuristic, not an objective measurement of human capability "
            "or a prediction of job performance. Always interpret the point estimate alongside its Plausible Score Interval, "
            "Measurement Confidence, and Evidence Completeness."
        ),
        "three_pillar_distinction": {
            "1_alignment_score": {
                "point_estimate": round(composite_score, 1),
                "literal_only_score": round(legacy_literal_score, 1),
                "plausible_score_interval": [lower_bound_score, upper_bound_score],
                "interval_width_pts": interval_width,
                "uncertainty_band": uncertainty_band,
            },
            "2_measurement_confidence": {
                "combined_confidence": combined_meas_conf,
                "combined_confidence_pct": round(combined_meas_conf * 100.0, 1),
                "resume_parse_confidence": res_parse_conf,
                "requisition_extraction_confidence": req_ext_conf,
                "confidence_band": meas_conf_band,
                "inferred_requirement_count": len(inferred_reqs),
            },
            "3_evidence_completeness": {
                "fully_verified_1x_count": len(full_1x_items),
                "partially_verified_count": len(partial_items),
                "unverified_missing_count": len(missing_items),
                "total_requirements_count": len(ledger),
                "weighted_evidence_completeness_pct": weighted_completeness_pct,
                "item_verification_rate_pct": item_verification_pct,
                "completeness_band": completeness_band,
            },
        },
        "semantic_vs_literal_comparison": {
            "semantic_score": round(composite_score, 1),
            "literal_score": round(legacy_literal_score, 1),
            "semantic_minus_literal_delta": round(composite_score - legacy_literal_score, 1),
            "component_comparison": side_by_side_components,
            "divergent_requirements": divergent_reqs,
        },
        "missing_evidence_items": missing_evidence_list,
        "partial_evidence_items": partial_evidence_list,
        "point_deductions_ledger": point_deductions_ledger,
        "total_points_deducted_from_100": total_pts_deducted,
    }

    return AuditReport(
        composite_score=round(composite_score, 1),
        modern_semantic_score=round(composite_score, 1),
        legacy_literal_score=round(legacy_literal_score, 1),
        knockout_passed=knockout_passed,
        failed_knockouts=failed_knockouts,
        verdict=verdict,
        primary_bottleneck=primary_bottleneck,
        comp1_kw_score=round(comp1_kw_score, 2),
        comp1_literal_score=round(comp1_literal_score, 2),
        comp2_context_score=round(comp2_context_score, 2),
        comp3_alignment_score=round(comp3_alignment_score, 2),
        comp3_breakdown=comp3_breakdown,
        comp4_knockout_score=round(comp4_knockout_score, 2),
        comp5_schema_score=round(comp5_schema_score, 2),
        comp5_deductions=resume.schema_deductions,
        total_max_item_weights=round(total_max_weights, 2),
        sum_kw_matched_weights=round(sum_kw_matched, 2),
        sum_literal_matched_weights=round(sum_literal_matched, 2),
        sum_earned_item_scores=round(sum_earned_context, 2),
        ledger=ledger,
        recovery_plan=recovery_plan,
        effective_org_config=eff_cfg_summary,
        uncertainty_and_explanations=uncertainty_payload,
    )


# ---------------------------------------------------------------------------
# Markdown Scorecard Renderer
# ---------------------------------------------------------------------------

def render_markdown_report(report: AuditReport) -> str:
    gate_str = "PASS" if report.knockout_passed else f"NOT VERIFIED ({', '.join(report.failed_knockouts)}) - confirm with candidate"
    eff = report.effective_org_config or {}
    c1_m = eff.get("comp1_keyword_max", 45.0)
    c2_m = eff.get("comp2_context_max", 20.0)
    c3_m = eff.get("comp3_alignment_max", 15.0)
    c4_m = eff.get("comp4_knockout_max", 10.0)
    c5_m = eff.get("comp5_schema_max", 10.0)
    st_t = eff.get("jd_strong_match_threshold", 80.0)
    bd_t = eff.get("jd_borderline_threshold", 65.0)
    org_line = (
        f"  - *Effective Organization Config:* `{eff.get('org_id', 'default-baseline-org')}` v`{eff.get('config_version', '1.5.0')}` "
        f"(`{eff.get('approval_status', 'APPROVED')}` | Weights `{c1_m:g}/{c2_m:g}/{c3_m:g}/{c4_m:g}/{c5_m:g}` | "
        f"Cutoffs `Strong>={st_t:g}, Borderline>={bd_t:g}`)"
    )
    lines: List[str] = [
        "# JD Alignment Scorecard",
        "",
        "*A transparent heuristic for finding fixable gaps. It is not any ATS vendor's algorithm or a prediction of a specific system's decision.*",
        "",
        "## 1. Executive Scorecard Summary",
        f"- **JD Alignment Score:** `{report.composite_score:.1f} / 100`",
        f"  - *Semantic view (synonyms earn 0.5x credit):* `{report.modern_semantic_score:.1f}%`",
        f"  - *Literal-only view (exact terms only, approximates strict keyword filters):* `{report.legacy_literal_score:.1f}%` *(excludes 0.5x synonym credits)*",
        f"  - *Knockout Gate Status:* `{gate_str}`",
        f"  - *Alignment Verdict:* **`{report.verdict}`** — {report.primary_bottleneck}",
        org_line,
        "",
        "---",
        "",
        "## 2. 5-Component Breakdown Table",
        "",
        "| Component | Max Points | Underlying Formula / Fraction Math | Earned Points |",
        "| :--- | :---: | :--- | :---: |",
        f"| **1. Weighted Keyword & Taxonomy** | {c1_m:.1f} | `{c1_m:g} * ({report.sum_kw_matched_weights:.2f} / {report.total_max_item_weights:.2f})` *(Literal: `{report.sum_literal_matched_weights:.2f}/{report.total_max_item_weights:.2f}`)* | **{report.comp1_kw_score:.2f}** |",
        f"| **2. Contextual Placement & Recency** | {c2_m:.1f} | `{c2_m:g} * ({report.sum_earned_item_scores:.2f} / {report.total_max_item_weights:.2f})` | **{report.comp2_context_score:.2f}** |",
        f"| **3. Title, Seniority & Tenure Alignment** | {c3_m:.1f} | "
        + "; ".join(f"{k}: `{v:.1f}/5`" for k, v in report.comp3_breakdown.items())
        + f" | **{report.comp3_alignment_score:.2f}** |",
        f"| **4. Hard Knockout / Minimum Gate** | {c4_m:.1f} | `{'All mandatory gates verified' if report.knockout_passed else 'Not verified: ' + ', '.join(report.failed_knockouts)}` | **{report.comp4_knockout_score:.2f}** |",
        f"| **5. HR-XML Parseability & Schema** | {c5_m:.1f} | `{'Zero schema defects (10.0)' if not report.comp5_deductions else '; '.join(d[0] for d in report.comp5_deductions)}` | **{report.comp5_schema_score:.2f}** |",
        f"| **TOTAL COMPOSITE SCORE** | **100.0** | **Sum of Components 1–5** | **`{report.composite_score:.1f} / 100`** |",
        "",
        "---",
        "",
        "## 3. Line-by-Line Audit Ledger",
        "",
        "| JD Requirement | Tier | Base Wt | Max Wt | Match (`1.0`/`0.5`/`0.0`) | Placement (`1.0x`/`0.8x`/`0.4x`) | Earned Wt | Exact Resume Quote & Location | Composite Pts Lost |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :---: |",
    ]

    for row in report.ledger:
        tier_str = f"{row.requirement.tier.title()} ({row.requirement.req_multiplier:.1f}x)"
        kw_loss = 45.0 * ((row.max_weight - row.keyword_weighted_score) / report.total_max_item_weights)
        ctx_loss = 20.0 * ((row.max_weight - row.earned_item_score) / report.total_max_item_weights)
        pts_lost = kw_loss + ctx_loss
        quote_display = (
            f"*{row.placement_zone}:* \"{row.exact_quote}\""
            if row.match_credit > 0.0
            else "`\"MISSING\"`"
        )
        lines.append(
            f"| **{row.requirement.display_name}** | {tier_str} | {row.requirement.base_weight} | {row.max_weight:.1f} | `{row.match_credit:.1f}` | `{row.placement_multiplier:.1f}x` | `{row.earned_item_score:.2f}` | {quote_display} | `-{pts_lost:.2f}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Highest-ROI Point Recovery Plan",
            "",
            "| Rank | JD Requirement | Fix Strategy Tier | Recoverable Points | Actionable Instruction |",
            "| :---: | :--- | :--- | :---: | :--- |",
        ]
    )

    for idx, item in enumerate(report.recovery_plan, start=1):
        lines.append(
            f"| {idx} | **{item['requirement']}** ({item['tier_label']}) | {item['fix_tier']} | **`+{item['points_recoverable']:.2f} pts`** | {item['action']} |"
        )

    unc = report.uncertainty_and_explanations or {}
    if unc:
        pillars = unc.get("three_pillar_distinction", {})
        p1 = pillars.get("1_alignment_score", {})
        p2 = pillars.get("2_measurement_confidence", {})
        p3 = pillars.get("3_evidence_completeness", {})
        svl = unc.get("semantic_vs_literal_comparison", {})
        interval = p1.get("plausible_score_interval", [report.legacy_literal_score, report.composite_score])

        lines.extend([
            "",
            "---",
            "",
            "## 5. Transparent Uncertainty: Score vs. Confidence vs. Evidence Completeness",
            "",
            f"*{unc.get('epistemic_notice', '')}*",
            "",
            "| Distinct Evaluation Pillar | Value / Band | What This Measures (and Does Not Measure) |",
            "| :--- | :--- | :--- |",
            f"| **1. Heuristic Alignment Score & Plausible Band** | **`{report.composite_score:.1f} / 100`** (Plausible Band: **`[{interval[0]:.1f}, {interval[1]:.1f}]`**, spread `{p1.get('interval_width_pts', 0.0):.1f} pts` — `{p1.get('uncertainty_band', '')}`) | Point estimate under active rubric weights. Lower bound (`{interval[0]:.1f}`) reflects strict literal-only filtering; upper bound (`{interval[1]:.1f}`) reflects full credit for synonyms & skills-list items already on the resume (excluding `0.0x` missing skills). |",
            f"| **2. Measurement & Parser Confidence** | **`{p2.get('combined_confidence_pct', 100.0):.1f}%`** (`{p2.get('confidence_band', '')}` — Resume Parse: `{p2.get('resume_parse_confidence', 1.0) * 100:.0f}%`, JD Config: `{p2.get('requisition_extraction_confidence', 1.0) * 100:.0f}%`) | Technical certainty that resume sections, work-history dates, and requisition requirements were parsed without structural loss. High parser confidence does **not** mean the candidate meets all requirements. |",
            f"| **3. Evidence Completeness** | **`{p3.get('weighted_evidence_completeness_pct', 0.0):.1f}%`** (`{p3.get('completeness_band', '')}` — `{p3.get('fully_verified_1x_count', 0)}` Full `1.0x` / `{p3.get('partially_verified_count', 0)}` Partial / `{p3.get('unverified_missing_count', 0)}` Missing of `{p3.get('total_requirements_count', 0)}`) | Share of weighted requisition requirements backed by direct `1.0x` recent-role evidence vs. partial (`0.5x` synonym / `0.4x` skills-list) or unverified (`0.0x`) items. |",
            "",
            "### Side-by-Side Semantic vs. Literal View Comparison",
            "",
            "| Component | Max Pts | Semantic View (`0.5x` Synonyms) | Literal-Only View (`1.0x` Exact Only) | Spread (`Semantic - Literal`) | Why the Views Agree or Diverge |",
            "| :--- | :-: | :-: | :-: | :-: | :--- |",
        ])
        for comp_row in svl.get("component_comparison", []):
            lines.append(
                f"| **{comp_row['component']}** | {comp_row['max_points']:.1f} | **{comp_row['semantic_earned']:.2f}** | "
                f"`{comp_row['literal_earned']:.2f}` | `{comp_row['delta']:+.2f} pts` | {comp_row['explanation']} |"
            )
        lines.append(
            f"| **Total Score Comparison** | **100.0** | **`{svl.get('semantic_score', report.composite_score):.1f} / 100`** | "
            f"**`{svl.get('literal_score', report.legacy_literal_score):.1f}%`** | **`{svl.get('semantic_minus_literal_delta', 0.0):+.1f} pts`** | "
            f"{len(svl.get('divergent_requirements', []))} requirement(s) matched via same-capability synonym |"
        )

        missing_ev = unc.get("missing_evidence_items", [])
        partial_ev = unc.get("partial_evidence_items", [])
        lines.extend([
            "",
            "### Explicit Missing & Partially Verified Evidence",
            "",
        ])
        if missing_ev:
            lines.append("**Unverified / Missing Requirements (`0.0x` — Never Auto-Inferred):**")
            for m_item in missing_ev:
                gate_badge = " **[Required Knockout Gate]**" if m_item.get("is_knockout_gate") else ""
                lines.append(
                    f"- **{m_item['requirement']}** ({m_item['tier']}, `{m_item['max_weight']:g}w`, `-{m_item['points_at_stake']:.2f} pts`){gate_badge}: "
                    f"{m_item['diagnostic_note']} *Action:* {m_item['candidate_verification_question']}"
                )
        else:
            lines.append("- **Unverified / Missing Requirements (`0.0x`):** *(None — all requirements have at least partial or full evidence on the resume)*")

        if partial_ev:
            lines.append("")
            lines.append("**Partially Verified Requirements (`0.5x` Synonym or `<1.0x` Placement):**")
            for p_item in partial_ev:
                lines.append(
                    f"- **{p_item['requirement']}** ({p_item['tier']}, `-{p_item['points_deducted']:.2f} pts`): "
                    f"{p_item['what_is_missing_for_full_credit']}"
                )

        ded_ledger = unc.get("point_deductions_ledger", [])
        lines.extend([
            "",
            f"## 6. Complete Point-Deduction Explanations (Reconciling `100.0` -> `{report.composite_score:.1f}`)",
            "",
        ])
        if ded_ledger:
            lines.extend([
                "| # | Component | Requirement / Criterion | Deduction Type | C1 Lost | C2 Lost | C3–C5 Lost | Total Deducted | Plain-Language Explanation |",
                "| :-: | :--- | :--- | :--- | :-: | :-: | :-: | :-: | :--- |",
            ])
            for d_idx, d_row in enumerate(ded_ledger, start=1):
                lines.append(
                    f"| {d_idx} | `{d_row['component']}` | **{d_row['target']}** | `{d_row['deduction_type']}` | "
                    f"`-{d_row['comp1_points_lost']:.2f}` | `-{d_row['comp2_points_lost']:.2f}` | `-{d_row['other_component_points_lost']:.2f}` | "
                    f"**`-{d_row['points_deducted']:.2f} pts`** | {d_row['explanation']} |"
                )
        else:
            lines.append("- **Zero points deducted (`0.00 pts` lost):** All 5 components earned maximum points (`100.0 / 100`).")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Heuristic Plain-Text JD Extractor & Built-In Verification Demo
# ---------------------------------------------------------------------------

_JD_BOILERPLATE_PREFIX_RE = re.compile(
    r"^(?:(?:\d+\+?\s*(?:[-–]\s*\d+\s*)?years?\s+(?:of\s+)?)?"
    r"(?:hands[-\s]on\s+|proven\s+|demonstrated\s+|strong\s+|deep\s+|advanced\s+|solid\s+|working\s+|practical\s+)?"
    r"(?:experience|proficiency|expertise|knowledge|background|familiarity|ability|track\s+record|skills?)"
    r"\s+(?:in|with|using|building|managing|developing|leading|operating|across)\s+)",
    re.IGNORECASE,
)


def extract_jd_from_text(jd_text: str) -> Tuple[List[JDRequirement], AlignmentConfig]:
    """Heuristic extractor when a plain-text JD is supplied without a JSON config."""
    reqs: List[JDRequirement] = []
    seen_names: set[str] = set()
    in_preferred = False
    target_title = ""
    min_years = 5.0

    # Extract min_years_required if stated in JD
    years_matches = [
        float(m.group(1))
        for m in re.finditer(
            r"\b(\d{1,2})\+?\s*(?:[-–]\s*\d{1,2}\s*)?years?\s+(?:of\s+)?(?:relevant\s+|professional\s+|industry\s+|progressive\s+)?experience\b",
            jd_text,
            re.IGNORECASE,
        )
    ]
    if years_matches:
        min_years = max(years_matches)

    for raw_line in jd_text.splitlines():
        s = raw_line.strip()
        if not s:
            continue

        # Detect explicit Job Title header
        title_match = re.match(r"^(?:job[ \t]+title|title|role|position)[ \t]*[:–-][ \t]*(\S.*)$", s[:200], re.IGNORECASE)
        if title_match and not target_title:
            target_title = title_match.group(1).strip()
            continue
        if not target_title and not s.startswith(("-", "*", "•")) and len(s) <= 60 and not s.endswith(":"):
            target_title = s.strip("#* \t").strip()

        # Section header tier switching (resets when moving back to required section)
        is_bullet = s.startswith(("-", "*", "•")) or bool(re.match(r"^\d+[.)]\s", s))
        if not is_bullet and len(s) <= 80:
            if re.search(r"\b(preferred|nice[-\s]to[-\s]have|desired|bonus|plus)\b", s, re.IGNORECASE):
                in_preferred = True
                continue
            if re.search(r"\b(required|minimum|must[-\s]have|qualifications|requirements|what\s+you\s+bring)\b", s, re.IGNORECASE):
                in_preferred = False
                continue

        if is_bullet:
            clean = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", s).strip()
            bullet_tier = "preferred" if in_preferred else "required"
            if re.match(r"^(?:preferred|nice\s+to\s+have|bonus|desired)\s*[:–-]\s*", clean, re.IGNORECASE):
                bullet_tier = "preferred"
                clean = re.sub(r"^(?:preferred|nice\s+to\s+have|bonus|desired)\s*[:–-]\s*", "", clean, flags=re.IGNORECASE)

            # Skip pure years-of-experience lines if no concrete skill follows
            clean = _JD_BOILERPLATE_PREFIX_RE.sub("", clean).strip()

            # Extract parenthetical examples as synonyms if present, e.g. 'Cloud Data Warehousing (Snowflake, Redshift)'
            synonyms: List[str] = []
            paren_match = re.search(
                r"\((?:e\.g\.,?[ \t]*|such[ \t]+as[ \t]*|including[ \t]*)?([^\s()][^()]{0,200})\)",
                clean[:400],
                re.IGNORECASE,
            )
            if paren_match:
                raw_syns = [p.strip() for p in re.split(r",|\bor\b|/", paren_match.group(1)) if 2 <= len(p.strip()) <= 35]
                synonyms = [p for p in raw_syns if not p.lower().startswith(("etc", "and "))]
                clean = (clean[:paren_match.start()] + clean[paren_match.end():]).strip()

            key_phrase = re.split(r"[:;,]|\band\b|\bwith\b|\bto\b|\bfor\b", clean, maxsplit=1)[0].strip(" .-")
            if 2 <= len(key_phrase) <= 45 and key_phrase.lower() not in seen_names:
                is_gate = bool(re.search(r"\b(b\.?s\.?|bachelor|master|m\.?s\.?|ph\.?d|degree|clearance|license)\b", key_phrase, re.IGNORECASE))
                seen_names.add(key_phrase.lower())
                reqs.append(
                    JDRequirement(
                        name=key_phrase,
                        category="hard_skill",
                        tier=bullet_tier,
                        synonyms=synonyms,
                        is_knockout_gate=is_gate and bullet_tier == "required",
                        confidence=0.80 if not in_preferred else 0.75,
                        certainty="MODERATE",
                        extraction_status="INFERRED",
                        source_quote=raw_line.strip(),
                        provenance_section="preferred_section" if in_preferred else "required_section",
                        provenance_rule="heuristic_phrase_fallback",
                    )
                )

    return reqs, AlignmentConfig(target_title=target_title, min_years_required=min_years)


def load_jd_config(path: str) -> Tuple[List[JDRequirement], AlignmentConfig]:
    """Load structured JD requirements and AlignmentConfig from a JSON file."""
    path = _resolve_input_path(path)
    with open(path, "r", encoding="utf-8") as jf:
        cfg_data = json.load(jf)
    if "ats_config" in cfg_data and isinstance(cfg_data["ats_config"], dict):
        cfg_data = cfg_data["ats_config"]
    reqs = [JDRequirement(**item) for item in cfg_data.get("requirements", [])]
    align_dict = dict(cfg_data.get("alignment", {}))
    align_dict.pop("reference_date", None)
    align_cfg = AlignmentConfig(**align_dict)
    return reqs, align_cfg


def get_demo_fixture() -> Tuple[str, List[JDRequirement], AlignmentConfig]:
    demo_resume = """JORDAN VANCE
St. Louis, MO | jordan.vance@email.com | 314-555-0192

Professional Summary
Senior Data & Supply Chain Analytics Manager with 7+ years of experience driving inventory optimization, cloud data warehousing, and executive KPI reporting across Fortune 500 retail operations.

Work Experience
Senior Analytics Manager - Apex Global Retail (03/2022 - Present)
- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue.
- Built automated data pipelines in Snowflake and Tableau to track supplier SLA compliance for 1,200+ global vendors.
- Partnered with cross-functional leadership on capacity planning and annual operating budget reviews.

Supply Chain Data Analyst - Midwest Logistics Corp (06/2018 - 02/2022)
- Automated weekly freight reconciliation reporting using SQL and Excel, cutting manual reporting cycle time by 12 hours per week.
- Supported ERP migration readiness and warehouse slotting audits.

Education
B.S. in Industrial Engineering - University of Missouri (05/2018)

Technical & Core Skills
SQL, Python, Snowflake, Power BI, SAP S/4HANA, Kubernetes, Cross-Functional Leadership
"""

    demo_requirements = [
        JDRequirement("Python", category="hard_skill", tier="required", synonyms=["PySpark"]),
        JDRequirement("SQL", category="hard_skill", tier="required", synonyms=["PostgreSQL", "T-SQL"]),
        JDRequirement("Snowflake", category="hard_skill", tier="required", synonyms=["BigQuery", "Redshift"]),
        JDRequirement("SAP S/4HANA", category="hard_skill", tier="required", synonyms=["SAP ERP", "Oracle NetSuite"]),
        JDRequirement("Power BI", category="hard_skill", tier="required", synonyms=["Tableau", "Looker"]),
        JDRequirement("Workforce Capacity Modeling", category="hard_skill", tier="required", synonyms=["capacity planning"]),
        JDRequirement("Demand Forecasting", category="hard_skill", tier="required", synonyms=["demand planning"]),
        JDRequirement("P&L Ownership", category="title_scope", tier="required", synonyms=["operating budget"]),
        JDRequirement("Kubernetes", category="hard_skill", tier="preferred", synonyms=["Docker", "EKS"]),
        JDRequirement("Six Sigma Black Belt", category="hard_skill", tier="preferred", synonyms=["Lean Six Sigma"]),
        JDRequirement("Cross-Functional Leadership", category="soft_skill", tier="required", synonyms=["cross-functional"]),
        JDRequirement("B.S.", category="hard_skill", tier="required", synonyms=["Bachelor"], is_knockout_gate=True),
    ]

    demo_alignment = AlignmentConfig(
        target_title="Senior Analytics Manager",
        title_synonyms=["Analytics Manager", "Data Analytics Manager"],
        min_years_required=6.0,
    )
    return demo_resume, demo_requirements, demo_alignment


def export_updated_docx(src_docx_path: str, dst_docx_path: str, replacements: Dict[str, str]) -> int:
    """Clone a .docx file and apply exact or substring text replacements inside paragraphs while preserving formatting."""
    src_docx_path = _resolve_input_path(src_docx_path)
    os.makedirs(os.path.dirname(os.path.abspath(dst_docx_path)), exist_ok=True)
    ns_uri = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    ns = {"w": ns_uri}
    ET.register_namespace("w", ns_uri)

    with zipfile.ZipFile(src_docx_path, "r") as zin:
        xml_bytes = zin.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        replaced_count = 0
        for p in root.findall(".//w:p", ns):
            t_nodes = p.findall(".//w:t", ns)
            if not t_nodes:
                continue
            full_p_text = "".join(t.text or "" for t in t_nodes)
            updated_text = full_p_text
            for old_str, new_str in replacements.items():
                if old_str and old_str in updated_text:
                    updated_text = updated_text.replace(old_str, new_str)
            if updated_text != full_p_text:
                replaced_count += 1
                t_nodes[0].text = updated_text
                for extra_t in t_nodes[1:]:
                    extra_t.text = ""
        new_xml_bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        with zipfile.ZipFile(dst_docx_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == "word/document.xml":
                    zout.writestr(item, new_xml_bytes)
                else:
                    zout.writestr(item, zin.read(item.filename))
    return replaced_count


def _import_collector():
    """Lazy-import sibling job_posting_collector whether run as CLI script or package module."""
    try:
        from . import job_posting_collector as collector
    except ImportError:
        scripts_dir = os.path.dirname(os.path.abspath(__file__))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import job_posting_collector as collector
    return collector


def extract_reqs_consistent(
    raw_text: str,
    title: str = "",
    jd_record_out: Optional[str] = None,
) -> Tuple[List[JDRequirement], AlignmentConfig]:
    """
    Single deterministic extraction path across local files, cached JSON, and public APIs.
    Prepends clean job title if missing from body text; excludes external attribution noise.
    """
    clean_body = raw_text.strip()
    if title and title.lower() not in clean_body[:250].lower():
        clean_input = f"Job Title: {title.strip()}\n\n{clean_body}"
    else:
        clean_input = clean_body

    try:
        jpc = _import_collector()
        if hasattr(jpc, "parse_job_posting_text"):
            if "<html" in clean_input.lower() or "application/ld+json" in clean_input.lower():
                jsonld = jpc.extract_jsonld_job_posting(clean_input)
                plain = jpc.html_fragment_to_text(clean_input)
                rec = jpc.parse_job_posting_text(plain, jsonld=jsonld)
            else:
                rec = jpc.parse_job_posting_text(clean_input)

            if jd_record_out:
                os.makedirs(os.path.dirname(os.path.abspath(jd_record_out)), exist_ok=True)
                with open(jd_record_out, "w", encoding="utf-8") as jf:
                    json.dump(dataclasses.asdict(rec), jf, indent=2, default=str)

            cfg_data = getattr(rec, "ats_config", {}) or {}
            if cfg_data.get("requirements"):
                reqs = [JDRequirement(**item) for item in cfg_data["requirements"]]
                align_dict = dict(cfg_data.get("alignment", {}))
                align_dict.pop("reference_date", None)
                align_cfg = AlignmentConfig(**align_dict)
                return reqs, align_cfg
    except ImportError:
        pass

    return extract_jd_from_text(clean_input)


def audit_resume_against_jd(
    resume_text: str,
    jd_text: str,
    title: str = "",
    reference_date: Optional[datetime] = None,
    jd_record_out: Optional[str] = None,
) -> AuditReport:
    """Audit resume text against clean JD text + title only (excluding attribution/disclaimer noise)."""
    reqs, align_cfg = extract_reqs_consistent(jd_text, title=title, jd_record_out=jd_record_out)
    return run_ats_audit(resume_text, reqs, align_cfg, reference_date=reference_date)


def load_jds_for_audit(
    *,
    url: Optional[str] = None,
    greenhouse: Optional[str] = None,
    lever: Optional[str] = None,
    ashby: Optional[str] = None,
    usajobs: Optional[str] = None,
    location: str = "",
    jd_file: Optional[str] = None,
    from_json: Optional[str] = None,
    cache_json: Optional[str] = None,
    title_filter: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Loads normalized job dicts from offline files or compliant APIs."""
    if from_json:
        from_json = _resolve_input_path(from_json)
        with open(from_json, "r", encoding="utf-8") as f:
            raw = json.load(f)
        jobs = raw if isinstance(raw, list) else [raw]
    elif jd_file:
        jd_file = _resolve_input_path(jd_file)
        with open(jd_file, "r", encoding="utf-8") as f:
            text = f.read().strip()
        stem = os.path.splitext(os.path.basename(jd_file))[0]
        jobs = [{
            "source": "local_file",
            "job_id": stem,
            "company": "Local",
            "title": "",
            "location": "",
            "compensation": "",
            "url": "",
            "description_text": text,
            "attribution": "",
        }]
    else:
        jpc = _import_collector()
        if url:
            try:
                objs = jpc.collect_from_url(url)
                jobs = [obj.to_dict() for obj in objs]
            except ValueError:
                # Non-Greenhouse/Lever/Ashby corporate career URL -> fallback to compliant fetch_job_source
                jpc.enforce_domain_compliance(url)
                fetch_res = jpc.fetch_job_source(url)
                if not fetch_res.get("ok"):
                    raise RuntimeError(
                        fetch_res.get("error")
                        or "Could not fetch JD from that URL. Paste text into a file and pass --jd-text instead."
                    )
                raw_body = fetch_res.get("text") or fetch_res.get("html") or ""
                jobs = [{
                    "source": "career_page",
                    "job_id": url,
                    "company": fetch_res.get("company", "Employer Site"),
                    "title": fetch_res.get("title", ""),
                    "location": "",
                    "compensation": "",
                    "url": url,
                    "description_text": raw_body,
                    "attribution": "",
                }]
        elif greenhouse:
            jobs = [obj.to_dict() for obj in jpc.fetch_greenhouse(greenhouse)]
        elif lever:
            jobs = [obj.to_dict() for obj in jpc.fetch_lever(lever)]
        elif ashby:
            jobs = [obj.to_dict() for obj in jpc.fetch_ashby(ashby)]
        elif usajobs:
            jobs = [obj.to_dict() for obj in jpc.fetch_usajobs(usajobs, location=location)]
        else:
            raise ValueError("No JD source specified.")

        if cache_json:
            os.makedirs(os.path.dirname(os.path.abspath(cache_json)), exist_ok=True)
            with open(cache_json, "w", encoding="utf-8") as cf:
                json.dump(jobs, cf, indent=2, ensure_ascii=False)

    if title_filter:
        pattern = re.compile(title_filter, re.IGNORECASE)
        jobs = [j for j in jobs if pattern.search(j.get("title", ""))]

    return jobs


def print_scorecard(scorecard: AuditReport) -> None:
    """Print rendered Markdown scorecard to stdout."""
    print(render_markdown_report(scorecard))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="ops-req-audit", description="ops-requisition-auditor: deterministic, explainable JD Alignment scoring (offline)")
    parser.add_argument("--resume", type=str, help="Path to candidate resume (.docx, .pdf, .txt, or .md)")
    parser.add_argument("--jd-config", type=str, help="Path to structured JD JSON config")
    parser.add_argument("--jd-text", "--jd-file", dest="jd_file", type=str,
                        help="Path to local Job Description text or HTML file (100%% offline)")
    parser.add_argument("--from-json", type=str,
                        help="Path to cached normalized job JSON file from a prior API pull (100%% offline)")
    parser.add_argument("--url", "--jd-url", dest="url", type=str,
                        help="Job URL (Greenhouse, Lever, Ashby, or open career page; blocks restricted job boards)")
    parser.add_argument("--greenhouse", type=str, help="Greenhouse board token (fetches public Job Board API)")
    parser.add_argument("--lever", type=str, help="Lever company slug (fetches public Postings API)")
    parser.add_argument("--ashby", type=str, help="Ashby board name (fetches public Job Postings API)")
    parser.add_argument("--usajobs", type=str, help="USAJobs keyword query (requires USAJOBS_API_KEY + USAJOBS_USER_AGENT)")
    parser.add_argument("--location", type=str, default="", help="Optional location filter for --usajobs")
    parser.add_argument("--cache-json", type=str, help="Optional path to cache fetched normalized jobs as JSON")
    parser.add_argument("--title-filter", type=str, help="Optional regex to filter job titles on multi-job pulls")
    parser.add_argument("--top", type=int, default=0, help="Optional limit to surface only the top N highest-scoring job matches on multi-job pulls")
    parser.add_argument("--jd-record-out", type=str, help="Optional path to write extracted JobPostingRecord JSON")
    parser.add_argument("--export-docx", type=str, help="Optional output .docx path to write an updated Word resume")
    parser.add_argument("--replacements-json", type=str, help="Optional JSON mapping {old_bullet: new_bullet} for --export-docx")
    parser.add_argument("--output", type=str, help="Path to write Markdown scorecard report")
    parser.add_argument("--json-out", type=str, help="Path to write raw JSON scorecard data")
    parser.add_argument("--reference-date", type=str, help="Optional YYYY-MM-DD reference date for tenure/recency")
    parser.add_argument("--org-profile", type=str, help="Optional path to an organization-native customization profile JSON")
    parser.add_argument("--require-approved-org-profile", action="store_true", help="Fail closed (exit code 2) if --org-profile is not in APPROVED status with dual sign-off")
    parser.add_argument("--demo", action="store_true", help="Run built-in deterministic verification demo")

    args = parser.parse_args()

    org_prof = None
    if args.org_profile:
        try:
            import org_customization_registry as ocr
            org_prof = ocr.load_org_profile(args.org_profile, require_approved=args.require_approved_org_profile)
        except Exception as exc:
            sys.stderr.write(f"{exc}\n")
            sys.exit(2)

    ref_date: Optional[datetime] = None
    if args.reference_date:
        ref_date = datetime.strptime(args.reference_date, "%Y-%m-%d")

    has_loader_src = any([
        args.jd_file, args.from_json, args.url,
        args.greenhouse, args.lever, args.ashby, args.usajobs,
    ])

    if args.demo or (not args.resume and not args.jd_config and not has_loader_src):
        resume_text, reqs, align_cfg = get_demo_fixture()
        if ref_date is None:
            ref_date = datetime(2026, 11, 1)
        report = run_ats_audit(resume_text, reqs, align_cfg, reference_date=ref_date, org_profile=org_prof)
        md_final = render_markdown_report(report)
        json_payload: Any = dataclasses.asdict(report)
    else:
        if not args.resume:
            parser.error("Please provide --resume <path> or run with --demo.")
        resume_text = load_resume_file(args.resume)

        if args.export_docx and args.resume.lower().endswith(".docx"):
            repl_map: Dict[str, str] = {}
            if args.replacements_json:
                r_path = _resolve_input_path(args.replacements_json)
                if os.path.exists(r_path):
                    with open(r_path, "r", encoding="utf-8") as rf:
                        repl_map = json.load(rf)
            export_updated_docx(args.resume, args.export_docx, repl_map)

        if args.jd_config:
            reqs, align_cfg = load_jd_config(args.jd_config)
            report = run_ats_audit(resume_text, reqs, align_cfg, reference_date=ref_date, org_profile=org_prof)
            md_final = render_markdown_report(report)
            json_payload = dataclasses.asdict(report)
        elif has_loader_src:
            try:
                jobs = load_jds_for_audit(
                    url=args.url,
                    greenhouse=args.greenhouse,
                    lever=args.lever,
                    ashby=args.ashby,
                    usajobs=args.usajobs,
                    location=args.location,
                    jd_file=args.jd_file,
                    from_json=args.from_json,
                    cache_json=args.cache_json,
                    title_filter=args.title_filter,
                )
            except Exception as exc:
                if exc.__class__.__name__ == "ComplianceBlockError" or str(exc).startswith("BLOCKED"):
                    sys.stderr.write(f"[COMPLIANCE GATE] {exc}\n")
                    sys.exit(2)
                parser.error(str(exc))

            if not jobs:
                parser.error("No matching job postings found for the given source / --title-filter.")

            single_mode = len(jobs) == 1
            scored_entries: List[Tuple[float, str, Dict[str, Any]]] = []

            for job in jobs:
                scorecard = audit_resume_against_jd(
                    resume_text=resume_text,
                    jd_text=job["description_text"],
                    title=job.get("title", ""),
                    reference_date=ref_date,
                    jd_record_out=args.jd_record_out if single_mode else None,
                )
                rendered_sc = render_markdown_report(scorecard)

                # Build clean metadata header for multi-job runs or live API pulls (kept out of scorer input)
                header_lines: List[str] = []
                if job.get("title") or job.get("company") not in ("", "Local"):
                    header_lines.append(
                        f"## {job.get('title') or 'Untitled Role'} — {job.get('company', '')} (`{job.get('source', '')}`)"
                    )
                if job.get("url"):
                    header_lines.append(f"**Source URL:** {job['url']}")
                if job.get("attribution"):
                    header_lines.append(f"> {job['attribution']}")

                full_block = ("\n".join(header_lines) + "\n\n" + rendered_sc) if header_lines else rendered_sc

                sc_dict = dataclasses.asdict(scorecard)
                if single_mode:
                    json_entry: Dict[str, Any] = sc_dict  # Preserves exact existing JSON schema for 1-JD runs
                else:
                    json_entry = {
                        "job_meta": {
                            "source": job.get("source", ""),
                            "job_id": job.get("job_id", ""),
                            "company": job.get("company", ""),
                            "title": job.get("title", ""),
                            "location": job.get("location", ""),
                            "compensation": job.get("compensation", ""),
                            "url": job.get("url", ""),
                            "attribution": job.get("attribution", ""),
                        },
                        "scorecard": sc_dict,
                    }
                scored_entries.append((scorecard.overall_score, full_block, json_entry))

            if not single_mode:
                scored_entries.sort(key=lambda item: item[0], reverse=True)
                if args.top and args.top > 0:
                    scored_entries = scored_entries[:args.top]

            md_blocks = [item[1] for item in scored_entries]
            json_reports = [item[2] for item in scored_entries]

            md_final = "\n\n---\n\n".join(md_blocks)
            json_payload = json_reports[0] if single_mode else json_reports
        else:
            parser.error("Provide --jd-config, --jd-text/--jd-file, --from-json, --url/--jd-url, or a board API flag.")

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md_final + "\n")

    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(json_payload, f, indent=2, default=str)

    print(md_final)


if __name__ == "__main__":
    main()
