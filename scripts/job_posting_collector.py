#!/usr/bin/env python3
"""
Universal Job Posting Collector & Structured JD Parser
======================================================
Parses job postings from pasted text or saved pages (any site: LinkedIn, Indeed, ZipRecruiter,
Glassdoor, employer career pages, ATS boards) and can fetch employer-published postings
directly: Greenhouse, Lever (US + EU), Ashby, and USAJobs via their public posting APIs,
and other employer career pages via embedded Schema.org JobPosting metadata.

LinkedIn, Indeed, SimplyHired, ZipRecruiter, Glassdoor, Monster, CareerBuilder, Dice,
BuiltIn, and Wellfound/AngelList links are never fetched, because those sites' terms
restrict automated access. Paste the description (or save the page) and use --input-file.

Extracts a complete, structured JobPostingRecord containing:
  - Job Title, Company Name, Source Site / ATS Platform, URL, Job / Requisition ID
  - Location, Workplace Model (Remote / Hybrid / On-Site), Employment Type, Seniority Level
  - Department / Function, Industry, Date Posted, Application Deadline, Apply URL
  - Salary / Compensation (raw string, min, max, currency, pay period: yearly/hourly)
  - Benefits & Perks
  - Role Summary & Core Responsibilities
  - Required Qualifications / Must-Have Requirements
  - Preferred Qualifications / Nice-to-Have Skills
  - Education, Certifications / Licenses, Minimum Years of Experience
  - Security Clearance, Work Authorization / Sponsorship, Travel Requirements
  - Technical & Hard Skills, Soft Skills / Competencies
  - Full Normalized Job Posting Text + Direct `ats_config` for `offline_ats_auditor.py`
"""

from __future__ import annotations

import argparse
import dataclasses
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import html as html_lib
from html.parser import HTMLParser
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Supported Job Sites & Enterprise ATS Platforms
# ---------------------------------------------------------------------------

SITE_PATTERNS: List[Tuple[str, str]] = [
    (r"linkedin\.com", "LinkedIn"),
    (r"indeed\.com", "Indeed"),
    (r"ziprecruiter\.com", "ZipRecruiter"),
    (r"glassdoor\.com", "Glassdoor"),
    (r"monster\.com", "Monster"),
    (r"dice\.com", "Dice"),
    (r"builtin\.com", "BuiltIn"),
    (r"wellfound\.com|angel\.co", "Wellfound"),
    (r"simplyhired\.com", "SimplyHired"),
    (r"careerbuilder\.com", "CareerBuilder"),
    (r"usajobs\.gov", "USAJobs"),
    (r"greenhouse\.io", "Greenhouse ATS"),
    (r"lever\.co", "Lever ATS"),
    (r"myworkdayjobs\.com|workday\.com", "Workday ATS"),
    (r"ashbyhq\.com", "Ashby ATS"),
    (r"smartrecruiters\.com", "SmartRecruiters ATS"),
    (r"icims\.com", "iCIMS ATS"),
    (r"bamboohr\.com", "BambooHR ATS"),
    (r"workable\.com", "Workable ATS"),
    (r"rippling\.com|ats\.rippling\.com", "Rippling ATS"),
    (r"taleo\.net", "Oracle Taleo ATS"),
    (r"jobvite\.com", "Jobvite ATS"),
    (r"breezy\.hr", "Breezy HR"),
    (r"jazzhr\.com|applytojob\.com", "JazzHR"),
]


@dataclass
class SalaryInfo:
    raw_text: str = ""
    currency: str = "USD"
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    period: str = ""  # "year", "hour", "month", "week"
    additional_comp: List[str] = field(default_factory=list)  # bonus, equity, commission


@dataclass
class JobPostingRecord:
    source_url: str = ""
    source_platform: str = "Direct / Unknown"
    job_id: str = ""
    job_title: str = ""
    company_name: str = ""
    company_overview: str = ""
    location: str = ""
    workplace_type: str = ""        # Remote, Hybrid, On-site
    employment_type: str = ""       # Full-time, Part-time, Contract, Internship
    seniority_level: str = ""       # Entry, Mid, Senior, Staff/Principal, Manager, Director, VP/Exec
    department: str = ""
    industry: str = ""
    date_posted: str = ""
    valid_through: str = ""
    apply_url: str = ""
    salary: SalaryInfo = field(default_factory=SalaryInfo)
    benefits: List[str] = field(default_factory=list)
    role_summary: str = ""
    responsibilities: List[str] = field(default_factory=list)
    required_qualifications: List[str] = field(default_factory=list)
    preferred_qualifications: List[str] = field(default_factory=list)
    education_requirements: List[str] = field(default_factory=list)
    certifications_licenses: List[str] = field(default_factory=list)
    min_years_experience: Optional[float] = None
    max_years_experience: Optional[float] = None
    hard_skills: List[str] = field(default_factory=list)
    soft_skills: List[str] = field(default_factory=list)
    security_clearance: str = ""
    work_authorization: str = ""
    travel_requirement: str = ""
    full_description_text: str = ""
    ats_config: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# HTML / JSON-LD / DOM Helpers
# ---------------------------------------------------------------------------

class _HTMLToTextParser(HTMLParser):
    """Convert HTML into clean, line-structured plain text with bullets preserved."""

    BLOCK_TAGS = {
        "p", "div", "section", "article", "header", "footer", "aside",
        "h1", "h2", "h3", "h4", "h5", "h6", "tr", "br", "hr", "blockquote",
    }
    SKIP_TAGS = {"script", "style", "noscript", "svg", "path", "head", "nav", "footer"}

    def __init__(self) -> None:
        super().__init__()
        self.chunks: List[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth > 0:
            return
        if t == "li":
            self.chunks.append("\n- ")
        elif t in self.BLOCK_TAGS:
            self.chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self.SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth > 0:
            return
        if t in self.BLOCK_TAGS or t == "li":
            self.chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data:
            self.chunks.append(data)

    def get_text(self) -> str:
        raw = html_lib.unescape("".join(self.chunks))
        lines: List[str] = []
        for ln in raw.splitlines():
            cleaned = re.sub(r"[ \t]+", " ", ln).strip()
            if cleaned in ("-", "*", "•"):
                continue
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines)


def html_fragment_to_text(fragment: str) -> str:
    if not fragment:
        return ""
    parser = _HTMLToTextParser()
    parser.feed(html_lib.unescape(fragment))
    return parser.get_text()


def detect_platform(url: str, raw_text: str = "") -> str:
    target = f"{url} {raw_text[:1000]}".lower()
    for pattern, label in SITE_PATTERNS:
        if re.search(pattern, target, re.IGNORECASE):
            return label
    return "Direct / Career Site"


