#!/usr/bin/env python3
"""
ops-requisition-auditor: Requisition Quality Audit (Employer Edition, Module 1)
==============================================================================
Scores a *job requisition itself* (not a candidate) with a deterministic 100-point
Requisition Quality Score (RQS), and suggests specific edits that widen the qualified
applicant pool without lowering the bar.

What it looks for (rubric: references/requisition-quality-rubric.md):
  1. Gate necessity           (25)  degree gates with no equivalency, years above level norms,
                                    required certifications, GPA/prestige and employment-gap screens
  2. Requirement load         (20)  "unicorn" lists, competing products all required, no must-have/nice-to-have split
  3. Clarity & tiering        (20)  untiered requirements, vague cliches, compound requirements, missing duties
  4. Title / level / scope    (15)  missing or coded titles, manager titles with no stated scope
  5. Language & transparency  (20)  pay range, unexpanded acronyms, potentially exclusionary wording,
                                    accommodation/EEO statement, length

Every finding quotes the exact requisition line it comes from. The score is a transparent
heuristic for finding fixable issues; it is not legal advice and does not predict applicant
volume or any system's behaviour. Level benchmarks are defaults: replace them with your own
leveling guide via --level-benchmarks.

Runs on the Python 3 standard library only. Reuses the requisition parser and the compliant
public-board fetchers from job_posting_collector.py (restricted boards are never fetched).
"""
from __future__ import annotations

import argparse
import csv
import dataclasses
import hashlib
import io
import json
import os
import re
import statistics
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import job_posting_collector as jpc  # noqa: E402

ENGINE_VERSION = "1.5.0"
RUBRIC_VERSION = "rqs-0.2"

COMPONENTS: List[Tuple[str, str, float]] = [
    ("gates", "Gate necessity", 25.0),
    ("load", "Requirement load", 20.0),
    ("clarity", "Clarity & tiering", 20.0),
    ("title", "Title / level / scope", 15.0),
    ("language", "Language & transparency", 20.0),
]
COMPONENT_MAX = {key: mx for key, _, mx in COMPONENTS}
COMPONENT_LABEL = {key: label for key, label, _ in COMPONENTS}

AMBIGUOUS_LEVELS = {"lead", "mid"}

BANDS = [(85.0, "Clear & inclusive"), (70.0, "Needs tuning"), (0.0, "High hidden-worker risk")]

# Default years-of-experience ceilings per level. Heuristic defaults only: override with --level-benchmarks.
DEFAULT_LEVEL_BENCHMARKS: Dict[str, float] = {
    "entry": 2, "level_1": 2, "level_2": 4, "level_3": 6, "mid": 5, "senior": 8, "lead": 8,
    "staff_principal": 10, "manager": 8, "senior_manager": 10, "director": 12, "executive": 15,
}
LEVEL_LABELS = {
    "entry": "entry-level", "level_1": "level I", "level_2": "level II", "level_3": "level III",
    "mid": "mid-level (no level stated)", "senior": "senior", "lead": "lead", "staff_principal": "staff/principal",
    "manager": "manager", "senior_manager": "senior manager", "director": "director", "executive": "executive/VP",
}

# Product families: requiring several competing products at once is a classic "unicorn" pattern.
PRODUCT_FAMILIES: Dict[str, List[str]] = {
    "Contact-center WFM platform": ["NICE IEX", "NICE WFM", "NICE", "Verint", "Genesys Cloud", "Genesys", "Aspect",
                                    "Calabrio", "Alvaria", "injixo", "Assembled"],
    "BI / dashboard tool": ["Power BI", "Tableau", "Looker", "Qlik Sense", "QlikView", "Qlik", "MicroStrategy",
                            "Domo", "Sisense"],
    "Cloud data warehouse": ["Snowflake", "BigQuery", "Redshift", "Databricks", "Azure Synapse", "Teradata"],
    "Cloud platform": ["AWS", "Amazon Web Services", "Azure", "Microsoft Azure", "GCP", "Google Cloud"],
    "CRM": ["Salesforce", "HubSpot", "Microsoft Dynamics", "Dynamics 365", "Zoho CRM"],
    "ERP": ["SAP S/4HANA", "SAP ERP", "SAP", "Oracle NetSuite", "NetSuite", "Oracle EBS", "Oracle Cloud ERP",
            "Infor", "Epicor"],
    "Statistics package": ["SAS", "SPSS", "Stata"],
    "CCaaS / telephony platform": ["Five9", "NICE CXone", "Amazon Connect", "Talkdesk", "Avaya"],
}
_CASE_SENSITIVE_PRODUCTS = {"NICE", "SAS", "SAP", "AWS", "GCP", "Domo"}

DEGREE_RE = re.compile(
    r"\b(?:bachelor|master)(?:'?s)?\b|\bmasters\b|\bassociate'?s\s+degree\b|\bMBA\b|\bPh\.?D\b|\bdoctorate\b"
    r"|\bB\.S\.|\bB\.A\.|\bM\.S\.|\bBS/BA\b|\bBA/BS\b"
    r"|\b(?:university|college|academic|undergraduate|graduate|four[-\s]year|4[-\s]year|law)\s+degree\b"
    r"|\b(?:a|your)\s+degree\b(?!\s+of\b)|\bdegree\s+(?:in|from|required)\b",
    re.IGNORECASE,
)
STUDENT_PROGRAM_RE = re.compile(r"\b(?:currently\s+)?(?:enrolled|pursuing|candidate\s+for|working\s+toward)\b", re.IGNORECASE)
ADVANCED_OPTIONAL_RE = re.compile(
    r"(?:master'?s?|masters|MBA|Ph\.?D|doctorate)[^.;]{0,40}?(?:a\s+plus|preferred|nice\s+to\s+have|bonus|an?\s+asset|desired)",
    re.IGNORECASE,
)
PERM_RE = re.compile(r"minimum\s+requirements\s+for\s+(?:the\s+)?position|foreign\s+(?:degree\s+)?equivalent", re.IGNORECASE)
SOFT_REQUIREMENT_RE = re.compile(r"\b(?:ideally|preferably|preferred|a\s+plus|nice\s+to\s+have|bonus)\b", re.IGNORECASE)
PAY_RANGE_RE = re.compile(
    r"(?:[$\u20ac\u00a3\u20b9]|\b(?:USD|EUR|GBP|CAD|INR|AUD)\s?)\s?\d[\d,.\u00a0 ]*(?:k|K)?\s*(?:[-\u2013\u2014]|to)\s*"
    r"(?:[$\u20ac\u00a3\u20b9]|\b(?:USD|EUR|GBP|CAD|INR|AUD)\s?)?\s?\d"
)
ADVANCED_DEGREE_RE = re.compile(r"\bmaster'?s\b|\bmasters\b|\bMBA\b|\bPh\.?D\b|\bdoctorate\b|\bM\.S\.", re.IGNORECASE)
EQUIVALENCY_RE = re.compile(
    r"or\s+(?:an?\s+)?equivalent|equivalent\s+(?:combination|work|practical|professional|relevant)?\s*(?:of\s+)?(?:education|experience)"
    r"|in\s+lieu\s+of|combination\s+of\s+(?:education|training)\s+and\s+experience"
    r"|or\s+(?:relevant|related|comparable)\s+(?:work\s+)?experience|degree\s+(?:is\s+)?(?:preferred|a\s+plus|not\s+required)",
    re.IGNORECASE,
)
YEARS_RE = re.compile(
    r"\b(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*(\d{1,2})\s*)?\+?\s*years?(?!\s+(?:of\s+age|old|or\s+older|ago)\b)\b",
    re.IGNORECASE,
)
NON_EXPERIENCE_YEARS_RE = re.compile(
    r"\byears?\s+(?:of\s+age|old|or\s+older|ago)\b|\bfor\s+(?:nearly|over|more\s+than|almost)\s+\d+\s+years\b",
    re.IGNORECASE,
)
LICENSURE_RE = re.compile(r"licen[sc]e|licensure|\bRN\b|\bCPA\b|\bCDL\b|\bPE\b|bar\s+admission|board[-\s]certified", re.IGNORECASE)
PRESTIGE_RE = re.compile(r"\bGPA\b|top[-\s]tier\s+(?:university|school|college)|\bIvy\s+League\b|elite\s+(?:university|school)", re.IGNORECASE)
GAP_RE = re.compile(
    r"no\s+(?:employment\s+|work\s+)?gaps|gap[-\s]free|continuous(?:ly)?\s+employ|uninterrupted\s+employment|must\s+be\s+currently\s+employed|currently\s+employed\s+candidates\s+only",
    re.IGNORECASE,
)
ALT_LINE_RE = re.compile(r"\bor\b|and/or|any\s+(?:one\s+)?of|such\s+as|e\.g\.|similar|comparable|equivalent", re.IGNORECASE)
SCOPE_RE = re.compile(
    r"direct\s+reports?|team\s+of\s+\d+|lead(?:s|ing)?\s+(?:a\s+)?team|manag(?:e|es|ing)\s+(?:a\s+)?(?:team|staff|people)|people\s+manage"
    r"|supervis|budget|headcount|hir(?:e|ing)\b|coach|mentor|\bP&L\b",
    re.IGNORECASE,
)
CODED_TITLE_RE = re.compile(r"\b(?:L\d{1,2}|IC\d|P\d|M\d|Band\s*\d+|Grade\s*\d+|Level\s*\d+|REQ[-\s]?\d+)\b")
EEO_RE = re.compile(r"equal\s+opportunity|\bEEO\b|reasonable\s+accommodation|accommodations?\s+(?:are\s+)?available", re.IGNORECASE)

