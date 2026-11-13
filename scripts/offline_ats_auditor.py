#!/usr/bin/env python3
"""
Offline Enterprise ATS Auditor & Deterministic Scoring Engine
=============================================================
A transparent, repeatable JD-alignment heuristic inspired by common ATS parsing behavior.
Weights are this project's own, not any vendor's algorithm; use it to find fixable gaps.

Implements the 100-Point Deterministic ATS Scorecard:
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
import sys
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
import zipfile


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


@dataclass
class ParsedResume:
    raw_text: str
    sections: Dict[str, str]
    roles: List[RoleBlock]
    total_relevant_tenure_years: float
    schema_deductions: List[Tuple[str, float]]
    schema_score: float


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


# ---------------------------------------------------------------------------
# Resume Parser & HR-XML Schema Auditor
# ---------------------------------------------------------------------------

STANDARD_SECTION_HEADERS = {
    "professional summary": "summary",
    "executive summary": "summary",
    "career summary": "summary",
    "summary of qualifications": "summary",
    "summary": "summary",
    "profile": "summary",
    "professional profile": "summary",
    "work experience": "experience",
    "professional experience": "experience",
    "relevant experience": "experience",
    "experience": "experience",
    "employment history": "experience",
    "career history": "experience",
    "education": "education",
    "education & certifications": "education",
    "education and certifications": "education",
    "academic background": "education",
    "certifications": "certifications",
    "licenses & certifications": "certifications",
    "licenses and certifications": "certifications",
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
}


def _classify_standard_header(clean_header: str) -> Optional[str]:
    if clean_header in STANDARD_SECTION_HEADERS:
        return STANDARD_SECTION_HEADERS[clean_header]
    if len(clean_header) <= 55:
        if re.match(r"^(?:core\s+competencies|technical\s+skills|key\s+skills|areas\s+of\s+expertise)(?:\s*(?:&|and|/|\|)\s*(?:core\s+competencies|technical\s+skills|tools|proficiencies|skills))?$", clean_header):
            return "skills"
        if re.match(r"^(?:professional|Work|relevant|career)\s+experience$", clean_header):
            return "experience"
        if re.match(r"^(?:professional|executive|career)\s+(?:summary|profile|overview)$", clean_header):
            return "summary"
        if re.match(r"^education(?:\s*(?:&|and|/)\s*(?:certifications|licenses|training))?$", clean_header):
            return "education"
    return None


def load_resume_file(path: str) -> str:
    """Load resume text from .txt, .md, .docx (zero-dependency XML), or .pdf."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        with zipfile.ZipFile(path, "r") as zf:
            xml_bytes = zf.read("word/document.xml")
        root = ET.fromstring(xml_bytes)
        ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines: List[str] = []
        body = root.find("w:body", ns)
        if body is None:
            body = root
        for elem in body:
            tag = elem.tag.split("}")[-1]
            if tag == "p":
                texts = [t.text for t in elem.findall(".//w:t", ns) if t.text]
                if texts:
                    lines.append("".join(texts))
            elif tag == "tbl":
                for row in elem.findall(".//w:tr", ns):
                    cell_texts: List[str] = []
                    for cell in row.findall(".//w:tc", ns):
                        c_txt = " ".join("".join(t.text for t in p.findall(".//w:t", ns) if t.text).strip() for p in cell.findall(".//w:p", ns)).strip()
                        if c_txt:
                            cell_texts.append(c_txt)
                    if len(cell_texts) > 1:
                        lines.append(" | ".join(cell_texts))
                    elif len(cell_texts) == 1:
                        lines.append(cell_texts[0])
        return "\n".join(lines)
    if ext == ".pdf":
        try:
            import pypdf
            reader = pypdf.PdfReader(path)
            return "\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            try:
                import fitz
                doc = fitz.open(path)
                return "\n".join(page.get_text() for page in doc)
            except Exception as e:
                raise RuntimeError(f"Unable to parse PDF resume '{path}': {e}")
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()

NON_STANDARD_HEADERS = [
    "where i've been",
    "my journey",
    "what i do",
    "career story",
    "about me",
    "toolbox",
    "superpowers",
]