def extract_jsonld_job_posting(raw_html: str) -> Optional[Dict[str, Any]]:
    """Extract Schema.org JobPosting JSON-LD block if embedded in the HTML page."""
    script_blocks = re.findall(
        r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        raw_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in script_blocks:
        cleaned = block.strip()
        if not cleaned:
            continue
        try:
            data = json.loads(cleaned)
        except Exception:
            # Attempt minor cleanup for trailing commas or control chars
            try:
                data = json.loads(re.sub(r"[\x00-\x1f]", " ", cleaned))
            except Exception:
                continue

        candidates: List[Dict[str, Any]] = []
        if isinstance(data, dict):
            candidates.append(data)
            if isinstance(data.get("@graph"), list):
                candidates.extend(x for x in data["@graph"] if isinstance(x, dict))
        elif isinstance(data, list):
            candidates.extend(x for x in data if isinstance(x, dict))

        for item in candidates:
            t = item.get("@type", "")
            types = [t] if isinstance(t, str) else (t if isinstance(t, list) else [])
            if any("jobposting" in str(x).lower() for x in types):
                return item
    return None


# ---------------------------------------------------------------------------
# Employer-Published Posting Helpers (Greenhouse API, Lever API, direct career pages)
# ---------------------------------------------------------------------------

USER_AGENT = "ats-auditor-recruiter/1.4 (personal, low-volume job-posting reader; no login or CAPTCHA handling)"

PASTE_ONLY_HOSTS = re.compile(
    r"(?:^|\.)(linkedin|indeed|simplyhired|ziprecruiter|glassdoor|monster|careerbuilder|dice|builtin|wellfound|angel)\.[a-z.]+$",
    re.IGNORECASE,
)
PASTE_ONLY_LABELS = {
    "linkedin": "LinkedIn",
    "indeed": "Indeed",
    "simplyhired": "SimplyHired",
    "ziprecruiter": "ZipRecruiter",
    "glassdoor": "Glassdoor",
    "monster": "Monster",
    "careerbuilder": "CareerBuilder",
    "dice": "Dice",
    "builtin": "BuiltIn",
    "wellfound": "Wellfound",
    "angel": "Wellfound",
}


def paste_only_platform(url: str) -> Optional[str]:
    """Return the job board's name if `url` is on a paste-only host, else None (host-based, not substring-based)."""
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    m = PASTE_ONLY_HOSTS.search(host)
    return PASTE_ONLY_LABELS[m.group(1).lower()] if m else None


def _http_get(url: str, timeout: int = 20) -> Tuple[int, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read().decode(charset, errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


def normalized_job_to_record(njob: NormalizedJob) -> JobPostingRecord:
    header_lines = [
        f"Job Title: {njob.title}",
        f"Company: {njob.company}",
        f"Location: {njob.location}",
        f"Workplace Type: {njob.workplace_type}",
        f"Job ID: {njob.job_id}",
    ]
    if njob.compensation:
        header_lines.append(f"Salary: {njob.compensation}")
    synthesized = chr(10).join(header_lines) + chr(10) + chr(10) + (njob.description_text or "")
    rec = parse_job_posting_text(synthesized, url=njob.url, platform_hint=njob.source)
    rec.job_id = njob.job_id or rec.job_id
    rec.company_name = njob.company or rec.company_name
    rec.job_title = njob.title or rec.job_title
    rec.location = njob.location or rec.location
    rec.workplace_type = njob.workplace_type or rec.workplace_type
    rec.date_posted = njob.published_at or rec.date_posted
    return rec


def fetch_job_source(url: str) -> Dict[str, Any]:
    """
    Fetch a job posting URL using the best available compliant endpoint:
      1. Greenhouse Board API (`boards-api.greenhouse.io/v1/boards/{b}/jobs/{id}?questions=true`)
      2. Lever Postings API (`api.lever.co` or `api.eu.lever.co/v0/postings/{c}/{id}?mode=json`)
      3. Ashby Public Job Board API (`api.ashbyhq.com/posting-api/job-board/{b}?includeCompensation=true`)
      4. Direct HTML fetch (extracting Schema.org/JobPosting JSON-LD + body text)
    """
    try:
        enforce_domain_compliance(url)
    except ComplianceBlockError as exc:
        return {
            "ok": False,
            "error": str(exc),
            "status": 0,
            "platform": paste_only_platform(url) or detect_platform(url),
            "mode": "paste_required",
            "html": "",
            "url": url,
            "blocked": False,
            "paste_required": True,
        }
    platform = detect_platform(url)

    # 1. Greenhouse direct API
    gh_match = re.search(r"boards\.greenhouse\.io/([^/?#]+)/jobs/(\d+)", url, re.IGNORECASE)
    if not gh_match:
        gh_match = re.search(r"job-boards\.greenhouse\.io/([^/?#]+)/jobs/(\d+)", url, re.IGNORECASE)
    if gh_match:
        board, jid = gh_match.group(1), gh_match.group(2)
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}?questions=true"
        status, body = _http_get(api_url)
        if status == 200:
            try:
                data = json.loads(body)
                return {"ok": True, "platform": "Greenhouse ATS", "mode": "greenhouse_api", "data": data, "url": url}
            except Exception:
                pass

    # 2. Lever direct API (US + EU)
    lever_match = re.search(r"jobs\.(?:(eu)\.)?lever\.co/([^/?#]+)/([0-9a-fA-F-]{20,})", url, re.IGNORECASE)
    if lever_match:
        is_eu, company, jid = bool(lever_match.group(1)), lever_match.group(2), lever_match.group(3)
        api_host = "api.eu.lever.co" if is_eu else "api.lever.co"
        api_url = f"https://{api_host}/v0/postings/{company}/{jid}?mode=json"
        status, body = _http_get(api_url)
        if status == 200:
            try:
                data = json.loads(body)
                return {"ok": True, "platform": "Lever ATS", "mode": "lever_api", "data": data, "url": url}
            except Exception:
                pass

    # 3. General direct HTML fetch (honest User-Agent; employer career pages)
    status, body = _http_get(url)
    is_blocked = (
        status in (401, 403, 429, 503)
        or len(body) < 500
        or bool(re.search(r"<title>\s*(?:Just a moment|Attention Required|Access Denied|Security Measure|Robot or human)", body, re.IGNORECASE))
    )
    return {
        "ok": not is_blocked and status == 200,
        "status": status,
        "platform": platform,
        "mode": "html",
        "html": body,
        "url": url,
        "blocked": is_blocked,
    }


# ---------------------------------------------------------------------------
# Taxonomy & Heuristic Field Extractors
# ---------------------------------------------------------------------------

KNOWN_HARD_SKILLS: List[str] = [
    "Python", "SQL", "R", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "Golang", "Rust",
    "Scala", "Ruby", "PHP", "Swift", "Kotlin", "Bash", "PowerShell", "HTML", "CSS", "SAS", "MATLAB",
    "React", "Next.js", "Angular", "Vue.js", "Node.js", "Django", "Flask", "FastAPI", "Spring Boot",
    ".NET", "GraphQL", "REST API", "gRPC", "Microservices",
    "AWS", "Azure", "GCP", "Google Cloud", "Docker", "Kubernetes", "Terraform", "Ansible", "Jenkins",
    "GitHub Actions", "GitLab CI", "CI/CD", "Linux", "Datadog", "Prometheus", "Grafana", "Splunk",
    "Snowflake", "BigQuery", "Redshift", "Databricks", "Apache Spark", "PySpark", "Kafka", "Airflow",
    "dbt", "Fivetran", "ETL", "ELT", "Data Warehousing", "Data Modeling", "PostgreSQL", "MySQL",
    "SQL Server", "T-SQL", "PL/SQL", "Oracle", "MongoDB", "Redis", "DynamoDB", "Elasticsearch",
    "Power BI", "Tableau", "Looker", "Qlik", "Excel", "VBA", "DAX", "SSIS", "SSRS", "SSAS",
    "Alteryx", "DOMO", "QuickSight",
    "SAP", "SAP S/4HANA", "SAP ERP", "Oracle NetSuite", "Oracle Cloud", "Workday", "Salesforce",
    "ServiceNow", "Jira", "Confluence", "Smartsheet", "Asana", "HubSpot", "Marketo", "Zendesk",
    "Machine Learning", "Deep Learning", "NLP", "LLM", "GenAI", "PyTorch", "TensorFlow", "Scikit-learn",
    "Pandas", "NumPy", "A/B Testing", "Statistical Modeling", "Forecasting", "Demand Forecasting",
    "Capacity Planning", "Workforce Capacity Modeling", "Inventory Optimization", "Supply Chain Management",
    "Workforce Management", "NICE IEX", "Genesys Cloud", "Amazon Connect", "Verint", "Calabrio", "Aspect",
    "Five9", "Talkdesk", "Avaya", "UKG", "Kronos", "Intraday Management", "Real-Time Adherence",
    "Schedule Adherence", "Shrinkage", "Occupancy", "Queue Optimization", "Staffing Models", "Scheduling",
    "Erlang", "Erlang C", "SLA/ASA", "Service Level Agreement", "Average Speed of Answer", "AHT",
    "Call Volume Forecasting", "Contact Center Operations", "Epic", "MicroStrategy", "Power Query",
    "PivotTables", "Salesforce CRM", "Accounts Receivable", "Grant Management", "Billing Reconciliation",
    "P&L Ownership", "Financial Modeling", "Budgeting", "Variance Analysis", "GAAP", "SOX", "IFRS",
    "HIPAA", "GDPR", "SOC 2", "ISO 27001", "NIST", "PCI-DSS", "OSHA", "FDA", "cGMP",
    "Agile", "Scrum", "Kanban", "SDLC", "ITIL", "Lean Six Sigma", "Six Sigma",
]

KNOWN_SOFT_SKILLS: List[str] = [
    "Cross-Functional Leadership", "Stakeholder Management", "Executive Communication",
    "Change Management", "Team Leadership", "Mentorship", "Strategic Planning",
    "Problem Solving", "Critical Thinking", "Conflict Resolution", "Negotiation",
    "Time Management", "Adaptability", "Collaboration", "Customer Success",
    "Written and Verbal Communication", "Presentation Skills", "Vendor Management",
]

KNOWN_CERTIFICATIONS: List[str] = [
    "PMP", "Certified ScrumMaster", "CSM", "CSPO", "Six Sigma Black Belt", "Six Sigma Green Belt",
    "Lean Six Sigma", "CPA", "CFA", "CMA", "SHRM-CP", "SHRM-SCP", "PHR", "SPHR",
    "CISSP", "CISA", "CISM", "Security+", "CEH", "CCNA", "CCNP", "AWS Certified",
    "Azure Administrator", "Google Cloud Certified", "CKA", "ITIL", "PE", "RN", "APICS", "CSCP", "CPIM",
]

KNOWN_BENEFITS: List[Tuple[str, str]] = [
    (r"\b401\s*\(\s*k\s*\)|\b401k\b|retirement\s+plan|company\s+match", "401(k) / Retirement Plan"),
    (r"\b(?:medical|health|dental|vision)\s+(?:insurance|coverage|benefits|plan)", "Medical / Dental / Vision Insurance"),
    (r"\b(?:paid\s+time\s+off|pto|unlimited\s+pto|paid\s+vacation|paid\s+holidays)", "Paid Time Off (PTO) / Vacation"),
    (r"\b(?:parental\s+leave|maternity|paternity\s+leave)", "Parental Leave"),
    (r"\b(?:tuition\s+reimbursement|education\s+stipend|learning\s+budget)", "Tuition / Continuing Education Assistance"),
    (r"\b(?:equity|RSUs?|stock\s+options|ESPP)", "Equity / Stock Options / RSU"),
    (r"\b(?:annual\s+bonus|performance\s+bonus|incentive\s+bonus|commission)", "Performance / Annual Bonus"),
    (r"\b(?:HSA|FSA|health\s+savings\s+account|flexible\s+spending)", "HSA / FSA"),
    (r"\b(?:life\s+insurance|disability\s+insurance|AD&D)", "Life & Disability Insurance"),
    (r"\b(?:home\s+office\s+stipend|remote\s+stipend|wellness\s+stipend)", "Wellness / Remote Office Stipend"),
    (r"\b(?:relocation\s+assistance|relocation\s+package)", "Relocation Assistance"),
]


# ---------------------------------------------------------------------------
# Salary, Section & Metadata Parsers
# ---------------------------------------------------------------------------

_SALARY_RANGE_RE = re.compile(
    r"(?P<curr>\$|USD\s*|£|€|CAD\s*\$?)"
    r"(?P<v1>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{2,3}(?:\.\d+)?\s*[kK]|\d{2,3}(?:\.\d{1,2})?)"
    r"(?:\s*(?:[-–—]|to)\s*"
    r"(?P<curr2>\$|USD\s*|£|€|CAD\s*\$?)?"
    r"(?P<v2>\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d{2,3}(?:\.\d+)?\s*[kK]|\d{2,3}(?:\.\d{1,2})?))?"
    r"(?:\s*(?:/|per\s+|an?\s+)?(?P<unit>year|yr|annum|annual|annually|hour|hr|hourly|month|mo|monthly|week|wk|weekly))?",
    re.IGNORECASE,
)


def _parse_dollar_num(raw_num: str) -> Optional[float]:
    if not raw_num:
        return None
    s = raw_num.strip().replace(",", "")
    mult = 1.0
    if s.lower().endswith("k"):
        mult = 1000.0
        s = s[:-1].strip()
    try:
        return float(s) * mult
    except ValueError:
        return None


def extract_salary_from_text(text: str) -> SalaryInfo:
    info = SalaryInfo()
    best_match_str = ""
    best_min: Optional[float] = None
    best_max: Optional[float] = None
    best_unit = ""

    for line in text.splitlines():
        # Skip lines that clearly describe revenue/budget scale rather than compensation
        if re.search(r"\b(?:revenue|budget|P&L|sales\s+volume|ARR|EBITDA|funding|valuation|savings)\b", line, re.IGNORECASE) and not re.search(
            r"\b(?:salary|pay|compensation|wage|base|hourly|OTE|range)\b", line, re.IGNORECASE
        ):
            continue

        for m in _SALARY_RANGE_RE.finditer(line):
            v1 = _parse_dollar_num(m.group("v1"))
            v2 = _parse_dollar_num(m.group("v2")) if m.group("v2") else None
            unit_raw = (m.group("unit") or "").lower()
            has_comp_context = bool(
                re.search(r"\b(?:salary|compensation|pay|base|wage|range|per\s+hour|per\s+year|annually|hourly|OTE|/hr|/yr)\b", line, re.IGNORECASE)
            )

            if v1 is None:
                continue

            # If v1 is a small number like $25 without 'k' and v2 is $35/hr
            if v2 is not None and v1 < 1000 and v2 >= 20000 and not m.group("v1").lower().endswith("k"):
                v1 = v1 * 1000.0

            # Plausibility checks for annual vs hourly wages
            unit = ""
            if unit_raw.startswith(("hour", "hr")):
                unit = "hour"
            elif unit_raw.startswith(("year", "yr", "ann")):
                unit = "year"
            elif unit_raw.startswith(("month", "mo")):
                unit = "month"
            elif unit_raw.startswith(("week", "wk")):
                unit = "week"
            elif 30000 <= v1 <= 950000:
                unit = "year"
            elif 12 <= v1 <= 250 and (v2 is not None or has_comp_context):
                unit = "hour"

            if not unit:
                continue
            if unit == "hour" and not (10 <= v1 <= 350):
                continue
            if unit == "year" and not (20000 <= v1 <= 1500000):
                continue
            if v2 is None and not has_comp_context and unit_raw == "":
                continue

            best_min = v1
            best_max = v2 if (v2 is not None and v2 >= v1) else v1
            best_unit = unit
            best_match_str = m.group(0).strip()
            break
        if best_match_str:
            break

    comp_extras: List[str] = []
    if re.search(r"\b(?:annual\s+bonus|performance\s+bonus|target\s+bonus|bonus\s+eligible)", text, re.IGNORECASE):
        comp_extras.append("Bonus Eligible")
    if re.search(r"\b(?:equity|RSUs?|stock\s+options|ESPP)\b", text, re.IGNORECASE):
        comp_extras.append("Equity / Stock / RSUs")
    if re.search(r"\b(?:commission|OTE|on[-\s]target\s+earnings)\b", text, re.IGNORECASE):
        comp_extras.append("Commission / OTE")
    if re.search(r"\b(?:sign[-\s]on\s+bonus|signing\s+bonus)\b", text, re.IGNORECASE):
        comp_extras.append("Sign-On Bonus")

    info.raw_text = best_match_str
    info.min_amount = best_min
    info.max_amount = best_max
    info.period = best_unit
    info.additional_comp = comp_extras
    return info


_SECTION_HEADER_MAP: List[Tuple[str, str]] = [
    (r"^(?:preferred|desired|nice[-\s]to[-\s]have|bonus|plus|additional|ideal)\s+(?:qualifications?|requirements?|skills?|experience|attributes?)", "preferred"),
    (r"^(?:what\s+makes\s+you\s+stand\s+out|even\s+better\s+if\s+you\s+have|nice\s+to\s+haves?|preferred|bonus\s+points?)", "preferred"),
    (r"^(?:required|minimum|basic|essential|must[-\s]have|key)\s+(?:qualifications?|requirements?|skills?|experience|criteria)", "required"),
    (r"^(?:qualifications?|requirements?|what\s+you(?:\s+will)?\s+bring|what\s+we(?:\s+are|'re)\s+looking\s+for|who\s+you\s+are|skills\s+(?:&|and)\s+experience|candidate\s+profile)", "required"),
    (r"^(?:responsibilities|key\s+responsibilities|essential\s+duties|duties|what\s+you(?:\s+will|'ll)\s+do|the\s+role|day[-\s]to[-\s]day|job\s+duties|role\s+overview|in\s+this\s+role)", "responsibilities"),
    (r"^(?:benefits|perks|compensation\s*(?:&|and)?\s*benefits|what\s+we\s+offer|why\s+work\s+(?:here|with\s+us)|total\s+rewards)", "benefits"),
    (r"^(?:about\s+(?:us|the\s+company)|company\s+overview|who\s+we\s+are)", "company"),
    (r"^(?:about\s+the\s+(?:job|role|position|opportunity)|job\s+summary|position\s+summary|summary|overview)", "summary"),
]


def classify_section_header(line: str) -> Optional[str]:
    cleaned = re.sub(r"^[#*\-•\s]+|[#*:_\s]+$", "", line).strip()
    if not cleaned or len(cleaned) > 75:
        return None
    # Must not look like a full sentence ending with a period
    if cleaned.endswith(".") and len(cleaned.split()) > 7:
        return None
    for pattern, bucket in _SECTION_HEADER_MAP:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return bucket
    return None


def parse_job_posting_text(
    raw_text: str,
    url: str = "",
    jsonld: Optional[Dict[str, Any]] = None,
    platform_hint: str = "",
) -> JobPostingRecord:
    """Parse raw job posting text (and optional JSON-LD metadata) into a complete JobPostingRecord."""
    rec = JobPostingRecord(
        source_url=url,
        source_platform=platform_hint or detect_platform(url, raw_text),
    )

    # 1. Populate structured fields from Schema.org JobPosting JSON-LD if available
    if jsonld:
        rec.job_title = str(jsonld.get("title") or jsonld.get("name") or "").strip()
        rec.date_posted = str(jsonld.get("datePosted") or "").strip()
        rec.valid_through = str(jsonld.get("validThrough") or "").strip()
        ident = jsonld.get("identifier")
        if isinstance(ident, dict):
            rec.job_id = str(ident.get("value") or "")
        elif isinstance(ident, (str, int)):
            rec.job_id = str(ident)
        elif jsonld.get("reqId"):
            rec.job_id = str(jsonld.get("reqId"))

        org = jsonld.get("hiringOrganization")
        if isinstance(org, dict):
            rec.company_name = str(org.get("name") or "").strip()
        elif isinstance(org, str):
            rec.company_name = org.strip()

        emp = jsonld.get("employmentType")
        if isinstance(emp, list):
            rec.employment_type = ", ".join(str(x) for x in emp)
        elif isinstance(emp, str):
            rec.employment_type = emp

        if jsonld.get("industry"):
            rec.industry = str(jsonld.get("industry"))

        loc = jsonld.get("jobLocation")
        loc_obj = loc[0] if isinstance(loc, list) and loc else (loc if isinstance(loc, dict) else None)
        if isinstance(loc_obj, dict):
            addr = loc_obj.get("address")
            if isinstance(addr, dict):
                parts = [
                    str(addr.get("addressLocality") or "").strip(),
                    str(addr.get("addressRegion") or "").strip(),
                    str(addr.get("addressCountry") or "").strip(),
                ]
                rec.location = ", ".join(p for p in parts if p)

        if str(jsonld.get("jobLocationType") or "").upper() == "TELECOMMUTE":
            rec.workplace_type = "Remote"

        base_sal = jsonld.get("baseSalary")
        if isinstance(base_sal, dict):
            curr = str(base_sal.get("currency") or "USD")
            val = base_sal.get("value")
            if isinstance(val, dict):
                min_v = val.get("minValue") or val.get("value")
                max_v = val.get("maxValue") or min_v
                unit = str(val.get("unitText") or "YEAR").lower()
                if min_v is not None:
                    try:
                        rec.salary.min_amount = float(min_v)
                        rec.salary.max_amount = float(max_v) if max_v is not None else float(min_v)
                        rec.salary.currency = curr
                        rec.salary.period = "hour" if "hour" in unit else "year"
                        rec.salary.raw_text = f"${rec.salary.min_amount:,.0f} - ${rec.salary.max_amount:,.0f} per {rec.salary.period}"
                    except Exception:
                        pass

        # If JSON-LD has a fuller description than raw_text, combine or use it
        desc_html = str(jsonld.get("description") or "")
        if desc_html:
            desc_text = html_fragment_to_text(desc_html)
            if len(desc_text) > len(raw_text) * 0.5:
                raw_text = f"{raw_text}\n{desc_text}" if raw_text else desc_text

    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    rec.full_description_text = "\n".join(lines)

    # 2. Extract Metadata Labels (Job Title, Company, Location, Req ID, Date) from text when missing
    for idx, line in enumerate(lines[:30]):
        m_title = re.match(r"^(?:job\s+title|position\s+title|title|role|position)\s*[:–-]\s*(.+)$", line, re.IGNORECASE)
        if m_title and not rec.job_title:
            rec.job_title = m_title.group(1).strip()
            continue

        m_comp = re.match(r"^(?:company|organization|employer|hiring\s+company)\s*[:–-]\s*(.+)$", line, re.IGNORECASE)
        if m_comp and not rec.company_name:
            rec.company_name = m_comp.group(1).strip()
            continue

        m_loc = re.match(r"^(?:location|work\s+location|office\s+location|city)\s*[:–-]\s*(.+)$", line, re.IGNORECASE)
        if m_loc and not rec.location:
            rec.location = m_loc.group(1).strip()
            continue

        m_id = re.match(r"^(?:job\s*(?:id|#|number)|req(?:uisition)?\s*(?:id|#|number)?)\s*[:–-]?\s*([A-Za-z0-9_-]{3,25})$", line, re.IGNORECASE)
        if m_id and not rec.job_id:
            rec.job_id = m_id.group(1).strip()
            continue

        m_dept = re.match(r"^(?:department|team|function|business\s+unit)\s*[:–-]\s*(.+)$", line, re.IGNORECASE)
        if m_dept and not rec.department:
            rec.department = m_dept.group(1).strip()
            continue

    # Fallback heuristics for Job Title, Company, and City/State in top lines
    if not rec.job_title and lines:
        for candidate in lines[:6]:
            clean_c = re.sub(r"^[#*\s]+|[#*\s]+$", "", candidate)
            if 4 <= len(clean_c) <= 85 and not clean_c.endswith(":") and not clean_c.startswith(("-", "*", "•")):
                # Strip trailing '| LinkedIn' or '- Indeed.com'
                clean_c = re.split(r"\s+[|\-–]\s+(?:LinkedIn|Indeed|ZipRecruiter|Glassdoor).*$", clean_c, flags=re.IGNORECASE)[0].strip()
                rec.job_title = clean_c
                break

    if not rec.location:
        loc_m = re.search(
            r"\b([A-Z][a-zA-Z.\s]{2,25},\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)(?:\s*\((?:Remote|Hybrid|On[-\s]?site)\))?|Remote(?:\s*[-–,]\s*(?:US|USA|United States))?)\b",
            "\n".join(lines[:25]),
        )
        if loc_m:
            rec.location = loc_m.group(1).strip()

    if not rec.company_name and len(lines) >= 2:
        for candidate in lines[1:8]:
            if candidate == rec.job_title or candidate == rec.location:
                continue
            if 2 <= len(candidate) <= 55 and not candidate.startswith(("-", "*", "•")) and not candidate.endswith(":") and not classify_section_header(candidate):
                if not re.search(r"\b(?:full[-\s]?time|part[-\s]?time|contract|salary|posted|apply|remote|hybrid)\b", candidate, re.IGNORECASE):
                    rec.company_name = candidate.strip()
                    break

    # 3. Workplace Type, Employment Type, Seniority Level
    full_text = rec.full_description_text
    if not rec.workplace_type:
        if re.search(r"\b(?:100%\s+remote|fully\s+remote|work\s+from\s+home|remote[-\s]first|\(remote\)|location\s*:\s*remote)\b", full_text, re.IGNORECASE):
            rec.workplace_type = "Remote"
        elif re.search(r"\bhybrid\b", full_text, re.IGNORECASE):
            rec.workplace_type = "Hybrid"
        elif re.search(r"\b(?:on[-\s]?site|in[-\s]?office|in[-\s]?person)\b", full_text, re.IGNORECASE):
            rec.workplace_type = "On-site"
        elif re.search(r"\bremote\b", "\n".join(lines[:15]), re.IGNORECASE):
            rec.workplace_type = "Remote"

    if not rec.employment_type:
        emp_types: List[str] = []
        if re.search(r"\bfull[-\s]?time\b", full_text, re.IGNORECASE):
            emp_types.append("Full-time")
        if re.search(r"\bpart[-\s]?time\b", full_text, re.IGNORECASE):
            emp_types.append("Part-time")
        if re.search(r"\b(?:contract|contractor|c2c|w2\s+contract|1099|temp[-\s]to[-\s]hire)\b", full_text, re.IGNORECASE):
            emp_types.append("Contract")
        if re.search(r"\b(?:internship|intern)\b", full_text, re.IGNORECASE):
            emp_types.append("Internship")
        rec.employment_type = ", ".join(emp_types) if emp_types else "Full-time"

    title_lower = rec.job_title.lower()
    if re.search(r"\b(?:vp|vice\s+president|chief|cxo|cfo|cto|cio|coo|svp|evp|head\s+of)\b", title_lower):
        rec.seniority_level = "Executive / VP"
    elif re.search(r"\bdirector\b", title_lower):
        rec.seniority_level = "Director"
    elif re.search(r"\b(?:manager|supervisor|team\s+lead)\b", title_lower):
        rec.seniority_level = "Manager"
    elif re.search(r"\b(?:principal|staff|distinguished|architect|fellow)\b", title_lower):
        rec.seniority_level = "Staff / Principal"
    elif re.search(r"\b(?:senior|sr\.?|lead)\b", title_lower):
        rec.seniority_level = "Senior"
    elif re.search(r"\b(?:junior|jr\.?|entry[-\s]level|associate|intern|grad)\b", title_lower):
        rec.seniority_level = "Entry / Associate"
    else:
        rec.seniority_level = "Mid-Senior"

    # 4. Salary & Benefits Extraction
    if not rec.salary.raw_text:
        rec.salary = extract_salary_from_text(full_text)
    else:
        extra_sal = extract_salary_from_text(full_text)
        rec.salary.additional_comp = extra_sal.additional_comp

    for pat, label in KNOWN_BENEFITS:
        if re.search(pat, full_text, re.IGNORECASE) and label not in rec.benefits:
            rec.benefits.append(label)

    # 5. Split Job Posting Lines into Sections (Summary, Responsibilities, Required, Preferred, Benefits)
    current_bucket = "summary"
    summary_lines: List[str] = []
    company_lines: List[str] = []

    for line in lines:
        header_bucket = classify_section_header(line)
        if header_bucket:
            current_bucket = header_bucket
            continue

        is_bullet = bool(re.match(r"^(?:[-*•▪▸]|\d+[.)])\s+", line))
        clean_item = re.sub(r"^(?:[-*•▪▸]|\d+[.)])\s*", "", line).strip()
        if not clean_item:
            continue

        # Inline preferred override inside a required list
        item_bucket = current_bucket
        if re.match(r"^(?:preferred|nice[-\s]to[-\s]have|bonus|desired|plus)\s*[:–-]", clean_item, re.IGNORECASE):
            item_bucket = "preferred"

        if item_bucket == "responsibilities" and (is_bullet or len(clean_item) > 20):
            rec.responsibilities.append(clean_item)
        elif item_bucket == "required" and (is_bullet or len(clean_item) > 15):
            rec.required_qualifications.append(clean_item)
        elif item_bucket == "preferred" and (is_bullet or len(clean_item) > 12):
            rec.preferred_qualifications.append(clean_item)
        elif item_bucket == "benefits" and (is_bullet or len(clean_item) > 12):
            if clean_item not in rec.benefits:
                rec.benefits.append(clean_item)
        elif item_bucket == "company":
            company_lines.append(clean_item)
        elif item_bucket == "summary":
            if is_bullet:
                # Unlabeled bullets before a Qualifications header are usually responsibilities or requirements
                if re.search(r"\b(?:years?\s+of\s+experience|bachelor|master|degree|proficiency\s+in|experience\s+with|must\s+have)\b", clean_item, re.IGNORECASE):
                    rec.required_qualifications.append(clean_item)
                else:
                    rec.responsibilities.append(clean_item)
            elif len(clean_item) > 40 and clean_item != rec.job_title and not re.match(r"^(?:job\s+title|company|location|job\s+id|req(?:uisition)?\s+id|department|date\s+posted)\s*:", clean_item, re.IGNORECASE):
                summary_lines.append(clean_item)

    rec.role_summary = " ".join(summary_lines[:4])[:900]
    rec.company_overview = " ".join(company_lines[:3])[:700]

    # 6. Years of Experience, Education, Certifications, Clearance, Work Auth, Travel
    yr_matches = list(
        re.finditer(
            r"\b(\d{1,2})\s*(?:\+|(?:[-–]|to)\s*(\d{1,2}))?\s*years?\s+(?:of\s+)?(?:[a-zA-Z\-/]+\s+){0,4}?(?:experience|exp\b|background|track\s+record)",
            full_text,
            re.IGNORECASE,
        )
    )
    if yr_matches:
        mins = [float(m.group(1)) for m in yr_matches if float(m.group(1)) <= 25]
        maxs = [float(m.group(2)) for m in yr_matches if m.group(2) and float(m.group(2)) <= 30]
        if mins:
            rec.min_years_experience = max(mins)
        if maxs:
            rec.max_years_experience = max(maxs)

    # Education lines
    for line in rec.required_qualifications + rec.preferred_qualifications + lines:
        if re.search(r"\b(?:Bachelor'?s?|Master'?s?|B\.?S\.?|M\.?S\.?|B\.?A\.?|M\.?B\.?A\.?|Ph\.?D\.?|Associate'?s?\s+degree|High\s+School\s+Diploma|GED|degree\s+in)\b", line, re.IGNORECASE):
            clean_edu = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line).strip()
            if clean_edu not in rec.education_requirements and len(clean_edu) <= 180:
                rec.education_requirements.append(clean_edu)

    # Certifications
    for cert in KNOWN_CERTIFICATIONS:
        pat = rf"(?<!\w){re.escape(cert)}(?!\w)"
        if re.search(pat, full_text, re.IGNORECASE) and cert not in rec.certifications_licenses:
            rec.certifications_licenses.append(cert)

    # Hard & Soft Skills
    for skill in KNOWN_HARD_SKILLS:
        left = r"(?<!\w)" if skill[0].isalnum() else r"(?:^|(?<=[\s,;/(\[]))"
        right = r"(?!\w)" if skill[-1].isalnum() else r"(?:$|(?=[\s,;/.)\]]))"
        if re.search(rf"{left}{re.escape(skill)}{right}", full_text, re.IGNORECASE):
            if skill not in rec.hard_skills:
                rec.hard_skills.append(skill)

    for s_skill in KNOWN_SOFT_SKILLS:
        if re.search(rf"\b{re.escape(s_skill)}\b", full_text, re.IGNORECASE):
            if s_skill not in rec.soft_skills:
                rec.soft_skills.append(s_skill)

    # Clearance, Work Auth, Travel
    for line in lines:
        if not rec.security_clearance and re.search(r"\b(?:security\s+clearance|top\s+secret|ts/sci|secret\s+clearance|public\s+trust|dod\s+clearance)\b", line, re.IGNORECASE):
            rec.security_clearance = line
        if not rec.work_authorization and re.search(r"\b(?:authorized\s+to\s+work|work\s+authorization|visa\s+sponsorship|sponsorship\s+is\s+not|u\.?s\.?\s+citizen(?:ship)?)\b", line, re.IGNORECASE):
            rec.work_authorization = line
        if not rec.travel_requirement and re.search(r"\b(?:travel\s+up\s+to|\d{1,2}%\s+travel|willingness\s+to\s+travel|overnight\s+travel)\b", line, re.IGNORECASE):
            rec.travel_requirement = line

    # 7. Build Direct `ats_config` for `offline_ats_auditor.py`
    rec.ats_config = build_ats_config_from_record(rec)
    return rec


# ---------------------------------------------------------------------------
# ATS Config Builder (Bridges JobPostingRecord -> offline_ats_auditor.py)
# ---------------------------------------------------------------------------

SKILL_SYNONYM_MAP: Dict[str, List[str]] = {
    "python": ["PySpark", "Pandas"],
    "sql": ["PostgreSQL", "T-SQL", "MySQL", "PL/SQL"],
    "snowflake": ["BigQuery", "Redshift", "Databricks"],
    "power bi": ["Tableau", "Looker", "Qlik"],
    "tableau": ["Power BI", "Looker", "Qlik"],
    "aws": ["Amazon Web Services", "EC2", "S3"],
    "gcp": ["Google Cloud", "BigQuery"],
    "azure": ["Microsoft Azure"],
    "kubernetes": ["EKS", "GKE", "AKS", "Docker"],
    "sap s/4hana": ["SAP ERP", "SAP", "Oracle NetSuite"],
    "p&l ownership": ["operating budget", "P&L management"],
    "demand forecasting": ["demand planning", "inventory forecasting", "call volume forecasting"],
    "workforce capacity modeling": ["capacity planning", "resource modeling", "staffing models", "capacity & workforce planning"],
    "capacity planning": ["workforce capacity modeling", "staffing models", "capacity & workforce planning"],
    "workforce management": ["WFM", "workforce planning", "workforce analytics", "senior workforce analyst", "intraday management"],
    "nice iex": ["NICE WFM", "IEX", "Verint", "Calabrio", "Aspect"],
    "verint": ["NICE IEX", "Calabrio", "Aspect", "Genesys Cloud"],
    "calabrio": ["NICE IEX", "Verint", "Genesys Cloud"],
    "genesys cloud": ["Amazon Connect", "NICE IEX", "Five9", "Avaya"],
    "amazon connect": ["Genesys Cloud", "NICE IEX", "Five9"],
    "intraday management": ["real-time adherence", "schedule adherence", "queue optimization", "shrinkage"],
    "real-time adherence": ["schedule adherence", "adherence", "intraday management"],
    "schedule adherence": ["real-time adherence", "adherence", "intraday management"],
    "shrinkage": ["occupancy", "intraday management", "real-time adherence"],
    "occupancy": ["shrinkage", "utilization", "capacity & workforce planning", "queue optimization"],
    "aht": ["average handle time", "SLA/ASA", "ASA", "queue optimization"],
    "service level agreement": ["SLA", "SLA/ASA", "KPI"],
    "cross-functional leadership": ["cross-functional", "stakeholder management", "cross-functional collaboration"],
    "six sigma black belt": ["Lean Six Sigma", "Six Sigma Green Belt"],
}


def build_ats_config_from_record(rec: JobPostingRecord) -> Dict[str, Any]:
    """Generate the exact `--jd-config` JSON structure consumed by `offline_ats_auditor.py`."""
    requirements: List[Dict[str, Any]] = []
    seen: set[str] = set()

    req_text_block = "\n".join(rec.required_qualifications)
    pref_text_block = "\n".join(rec.preferred_qualifications)

    # 1. Add matched hard skills with tier detection
    for hs in rec.hard_skills:
        key = hs.lower()
        if key in seen:
            continue
        in_pref_only = bool(re.search(re.escape(hs), pref_text_block, re.IGNORECASE)) and not bool(
            re.search(re.escape(hs), req_text_block, re.IGNORECASE)
        )
        tier = "preferred" if in_pref_only else "required"
        cat = "title_scope" if hs in ("P&L Ownership", "Budgeting") else "hard_skill"
        seen.add(key)
        requirements.append(
            {
                "name": hs,
                "category": cat,
                "tier": tier,
                "synonyms": SKILL_SYNONYM_MAP.get(key, []),
                "is_knockout_gate": False,
            }
        )

    # 2. Add certifications
    for cert in rec.certifications_licenses:
        key = cert.lower()
        if key in seen:
            continue
        in_pref = bool(re.search(re.escape(cert), pref_text_block, re.IGNORECASE)) and not bool(
            re.search(re.escape(cert), req_text_block, re.IGNORECASE)
        )
        seen.add(key)
        requirements.append(
            {
                "name": cert,
                "category": "hard_skill",
                "tier": "preferred" if in_pref else "required",
                "synonyms": SKILL_SYNONYM_MAP.get(key, []),
                "is_knockout_gate": False,
            }
        )

    # 3. Add soft skills
    for ss in rec.soft_skills:
        key = ss.lower()
        if key in seen:
            continue
        seen.add(key)
        requirements.append(
            {
                "name": ss,
                "category": "soft_skill",
                "tier": "required",
                "synonyms": SKILL_SYNONYM_MAP.get(key, []),
                "is_knockout_gate": False,
            }
        )

    # 4. Add degree knockout gate if required
    edu_joined = " ".join(rec.education_requirements)
    if re.search(r"\b(?:bachelor|b\.?s\.?|b\.?a\.?)\b", edu_joined, re.IGNORECASE) and "b.s." not in seen:
        seen.add("b.s.")
        requirements.append(
            {
                "name": "B.S.",
                "category": "hard_skill",
                "tier": "required",
                "synonyms": ["Bachelor", "Bachelor's", "B.A.", "Master's", "M.S.", "MBA"],
                "is_knockout_gate": True,
            }
        )

    # 5. If heuristic bullet phrases exist that weren't in KNOWN_HARD_SKILLS, extract top clean phrases
    if len(requirements) < 6:
        for qual_list, tier_name in ((rec.required_qualifications, "required"), (rec.preferred_qualifications, "preferred")):
            for line in qual_list:
                cleaned = re.sub(
                    r"^(?:(?:\d+\+?\s*years?\s+(?:of\s+)?)?(?:hands[-\s]on\s+|strong\s+|proven\s+|demonstrated\s+)?(?:experience|proficiency|expertise|knowledge|skills?)\s+(?:in|with|using)\s+)",
                    "",
                    line,
                    flags=re.IGNORECASE,
                ).strip()
                phrase = re.split(r"[:;,]|\band\b|\bwith\b|\bto\b|\bfor\b", cleaned, maxsplit=1)[0].strip(" .-")
                if 3 <= len(phrase) <= 42 and phrase.lower() not in seen:
                    seen.add(phrase.lower())
                    requirements.append(
                        {
                            "name": phrase,
                            "category": "hard_skill",
                            "tier": tier_name,
                            "synonyms": [],
                            "is_knockout_gate": False,
                        }
                    )

    return {
        "alignment": {
            "target_title": rec.job_title,
            "title_synonyms": [],
            "min_years_required": rec.min_years_experience if rec.min_years_experience is not None else 5.0,
        },
        "requirements": requirements,
    }


# ---------------------------------------------------------------------------
# Structured API Converters (Greenhouse & Lever JSON -> JobPostingRecord)
# ---------------------------------------------------------------------------

def parse_from_fetch_result(fetch_res: Dict[str, Any]) -> JobPostingRecord:
    mode = fetch_res.get("mode", "html")
    url = str(fetch_res.get("url") or "")
    platform = str(fetch_res.get("platform") or "Direct")

    if mode == "greenhouse_api":
        data = fetch_res["data"]
        content_text = html_fragment_to_text(str(data.get("content") or ""))
        title = str(data.get("title") or "")
        loc = str((data.get("location") or {}).get("name") or "")
        comp = str(data.get("company_name") or "")
        header_block = f"Job Title: {title}\nCompany: {comp}\nLocation: {loc}\nJob ID: {data.get('id', '')}\n\n{content_text}"
        rec = parse_job_posting_text(header_block, url=url, platform_hint="Greenhouse ATS")
        rec.date_posted = str(data.get("updated_at") or "")
        rec.apply_url = str(data.get("absolute_url") or url)
        return rec

    if mode == "lever_api":
        data = fetch_res["data"]
        cats = data.get("categories") or {}
        lists_html = "\n".join(
            f"<h3>{lst.get('text', '')}</h3><ul>{lst.get('content', '')}</ul>"
            for lst in (data.get("lists") or [])
        )
        full_html = f"{data.get('description', '')}\n{lists_html}\n{data.get('additional', '')}"
        content_text = html_fragment_to_text(full_html)
        header_block = (
            f"Job Title: {data.get('text', '')}\n"
            f"Location: {cats.get('location', '')}\n"
            f"Department: {cats.get('department', '')}\n"
            f"Job ID: {data.get('id', '')}\n\n{content_text}"
        )
        rec = parse_job_posting_text(header_block, url=url, platform_hint="Lever ATS")
        rec.workplace_type = str(data.get("workplaceType") or rec.workplace_type)
        rec.apply_url = str(data.get("applyUrl") or url)
        sal_range = data.get("salaryRange")
        if isinstance(sal_range, dict) and sal_range.get("min"):
            rec.salary.min_amount = float(sal_range["min"])
            rec.salary.max_amount = float(sal_range.get("max") or sal_range["min"])
            rec.salary.currency = str(sal_range.get("currency") or "USD")
            rec.salary.period = str(sal_range.get("interval") or "year")
            rec.salary.raw_text = f"${rec.salary.min_amount:,.0f} - ${rec.salary.max_amount:,.0f} ({rec.salary.period})"
        return rec

    raw_html = str(fetch_res.get("html") or "")
    jsonld = extract_jsonld_job_posting(raw_html)
    plain_text = html_fragment_to_text(raw_html)
    rec = parse_job_posting_text(plain_text, url=url, jsonld=jsonld, platform_hint=platform)
    if fetch_res.get("job_id") and not rec.job_id:
        rec.job_id = str(fetch_res["job_id"])
    return rec


# ---------------------------------------------------------------------------
# Markdown Report Formatter
# ---------------------------------------------------------------------------

def render_job_posting_markdown(rec: JobPostingRecord) -> str:
    sal_str = rec.salary.raw_text or "Not disclosed on posting"
    if rec.salary.min_amount and rec.salary.max_amount:
        sal_str = f"{sal_str} (Normalized: {rec.salary.currency} ${rec.salary.min_amount:,.0f} - ${rec.salary.max_amount:,.0f} / {rec.salary.period or 'year'})"
    if rec.salary.additional_comp:
        sal_str += f" + {', '.join(rec.salary.additional_comp)}"

    lines: List[str] = [
        f"# Structured Job Posting Intelligence: {rec.job_title or 'Untitled Role'}",
        "",
        "## 1. Core Job & Company Metadata",
        "| Field | Extracted Value |",
        "| :--- | :--- |",
        f"| **Job Title** | {rec.job_title or 'N/A'} |",
        f"| **Company Name** | {rec.company_name or 'N/A'} |",
        f"| **Source Platform** | {rec.source_platform} |",
        f"| **Location** | {rec.location or 'N/A'} |",
        f"| **Workplace Model** | {rec.workplace_type or 'N/A'} |",
        f"| **Employment Type** | {rec.employment_type or 'N/A'} |",
        f"| **Seniority Level** | {rec.seniority_level or 'N/A'} |",
        f"| **Department / Function** | {rec.department or 'N/A'} |",
        f"| **Minimum Experience** | {f'{rec.min_years_experience:.0f}+ years' if rec.min_years_experience is not None else 'Not specified'} |",
        f"| **Salary / Compensation** | {sal_str} |",
        f"| **Job / Requisition ID** | {rec.job_id or 'N/A'} |",
        f"| **Date Posted** | {rec.date_posted or 'N/A'} |",
        f"| **Source / Apply URL** | {rec.apply_url or rec.source_url or 'N/A'} |",
        "",
    ]

    if rec.role_summary or rec.company_overview:
        lines.append("## 2. Role & Company Overview")
        if rec.company_overview:
            lines.append(f"- **Company Overview:** {rec.company_overview}")
        if rec.role_summary:
            lines.append(f"- **Role Summary:** {rec.role_summary}")
        lines.append("")

    lines.append("## 3. Required Qualifications & Must-Have Requirements")
    if rec.required_qualifications:
        for q in rec.required_qualifications:
            lines.append(f"- {q}")
    else:
        lines.append("- *(No explicit bulleted required qualifications section separated; see extracted skills below)*")
    lines.append("")

    lines.append("## 4. Preferred Qualifications & Nice-to-Have Skills")
    if rec.preferred_qualifications:
        for q in rec.preferred_qualifications:
            lines.append(f"- {q}")
    else:
        lines.append("- *(None explicitly marked preferred)*")
    lines.append("")

    lines.append("## 5. Key Responsibilities & Scope of Work")
    if rec.responsibilities:
        for r in rec.responsibilities:
            lines.append(f"- {r}")
    else:
        lines.append("- *(See full description text)*")
    lines.append("")

    lines.extend(
        [
            "## 6. Extracted Skills, Credentials & Gate Requirements",
            f"- **Hard Skills / Tools / Technologies ({len(rec.hard_skills)}):** {', '.join(rec.hard_skills) if rec.hard_skills else 'None detected'}",
            f"- **Soft Skills / Core Competencies ({len(rec.soft_skills)}):** {', '.join(rec.soft_skills) if rec.soft_skills else 'None detected'}",
            f"- **Education Requirements:** {'; '.join(rec.education_requirements) if rec.education_requirements else 'Not specified'}",
            f"- **Certifications / Licenses:** {', '.join(rec.certifications_licenses) if rec.certifications_licenses else 'None specified'}",
            f"- **Benefits & Perks:** {', '.join(rec.benefits) if rec.benefits else 'Not listed'}",
            f"- **Security Clearance:** {rec.security_clearance or 'None specified'}",
            f"- **Work Authorization / Sponsorship:** {rec.work_authorization or 'Not specified'}",
            f"- **Travel Requirement:** {rec.travel_requirement or 'Not specified'}",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Built-In Verification Demo (Multi-Site LinkedIn / Indeed / ZipRecruiter Sample)
# ---------------------------------------------------------------------------

DEMO_JOB_POSTING_TEXT = """Job Title: Senior Analytics Manager
Company: Apex Global Supply Chain Solutions
Location: St. Louis, MO (Hybrid)
Job ID: REQ-2026-8841
Department: Enterprise Data & Supply Chain Analytics

About the Role
Apex Global Supply Chain Solutions is seeking a Senior Analytics Manager to lead inventory optimization, executive KPI reporting, and cloud data engineering across our North American distribution network. Base salary range is $135,000 - $165,000 per year plus a 15% annual bonus and equity (RSUs).

Key Responsibilities
- Spearhead enterprise demand forecasting and inventory optimization models across 400+ distribution centers.
- Architect automated ELT data pipelines in Snowflake and build executive dashboards in Power BI.
- Partner with VP/Director stakeholders on workforce capacity modeling and P&L ownership ($50M+ operating budget).
- Mentor a team of 6 supply chain data analysts and analytics engineers.

Required Qualifications
- B.S. degree in Industrial Engineering, Computer Science, Data Science, Statistics, or related quantitative field.
- 6+ years of progressive experience in data analytics, supply chain analytics, or business intelligence.
- Advanced proficiency in Python and SQL for large-scale data modeling and automation.
- Hands-on experience with Snowflake cloud data warehousing and SAP S/4HANA ERP data structures.
- Proven track record building executive KPI reporting suites in Power BI.
- Demonstrated experience in Workforce Capacity Modeling, Demand Forecasting, and P&L Ownership.
- Strong Cross-Functional Leadership and Stakeholder Management skills.
- Must be authorized to work in the United States without visa sponsorship.

Preferred Qualifications
- Master's degree (M.S. or MBA) in Supply Chain or Business Analytics.
- Experience with Kubernetes, Docker, or Airflow container orchestration.
- Six Sigma Black Belt or Lean Six Sigma certification.

Benefits & Perks
- Comprehensive medical, dental, and vision insurance from day one.
- 401(k) retirement plan with 6% company match.
- Paid time off (PTO), 12 paid holidays, and paid parental leave.
- $5,000 annual tuition reimbursement and professional development stipend.
"""


# ---------------------------------------------------------------------------
# Stage 1 Public API Functions & Unified 10-Board ToS Compliance Router
# ---------------------------------------------------------------------------

DEFAULT_USER_AGENT = "ATS-Ledger-CompliantCollector/1.0 (Public-API-Only)"

# Unified 10-board blocklist inside job_posting_collector.py
RESTRICTED_DOMAINS: dict[str, str] = {
    "linkedin.com": "LinkedIn Terms of Service prohibit automated scraping and bots.",
    "indeed.com": "Indeed Terms of Service prohibit crawlers, scrapers, bots, and automated extraction.",
    "simplyhired.com": "SimplyHired is governed by Indeed Terms of Service, which ban crawlers, scrapers, and bots.",
    "ziprecruiter.com": "ZipRecruiter Terms of Use prohibit automated scraping and data extraction.",
    "glassdoor.com": "Glassdoor Terms of Use prohibit bots, scrapers, and automated access.",
    "monster.com": "Monster/BOLD unified Terms of Use (§ 2) prohibit bots, scrapers, and external app reuse.",
    "careerbuilder.com": "CareerBuilder/BOLD unified Terms of Use (§ 2) prohibit bots, scrapers, and external app reuse.",
    "dice.com": "Dice Terms & Conditions (Clause aa) prohibit robots, spiders, and retrieval scripts.",
    "builtin.com": "Built In Community Terms (§ 3(f)) prohibit bots, scrapers, crawlers, and indexing agents.",
    "wellfound.com": "Wellfound General Terms prohibit spiders, robots, offline readers, and automated access.",
    "angel.co": "Wellfound (formerly AngelList) General Terms prohibit automated scraping.",
}


class ComplianceBlockError(RuntimeError):
    """Raised when a target domain prohibits automated retrieval under its Terms of Service."""


@dataclass
class NormalizedJob:
    source: str
    job_id: str
    company: str
    title: str
    location: str
    workplace_type: str
    compensation: str
    url: str
    published_at: str
    description_text: str
    attribution: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class _BlockPreservingHTMLParser(HTMLParser):
    """Converts HTML job descriptions into clean plain text while keeping bullet/line breaks."""

    BLOCK_TAGS = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "ul", "ol"}

    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "li":
            self._chunks.append("\n- ")
        elif t in self.BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.BLOCK_TAGS or tag.lower() == "li":
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        self._chunks.append(data)

    def get_text(self) -> str:
        raw = "".join(self._chunks)
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in raw.splitlines()]
        cleaned = "\n".join(lines)
        return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def html_to_plain_text(raw_html: str | None) -> str:
    if not raw_html:
        return ""
    # Unescape twice to handle Greenhouse double-encoded HTML (&lt;p&gt; -> <p>)
    decoded = html_lib.unescape(html_lib.unescape(raw_html))
    parser = _BlockPreservingHTMLParser()
    parser.feed(decoded)
    parser.close()
    return parser.get_text()


def _http_get_json(
    url: str,
    headers: dict[str, str] | None = None,
    max_retries: int = 3,
    timeout: int = 20,
) -> Any:
    req_headers = {"Accept": "application/json", "User-Agent": DEFAULT_USER_AGENT}
    if headers:
        req_headers.update(headers)

    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=req_headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return json.loads(resp.read().decode(charset))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                retry_after = exc.headers.get("Retry-After")
                wait = float(retry_after) if (retry_after and retry_after.isdigit()) else (2 ** attempt)
                time.sleep(min(wait, 15.0))
                continue
            raise
    raise RuntimeError(f"Exhausted retries for {url}")


def enforce_domain_compliance(url: str) -> None:
    host = urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    for blocked_domain, reason in RESTRICTED_DOMAINS.items():
        if host == blocked_domain or host.endswith(f".{blocked_domain}"):
            raise ComplianceBlockError(
                f"BLOCKED ({blocked_domain}): {reason}\n"
                f"Copy the job description manually and pass `--jd-file <path>` or `--stdin` instead."
            )


def fetch_greenhouse(board_token: str, job_id: str | None = None) -> list[NormalizedJob]:
    token = urllib.parse.quote(board_token.strip("/"))
    if job_id:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{urllib.parse.quote(job_id)}"
        raw_jobs = [_http_get_json(url)]
    else:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        payload = _http_get_json(url)
        raw_jobs = payload.get("jobs", [])

    results: list[NormalizedJob] = []
    for job in raw_jobs:
        loc = (job.get("location") or {}).get("name") or ""
        workplace = "Remote" if "remote" in loc.lower() else ("Hybrid" if "hybrid" in loc.lower() else "Unspecified")
        results.append(
            NormalizedJob(
                source="greenhouse",
                job_id=str(job.get("id", "")),
                company=board_token,
                title=(job.get("title") or "").strip(),
                location=loc.strip(),
                workplace_type=workplace,
                compensation="",
                url=job.get("absolute_url") or "",
                published_at=job.get("updated_at") or job.get("first_published") or "",
                description_text=html_to_plain_text(job.get("content")),
            )
        )
    return results


def _parse_lever_posting(site: str, post: dict[str, Any]) -> NormalizedJob:
    cats = post.get("categories") or {}
    body_parts: list[str] = []
    if post.get("descriptionPlain"):
        body_parts.append(post["descriptionPlain"].strip())
    for section in post.get("lists") or []:
        heading = (section.get("text") or "").strip()
        items_text = html_to_plain_text(section.get("content"))
        if heading or items_text:
            body_parts.append(f"{heading}\n{items_text}".strip())
    if post.get("additionalPlain"):
        body_parts.append(post["additionalPlain"].strip())

    sal = post.get("salaryRange") or {}
    comp = ""
    if sal.get("min") is not None and sal.get("max") is not None:
        comp = f"{sal.get('currency', 'USD')} {sal['min']}-{sal['max']} ({sal.get('interval', '')})".strip()
    elif post.get("salaryDescriptionPlain"):
        comp = post["salaryDescriptionPlain"].strip()

    created_ms = post.get("createdAt")
    published = (
        datetime.fromtimestamp(created_ms / 1000.0, tz=timezone.utc).isoformat()
        if isinstance(created_ms, (int, float))
        else ""
    )

    return NormalizedJob(
        source="lever",
        job_id=str(post.get("id", "")),
        company=site,
        title=(post.get("text") or "").strip(),
        location=(cats.get("location") or "").strip(),
        workplace_type=(post.get("workplaceType") or "unspecified").strip(),
        compensation=comp,
        url=post.get("hostedUrl") or post.get("applyUrl") or "",
        published_at=published,
        description_text="\n\n".join(body_parts),
    )


def fetch_lever(site_slug: str, job_id: str | None = None, eu_hint: bool = False) -> list[NormalizedJob]:
    slug = urllib.parse.quote(site_slug.strip("/"))
    hosts = ["api.eu.lever.co", "api.lever.co"] if eu_hint else ["api.lever.co", "api.eu.lever.co"]

    for idx, host in enumerate(hosts):
        try:
            if job_id:
                url = f"https://{host}/v0/postings/{slug}/{urllib.parse.quote(job_id)}?mode=json"
                post = _http_get_json(url)
                return [_parse_lever_posting(site_slug, post)]

            postings: list[NormalizedJob] = []
            skip, limit = 0, 100
            while True:
                url = f"https://{host}/v0/postings/{slug}?mode=json&skip={skip}&limit={limit}"
                batch = _http_get_json(url)
                if not isinstance(batch, list) or not batch:
                    break
                postings.extend(_parse_lever_posting(site_slug, item) for item in batch)
                if len(batch) < limit:
                    break
                skip += limit
                time.sleep(1.0)
            return postings
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and idx == 0:
                continue
            raise
    return []


def fetch_ashby(board_name: str, job_id: str | None = None) -> list[NormalizedJob]:
    slug = urllib.parse.quote(board_name.strip("/"))
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    payload = _http_get_json(url)

    results: list[NormalizedJob] = []
    for job in payload.get("jobs") or []:
        jid = str(job.get("id") or "")
        if job_id:
            if jid != job_id and job_id not in (job.get("jobUrl") or ""):
                continue
        elif job.get("isListed") is False:
            continue

        comp_obj = job.get("compensation") or {}
        comp_str = (
            comp_obj.get("scrapeableCompensationSalarySummary")
            or comp_obj.get("compensationTierSummary")
            or ""
        )
        desc = (job.get("descriptionPlain") or "").strip() or html_to_plain_text(job.get("descriptionHtml"))

        results.append(
            NormalizedJob(
                source="ashby",
                job_id=jid,
                company=board_name,
                title=(job.get("title") or "").strip(),
                location=(job.get("location") or "").strip(),
                workplace_type=(job.get("workplaceType") or "Unspecified").strip(),
                compensation=comp_str.strip(),
                url=job.get("jobUrl") or job.get("applyUrl") or "",
                published_at=job.get("publishedAt") or "",
                description_text=desc,
            )
        )
    return results


def fetch_usajobs(
    keyword: str,
    location: str = "",
    results_per_page: int = 25,
    page: int = 1,
    api_key: str | None = None,
    user_agent_email: str | None = None,
) -> list[NormalizedJob]:
    key = api_key or os.environ.get("USAJOBS_API_KEY", "").strip()
    ua_email = user_agent_email or os.environ.get("USAJOBS_USER_AGENT", "").strip()
    if not key or not ua_email or "@" not in ua_email:
        raise RuntimeError(
            "USAJobs API requires both USAJOBS_API_KEY and USAJOBS_USER_AGENT "
            "(set to the exact email address registered at developer.usajobs.gov)."
        )

    params: dict[str, Any] = {
        "Keyword": keyword,
        "ResultsPerPage": min(max(results_per_page, 1), 500),
        "Page": max(page, 1),
        "Fields": "full",
    }
    if location:
        params["LocationName"] = location

    url = f"https://data.usajobs.gov/api/search?{urllib.parse.urlencode(params)}"
    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": ua_email,
        "Authorization-Key": key,
    }
    payload = _http_get_json(url, headers=headers)
    items = (payload.get("SearchResult") or {}).get("SearchResultItems") or []
    retrieved_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    results: list[NormalizedJob] = []
    for item in items:
        desc = item.get("MatchedObjectDescriptor") or {}
        details = (desc.get("UserArea") or {}).get("Details") or {}

        duties = details.get("MajorDuties") or ""
        if isinstance(duties, list):
            duties = "\n- ".join(str(d).strip() for d in duties if str(d).strip())
            if duties:
                duties = f"- {duties}"

        sections = [
            ("Summary", details.get("JobSummary")),
            ("Major Duties", duties),
            ("Qualifications", desc.get("QualificationSummary")),
            ("Education", details.get("Education")),
            ("Evaluations", details.get("Evaluations")),
        ]
        full_text = "\n\n".join(f"{label}:\n{val.strip()}" for label, val in sections if val and str(val).strip())

        remun = (desc.get("PositionRemuneration") or [{}])[0]
        comp = ""
        if remun.get("MinimumRange") and remun.get("MaximumRange"):
            comp = f"${remun['MinimumRange']} - ${remun['MaximumRange']} {remun.get('Description', '')}".strip()

        pos_uri = desc.get("PositionURI") or ""
        results.append(
            NormalizedJob(
                source="usajobs",
                job_id=str(item.get("MatchedObjectId") or desc.get("PositionID") or ""),
                company=(desc.get("OrganizationName") or desc.get("DepartmentName") or "").strip(),
                title=(desc.get("PositionTitle") or "").strip(),
                location=(desc.get("PositionLocationDisplay") or "").strip(),
                workplace_type="Remote" if details.get("TeleworkEligible") else "OnSite/Specified",
                compensation=comp,
                url=pos_uri,
                published_at=desc.get("PublicationStartDate") or "",
                description_text=full_text,
                attribution=(
                    f"Data source: USAJOBS (retrieved {retrieved_date}). Official announcement: {pos_uri}. "
                    "Not endorsed by or affiliated with OPM or any U.S. Government agency."
                ),
            )
        )
    return results