PAY_TRANSPARENCY_LOCATION_RE = re.compile(
    r"\b(?:California|Colorado|New\s+York|Washington(?!,?\s*D\.?C)|Illinois|Maryland|Minnesota|Vermont|Massachusetts|New\s+Jersey|Hawaii"
    r"|District\s+of\s+Columbia|Washington,?\s*D\.?C\.?|CA|CO|NY|WA|IL|MD|MN|VT|MA|NJ|HI|DC|Remote)\b"
)

# (pattern, label, points, automatic replacement or None, guidance)
VAGUE_PHRASES: List[Tuple[str, str, Optional[str], str]] = [
    (r"\brock\s?stars?\b", "rockstar", None, "Name the concrete skill or outcome you need instead."),
    (r"\bninjas?\b", "ninja", None, "Name the concrete skill or outcome you need instead."),
    (r"\bgurus?\b", "guru", None, "Name the concrete skill or outcome you need instead."),
    (r"\bunicorns?\b", "unicorn", None, "Name the concrete skill or outcome you need instead."),
    (r"\bsuperstars?\b", "superstar", None, "Name the concrete skill or outcome you need instead."),
    (r"\bwear\s+many\s+hats\b", "wear many hats", None, "List the actual responsibilities instead."),
    (r"\bwork\s+hard,?\s+play\s+hard\b", "work hard, play hard", None, "Describe actual hours, schedule, and culture."),
    (r"\bhit\s+the\s+ground\s+running\b", "hit the ground running", None, "State the real ramp-up expectation (e.g., independent within 60 days)."),
    (r"\bfast[-\s]paced\b", "fast-paced", None, "Describe the pace concretely (e.g., 24/7 operation with intraday schedule changes)."),
    (r"\bself[-\s]starters?\b", "self-starter", None, "Describe what the person will do independently."),
    (r"\bgo[-\s]getters?\b", "go-getter", None, "Name the concrete skill or outcome you need instead."),
    (r"\bthrives?\s+in\s+ambiguity\b", "thrives in ambiguity", None, "Describe the actual uncertainty (e.g., new team, changing priorities)."),
]

# (pattern, label, points, automatic replacement or None, why)
EXCLUSIONARY_PHRASES: List[Tuple[str, str, float, Optional[str], str]] = [
    (r"\bdigital\s+natives?\b", "digital native", 3.0, None,
     "Age-coded. Name the specific tools instead (e.g., 'comfortable with [INSERT: tools]')."),
    (r"\brecent\s+(?:college\s+)?grad(?:uate)?s?\b", "recent graduate", 3.0, "early-career candidates",
     "Can read as age-coded. Prefer 'early-career candidates'."),
    (r"\byoung(?:er)?\s+(?:and\s+)?(?:energetic|dynamic|team|professionals?)\b", "young/energetic", 3.0, None,
     "Age-coded. Describe the work instead."),
    (r"\bnative\s+English\s+speakers?\b", "native English speaker", 3.0, "fluent English speaker",
     "Can exclude by national origin. Prefer 'fluent in English (written and spoken)'."),
    (r"\bsales(?:man|men)\b", "salesman", 3.0, "salesperson", "Gendered term."),
    (r"\bchair(?:man|men)\b", "chairman", 3.0, "chair", "Gendered term."),
    (r"\bmanpower\b", "manpower", 3.0, "staffing", "Gendered term."),
    (r"\bhe\s*/\s*she\b|\bhe\s+or\s+she\b|\bs/he\b", "he/she", 3.0, "they", "Use gender-neutral 'they'."),
    (r"\bable[-\s]bodied\b", "able-bodied", 3.0, None,
     "Describe the physical task (e.g., 'lift up to 25 lb') and note reasonable accommodation."),
    (r"\bno\s+(?:criminal\s+)?(?:record|felon(?:y|ies|s)?|convictions?)\b", "no criminal record", 3.0, None,
     "Describe the background check instead and follow applicable fair-chance hiring laws."),
    (r"\bculture\s+fit\b", "culture fit", 1.0, "values alignment",
     "Vague and can mask bias. Name the values or working norms."),
]