_MONTH_TOKEN = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{4}"
_NUM_DATE_TOKEN = r"(?:(?:0?[1-9]|1[0-2])[/.-]\d{4}|\d{4}[/.-](?:0?[1-9]|1[0-2]))"
_SINGLE_DATE_PATTERN = rf"(?:{_NUM_DATE_TOKEN}|{_MONTH_TOKEN})"

DATE_RANGE_REGEX = re.compile(
    rf"(?P<start>{_SINGLE_DATE_PATTERN})"
    r"\s*(?:[-–—]|to|through)\s*"
    rf"(?P<end>{_SINGLE_DATE_PATTERN}|Present|Current|Now)",
    re.IGNORECASE,
)

YEAR_ONLY_RANGE_REGEX = re.compile(r"\b(19\d{2}|20\d{2})\s*[-–—]\s*(19\d{2}|20\d{2}|Present|Current)\b", re.IGNORECASE)

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
    if token.lower() in ("present", "current", "now"):
        return reference_date

    # Normalize 'Sept' / 'Sept.' and month periods (e.g. 'Jan. 2022' -> 'Jan 2022')
    normalized = re.sub(r"\bSept\.?\b", "Sep", token, flags=re.IGNORECASE)
    normalized = re.sub(r"^([A-Za-z]+)\.\s+", r"\1 ", normalized)
    normalized = normalized.replace(".", "/").replace("-", "/")

    for fmt in ("%m/%Y", "%Y/%m", "%B %Y", "%b %Y"):
        try:
            return datetime.strptime(normalized, fmt)
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

        clean_header = re.sub(r"^[#*\-\s]+|[#*:_\s]+$", "", stripped).lower()
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
    total_tenure = _merge_intervals(
        [(r.start_date, r.end_date) for r in roles if r.start_date is not None and r.end_date is not None]
    )

    joined_sections = {k: "\n".join(v) for k, v in sections.items()}
    return ParsedResume(
        raw_text=raw_text,
        sections=joined_sections,
        roles=roles,
        total_relevant_tenure_years=round(total_tenure, 2),
        schema_deductions=deductions,
        schema_score=schema_score,
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


def evaluate_requirement(req: JDRequirement, resume: ParsedResume) -> AuditLedgerRow:
    """
    Evaluate a single JD requirement for Match Credit and Placement/Recency Multiplier.

    Selects the primary match that maximizes composite ATS points: `credit * (45 + 20 * mult)`,
    ensuring an exact literal match (1.0 credit) on the resume is never erased by a 0.5x synonym,
    while also tracking the highest contextual factor `max(credit * mult)` for Component 2.
    """
    candidates: List[Tuple[str, float]] = [(req.name, 1.0)] + [(syn, 0.5) for syn in req.synonyms]

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

    return AuditLedgerRow(
        requirement=req,
        match_credit=best_credit,
        placement_multiplier=best_multiplier,
        matched_term=best_term,
        placement_zone=best_zone,
        exact_quote=best_quote,
        context_credit_factor=best_credit * best_multiplier,
        literal_placement_multiplier=best_literal_mult,
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
) -> AuditReport:
    resume = parse_resume(resume_text, reference_date=reference_date)

    ledger: List[AuditLedgerRow] = [evaluate_requirement(req, resume) for req in requirements]

    total_max_weights = sum(r.max_weight for r in ledger) or 1.0
    sum_kw_matched = sum(r.keyword_weighted_score for r in ledger)
    sum_literal_matched = sum(r.literal_only_keyword_score for r in ledger)
    sum_earned_context = sum(r.earned_item_score for r in ledger)

    # Component 1: Weighted Keyword & Taxonomy Score (Max 45 pts)
    comp1_kw_score = 45.0 * (sum_kw_matched / total_max_weights)
    comp1_literal_score = 45.0 * (sum_literal_matched / total_max_weights)

    # Component 2: Contextual Placement & Recency Score (Max 20 pts)
    comp2_context_score = 20.0 * (sum_earned_context / total_max_weights)

    # Component 3: Title, Seniority & Calculated Tenure Alignment (Max 15 pts)
    # 3a. Title Taxonomy Match (5 pts)
    title_pts = _score_title_alignment(alignment, resume)

    # 3b. Calculated Domain Tenure (5 pts)
    if resume.total_relevant_tenure_years >= alignment.min_years_required:
        tenure_pts = 5.0
    elif resume.total_relevant_tenure_years >= max(0.0, alignment.min_years_required - 1.0):
        tenure_pts = 3.0
    else:
        tenure_pts = 0.0

    # 3c. Seniority & Enterprise Scale (5 pts)
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

    comp3_alignment_score = title_pts + tenure_pts + seniority_pts
    comp3_breakdown = {
        "Title Taxonomy Match": title_pts,
        f"Calculated Domain Tenure ({resume.total_relevant_tenure_years:.1f} yrs vs {alignment.min_years_required:.1f} yrs req)": tenure_pts,
        "Seniority & Quantified Enterprise Scale": seniority_pts,
    }

    # Component 4: Hard Knockout / Minimum Gate Verification (Max 10 pts)
    knockout_rows = [r for r in ledger if r.requirement.is_knockout_gate]
    failed_knockouts = [r.requirement.name for r in knockout_rows if r.match_credit == 0.0]
    knockout_passed = len(failed_knockouts) == 0
    comp4_knockout_score = 10.0 if knockout_passed else 0.0

    # Component 5: HR-XML Parseability & Schema Integrity (Max 10 pts)
    comp5_schema_score = resume.schema_score

    composite_score = comp1_kw_score + comp2_context_score + comp3_alignment_score + comp4_knockout_score + comp5_schema_score
    comp2_literal_context = 20.0 * (sum(r.literal_earned_item_score for r in ledger) / total_max_weights)
    legacy_literal_score = comp1_literal_score + comp2_literal_context + comp3_alignment_score + comp4_knockout_score + comp5_schema_score

    # Verdict & Primary Bottleneck
    if not knockout_passed:
        verdict = "Gate Risk (knockout gate not verified)"
        primary_bottleneck = f"Mandatory gate(s) not found on the resume: {', '.join(failed_knockouts)}. Confirm with the candidate; 'or equivalent' wording may apply."
    elif composite_score >= 80.0:
        verdict = "Strong match"
        primary_bottleneck = "High alignment across taxonomy and context; minor lexical polish remaining."
    elif composite_score >= 65.0:
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
            action = f"Ask whether the candidate has hands-on experience with '{r.requirement.name}'; write a bullet only from what they confirm."

        recovery_plan.append(
            {
                "requirement": r.requirement.name,
                "tier_label": f"{r.requirement.tier.title()} ({r.requirement.req_multiplier:.1f}x)",
                "max_item_weight": r.max_weight,
                "fix_tier": fix_tier,
                "points_recoverable": total_pts_recoverable,
                "action": action,
            }
        )

    recovery_plan.sort(key=lambda x: float(x["points_recoverable"]), reverse=True)

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
    )