def collect_from_url(url: str) -> list[NormalizedJob]:
    enforce_domain_compliance(url)
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    parts = [p for p in parsed.path.split("/") if p]

    # Greenhouse: boards.greenhouse.io/{token}[/jobs/{id}] or job-boards.greenhouse.io/{token}[/jobs/{id}]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io") and parts:
        token = parts[0]
        job_id = parts[2] if len(parts) >= 3 and parts[1] == "jobs" else None
        return fetch_greenhouse(token, job_id=job_id)

    # Lever: jobs.lever.co/{site}[/{id}] or jobs.eu.lever.co/{site}[/{id}]
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        site = parts[0]
        job_id = parts[1] if len(parts) >= 2 and parts[1] != "apply" else None
        return fetch_lever(site, job_id=job_id, eu_hint=(host == "jobs.eu.lever.co"))

    # Ashby: jobs.ashbyhq.com/{board}[/{job_id}]
    if host == "jobs.ashbyhq.com" and parts:
        board = parts[0]
        job_id = parts[1] if len(parts) >= 2 and parts[1] != "application" else None
        return fetch_ashby(board, job_id=job_id)

    raise ValueError(
        f"Unrecognized or custom-domain URL ({host}). "
        "Pass an explicit --greenhouse, --lever, --ashby, or --usajobs flag, or --jd-file for manual text."
    )