COMMON_ACRONYMS = {
    "US", "USA", "U", "S", "HR", "IT", "PTO", "EEO", "CEO", "CFO", "COO", "CTO", "CIO", "VP", "SVP", "EVP", "API", "APIs",
    "KPI", "BI", "UI", "UX", "PC", "IBM", "OK", "ID", "FAQ", "PDF", "LLC", "INC", "AM", "PM", "EST", "CST", "PST", "MST",
    "ET", "CT", "PT", "MT", "Q1", "Q2", "Q3", "Q4", "B2B", "B2C", "AI", "ML", "CRM", "ERP", "HQ", "MBA", "GPA", "RN", "PE",
    "CPA", "CDL", "NA", "EU", "UK", "II", "III", "IV", "AND", "OR", "THE", "DC", "REQ", "JOB",
    "USD", "EUR", "GBP", "CAD", "INR", "AUD", "NYC", "SF", "LA", "CV", "AEDT", "HYBRID", "REMOTE", "ONSITE", "NOTE",
    "EMEA", "APAC", "AMER", "LATAM", "NA", "SMB",
}
# Product and language names written in capitals: these are names, not jargon to expand.
TECH_NAMES = {
    "SQL", "AWS", "GCP", "HTML", "CSS", "PHP", "SAS", "SAP", "NICE", "IEX", "UKG", "DOMO", "VBA", "DAX", "ETL", "ELT",
    "REST", "SPSS", "MATLAB", "JSON", "XML", "CSV",
}
# Standard accounting, security, clinical, and project-management credentials/frameworks.
STANDARD_CERT_ACRONYMS = {
    "CPA", "CFA", "CIA", "CFE", "CMA", "ACCA", "EA", "GAAP", "IFRS", "SOX", "COSO", "FCPA", "PCAOB",
    "CISSP", "CISM", "CISA", "CRISC", "CCSP", "CEH", "OSCP", "GSEC", "GCIH", "GIAC", "NIST", "ISO", "SOC",
    "HIPAA", "HITRUST", "GDPR", "CCPA", "FEDRAMP", "FISMA", "PCI", "DSS",
    "PMP", "CAPM", "SHRM", "PHR", "SPHR", "AAP", "AFPP",
    "RN", "LPN", "LVN", "NP", "APRN", "PA", "MD", "BSN", "MSN", "BLS", "ACLS", "PALS", "CNA",
}
# Role-domain acronym allowlists so specialist accounting, security, and clinical roles aren't penalized for domain terms.
SPECIALIST_DOMAIN_ALLOWLISTS: List[Tuple[re.Pattern[str], set[str]]] = [
    (
        re.compile(r"\b(?:security|cyber|infosec|soc|grc|privacy|trust|threat|vulnerability|fraud|identity)\b", re.IGNORECASE),
        {
            "SOC", "SIEM", "SOAR", "EDR", "XDR", "MDR", "DLP", "CSPM", "CWPP", "CNAPP", "CASB", "SASE", "ZTNA",
            "IAM", "PAM", "SSO", "MFA", "SAML", "OIDC", "RBAC", "ABAC", "PKI", "TLS", "SSL", "SSH", "DNS", "DHCP",
            "TCP", "UDP", "VPN", "WAF", "IDS", "IPS", "CVE", "CVSS", "OWASP", "MITRE", "GRC", "CIPP", "CIPM", "CIPT",
            "ISACA", "SANS", "OSINT", "DFIR", "CTI",
        },
    ),
    (
        re.compile(
            r"\b(?:account(?:ant|ing|s)|audit(?:or|ing)?|tax|treasury|controller|finance|financial|payment|payroll"
            r"|compliance|regulatory|risk|due\s+diligence|brokerage|clearing|underwriting|actuar(?:y|ial)|fp&a|royalt(?:y|ies))\b",
            re.IGNORECASE,
        ),
        {
            "FINRA", "SEC", "IRS", "OCC", "FDIC", "CFPB", "FINCEN", "AML", "KYC", "KYB", "BSA", "OFAC", "SAR", "CTR",
            "EDD", "CDD", "ACH", "RTP", "EFT", "SWIFT", "SEPA", "NACHA", "SOC", "GRC", "AAP", "AFPP", "PEO",
            "GL", "AP", "AR", "FP", "FPA", "EBITDA", "ARR", "MRR", "COGS", "OPEX", "CAPEX", "ASC", "FASB", "NAV", "AUM",
            "LTV", "CAC", "ROI", "ROE",
        },
    ),
    (
        re.compile(
            r"\b(?:nurs(?:e|ing)|clinical|medical|health(?:care)?|pharmac(?:y|ist)|physician|therapist|patient|care"
            r"|hospital|dental|behavioral|utilization|credentialing|hedis|medicare|medicaid)\b",
            re.IGNORECASE,
        ),
        {
            "EMR", "EHR", "EPIC", "CMS", "NCQA", "HEDIS", "HL7", "FHIR", "ICD", "CPT", "HCPCS", "DRG", "NPI", "CLIA",
            "OSHA", "JCAHO", "PHI", "PII", "PBM", "NDC", "FDA", "GCP", "GLP", "GMP", "IRB", "CRO", "DME", "SNF",
            "LTAC", "ICU", "NICU", "PACU", "LCSW", "LMFT", "LMHC", "LPCC", "LPC", "BCBA",
        },
    ),
]
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
}