# ---------------------------------------------------------------------------
# Markdown Scorecard Renderer
# ---------------------------------------------------------------------------

def render_markdown_report(report: AuditReport) -> str:
    gate_str = "PASS" if report.knockout_passed else f"NOT VERIFIED ({', '.join(report.failed_knockouts)}) - confirm with candidate"
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
        "",
        "---",
        "",
        "## 2. 5-Component Breakdown Table",
        "",
        "| ATS Component | Max Points | Underlying Formula / Fraction Math | Earned Points |",
        "| :--- | :---: | :--- | :---: |",
        f"| **1. Weighted Keyword & Taxonomy** | 45.0 | `45 * ({report.sum_kw_matched_weights:.2f} / {report.total_max_item_weights:.2f})` *(Literal: `{report.sum_literal_matched_weights:.2f}/{report.total_max_item_weights:.2f}`)* | **{report.comp1_kw_score:.2f}** |",
        f"| **2. Contextual Placement & Recency** | 20.0 | `20 * ({report.sum_earned_item_scores:.2f} / {report.total_max_item_weights:.2f})` | **{report.comp2_context_score:.2f}** |",
        f"| **3. Title, Seniority & Tenure Alignment** | 15.0 | "
        + "; ".join(f"{k}: `{v:.1f}/5`" for k, v in report.comp3_breakdown.items())
        + f" | **{report.comp3_alignment_score:.2f}** |",
        f"| **4. Hard Knockout / Minimum Gate** | 10.0 | `{'All mandatory gates verified' if report.knockout_passed else 'Not verified: ' + ', '.join(report.failed_knockouts)}` | **{report.comp4_knockout_score:.2f}** |",
        f"| **5. HR-XML Parseability & Schema** | 10.0 | `{'Zero schema defects (10.0)' if not report.comp5_deductions else '; '.join(d[0] for d in report.comp5_deductions)}` | **{report.comp5_schema_score:.2f}** |",
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
            f"| **{row.requirement.name}** | {tier_str} | {row.requirement.base_weight} | {row.max_weight:.1f} | `{row.match_credit:.1f}` | `{row.placement_multiplier:.1f}x` | `{row.earned_item_score:.2f}` | {quote_display} | `-{pts_lost:.2f}` |"
        )

    lines.extend(
        [
            "",
            "---",
            "",
            "## 4. Highest-ROI Point Recovery Plan",
            "",
            "| Rank | JD Requirement | Fix Strategy Tier | Recoverable ATS Points | Actionable Instruction |",
            "| :---: | :--- | :--- | :---: | :--- |",
        ]
    )

    for idx, item in enumerate(report.recovery_plan, start=1):
        lines.append(
            f"| {idx} | **{item['requirement']}** ({item['tier_label']}) | {item['fix_tier']} | **`+{item['points_recoverable']:.2f} pts`** | {item['action']} |"
        )

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
        title_match = re.match(r"^(?:job\s+title|title|role|position)\s*[:–-]\s*(.+)$", s, re.IGNORECASE)
        if title_match and not target_title:
            target_title = title_match.group(1).strip()
            continue
        if not target_title and not s.startswith(("-", "*", "•")) and len(s) <= 60 and not s.endswith(":"):
            target_title = re.sub(r"^[#*\s]+|[#*\s]+$", "", s).strip()

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
            paren_match = re.search(r"\((?:e\.g\.,?\s*|such\s+as\s*|including\s*)?([^)]+)\)", clean, re.IGNORECASE)
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
                    )
                )

    return reqs, AlignmentConfig(target_title=target_title, min_years_required=min_years)