def load_manual_jd(text: str, title: str = "Manual Paste", company: str = "Manual Source", url: str = "") -> NormalizedJob:
    return NormalizedJob(
        source="manual",
        job_id="manual-local",
        company=company,
        title=title,
        location="",
        workplace_type="",
        compensation="",
        url=url,
        published_at=datetime.now(timezone.utc).isoformat(),
        description_text=text.strip(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Universal Job Posting Collector & Structured JD Parser")
    parser.add_argument("--url", type=str, help="Employer-published job posting URL (Greenhouse, Lever, Ashby, career page)")
    parser.add_argument("--input-file", type=str, help="Path to saved HTML or plain-text job posting file")
    parser.add_argument("--platform", type=str, default="", help="Optional source platform override (e.g., LinkedIn, Indeed, ZipRecruiter)")
    parser.add_argument("--output", type=str, help="Path to save Markdown summary report")
    parser.add_argument("--json-out", type=str, help="Path to save full structured JobPostingRecord JSON")
    parser.add_argument("--ats-config-out", type=str, help="Path to save `--jd-config` JSON for offline_ats_auditor.py")
    parser.add_argument("--demo", action="store_true", help="Run built-in verification demo")

    args = parser.parse_args()

    if args.demo or (not args.url and not args.input_file):
        rec = parse_job_posting_text(
            DEMO_JOB_POSTING_TEXT,
            url="https://www.linkedin.com/jobs/view/3988412026",
            platform_hint=args.platform or "LinkedIn",
        )
    elif args.url:
        fetch_res = fetch_job_source(args.url)
        if fetch_res.get("paste_required") or fetch_res.get("error"):
            err_msg = fetch_res.get("error") or f"{fetch_res.get('platform')} restricts automated access."
            sys.stderr.write(
                f"[COMPLIANCE GATE] {err_msg}\n"
                "Paste the job description into a text file (or save the page) and re-run with --input-file <path>.\n"
            )
            sys.exit(2)
        if not fetch_res.get("ok"):
            sys.stderr.write(
                f"[WARN] Direct fetch for {args.url} returned status={fetch_res.get('status')} (blocked={fetch_res.get('blocked')}).\n"
                "Tip: copy the job description into a text file and pass it with --input-file. Do not try to work around CAPTCHAs or login walls.\n"
            )
        rec = parse_from_fetch_result(fetch_res)
        if args.platform:
            rec.source_platform = args.platform
    else:
        with open(args.input_file, "r", encoding="utf-8") as f:
            raw_content = f.read()
        if "<html" in raw_content.lower() or "<body" in raw_content.lower() or "application/ld+json" in raw_content.lower():
            jsonld = extract_jsonld_job_posting(raw_content)
            plain = html_fragment_to_text(raw_content)
            rec = parse_job_posting_text(plain, url=args.url or "", jsonld=jsonld, platform_hint=args.platform)
        else:
            rec = parse_job_posting_text(raw_content, url=args.url or "", platform_hint=args.platform)

    md_report = render_job_posting_markdown(rec)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md_report + "\n")

    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(dataclasses.asdict(rec), f, indent=2, default=str)

    if args.ats_config_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.ats_config_out)), exist_ok=True)
        with open(args.ats_config_out, "w", encoding="utf-8") as f:
            json.dump(rec.ats_config, f, indent=2)

    print(md_report)


if __name__ == "__main__":
    main()