EEO_STATEMENT = (
    "We are an equal opportunity employer and provide reasonable accommodations to qualified applicants "
    "with disabilities upon request."
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    rule_id: str
    component: str
    severity: str  # "high" | "medium" | "low" | "info"
    points: float
    title: str
    quote: str
    explanation: str
    suggestion: str
    edit_old: Optional[str] = None  # exact substring to replace in the requisition text
    edit_new: Optional[str] = None
    hidden_worker_risk: bool = False


@dataclass
class ComponentScore:
    key: str
    label: str
    max_points: float
    deducted: float
    earned: float
    findings: int


@dataclass
class RequisitionAudit:
    tool: str
    module: str
    engine_version: str
    rubric_version: str
    input_sha256: str
    title: str
    company: str
    location: str
    source_url: str
    source_platform: str
    score: float
    band: str
    components: List[ComponentScore]
    findings: List[Finding]
    stats: Dict[str, Any]
    projected_score: Optional[float] = None
    projected_band: Optional[str] = None
    improved_text: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def band_for(score: float) -> str:
    for threshold, label in BANDS:
        if score >= threshold:
            return label
    return BANDS[-1][1]


def _source_line(lines: Sequence[str], fragment: str) -> str:
    """Return the original requisition line containing `fragment` (bullet markers kept), or the fragment itself."""
    frag = fragment.strip().lower()
    for ln in lines:
        if frag and frag in ln.lower():
            return ln.strip()
    return fragment.strip()


def pay_range_present(rec: jpc.JobPostingRecord) -> bool:
    return rec.salary.min_amount is not None or bool(PAY_RANGE_RE.search(rec.full_description_text))


def detect_level(title: str) -> str:
    t = title.lower()
    if re.search(r"\b(?:vp|vice\s+president|chief|svp|evp|head\s+of)\b", t):
        return "executive"
    if re.search(r"\bdirector\b", t):
        return "director"
    if re.search(r"\b(?:senior|sr\.?)\s+manager\b", t):
        return "senior_manager"
    if re.search(r"\b(?:manager|supervisor)\b", t):
        return "manager"
    if re.search(r"\b(?:principal|staff)\b", t):
        return "staff_principal"
    if re.search(r"\blead\b", t):
        return "lead"
    if re.search(r"\b(?:senior|sr\.?)\b", t):
        return "senior"
    if re.search(r"\b(?:intern|junior|jr\.?|entry[-\s]level|trainee|apprentice)\b", t):
        return "entry"
    if re.search(r"(?:^|\s)(?:III|3)(?:\s|$|[-,|])", title):
        return "level_3"
    if re.search(r"(?:^|\s)(?:II|2)(?:\s|$|[-,|])", title):
        return "level_2"
    if re.search(r"(?:^|\s)(?:I|1)(?:\s|$|[-,|])", title):
        return "level_1"
    if re.search(r"\bassociate\b", t):
        return "entry"
    return "mid"


def find_products(text: str) -> Dict[str, List[str]]:
    """Return {family: [canonical products found]} using longest-match spans (NICE IEX absorbs NICE)."""
    spans: List[Tuple[int, int, str, str]] = []
    for family, members in PRODUCT_FAMILIES.items():
        for name in members:
            flags = 0 if name in _CASE_SENSITIVE_PRODUCTS else re.IGNORECASE
            for m in re.finditer(rf"(?<![\w/]){re.escape(name)}(?![\w/])", text, flags):
                spans.append((m.start(), m.end(), family, name))
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept: List[Tuple[int, int, str, str]] = []
    for sp in spans:
        if any(k[0] <= sp[0] and sp[1] <= k[1] for k in kept):
            continue
        kept.append(sp)
    found: Dict[str, List[str]] = {}
    for _, _, family, name in kept:
        found.setdefault(family, [])
        if name not in found[family]:
            found[family].append(name)
    return found


def required_years(lines: Sequence[str]) -> Tuple[Optional[float], str]:
    """Highest minimum-years figure stated in the given requirement lines, with its line."""
    best: Tuple[Optional[float], str] = (None, "")
    for ln in lines:
        if NON_EXPERIENCE_YEARS_RE.search(ln):
            continue
        for m in YEARS_RE.finditer(ln):
            val = float(m.group(1))
            if val > 25:
                continue
            if best[0] is None or val > best[0]:
                best = (val, ln)
    return best


def unexpanded_acronyms(text: str, title: str) -> List[str]:
    tokens = re.findall(r"(?<![\w/&-])([A-Z]{2,6})(?![\w/&-])", text)
    domain_allowed: set[str] = set()
    role_context = f"{title}\n{text[:400]}"
    for domain_rx, allowed_set in SPECIALIST_DOMAIN_ALLOWLISTS:
        if domain_rx.search(title) or domain_rx.search(role_context):
            domain_allowed |= allowed_set
    seen: List[str] = []
    for tok in tokens:
        if (
            tok in seen
            or tok in COMMON_ACRONYMS
            or tok in TECH_NAMES
            or tok in STANDARD_CERT_ACRONYMS
            or tok in domain_allowed
            or tok in US_STATES
        ):
            continue
        if re.search(rf"\(\s*{re.escape(tok)}\s*\)", text) or re.search(rf"\b{re.escape(tok)}\s*\(", text):
            continue  # expanded somewhere: "average handle time (AHT)" or "AHT (average handle time)"
        if tok in title.split():
            continue
        seen.append(tok)
    return seen


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def _rules_gates(rec: jpc.JobPostingRecord, lines: List[str], req_lines: List[str], level: str,
                 benchmarks: Dict[str, float]) -> List[Finding]:
    out: List[Finding] = []
    full = rec.full_description_text
    scan = req_lines if req_lines else [ln for ln in lines if ln not in rec.preferred_qualifications]

    # G1 / G2: degree gates
    equivalency_anywhere = any(DEGREE_RE.search(ln) and EQUIVALENCY_RE.search(ln) for ln in lines)
    for ln in scan:
        if not DEGREE_RE.search(ln) or EQUIVALENCY_RE.search(ln) or equivalency_anywhere:
            continue
        if STUDENT_PROGRAM_RE.search(ln):
            continue  # enrolment requirements for internships/student programs are not degree gates
        src = _source_line(lines, ln)
        if PERM_RE.search(ln):
            out.append(Finding(
                "G1P", "gates", "info", 0.0, "Degree requirement in labor-certification style wording", src,
                "Wording like 'Minimum Requirements for Position' or 'foreign equivalent' often comes from immigration (PERM) "
                "labor-certification postings, where requirements are fixed by the filing. No points deducted.",
                "Check whether this posting is tied to a labor-certification filing before editing the requirements."))
            break
        base = src.rstrip(" .;")
        new_line = f"{base}, or an equivalent combination of education and experience."
        out.append(Finding(
            "G1", "gates", "high", 10.0, "Degree required with no equivalency path", src,
            "A required degree with no 'or equivalent experience' path screens out experienced candidates who learned "
            "on the job (HBS 'Hidden Workers' research).",
            "Add an equivalency path, or move the degree to Preferred if it is not essential.",
            edit_old=src, edit_new=new_line, hidden_worker_risk=True))
        if ADVANCED_DEGREE_RE.search(ln) and not ADVANCED_OPTIONAL_RE.search(ln):
            out.append(Finding(
                "G2", "gates", "medium", 4.0, "Advanced degree required", src,
                "Requiring a master's or doctorate narrows the pool sharply; confirm the work truly needs it.",
                "Move the advanced degree to Preferred unless the role legally or technically requires it.",
                hidden_worker_risk=True))
        break  # one degree finding is enough; avoid double counting

    # G3: years above the level benchmark
    yrs, yrs_line = required_years([ln for ln in scan if not SOFT_REQUIREMENT_RE.search(ln)])
    bench = benchmarks.get(level, DEFAULT_LEVEL_BENCHMARKS["mid"])
    if yrs is not None and yrs > bench:
        excess = yrs - bench
        ambiguous = level in AMBIGUOUS_LEVELS
        if ambiguous:
            pts, severity = 3.0, "low"
        else:
            pts, severity = (8.0, "high") if excess >= 3 else (5.0, "medium")
        src = _source_line(lines, yrs_line)
        note = (" The level was inferred from a title word that varies a lot between employers, so treat this as a prompt to check."
                if ambiguous else "")
        out.append(Finding(
            "G3", "gates", severity, pts,
            f"{yrs:g}+ years required for a {LEVEL_LABELS.get(level, level)} title", src,
            f"This is {excess:g} year(s) above the default benchmark of {bench:g} for a {LEVEL_LABELS.get(level, level)} role. "
            "Inflated year counts screen out capable candidates and conflict with the stated level." + note,
            "Use the years from your leveling guide, e.g. '[INSERT: N]+ years', or describe the capability instead of a year count.",
            hidden_worker_risk=True))

    # G4: required certifications that may not be essential
    cert_pts = 0.0
    for cert in jpc.KNOWN_CERTIFICATIONS:
        cert_rx = re.compile(rf"(?<!\w){re.escape(cert)}(?!\w)", 0 if cert.isupper() else re.IGNORECASE)
        hit = next((ln for ln in scan if cert_rx.search(ln)), None)
        if hit is None:
            continue
        src = _source_line(lines, hit)
        if LICENSURE_RE.search(cert) or LICENSURE_RE.search(src):
            out.append(Finding(
                "G4L", "gates", "info", 0.0, f"Licensure listed: {cert}", src,
                "Licenses can be legally required for the work. No points deducted.",
                "Confirm the license is legally required for this role and location."))
            continue
        if cert_pts >= 6.0:
            continue
        cert_pts += 3.0
        out.append(Finding(
            "G4", "gates", "medium", 3.0, f"Certification required: {cert}", src,
            "Certifications are often learnable on the job. Requiring them up front shrinks the pool.",
            f"Move '{cert}' to Preferred, or write 'or ability to obtain within [INSERT: N] months'.",
            hidden_worker_risk=True))

    # G5: GPA / prestige screens
    for ln in lines:
        if PRESTIGE_RE.search(ln):
            out.append(Finding(
                "G5", "gates", "medium", 3.0, "GPA or school-prestige screen", ln,
                "GPA and school-prestige screens correlate weakly with job performance and filter by background.",
                "Remove the GPA/prestige requirement and describe the needed skills instead.",
                hidden_worker_risk=True))
            break

    # G6: employment-gap screens
    for ln in lines:
        if GAP_RE.search(ln):
            out.append(Finding(
                "G6", "gates", "high", 6.0, "Employment-gap screen", ln,
                "Screens out caregivers, veterans, people with disabilities, and people returning to work.",
                "Remove this requirement.", edit_old=ln, edit_new="", hidden_worker_risk=True))
            break
    return out


def _rules_load(rec: jpc.JobPostingRecord, lines: List[str], req_lines: List[str]) -> List[Finding]:
    out: List[Finding] = []
    n_req = len(req_lines)
    if n_req > 14:
        out.append(Finding(
            "L1", "load", "high", 8.0, f"{n_req} required items", f"Required list: {n_req} items",
            "Long must-have lists discourage qualified applicants, who often apply only when they meet every item.",
            "Keep 6-10 true must-haves; move the rest to Preferred.", hidden_worker_risk=True))
    elif n_req > 10:
        out.append(Finding(
            "L1", "load", "medium", 4.0, f"{n_req} required items", f"Required list: {n_req} items",
            "Long must-have lists discourage qualified applicants.",
            "Keep 6-10 true must-haves; move the rest to Preferred.", hidden_worker_risk=True))

    # Count named tools/skills, treating options the requisition itself offers ("Power BI or Tableau") as one unit.
    units: List[str] = []
    for ln in req_lines:
        names = {sp[0] for sp in jpc._drop_contained_spans(jpc.find_skill_spans(ln))}
        if ALT_LINE_RE.search(ln):
            for family, members in find_products(ln).items():
                if len(members) >= 2:
                    member_keys = {m.lower() for m in members}
                    names = {n for n in names if n.lower() not in member_keys} | {f"any {family}"}
        for n in sorted(names):
            if n not in units:
                units.append(n)
    req_skills = sorted(units)
    if len(req_skills) > 8:
        out.append(Finding(
            "L2", "load", "medium", 6.0, f"{len(req_skills)} named tools/skills required", ", ".join(req_skills),
            "Requiring many named tools at once describes a 'unicorn' candidate.",
            "Require the 3-5 tools used daily; list the rest as Preferred or 'or similar'.", hidden_worker_risk=True))
    elif len(req_skills) > 6:
        out.append(Finding(
            "L2", "load", "low", 3.0, f"{len(req_skills)} named tools/skills required", ", ".join(req_skills),
            "A long list of required tools narrows the pool.",
            "Require the tools used daily; list the rest as Preferred.", hidden_worker_risk=True))

    # L3: competing products all required (not offered as alternatives)
    req_block = "\n".join(req_lines)
    family_pts = 0.0
    alt_groups = [{m.lower() for m in g} for g in rec.alternative_groups]
    for family, names in find_products(req_block).items():
        if len(names) < 2:
            continue
        lowered = {n.lower() for n in names}
        if any(lowered <= g for g in alt_groups):
            continue
        member_lines = [ln for ln in req_lines if any(re.search(rf"(?<![\w/]){re.escape(n)}(?![\w/])", ln,
                                                                 0 if n in _CASE_SENSITIVE_PRODUCTS else re.IGNORECASE) for n in names)]
        if len(member_lines) == 1 and ALT_LINE_RE.search(member_lines[0]):
            continue
        if family_pts >= 8.0:
            continue
        family_pts += 4.0
        src = _source_line(lines, member_lines[0]) if member_lines else ", ".join(names)
        edit_old = edit_new = None
        if len(member_lines) == 1:
            edit_old = src
            edit_new = f"{src.rstrip(' .;')} (experience with any one of these is sufficient)."
        out.append(Finding(
            "L3", "load", "high", 4.0, f"Competing {family}s all required: {', '.join(names)}", src,
            "These products do the same job. Few candidates have used all of them, and skills transfer between them.",
            f"Offer them as alternatives: 'experience with any one of {', '.join(names[:-1])} or {names[-1]}'.",
            edit_old=edit_old, edit_new=edit_new, hidden_worker_risk=True))

    if n_req > 6 and not rec.preferred_qualifications and not any(f.rule_id == "L1" for f in out):
        out.append(Finding(
            "L4", "load", "low", 2.0, "Everything is a must-have", f"Required: {n_req} items; Preferred: 0",
            "With no Preferred section, every item reads as a hard requirement.",
            "Split the list into Required (must-have on day one) and Preferred (nice to have)."))
    return out


def _rules_clarity(rec: jpc.JobPostingRecord, lines: List[str], req_lines: List[str]) -> List[Finding]:
    out: List[Finding] = []
    if not req_lines and not rec.preferred_qualifications:
        out.append(Finding(
            "C1", "clarity", "high", 8.0, "Requirements are not tiered", "No Required/Preferred sections detected",
            "Candidates and recruiters cannot tell must-haves from nice-to-haves.",
            "Add 'Required Qualifications' and 'Preferred Qualifications' headings."))

    vague_pts = 0.0
    full = rec.full_description_text
    for pat, label, _, guidance in VAGUE_PHRASES:
        m = re.search(pat, full, re.IGNORECASE)
        if not m or vague_pts >= 5.0:
            continue
        vague_pts += 1.0
        out.append(Finding(
            "C2", "clarity", "low", 1.0, f"Vague cliche: '{label}'", _source_line(lines, m.group(0)),
            "Cliches don't tell candidates what the job requires and can discourage some applicants.", guidance))

    long_pts = 0.0
    for ln in req_lines:
        if len(ln.split()) > 40 and long_pts < 3.0:
            long_pts += 1.0
            out.append(Finding(
                "C3", "clarity", "low", 1.0, "Compound requirement", _source_line(lines, ln),
                "Several requirements packed into one line are hard to assess and hard to meet as a unit.",
                "Split it into separate, specific requirements."))

    if not rec.responsibilities:
        out.append(Finding(
            "C4", "clarity", "medium", 4.0, "No responsibilities listed", "No Responsibilities section detected",
            "Without duties, candidates can't judge fit and requirements can't be checked against the actual work.",
            "Add 4-8 bullets describing what the person will actually do."))
    return out


def _rules_title(rec: jpc.JobPostingRecord, lines: List[str], level: str) -> List[Finding]:
    out: List[Finding] = []
    title = rec.job_title.strip()
    if not title:
        out.append(Finding("T1", "title", "high", 5.0, "No job title found", "(none)",
                           "Candidates search by title; an untitled requisition is hard to find.",
                           "Add a 'Job Title:' line with a standard, searchable title."))
        return out
    if not CODED_TITLE_RE.search(title) and len(title) > 80:
        out.append(Finding(
            "T2", "title", "low", 1.0, "Very long title", title,
            "Long titles get truncated in search results and job alerts.",
            "Keep the searchable job title short and move team details into the description."))
    if CODED_TITLE_RE.search(title):
        out.append(Finding(
            "T2", "title", "medium", 3.0, "Internal codes or clutter in the title", title,
            "Internal level codes, team names, and requisition numbers make titles harder to search and understand.",
            "Use a standard title (e.g., 'Workforce Management Analyst II') and move codes to internal fields."))
    if level in ("manager", "senior_manager", "director", "executive"):
        if not SCOPE_RE.search(rec.full_description_text):
            out.append(Finding(
                "T3", "title", "medium", 5.0, "Leadership title with no stated scope", title,
                "A manager/director title with no team size, budget, or people responsibilities leaves the level unclear.",
                "State the scope: direct reports, team size, budget, or decision rights."))
    return out


def _rules_language(rec: jpc.JobPostingRecord, lines: List[str], title: str, from_board_api: bool = False) -> List[Finding]:
    out: List[Finding] = []
    full = rec.full_description_text

    if not pay_range_present(rec):
        legal_hint = bool(PAY_TRANSPARENCY_LOCATION_RE.search(rec.location or ""))
        out.append(Finding(
            "P1", "language", "high" if legal_hint else "medium", 8.0, "No pay range", f"Location: {rec.location or 'not stated'}",
            "Postings without pay ranges get fewer qualified applicants."
            + (" This location may be covered by a pay-transparency law; verify current requirements." if legal_hint else ""),
            "Add 'Pay range: $[INSERT: min] - $[INSERT: max] per [year/hour]'."))

    acr = unexpanded_acronyms(full, title)
    if len(acr) >= 10:
        out.append(Finding("P2", "language", "medium", 4.0, f"{len(acr)} unexpanded acronyms", ", ".join(acr),
                           "Jargon screens out career-changers and candidates from other industries.",
                           "Spell out each acronym on first use, e.g. 'average handle time (AHT)'."))
    elif len(acr) >= 5:
        out.append(Finding("P2", "language", "low", 2.0, f"{len(acr)} unexpanded acronyms", ", ".join(acr),
                           "Jargon screens out career-changers and candidates from other industries.",
                           "Spell out each acronym on first use, e.g. 'average handle time (AHT)'."))

    excl_pts = 0.0
    for pat, label, pts, replacement, why in EXCLUSIONARY_PHRASES:
        m = re.search(pat, full, re.IGNORECASE)
        if not m or excl_pts >= 9.0:
            continue
        excl_pts += pts
        out.append(Finding(
            "P3", "language", "medium" if pts >= 3 else "low", pts, f"Potentially exclusionary wording: '{label}'",
            _source_line(lines, m.group(0)), why,
            f"Replace with '{replacement}'." if replacement else "Rewrite as suggested.",
            edit_old=m.group(0) if replacement else None, edit_new=replacement))

    if not EEO_RE.search(full) and from_board_api:
        out.append(Finding(
            "P4", "language", "info", 0.0, "No equal-opportunity statement in the posting text", "(not found)",
            "Job boards often add this statement outside the posting text, so no points are deducted for board audits.",
            "Confirm the statement appears on the live posting."))
    elif not EEO_RE.search(full):
        out.append(Finding(
            "P4", "language", "low", 2.0, "No equal-opportunity or accommodation statement", "(not found)",
            "An accommodation statement signals that candidates with disabilities are welcome to apply.",
            f"Add a statement such as: '{EEO_STATEMENT}' (review the wording with counsel).",
            edit_old="__APPEND__", edit_new=EEO_STATEMENT))

    words = len(full.split())
    if words < 120:
        out.append(Finding("P5", "language", "low", 3.0, f"Very short posting ({words} words)", "(whole posting)",
                           "Thin postings leave out the information candidates need to self-assess.",
                           "Add responsibilities, requirements, pay, and location details."))
    elif words > 2000:
        out.append(Finding("P5", "language", "low", 2.0, f"Very long posting ({words} words)", "(whole posting)",
                           "Very long postings bury the essential requirements.",
                           "Trim boilerplate and keep requirements near the top."))
    return out


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _score(findings: List[Finding]) -> Tuple[float, List[ComponentScore]]:
    comps: List[ComponentScore] = []
    total = 0.0
    for key, label, mx in COMPONENTS:
        deducted = min(mx, sum(f.points for f in findings if f.component == key))
        earned = round(mx - deducted, 2)
        total += earned
        comps.append(ComponentScore(key, label, mx, round(deducted, 2), earned,
                                    sum(1 for f in findings if f.component == key and f.points > 0)))
    return round(total, 1), comps


def apply_edits(text: str, findings: List[Finding]) -> str:
    """Apply only the concrete, deterministic edits. Placeholder suggestions are left for a human."""
    out = text
    append: List[str] = []
    for f in findings:
        if f.edit_old is None or f.edit_new is None:
            continue
        if f.edit_old == "__APPEND__":
            append.append(f.edit_new)
            continue
        if f.edit_new == "":
            out = "\n".join(ln for ln in out.splitlines() if ln.strip() != f.edit_old.strip())
            continue
        if f.edit_old in out:
            out = out.replace(f.edit_old, f.edit_new, 1)
        else:
            out = re.sub(re.escape(f.edit_old), f.edit_new, out, count=1, flags=re.IGNORECASE)
    if append:
        out = out.rstrip() + "\n\n" + "\n".join(append) + "\n"
    return out


def audit_record(rec: jpc.JobPostingRecord, raw_text: str = "",
                 benchmarks: Optional[Dict[str, float]] = None, project: bool = True,
                 from_board_api: bool = False) -> RequisitionAudit:
    benchmarks = {**DEFAULT_LEVEL_BENCHMARKS, **(benchmarks or {})}
    lines = [ln.strip() for ln in (raw_text or rec.full_description_text).splitlines() if ln.strip()]
    req_lines = list(rec.required_qualifications)
    level = detect_level(rec.job_title)

    findings: List[Finding] = []
    findings += _rules_gates(rec, lines, req_lines, level, benchmarks)
    findings += _rules_load(rec, lines, req_lines)
    findings += _rules_clarity(rec, lines, req_lines)
    findings += _rules_title(rec, lines, level)
    findings += _rules_language(rec, lines, rec.job_title, from_board_api)
    order = {k: i for i, (k, _, _) in enumerate(COMPONENTS)}
    findings.sort(key=lambda f: (order[f.component], -f.points, f.rule_id))

    score, comps = _score(findings)
    yrs, _ = required_years(req_lines or lines)
    stats = {
        "level_detected": LEVEL_LABELS.get(level, level),
        "level_benchmark_years": benchmarks.get(level),
        "required_items": len(req_lines),
        "preferred_items": len(rec.preferred_qualifications),
        "responsibilities": len(rec.responsibilities),
        "required_years": yrs,
        "pay_range_present": pay_range_present(rec),
        "word_count": len(rec.full_description_text.split()),
        "hidden_worker_risk_flags": sum(1 for f in findings if f.hidden_worker_risk),
        "degree_gate_without_equivalency": any(f.rule_id == "G1" for f in findings),
        "competing_products_required": any(f.rule_id == "L3" for f in findings),
        "employment_gap_screen": any(f.rule_id == "G6" for f in findings),
    }
    source_text = raw_text or rec.full_description_text
    result = RequisitionAudit(
        tool="ops-requisition-auditor", module="requisition-quality", engine_version=ENGINE_VERSION,
        rubric_version=RUBRIC_VERSION, input_sha256=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        title=rec.job_title, company=rec.company_name, location=rec.location, source_url=rec.source_url,
        source_platform=rec.source_platform, score=score, band=band_for(score), components=comps,
        findings=findings, stats=stats,
    )
    if project:
        improved = apply_edits(source_text, findings)
        if improved != source_text:
            proj = audit_text(improved, url=rec.source_url, platform_hint=rec.source_platform,
                              benchmarks=benchmarks, project=False, from_board_api=from_board_api)
            result.projected_score = proj.score
            result.projected_band = proj.band
            result.improved_text = improved
    return result


def audit_text(text: str, url: str = "", platform_hint: str = "", benchmarks: Optional[Dict[str, float]] = None,
               project: bool = True, from_board_api: bool = False) -> RequisitionAudit:
    raw = text
    if "<html" in text.lower() or "<body" in text.lower() or "application/ld+json" in text.lower():
        jsonld = jpc.extract_jsonld_job_posting(text)
        raw = jpc.html_fragment_to_text(text)
        rec = jpc.parse_job_posting_text(raw, url=url, jsonld=jsonld, platform_hint=platform_hint)
    else:
        rec = jpc.parse_job_posting_text(text, url=url, platform_hint=platform_hint)
    return audit_record(rec, raw_text=raw, benchmarks=benchmarks, project=project, from_board_api=from_board_api)


def audit_normalized_job(njob: jpc.NormalizedJob, benchmarks: Optional[Dict[str, float]] = None) -> RequisitionAudit:
    rec = jpc.normalized_job_to_record(njob)
    return audit_record(rec, raw_text=rec.full_description_text, benchmarks=benchmarks, from_board_api=True)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

DISCLAIMER = ("*A transparent heuristic for finding fixable requisition issues. It is not legal advice, does not "
              "predict applicant volume, and level benchmarks are defaults to replace with your own leveling guide.*")


def _md_cell(text: str, limit: int = 220) -> str:
    t = (text or "").replace("|", "\\|").replace("\n", " ").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def render_markdown(a: RequisitionAudit, include_improved: bool = True) -> str:
    title = a.title or "Untitled requisition"
    head = f"# Requisition Quality Report: {title}" + (f" — {a.company}" if a.company else "")
    md: List[str] = [head, "", DISCLAIMER, ""]
    md += ["## 1. Summary", ""]
    md.append(f"- **Requisition Quality Score (RQS):** `{a.score:.1f} / 100` — **{a.band}**")
    if a.projected_score is not None:
        md.append(f"- **Projected RQS after the automatic edits below:** `{a.projected_score:.1f} / 100` — {a.projected_band} "
                  "*(placeholder suggestions not included)*")
    s = a.stats
    md.append(f"- **Hidden-worker risk flags:** {s['hidden_worker_risk_flags']}")
    md.append(f"- **Level detected:** {s['level_detected']} (default benchmark: {s['level_benchmark_years']:g} years)"
              if s.get("level_benchmark_years") is not None else f"- **Level detected:** {s['level_detected']}")
    yrs = f"{s['required_years']:g}+" if s.get("required_years") is not None else "not stated"
    md.append(f"- **Requirement load:** {s['required_items']} required, {s['preferred_items']} preferred, "
              f"{s['responsibilities']} responsibilities; years required: {yrs}")
    md.append(f"- **Pay range present:** {'yes' if s['pay_range_present'] else 'no'}")
    if a.location or a.source_url:
        md.append(f"- **Location / source:** {a.location or 'n/a'}" + (f" — {a.source_url}" if a.source_url else ""))
    md += ["", "## 2. Component Breakdown", "",
           "| Component | Max | Deducted | Earned | Findings |", "| :--- | :-: | :-: | :-: | :-: |"]
    for c in a.components:
        md.append(f"| **{c.label}** | {c.max_points:g} | {c.deducted:.1f} | **{c.earned:.1f}** | {c.findings} |")
    md.append(f"| **TOTAL** | **100** | | **`{a.score:.1f}`** | |")

    md += ["", "## 3. Findings Ledger", ""]
    scored = [f for f in a.findings]
    if not scored:
        md.append("No issues found by the current rubric.")
    else:
        md += ["| # | Component | Severity | Points | Finding | Requisition quote | Suggested change |",
               "| :-: | :--- | :-: | :-: | :--- | :--- | :--- |"]
        for i, f in enumerate(scored, 1):
            flag = " 🔸" if f.hidden_worker_risk else ""
            md.append(f"| {i} | {COMPONENT_LABEL[f.component]} | {f.severity} | -{f.points:.1f} | **{_md_cell(f.title)}**{flag}"
                      f"<br>{_md_cell(f.explanation, 260)} | \"{_md_cell(f.quote)}\" | {_md_cell(f.suggestion, 260)} |")
        md += ["", "🔸 = hidden-worker risk (likely to screen out qualified candidates)."]

    section = 4
    edits = [f for f in a.findings if f.edit_old is not None and f.edit_new is not None]
    if edits:
        md += ["", f"## {section}. Automatic Edits (review before posting)", ""]
        section += 1
        for f in edits:
            if f.edit_old == "__APPEND__":
                md += [f"- **Add:** {f.edit_new}"]
            elif f.edit_new == "":
                md += [f"- **Remove:** \"{f.edit_old}\""]
            else:
                md += [f"- **Change:** \"{f.edit_old}\"", f"  **To:** \"{f.edit_new}\""]
    manual = [f for f in a.findings if f.points > 0 and (f.edit_old is None or f.edit_new is None)]
    if manual:
        md += ["", f"## {section}. Needs a Human Decision", ""]
        section += 1
        for f in manual:
            md.append(f"- **{f.title}:** {f.suggestion}")
    if include_improved and a.improved_text:
        md += ["", f"## {section}. Draft With Automatic Edits Applied", "", "```text", a.improved_text.strip(), "```"]
    md += ["", f"<sub>ops-requisition-auditor {a.engine_version} · rubric {a.rubric_version} · input sha256 {a.input_sha256[:12]}</sub>"]
    return "\n".join(md)


def render_portfolio_markdown(audits: List[RequisitionAudit], source_label: str, details: bool = False) -> str:
    md: List[str] = [f"# Requisition Portfolio Audit: {source_label}", "", DISCLAIMER, ""]
    n = len(audits)
    if n == 0:
        return "\n".join(md + ["No requisitions matched."])

    def pct(pred) -> str:
        return f"{100.0 * sum(1 for a in audits if pred(a)) / n:.0f}%"

    scores = sorted(a.score for a in audits)
    median = statistics.median(scores)
    median_req = statistics.median(a.stats["required_items"] for a in audits)
    md += ["## Portfolio Summary", "",
           f"- **Requisitions audited:** {n}",
           f"- **Median RQS:** `{median:.1f} / 100` (lowest {scores[0]:.1f}, highest {scores[-1]:.1f})",
           f"- **Degree required with no equivalency path:** {pct(lambda a: a.stats['degree_gate_without_equivalency'])}",
           f"- **No pay range:** {pct(lambda a: not a.stats['pay_range_present'])}",
           f"- **Competing products all required:** {pct(lambda a: a.stats['competing_products_required'])}",
           f"- **Employment-gap screen:** {pct(lambda a: a.stats['employment_gap_screen'])}",
           f"- **Median required items:** {median_req:g}",
           f"- **In the 'High hidden-worker risk' band:** {pct(lambda a: a.band == BANDS[-1][1])}",
           "", "## Requisitions (lowest score first)", "",
           "| # | Title | Location | RQS | Band | Top issue |", "| :-: | :--- | :--- | :-: | :--- | :--- |"]
    ranked = sorted(audits, key=lambda a: (a.score, a.title))
    for i, a in enumerate(ranked, 1):
        top = next((f.title for f in a.findings if f.points > 0), "—")
        md.append(f"| {i} | {_md_cell(a.title, 80)} | {_md_cell(a.location, 40)} | {a.score:.1f} | {a.band} | {_md_cell(top, 90)} |")
    if details:
        for a in ranked:
            md += ["", "---", "", render_markdown(a, include_improved=False).replace("# Requisition Quality Report", "## Requisition Quality Report", 1)]
    return "\n".join(md)


def audit_to_dict(a: RequisitionAudit) -> Dict[str, Any]:
    return dataclasses.asdict(a)


# ---------------------------------------------------------------------------
# Demo fixture (fictional employer)
# ---------------------------------------------------------------------------

DEMO_REQUISITION = """Job Title: Workforce Management Analyst II - WFM Ops - L4
Company: Northstar Contact Solutions
Location: St. Louis, MO
Job ID: REQ-20431

About the Role
We're looking for a WFM rockstar who can hit the ground running in a fast-paced contact center. You will wear many hats supporting our 24/7 operation.

Responsibilities
- Build weekly and intraday forecasts for inbound voice and chat queues.
- Create agent schedules and monitor RTA across all sites.
- Report AHT, ASA, shrinkage, and occupancy trends to operations leaders.

Requirements
- Bachelor's degree in Mathematics, Statistics, or Business required.
- 7+ years of workforce management experience in a contact center.
- Expert in NICE IEX, Verint, and Genesys Cloud.
- Advanced Power BI and Tableau dashboard development.
- SQL, Python, and R for forecasting.
- Six Sigma Black Belt certification required.
- Erlang C and capacity planning expertise.
- Experience with Salesforce and Microsoft Dynamics.
- Must have no gaps in employment history.
- Strong communication and stakeholder management skills.
- Digital native who is comfortable with new tools.
- Self-starter able to work independently with minimal supervision across multiple sites and time zones while managing competing priorities and stakeholder expectations in a rapidly changing environment.
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def render_rqs_csv(audits: Sequence[RequisitionAudit]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([
        "input_sha256", "title", "company", "location", "inferred_level",
        "score", "projected_score", "band", "gates_score", "load_score",
        "clarity_score", "title_score", "language_score", "findings_count", "top_issue",
    ])
    for a in sorted(audits, key=lambda x: (x.score, x.title or "")):
        c_map = {c.key: c.earned for c in a.components}
        top_issue = a.findings[0].title if a.findings else ""
        writer.writerow([
            a.input_sha256[:16] if a.input_sha256 else "",
            a.title or "",
            a.company or "",
            a.location or "",
            str(a.stats.get("inferred_level") or ""),
            f"{a.score:.1f}",
            f"{a.projected_score:.1f}" if a.projected_score is not None else f"{a.score:.1f}",
            a.band,
            f"{c_map.get('gates', 0.0):.1f}",
            f"{c_map.get('load', 0.0):.1f}",
            f"{c_map.get('clarity', 0.0):.1f}",
            f"{c_map.get('title', 0.0):.1f}",
            f"{c_map.get('language', 0.0):.1f}",
            len(a.findings),
            top_issue,
        ])
    return buf.getvalue()


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        prog="ops-req-jd-audit",
        description="ops-requisition-auditor: Requisition Quality Audit. Scores job requisitions (not candidates) "
                    "with a deterministic 100-point Requisition Quality Score and suggests edits.",
    )
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--jd-file", "--input-file", dest="jd_file", help="Requisition text or saved HTML file")
    src.add_argument("--stdin", action="store_true", help="Read requisition text from standard input")
    src.add_argument("--url", help="Employer-published posting URL (Greenhouse, Lever, Ashby, or career page)")
    src.add_argument("--greenhouse", metavar="BOARD_TOKEN", help="Audit every open role on a public Greenhouse board")
    src.add_argument("--lever", metavar="SITE_SLUG", help="Audit every open role on a public Lever site")
    src.add_argument("--ashby", metavar="BOARD_NAME", help="Audit every open role on a public Ashby board")
    src.add_argument("--demo", action="store_true", help="Audit the built-in fictional requisition")
    ap.add_argument("--title-filter", help="Regex to keep only matching titles (board audits)")
    ap.add_argument("--limit", type=int, default=0, help="Audit at most N roles (board audits)")
    ap.add_argument("--details", action="store_true", help="Include a full report per role in board audits")
    ap.add_argument("--level-benchmarks", help="JSON file overriding default years per level, e.g. {\"level_2\": 3}")
    ap.add_argument("--output", help="Write the Markdown report to this path")
    ap.add_argument("--json-out", help="Write the full JSON result to this path")
    ap.add_argument("--csv-out", help="Write a UTF-8 CSV summary table to this path")
    ap.add_argument("--improved-out", help="Write the draft requisition with automatic edits applied (single audits)")
    args = ap.parse_args(argv)

    benchmarks: Dict[str, float] = {}
    if args.level_benchmarks:
        with open(args.level_benchmarks, "r", encoding="utf-8") as fh:
            benchmarks = {str(k): float(v) for k, v in json.load(fh).items()}

    board_jobs: Optional[List[jpc.NormalizedJob]] = None
    source_label = ""
    try:
        if args.greenhouse:
            board_jobs, source_label = jpc.fetch_greenhouse(args.greenhouse), f"Greenhouse board '{args.greenhouse}'"
        elif args.lever:
            board_jobs, source_label = jpc.fetch_lever(args.lever), f"Lever site '{args.lever}'"
        elif args.ashby:
            board_jobs, source_label = jpc.fetch_ashby(args.ashby), f"Ashby board '{args.ashby}'"
    except jpc.ComplianceBlockError as exc:
        sys.stderr.write(f"[COMPLIANCE GATE] BLOCKED: {exc}\n")
        return 2
    except Exception as exc:  # network or API errors
        sys.stderr.write(f"[ERROR] Could not fetch the public board: {exc}\n")
        return 1

    if board_jobs is not None:
        if args.title_filter:
            rx = re.compile(args.title_filter, re.IGNORECASE)
            board_jobs = [j for j in board_jobs if rx.search(j.title or "")]
        if args.limit and args.limit > 0:
            board_jobs = board_jobs[: args.limit]
        audits = [audit_normalized_job(j, benchmarks) for j in board_jobs]
        report = render_portfolio_markdown(audits, source_label, details=args.details)
        if args.output:
            _write(args.output, report + "\n")
        if args.json_out:
            _write(args.json_out, json.dumps({"source": source_label, "audits": [audit_to_dict(a) for a in audits]},
                                             indent=2, default=str))
        if args.csv_out:
            _write(args.csv_out, render_rqs_csv(audits))
        print(report)
        return 0

    if args.url:
        res = jpc.fetch_job_source(args.url)
        if res.get("paste_required") or (res.get("error") and not res.get("ok")):
            msg = res.get("error") or f"{res.get('platform')} restricts automated access."
            sys.stderr.write(f"[COMPLIANCE GATE] {msg}\nPaste the requisition into a text file and re-run with --jd-file <path>.\n")
            return 2
        rec = jpc.parse_from_fetch_result(res)
        audit = audit_record(rec, raw_text=rec.full_description_text, benchmarks=benchmarks)
    elif args.stdin:
        audit = audit_text(sys.stdin.read(), benchmarks=benchmarks)
    elif args.jd_file:
        with open(args.jd_file, "r", encoding="utf-8") as fh:
            audit = audit_text(fh.read(), benchmarks=benchmarks)
    else:
        audit = audit_text(DEMO_REQUISITION, benchmarks=benchmarks)

    report = render_markdown(audit)
    if args.output:
        _write(args.output, report + "\n")
    if args.json_out:
        _write(args.json_out, json.dumps(audit_to_dict(audit), indent=2, default=str))
    if args.csv_out:
        _write(args.csv_out, render_rqs_csv([audit]))
    if args.improved_out and audit.improved_text:
        _write(args.improved_out, audit.improved_text)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