def _import_collector():
    """Lazy-import sibling job_posting_collector only when live API collection is requested."""
    try:
        from . import job_posting_collector as collector  # package mode
    except ImportError:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import job_posting_collector as collector  # direct CLI script mode
    return collector


def load_jds_for_audit(
    *,
    url: str | None = None,
    greenhouse: str | None = None,
    lever: str | None = None,
    ashby: str | None = None,
    usajobs: str | None = None,
    location: str = "",
    jd_file: str | None = None,
    from_json: str | None = None,
    cache_json: str | None = None,
    title_filter: str | None = None,
) -> list[dict[str, Any]]:
    """
    Returns normalized job dicts ready for scoring.
    Uses local disk only when `jd_file` or `from_json` is passed;
    lazy-imports `job_posting_collector` only for live compliant API calls.
    """
    # 1. Pure offline paths (zero network imports)
    if from_json:
        raw = json.loads(Path(from_json).read_text(encoding="utf-8"))
        jobs = raw if isinstance(raw, list) else [raw]
    elif jd_file:
        text = Path(jd_file).read_text(encoding="utf-8").strip()
        jobs = [{
            "source": "local_file",
            "job_id": Path(jd_file).stem,
            "company": "Local",
            "title": Path(jd_file).stem,
            "location": "",
            "compensation": "",
            "url": "",
            "description_text": text,
            "attribution": "",
        }]
    else:
        # 2. Direct in-memory function import from job_posting_collector.py
        collector = _import_collector()
        if url:
            objs = collector.collect_from_url(url)
        elif greenhouse:
            objs = collector.fetch_greenhouse(greenhouse)
        elif lever:
            objs = collector.fetch_lever(lever)
        elif ashby:
            objs = collector.fetch_ashby(ashby)
        elif usajobs:
            objs = collector.fetch_usajobs(usajobs, location=location)
        else:
            raise ValueError("Provide a JD input source (--jd-file, --from-json, --url, or an API board flag).")

        jobs = [obj.to_dict() for obj in objs]

        # Optional snapshot write so repeat resume audits stay 100% offline & deterministic
        if cache_json:
            Path(cache_json).write_text(
                json.dumps(jobs, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

    # 3. Optional title regex pre-filter for full-board pulls (Greenhouse/Lever/Ashby/USAJobs)
    if title_filter:
        pattern = re.compile(title_filter, re.IGNORECASE)
        jobs = [j for j in jobs if pattern.search(j.get("title", ""))]

    return jobs


def load_jd_config(path: str) -> Tuple[List[JDRequirement], AlignmentConfig]:
    """Load structured JD requirements and AlignmentConfig from a JSON file."""
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


def audit_resume_against_jd(
    resume_text: str,
    jd_text: str,
    reference_date: Optional[datetime] = None,
) -> AuditReport:
    """Audit resume text against clean JD text only (excluding external metadata/attribution noise)."""
    reqs, align_cfg = extract_jd_from_text(jd_text)
    return run_ats_audit(resume_text, reqs, align_cfg, reference_date=reference_date)


def print_scorecard(scorecard: AuditReport) -> None:
    """Print rendered Markdown scorecard to stdout."""
    print(render_markdown_report(scorecard))


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline Deterministic Enterprise ATS Auditor")
    parser.add_argument("--resume", type=str, help="Path to candidate resume (.docx, .pdf, .txt, or .md)")
    parser.add_argument("--jd-config", type=str, help="Path to structured JD JSON config")
    parser.add_argument("--jd-text", type=str, help="Path to raw Job Description text or HTML file")
    parser.add_argument("--jd-file", type=str, help="Path to local plain-text JD file (pure offline via load_jds_for_audit)")
    parser.add_argument("--from-json", type=str, help="Path to cached normalized job JSON file (pure offline via load_jds_for_audit)")
    parser.add_argument("--url", type=str, help="Compliant Greenhouse/Lever/Ashby job URL via load_jds_for_audit")
    parser.add_argument("--greenhouse", type=str, help="Greenhouse board token via load_jds_for_audit")
    parser.add_argument("--lever", type=str, help="Lever company slug via load_jds_for_audit")
    parser.add_argument("--ashby", type=str, help="Ashby board name via load_jds_for_audit")
    parser.add_argument("--usajobs", type=str, help="USAJobs keyword query via load_jds_for_audit")
    parser.add_argument("--location", type=str, default="", help="Optional location filter for USAJobs")
    parser.add_argument("--cache-json", type=str, help="Optional path to cache fetched normalized jobs as JSON for offline reuse")
    parser.add_argument("--title-filter", type=str, help="Optional regex to filter job titles when pulling full boards")
    parser.add_argument("--jd-url", type=str, help="URL of an employer posting (Greenhouse, Lever, or a career page). LinkedIn, Indeed, ZipRecruiter, and Glassdoor links are not fetched; use --jd-text instead.")
    parser.add_argument("--jd-record-out", type=str, help="Optional path to write full extracted JobPostingRecord JSON (company, salary, benefits, qualifications)")
    parser.add_argument("--export-docx", type=str, help="Optional output .docx path to write an updated Word resume preserving layout")
    parser.add_argument("--replacements-json", type=str, help="Optional JSON file mapping {old_bullet_text: new_bullet_text} for --export-docx")
    parser.add_argument("--output", type=str, help="Path to write Markdown scorecard report")
    parser.add_argument("--json-out", type=str, help="Path to write raw JSON scorecard data")
    parser.add_argument("--reference-date", type=str, help="Optional YYYY-MM-DD reference date for tenure/recency calculation")
    parser.add_argument("--demo", action="store_true", help="Run built-in deterministic verification demo")

    args = parser.parse_args()

    ref_date: Optional[datetime] = None
    if args.reference_date:
        ref_date = datetime.strptime(args.reference_date, "%Y-%m-%d")

    has_audit_loader_src = any([args.jd_file, args.from_json, args.url, args.greenhouse, args.lever, args.ashby, args.usajobs])
    if args.demo or (not args.resume and not args.jd_config and not args.jd_text and not args.jd_url and not has_audit_loader_src):
        resume_text, reqs, align_cfg = get_demo_fixture()
        if ref_date is None:
            ref_date = datetime(2026, 11, 1)
    else:
        if not args.resume:
            parser.error("Please provide --resume <path> or run with --demo.")
        resume_text = load_resume_file(args.resume)

        if args.jd_config:
            reqs, align_cfg = load_jd_config(args.jd_config)
        elif has_audit_loader_src:
            if args.export_docx and args.resume and args.resume.lower().endswith(".docx"):
                repl_map_early: Dict[str, str] = {}
                if args.replacements_json and os.path.exists(args.replacements_json):
                    with open(args.replacements_json, "r", encoding="utf-8") as rf:
                        repl_map_early = json.load(rf)
                export_updated_docx(args.resume, args.export_docx, repl_map_early)

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
            if not jobs:
                parser.error("No matching job postings found for the given source / title filter.")

            md_blocks: List[str] = []
            json_reports: List[Dict[str, Any]] = []
            for job in jobs:
                scorecard = audit_resume_against_jd(
                    resume_text=resume_text,
                    jd_text=job["description_text"],  # Clean JD text only — excludes USAJobs disclaimer noise
                    reference_date=ref_date,
                )
                print(f"=== {job['title']} | {job['company']} ({job['source']}) ===")
                if job.get("url"):
                    print(f"URL: {job['url']}")
                if job.get("attribution"):
                    print(f"Note: {job['attribution']}")
                print_scorecard(scorecard)
                md_blocks.append(render_markdown_report(scorecard))
                json_reports.append(dataclasses.asdict(scorecard))

            if args.output:
                os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write("\n\n---\n\n".join(md_blocks) + "\n")
            if args.json_out:
                os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
                with open(args.json_out, "w", encoding="utf-8") as f:
                    payload = json_reports[0] if len(json_reports) == 1 else json_reports
                    json.dump(payload, f, indent=2, default=str)
            return
        elif args.jd_url:
            import job_posting_collector as jpc
            fetch_res = jpc.fetch_job_source(args.jd_url)
            if not fetch_res.get("ok"):
                parser.error("Could not fetch a job description from that URL (LinkedIn, Indeed, ZipRecruiter, and Glassdoor links are never fetched, and some pages block automated access). Paste the description into a text file and use --jd-text instead.")
            rec = jpc.parse_from_fetch_result(fetch_res)
            if args.jd_record_out:
                os.makedirs(os.path.dirname(os.path.abspath(args.jd_record_out)), exist_ok=True)
                with open(args.jd_record_out, "w", encoding="utf-8") as jf:
                    json.dump(dataclasses.asdict(rec), jf, indent=2, default=str)
            cfg_data = rec.ats_config
            reqs = [JDRequirement(**item) for item in cfg_data.get("requirements", [])]
            align_cfg = AlignmentConfig(**cfg_data.get("alignment", {}))
        elif args.jd_text:
            with open(args.jd_text, "r", encoding="utf-8") as f:
                jd_raw = f.read()
            try:
                import job_posting_collector as jpc
                if "<html" in jd_raw.lower() or "application/ld+json" in jd_raw.lower():
                    jsonld = jpc.extract_jsonld_job_posting(jd_raw)
                    plain = jpc.html_fragment_to_text(jd_raw)
                    rec = jpc.parse_job_posting_text(plain, jsonld=jsonld)
                else:
                    rec = jpc.parse_job_posting_text(jd_raw)
                if args.jd_record_out:
                    os.makedirs(os.path.dirname(os.path.abspath(args.jd_record_out)), exist_ok=True)
                    with open(args.jd_record_out, "w", encoding="utf-8") as jf:
                        json.dump(dataclasses.asdict(rec), jf, indent=2, default=str)
                cfg_data = rec.ats_config
                if cfg_data.get("requirements"):
                    reqs = [JDRequirement(**item) for item in cfg_data["requirements"]]
                    align_cfg = AlignmentConfig(**cfg_data.get("alignment", {}))
                else:
                    reqs, align_cfg = extract_jd_from_text(jd_raw)
            except ImportError:
                reqs, align_cfg = extract_jd_from_text(jd_raw)
        else:
            parser.error("Please provide --jd-config <json>, --jd-text <txt>, or --jd-url <url>.")

    if args.export_docx and args.resume and args.resume.lower().endswith(".docx"):
        repl_map: Dict[str, str] = {}
        if args.replacements_json and os.path.exists(args.replacements_json):
            with open(args.replacements_json, "r", encoding="utf-8") as rf:
                repl_map = json.load(rf)
        export_updated_docx(args.resume, args.export_docx, repl_map)

    report = run_ats_audit(resume_text, reqs, align_cfg, reference_date=ref_date)
    md_output = render_markdown_report(report)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md_output + "\n")

    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(report), f, indent=2, default=str)

    print(md_output)


if __name__ == "__main__":
    main()
