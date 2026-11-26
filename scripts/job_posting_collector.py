#!/usr/bin/env python3
"""
ops-requisition-auditor: Job Posting Collector & Structured Requisition Parser
==============================================================================
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
import functools
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
import urllib.robotparser
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Source-platform labels (used to tag pasted or fetched postings; not a fetch list)
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
    alternative_groups: List[List[str]] = field(default_factory=list)  # options the JD itself offers
    security_clearance: str = ""
    work_authorization: str = ""
    travel_requirement: str = ""
    full_description_text: str = ""
    ats_config: Dict[str, Any] = field(default_factory=dict)
    detected_language: str = "en"
    extraction_confidence_score: float = 1.0
    extraction_confidence_band: str = "HIGH (>=0.80)"
    recommend_confirmed_jd_config: bool = False
    confirmed_requirements_count: int = 0
    inferred_requirements_count: int = 0
    ambiguity_diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    negative_exclusions: List[Dict[str, str]] = field(default_factory=list)
    extraction_ledger: List[Dict[str, Any]] = field(default_factory=list)


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

USER_AGENT = "ops-requisition-auditor/1.5.0 (personal, low-volume job-posting reader; no login or CAPTCHA handling)"

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


def robots_allows(url: str) -> bool:
    """False only when the site's robots.txt was read and asks automated tools not to fetch `url`.

    A missing robots.txt (404) or an unreachable site counts as allowed, since the page fetch that
    follows would fail anyway. A 401/403 on robots.txt counts as disallowed, per the usual convention.
    """
    parts = urllib.parse.urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return True
    status, body = _http_get(f"{parts.scheme}://{parts.netloc}/robots.txt")
    if status in (401, 403):
        return False
    if status != 200:
        return True
    rp = urllib.robotparser.RobotFileParser()
    rp.parse(body.splitlines())
    return rp.can_fetch("ops-requisition-auditor", url)


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

    # 3. Ashby Public Job Board API
    ashby_match = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)(?:/([^/?#]+))?", url, re.IGNORECASE)
    if ashby_match:
        board = urllib.parse.unquote(ashby_match.group(1))
        jid = ashby_match.group(2) if (ashby_match.group(2) and ashby_match.group(2).lower() != "application") else None
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{urllib.parse.quote(board)}?includeCompensation=true"
        status, body = _http_get(api_url)
        if status == 200:
            try:
                payload = json.loads(body)
                jobs_list = payload.get("jobs") or []
                matched_job = None
                for item in jobs_list:
                    item_id = str(item.get("id") or "")
                    if jid and (item_id == jid or jid in str(item.get("jobUrl") or "")):
                        matched_job = item
                        break
                if matched_job is None and not jid and jobs_list:
                    matched_job = jobs_list[0]
                if matched_job is not None:
                    return {"ok": True, "platform": "Ashby ATS", "mode": "ashby_api", "data": matched_job, "board": board, "url": url}
            except Exception:
                pass

    # 4. General direct HTML fetch (honest User-Agent; employer career pages), only if robots.txt allows it
    if not robots_allows(url):
        return {
            "ok": False,
            "error": "This site's robots.txt asks automated tools not to fetch this page (or could not be read).",
            "status": 0,
            "platform": platform,
            "mode": "paste_required",
            "html": "",
            "url": url,
            "blocked": False,
            "paste_required": True,
            "reason": "robots_txt",
        }
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
    "Workforce Management", "Amazon Connect", "Aspect", "Google Sheets",
    "Five9", "Talkdesk", "Avaya", "UKG", "Kronos", "Intraday Management", "Real-Time Adherence",
    "Schedule Adherence", "Shrinkage", "Occupancy", "Queue Optimization", "Staffing Models", "Scheduling",
    "Erlang", "Erlang C", "SLA/ASA", "Service Level Agreement", "Average Speed of Answer", "AHT",
    "ASA", "Service Level", "Abandonment Rate", "Conformance", "Headcount Modeling", "Real-Time Management", "Average Handle Time",
    "Call Volume Forecasting", "Contact Center Operations", "Epic", "MicroStrategy", "Power Query",
    "PivotTables", "Salesforce CRM", "Accounts Receivable", "Grant Management", "Billing Reconciliation",
    "P&L Ownership", "Financial Modeling", "Budgeting", "Variance Analysis", "GAAP", "SOX", "IFRS",
    "HIPAA", "GDPR", "SOC 2", "ISO 27001", "NIST", "PCI-DSS", "OSHA", "FDA", "cGMP",
    "Agile", "Scrum", "Kanban", "SDLC", "ITIL", "Lean Six Sigma", "Six Sigma",
]

# Contact-center WFM platforms. Each family is its own product (see references/wfm-taxonomy.md):
# platforms are never synonyms of each other. Aliases listed together are one vendor's names.
WFM_PLATFORM_ALIASES: List[str] = [
    "NICE IEX", "NICE CXone", "NICE inContact", "NICE WFM", "NICE", "IEX", "CXone", "inContact",
    "Genesys Cloud", "Genesys", "Verint", "Calabrio", "Alvaria", "Injixo", "Teleopti", "Playvox",
]
KNOWN_HARD_SKILLS.extend(a for a in WFM_PLATFORM_ALIASES if a not in KNOWN_HARD_SKILLS)

# Names that are also ordinary English words. They count only in a skills-style context and with
# exact capitalization, so "every aspect", "go beyond", and "Excel in a fast-paced team" are ignored.
AMBIGUOUS_SKILLS = {"R", "Go", "Aspect", "Epic", "Excel", "Swift", "Rust", "Scala"}
CASE_SENSITIVE_SKILLS = {"NICE", "IEX"}  # "nice to have" is not a vendor

_AMBIGUOUS_PRE = (
    r"(?:(?:in|with|using|of|and|or|including|via|plus|Microsoft|MS|Advanced|Strong|Expert|Proficient)\s+"
    r"|[,/(;]\s*|^[\s\-\u2022*]*)"
)
_AMBIGUOUS_POST = (
    r"(?=\s*[,/);.]|\s*$|\s*\(|\s+(?:and|or|skills?|proficiency|programming|language|formulas?|macros?|"
    r"modeling|modelling|pivot\w*|VBA|WFM|eWFM|Workforce|Quality|Recording|Software|EHR|EMR|Systems?)\b)"
)


@functools.lru_cache(maxsize=None)
def _skill_regex(skill: str) -> "re.Pattern[str]":
    if skill in AMBIGUOUS_SKILLS:
        return re.compile(rf"{_AMBIGUOUS_PRE}(?P<t>{re.escape(skill)}){_AMBIGUOUS_POST}", re.MULTILINE)
    left = r"(?<!\w)" if skill[0].isalnum() else r"(?:^|(?<=[\s,;/(\[]))"
    right = r"(?!\w)" if skill[-1].isalnum() else r"(?:$|(?=[\s,;/.)\]]))"
    flags = 0 if skill in CASE_SENSITIVE_SKILLS else re.IGNORECASE
    return re.compile(rf"{left}(?P<t>{re.escape(skill)}){right}", flags)


def find_skill_spans(text: str) -> List[Tuple[str, int, int]]:
    spans: List[Tuple[str, int, int]] = []
    for skill in KNOWN_HARD_SKILLS:
        for m in _skill_regex(skill).finditer(text):
            spans.append((skill, m.start("t"), m.end("t")))
    return spans


def _drop_contained_spans(spans: List[Tuple[str, int, int]]) -> List[Tuple[str, int, int]]:
    """A shorter name inside a longer one ('SAP' in 'SAP S/4HANA', 'NICE' in 'NICE IEX') is not a second requirement."""
    return [
        s for s in spans
        if not any(o is not s and o[1] <= s[1] and s[2] <= o[2] and (o[2] - o[1]) > (s[2] - s[1]) for o in spans)
    ]


_ALT_SEP_RE = re.compile(r"^(?:\s*,\s*(?:or\s+|and/or\s+|o\s+|u\s+|ou\s+|oder\s+)?|\s*/\s*|\s+(?:or|and/or|o|u|ou|oder)\s+)$", re.IGNORECASE)
_ALT_OR_RE = re.compile(r"\b(?:or|o|u|ou|oder)\b|and/or", re.IGNORECASE)
_ALT_LEAD_RE = re.compile(r"(?:such\s+as|e\.g\.,?|like|como|tels?\s+que|wie\s+z\.?\s*b\.?)\s*$", re.IGNORECASE)


# Negative context detection (skills, degrees, clearances explicitly stated as NOT required or not used)
_NEGATED_CLAUSE_PRE_RE_TMPL = (
    r"\b(?:no|not|without|never|neither|none|zero|sin|ning[uú]n[oa]?|pas\s+de|aucun[e]?|sans|kein[e]?|ohne)"
    r"\b[^.;:\n]{{0,40}}?\b{term}\b"
)
_NEGATED_CLAUSE_POST_RE_TMPL = (
    r"\b{term}\b[^.;:\n]{{0,35}}?\b(?:is\s+not|are\s+not|isn't|aren't|not|never|no\s+es|non|nicht)"
    r"\s+(?:required|needed|necessary|mandatory|expected|used|obligatorio|necesario|requis[e]?|erforderlich|notwendig)\b"
)
_NON_DEGREE_IDIOM_RE = re.compile(
    r"\b(?:high|highest|great|strong|significant|some|certain|varying|360)\s+degrees?\s+of\b|\b360[-\s]degree\b",
    re.IGNORECASE,
)
_NEGATED_DEGREE_RE = re.compile(
    r"\b(?:no\s+(?:college\s+|university\s+|4[-\s]year\s+|bachelor'?s?\s+)?degree\s+(?:is\s+)?(?:required|needed|necessary)"
    r"|degree\s+is\s+not\s+(?:required|needed|necessary)"
    r"|sin\s+t[ií]tulo\s+universitario|aucun\s+dipl[oô]me\s+requis|kein\s+(?:studium|hochschulabschluss)\s+erforderlich)\b",
    re.IGNORECASE,
)
_NON_SECURITY_CLEARANCE_RE = re.compile(
    r"\b(?:inventory|customs|medical|background|pre[-\s]employment|tax|cargo|stock|merchandise|warehouse)\s+clearance\b"
    r"|\bclearance\s+sale\b",
    re.IGNORECASE,
)
_NEGATED_CLEARANCE_RE = re.compile(
    r"\b(?:no\s+(?:security\s+)?clearance\s+(?:is\s+)?(?:required|needed|necessary)"
    r"|security\s+clearance\s+(?:is\s+)?not\s+(?:required|needed|necessary))\b",
    re.IGNORECASE,
)


def is_negated_skill_mention(text: str, skill: str) -> Tuple[bool, str]:
    """Return (True, matching_line) if every mention of `skill` in `text` is negated ('No SQL required', 'Python is not required')."""
    escaped = re.escape(skill)
    pre_rx = re.compile(_NEGATED_CLAUSE_PRE_RE_TMPL.format(term=escaped), re.IGNORECASE)
    post_rx = re.compile(_NEGATED_CLAUSE_POST_RE_TMPL.format(term=escaped), re.IGNORECASE)
    skill_rx = _skill_regex(skill)

    positive_lines: List[str] = []
    negated_lines: List[str] = []
    for line in text.splitlines():
        if not skill_rx.search(line):
            continue
        if pre_rx.search(line) or post_rx.search(line):
            negated_lines.append(line.strip())
        else:
            positive_lines.append(line.strip())
    if negated_lines and not positive_lines:
        return True, negated_lines[0]
    return False, ""


def detect_posting_language(text: str) -> str:
    """Lightweight deterministic language detector for English ('en'), Spanish ('es'), French ('fr'), and German ('de')."""
    sample = f" {text[:3000].lower()} "
    scores = {
        "es": len(re.findall(r"\b(?:requisitos|experiencia|responsabilidades|años|conocimientos|empresa|puesto|deseable|trabajo)\b", sample)),
        "fr": len(re.findall(r"\b(?:compétences|expérience|missions|profil|recherché|poste|entreprise|atouts|ans)\b", sample)),
        "de": len(re.findall(r"\b(?:anforderungen|aufgaben|erfahrung|kenntnisse|wünschenswert|studium|berufserfahrung|jahre|wir)\b", sample)),
        "en": len(re.findall(r"\b(?:requirements|qualifications|responsibilities|experience|skills|preferred|required|years|role)\b", sample)),
    }
    best_lang = max(scores, key=lambda k: scores[k])
    return best_lang if scores[best_lang] >= 2 else "en"


def find_alternative_groups(text: str) -> List[List[str]]:
    """Skills the JD itself offers as options ("NICE, Verint, or Genesys"; "SQL or Tableau"; "tools such as A, B, C").

    "and" lists ("Python and SQL") are never grouped: those are all required. Any one member of a group
    satisfies the requirement at full credit (references/wfm-taxonomy.md, rule 3).
    """
    groups: List[List[str]] = []
    for line in text.splitlines():
        spans = sorted(_drop_contained_spans(find_skill_spans(line)), key=lambda s: (s[1], s[2]))
        chains: List[List[Tuple[str, int, int]]] = []
        chain: List[Tuple[str, int, int]] = []
        for sp in spans:
            if chain and _ALT_SEP_RE.match(line[chain[-1][2]:sp[1]]):
                chain.append(sp)
            else:
                if chain:
                    chains.append(chain)
                chain = [sp]
        if chain:
            chains.append(chain)
        for ch in chains:
            if len(ch) < 2:
                continue
            between = [line[ch[i][2]:ch[i + 1][1]] for i in range(len(ch) - 1)]
            has_or = any(_ALT_OR_RE.search(b) for b in between)
            slash_platforms = all("/" in b for b in between) and all(s[0] in WFM_PLATFORM_ALIASES for s in ch)
            example_lead = bool(_ALT_LEAD_RE.search(line[:ch[0][1]]))
            if has_or or slash_platforms or example_lead:
                names: List[str] = []
                for s in ch:
                    if s[0] not in names:
                        names.append(s[0])
                if len(names) >= 2 and names not in groups:
                    groups.append(names)
    return groups

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
    (r"^(?:what\s+makes\s+you\s+stand\s+out|what\s+will\s+help\s+you\s+stand\s+out|even\s+better\s+if\s+you\s+have|nice\s+to\s+haves?|preferred|bonus\s+points?|optional\s+garnishes)", "preferred"),
    # Multilingual preferred headings (Spanish, French, German)
    (r"^(?:requisitos\s+deseables|calificaciones\s+deseables|deseable|valoramos|atouts?(?:\s+suppl[eé]mentaires)?|souhait[eé]|comp[eé]tences?\s+souhait[eé]es?|un\s+plus|w[üu]nschenswert(?:e\s+qualifikationen)?)", "preferred"),
    (r"^(?:required|minimum|basic|essential|must[-\s]have|key|candidate)\s+(?:qualifications?|requirements?|skills?|experience|criteria)", "required"),
    (r"^(?:qualifications?|requirements?|what\s+you(?:\s+will)?\s+bring|what\s+we(?:\s+are|'re)\s+looking\s+for|what\s+we\s+require|who\s+you\s+are|skills\s+(?:&|and)\s+(?:experience|qualifications?)|candidate\s+profile)", "required"),
    # Multilingual required headings (Spanish, French, German)
    (r"^(?:requisitos(?:\s+m[ií]nimos|\s+indispensables|\s+obligatorios)?|calificaciones\s+requeridas|perfil\s+(?:requerido|del\s+candidato)|lo\s+que\s+buscamos"
     r"|qualifications?\s+requises?|profil\s+recherch[eé]|comp[eé]tences?\s+requises?|pr[eé]requis|ce\s+que\s+nous\s+recherchons"
     r"|anforderungen|erforderliche\s+qualifikationen|das\s+bringst\s+du\s+mit|was\s+du\s+mitbringst|was\s+sie\s+mitbringen|dein\s+profil|ihr\s+profil)", "required"),
    (r"^(?:your\s+(?:expertise|experience|background|qualifications|skills(?:\s+(?:&|and)\s+(?:competencies|experience))?)"
     r"|(?:here's\s+)?what\s+we(?:'re|\s+are)\s+looking\s+for|we're\s+excited\s+about\s+you(?:\s+(?:because|if))?"
     r"|you\s+(?:might|may)\s+be\s+a\s+(?:good\s+|great\s+)?fit\s+if|about\s+you|what\s+you(?:'ll)?\s+(?:need|have)"
     r"|what\s+skills\s+do\s+i\s+need|must[-\s]haves?|you\s+must\s+have|you'll\s+be\s+successful\s+in\s+this\s+role\s+if(?:\s+you\s+have)?"
     r"|do\s+you\s+have\s+the\s+right\s+ingredients|must[-\s]have\s+pantry\s+staples"
     r"|you\s+(?:will\s+)?(?:have|bring)|the\s+ideal\s+candidate|skills\s+(?:&|and)\s+competencies)", "required"),
    (r"^(?:responsibilities|key\s+responsibilities|job\s+responsibilities|essential\s+duties|duties|what\s+you(?:\s+will|'ll)\s+do|the\s+role|day[-\s]to[-\s]day|job\s+duties|role\s+overview|in\s+this\s+role)", "responsibilities"),
    # Multilingual responsibilities headings (Spanish, French, German)
    (r"^(?:responsabilidades(?:\s+principales)?|funciones(?:\s+principales)?|qu[eé]\s+har[aá]s"
     r"|missions?(?:\s+principales)?|vos\s+missions|responsabilit[eé]s"
     r"|aufgaben|deine\s+aufgaben|ihre\s+aufgaben|verantwortlichkeiten)", "responsibilities"),
    (r"^(?:a\s+typical\s+day|a\s+day\s+in\s+the\s+life|(?:here's\s+)?what\s+you'll\s+(?:do|be\s+doing|own|cook\s+up)(?:\s+day[-\s]to[-\s]day)?"
     r"|what\s+will\s+i\s+be\s+doing|your\s+day[-\s]to[-\s]day(?:\s+is)?|what\s+success\s+looks\s+like"
     r"|as\s+a\s+[\w\s-]+,\s+you\s+will|^you\s+will$"
     r"|you're\s+excited\s+about\s+this\s+opportunity\s+because\s+you\s+will|your\s+(?:role|responsibilities|impact))", "responsibilities"),
    (r"^(?:benefits|perks|compensation\s*(?:&|and)?\s*benefits|what\s+we\s+offer|(?:what\s+yo)?u'll\s+get|why\s+work\s+(?:here|with\s+us)|total\s+rewards|our\s+total\s+rewards)", "benefits"),
    (r"^(?:compensation|(?:base\s+|annual\s+)?(?:pay|salary)(?:\s+range)?|how\s+we'll\s+take\s+care\s+of\s+you|see\s+below\s+for\s+paid\s+time\s+off)", "benefits"),
    (r"^(?:about\s+(?:us|the\s+company)|company\s+overview|who\s+we\s+are)", "company"),
    (r"^(?:about\s+the\s+(?:job|role|position|opportunity)|what's\s+the\s+opportunity|job\s+summary|position\s+summary|summary|overview)", "summary"),
    (r"^(?:about\s+(?:the\s+)?team|the\s+community\s+you\s+will\s+join|about\s+[A-Za-z][\w&.'+ -]{1,30})$", "company"),
    # Logistics and legal boilerplate: never requirements.
    (r"^(?:where\s+you'll\s+be|(?:your\s+)?location|our\s+commitment\s+to|equal\s+(?:employment\s+)?opportunity"
     r"|we\s+expect\s+this\s+position\s+to\s+be\s+filled|join\s+us\b|unleash\s+your\s+potential|policies\b|how\s+\w+\s+uses\s+ai)", "other"),
]

_REQUIREMENT_LIKE_RE = re.compile(
    r"\b(?:degree|bachelor|master'?s|years?\s+of|\d+\+?\s*years?|experience\s+(?:with|in)|proficien|certif|licen[sc])",
    re.IGNORECASE,
)
_LIST_END_RE = re.compile(
    r"compensation|salary|pay\s+range|\boffice\b|in[-\s]office|\bhybrid\b|\bremote\b|location|benefits|equity|bonus"
    r"|privacy|disclaimer|^note\s*:|we\s+(?:hire|offer|value)|our\s+(?:customers|team|mission)",
    re.IGNORECASE,
)

_BOILERPLATE_LINE_RE = re.compile(
    r"equal\s+(?:employment\s+)?opportunity\s+employer|reasonable\s+accommodations?|applicant\s+privacy|privacy\s+(?:notice|policy)"
    r"|we\s+expect\s+this\s+position\s+to\s+be\s+filled|e-verify|does\s+not\s+discriminate|without\s+regard\s+to\s+(?:race|age|sex)",
    re.IGNORECASE,
)


def classify_section_header(line: str) -> Optional[str]:
    normalized = line.replace("\u2019", "'").replace("\u2018", "'").replace("\xa0", " ")
    if re.match(r"^\s*(?:[-*•▪▸]|\d+[.)])\s+", normalized) and len(normalized.split()) > 5:
        return None  # a bullet sentence such as "- Requirements gathering with stakeholders" is content, not a heading
    cleaned = re.sub(r"^[#*\-•\s]+|(?:[#*:_?\s]|\.\.\.|\u2026)+$", "", normalized).strip()
    if not cleaned or len(cleaned) > 80:
        return None
    # Must not look like a full sentence ending with a period
    if cleaned.endswith(".") and len(cleaned.split()) > 7:
        return None
    for pattern, bucket in _SECTION_HEADER_MAP:
        if re.search(pattern, cleaned, re.IGNORECASE):
            return bucket
    # Check parenthetical section labels such as "About this roll* (Responsibilities)" or "Optional garnishes (Preferred Qualifications)"
    paren_m = re.search(r"\(([^()]{3,45})\)\s*$", cleaned)
    if paren_m:
        inner = paren_m.group(1).strip()
        for pattern, bucket in _SECTION_HEADER_MAP:
            if re.search(pattern, inner, re.IGNORECASE):
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
    bucket_has_bullets = False
    summary_lines: List[str] = []
    company_lines: List[str] = []

    for line in lines:
        header_bucket = classify_section_header(line)
        if header_bucket:
            current_bucket = header_bucket
            bucket_has_bullets = False
            continue

        is_bullet = bool(re.match(r"^(?:[-*•▪▸]|\d+[.)])\s+", line))
        clean_item = re.sub(r"^(?:[-*•▪▸]|\d+[.)])\s*", "", line).strip()
        if not clean_item:
            continue

        # A plain paragraph after a bulleted list usually ends that list (pay, office policy, and legal text often
        # follow a requirements list without their own heading). Short requirement-like lines are kept.
        if current_bucket in ("required", "preferred", "responsibilities"):
            if is_bullet:
                bucket_has_bullets = True
            elif bucket_has_bullets and not _REQUIREMENT_LIKE_RE.search(clean_item) and (
                    len(clean_item.split()) > 30 or _LIST_END_RE.search(clean_item)):
                current_bucket = "other"
                continue

        # Legal/logistics boilerplate is never a requirement or duty
        if current_bucket in ("required", "preferred", "responsibilities") and _BOILERPLATE_LINE_RE.search(clean_item):
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

    if not rec.required_qualifications and rec.preferred_qualifications:
        rec.required_qualifications = rec.preferred_qualifications
        rec.preferred_qualifications = []

    rec.role_summary = " ".join(summary_lines[:4])[:900]
    rec.company_overview = " ".join(company_lines[:3])[:700]
    rec.detected_language = detect_posting_language(full_text)

    # 6. Years of Experience, Education, Certifications, Clearance, Work Auth, Travel
    _NON_EXP_YEARS_LINE_RE = re.compile(
        r"\b(?:\d{1,2}\s*\+?\s*years?\s+of\s+age|at\s+least\s+\d{1,2}\s+years?\s+old"
        r"|(?:for\s+(?:over|nearly|more\s+than)\s+\d{1,3}\s+years?)\s*,?\s*(?:we|our|the\s+company))\b",
        re.IGNORECASE,
    )
    yr_matches: List[re.Match[str]] = []
    for line in lines:
        if _NON_EXP_YEARS_LINE_RE.search(line):
            rec.negative_exclusions.append({
                "category": "non_experience_years",
                "term": "years",
                "quote": line,
                "reason": "Excluded age requirement or company-history duration from required experience years.",
            })
            continue
        for m in re.finditer(
            r"\b(\d{1,2})\s*(?:\+|(?:[-–]|to|a|à|bis)\s*(\d{1,2}))?\s*(?:years?|a[ñn]os?|ans?|jahre[n]?)\s+"
            r"(?:(?:of\s+|de\s+|d['\u2019]\s*|von\s+))?(?:[\wÀ-ÿ\-/&]+\s+){0,7}?"
            r"(?:experience|exp\b|background|track\s+record|experiencia|exp[eé]rience|berufserfahrung|erfahrung)",
            line,
            re.IGNORECASE,
        ):
            yr_matches.append(m)

    if yr_matches:
        mins = [float(m.group(1)) for m in yr_matches if float(m.group(1)) <= 25]
        maxs = [float(m.group(2)) for m in yr_matches if m.group(2) and float(m.group(2)) <= 30]
        if mins:
            rec.min_years_experience = max(mins)
        if maxs:
            rec.max_years_experience = max(maxs)

    # Education lines (with negative-idiom, negation, and multilingual support)
    _EDU_DEGREE_RE = re.compile(
        r"\b(?:Bachelor'?s?|Master'?s?|B\.?S\.?|M\.?S\.?|B\.?A\.?|M\.?B\.?A\.?|Ph\.?D\.?|Associate'?s?\s+degree"
        r"|High\s+School\s+Diploma|GED|degree\s+in|licenciatura|grado\s+universitario|t[ií]tulo\s+universitario"
        r"|dipl[oô]me\s+d'ing[eé]nieur|bac\s*\+\s*[345]|licence\s+ou\s+master"
        r"|hochschulabschluss|studium\s+der|bachelorabschluss|masterabschluss)\b",
        re.IGNORECASE,
    )
    for line in rec.required_qualifications + rec.preferred_qualifications + lines:
        clean_edu = re.sub(r"^(?:[-*•]|\d+[.)])\s*", "", line).strip()
        if not clean_edu or len(clean_edu) > 180:
            continue
        if _NON_DEGREE_IDIOM_RE.search(clean_edu):
            if not any(e["quote"] == clean_edu for e in rec.negative_exclusions):
                rec.negative_exclusions.append({
                    "category": "non_degree_idiom",
                    "term": "degree",
                    "quote": clean_edu,
                    "reason": "Excluded non-educational idiom ('degree of ownership / 360-degree') from degree gates.",
                })
            continue
        if _NEGATED_DEGREE_RE.search(clean_edu):
            if not any(e["quote"] == clean_edu for e in rec.negative_exclusions):
                rec.negative_exclusions.append({
                    "category": "negated_degree",
                    "term": "degree",
                    "quote": clean_edu,
                    "reason": "Degree explicitly stated as not required.",
                })
            continue
        if _EDU_DEGREE_RE.search(clean_edu):
            if clean_edu not in rec.education_requirements:
                rec.education_requirements.append(clean_edu)

    # Certifications (with negative mention check)
    for cert in KNOWN_CERTIFICATIONS:
        pat = rf"(?<!\w){re.escape(cert)}(?!\w)"
        if re.search(pat, full_text, re.IGNORECASE) and cert not in rec.certifications_licenses:
            negated, neg_line = is_negated_skill_mention(full_text, cert)
            if negated:
                rec.negative_exclusions.append({
                    "category": "negated_certification",
                    "term": cert,
                    "quote": neg_line,
                    "reason": f"Certification '{cert}' explicitly negated in requisition text.",
                })
                continue
            rec.certifications_licenses.append(cert)

    # Hard & Soft Skills (a longer name absorbs a contained one; ambiguous words need skills-style context; negated mentions excluded)
    present_skills = {sp[0] for sp in _drop_contained_spans(find_skill_spans(full_text))}
    for skill in KNOWN_HARD_SKILLS:
        if skill in present_skills and skill not in rec.hard_skills:
            negated, neg_line = is_negated_skill_mention(full_text, skill)
            if negated:
                rec.negative_exclusions.append({
                    "category": "negated_hard_skill",
                    "term": skill,
                    "quote": neg_line,
                    "reason": f"Skill '{skill}' explicitly negated in requisition text.",
                })
                continue
            rec.hard_skills.append(skill)
    rec.alternative_groups = find_alternative_groups(full_text)

    for s_skill in KNOWN_SOFT_SKILLS:
        if re.search(rf"\b{re.escape(s_skill)}\b", full_text, re.IGNORECASE):
            if s_skill not in rec.soft_skills:
                rec.soft_skills.append(s_skill)

    # Clearance, Work Auth, Travel (with negative & non-security clearance filtering)
    for line in lines:
        if re.search(r"\bclearance\b", line, re.IGNORECASE):
            if _NON_SECURITY_CLEARANCE_RE.search(line):
                rec.negative_exclusions.append({
                    "category": "non_security_clearance",
                    "term": "clearance",
                    "quote": line,
                    "reason": "Excluded non-security clearance ('inventory/medical/customs clearance') from security gates.",
                })
            elif _NEGATED_CLEARANCE_RE.search(line):
                rec.negative_exclusions.append({
                    "category": "negated_clearance",
                    "term": "security clearance",
                    "quote": line,
                    "reason": "Security clearance explicitly stated as not required.",
                })
            elif not rec.security_clearance and re.search(
                r"\b(?:security\s+clearance|top\s+secret|ts/sci|secret\s+clearance|public\s+trust|dod\s+clearance)\b",
                line,
                re.IGNORECASE,
            ):
                rec.security_clearance = line
        if not rec.work_authorization and re.search(r"\b(?:authorized\s+to\s+work|work\s+authorization|visa\s+sponsorship|sponsorship\s+is\s+not|u\.?s\.?\s+citizen(?:ship)?)\b", line, re.IGNORECASE):
            rec.work_authorization = line
        if not rec.travel_requirement and re.search(r"\b(?:travel\s+up\s+to|\d{1,2}%\s+travel|willingness\s+to\s+travel|overnight\s+travel)\b", line, re.IGNORECASE):
            rec.travel_requirement = line

    # 7. Detect Ambiguous Experience, Degree, Clearance, and Location Language
    rec.ambiguity_diagnostics = detect_requisition_ambiguities(rec)

    # 8. Build Direct `ats_config` & Extraction Provenance Ledger for `offline_ats_auditor.py`
    rec.ats_config = build_ats_config_from_record(rec)
    return rec


# ---------------------------------------------------------------------------
# ATS Config Builder (Bridges JobPostingRecord -> offline_ats_auditor.py)
# ---------------------------------------------------------------------------

SKILL_SYNONYM_MAP: Dict[str, List[str]] = {
    # Rule: a synonym must name the SAME capability, or be a specific case of a generic requirement
    # (JD says "SQL", resume says "PostgreSQL"). Competing products and different methods are never
    # synonyms: Tableau/Power BI, Snowflake/BigQuery, Verint/NICE/Genesys, Erlang C/Erlang A.
    # Those become gap questions for the candidate instead (references/wfm-taxonomy.md).
    "sql": ["PostgreSQL", "T-SQL", "MySQL", "PL/SQL"],
    "aws": ["Amazon Web Services", "EC2", "S3"],
    "gcp": ["Google Cloud", "BigQuery"],
    "azure": ["Microsoft Azure"],
    "kubernetes": ["EKS", "GKE", "AKS"],
    "p&l ownership": ["operating budget", "P&L management"],
    "demand forecasting": ["demand planning"],
    "forecasting": ["volume forecasting", "contact volume forecasting", "call volume forecasting", "workload forecasting", "demand forecasting"],
    "workforce capacity modeling": ["capacity planning"],
    "capacity planning": ["workforce capacity modeling"],
    "workforce management": ["WFM"],
    "nice iex": ["IEX"],
    "intraday management": ["real-time management", "real-time monitoring"],
    "real-time management": ["intraday management", "real-time monitoring"],
    "real-time adherence": ["schedule adherence", "adherence"],
    "schedule adherence": ["real-time adherence", "adherence"],
    "aht": ["average handle time"],
    "average handle time": ["AHT"],
    "average speed of answer": ["ASA"],
    "asa": ["average speed of answer"],
    "service level agreement": ["SLA"],
    "cross-functional leadership": ["cross-functional", "cross-functional collaboration"],
}


def detect_requisition_ambiguities(rec: JobPostingRecord) -> List[Dict[str, Any]]:
    """
    Detect ambiguous experience, degree, clearance, and location language in a requisition
    so callers can confirm or lock `--jd-config` before scoring candidates.
    """
    flags: List[Dict[str, Any]] = []
    lines = [ln.strip() for ln in rec.full_description_text.splitlines() if ln.strip()]
    full_text = rec.full_description_text

    # 1. Ambiguous Experience Language
    for line in lines:
        m_range = re.search(
            r"\b(\d{1,2})\s*(?:[-–]|to|a|à|bis)\s*(\d{1,2})\s*(?:years?|años?|ans?|jahre[n]?)\b",
            line,
            re.IGNORECASE,
        )
        if m_range:
            lo, hi = float(m_range.group(1)), float(m_range.group(2))
            if hi - lo >= 3.0 and hi <= 25.0:
                flags.append({
                    "category": "AMBIGUOUS_EXPERIENCE",
                    "rule_id": "AMB-EXP-RANGE",
                    "severity": "medium",
                    "quote": line,
                    "detail": f"Wide experience range ({int(lo)}–{int(hi)} years); confirm whether minimum floor ({int(lo)} yrs) or upper bound ({int(hi)} yrs) is the target benchmark.",
                })
        if re.search(
            r"\b(?:ideally|up\s+to|around|approximately|or\s+equivalent\s+experience)\b[^.;:\n]{0,30}\b\d{1,2}\+?\s*years?\b",
            line,
            re.IGNORECASE,
        ):
            flags.append({
                "category": "AMBIGUOUS_EXPERIENCE",
                "rule_id": "AMB-EXP-SOFT",
                "severity": "low",
                "quote": line,
                "detail": "Hedged or soft experience-year wording ('ideally / up to / or equivalent'); confirm exact min_years_required in --jd-config.",
            })

    # 2. Ambiguous Degree Language
    for edu_line in rec.education_requirements:
        if re.search(
            r"\b(?:or\s+(?:an?\s+)?equivalent|equivalent\s+(?:combination|practical|work|experience)"
            r"|o\s+experiencia\s+equivalente|ou\s+exp[eé]rience\s+[eé]quivalente|oder\s+(?:vergleichbare|gleichwertige))\b",
            edu_line,
            re.IGNORECASE,
        ):
            flags.append({
                "category": "AMBIGUOUS_DEGREE",
                "rule_id": "AMB-DEG-EQUIV",
                "severity": "info",
                "quote": edu_line,
                "detail": "Degree includes an equivalent-experience pathway; is_knockout_gate set to False so non-degreed candidates with equivalent tenure are not penalized.",
            })
        elif re.search(
            r"\b(?:preferred|nice\s+to\s+have|desired|optional|or\s+currently\s+pursuing|pursuing\s+a)\b",
            edu_line,
            re.IGNORECASE,
        ):
            flags.append({
                "category": "AMBIGUOUS_DEGREE",
                "rule_id": "AMB-DEG-OPTIONAL",
                "severity": "low",
                "quote": edu_line,
                "detail": "Degree is marked preferred or in-progress rather than a mandatory completed degree gate.",
            })

    # 3. Ambiguous Clearance Language
    if rec.security_clearance:
        if re.search(
            r"\b(?:ability\s+to\s+obtain|eligible\s+(?:to\s+obtain|for)|must\s+be\s+able\s+to\s+obtain|willingness\s+to\s+obtain|if\s+required)\b",
            rec.security_clearance,
            re.IGNORECASE,
        ):
            flags.append({
                "category": "AMBIGUOUS_CLEARANCE",
                "rule_id": "AMB-CLR-CONDITIONAL",
                "severity": "medium",
                "quote": rec.security_clearance,
                "detail": "Clearance is phrased as future eligibility ('ability to obtain') rather than active possession at application time.",
            })

    # 4. Ambiguous Location / Workplace Language
    has_remote = bool(re.search(r"\bremote\b", f"{rec.location} {rec.workplace_type}", re.IGNORECASE))
    onsite_conflict_line = next(
        (
            ln for ln in lines
            if re.search(
                r"\b(?:in[-\s]office\s+\d\s+days?|on[-\s]?site\s+\d\s+days?|must\s+reside\s+within\s+\d+\s+miles|hybrid\s+schedule|commuting\s+distance)\b",
                ln,
                re.IGNORECASE,
            )
        ),
        "",
    )
    if has_remote and onsite_conflict_line:
        flags.append({
            "category": "AMBIGUOUS_LOCATION",
            "rule_id": "AMB-LOC-CONFLICT",
            "severity": "medium",
            "quote": onsite_conflict_line,
            "detail": "Posting header indicates 'Remote' but body text specifies in-office days, hybrid attendance, or commuting-radius constraints.",
        })

    return flags


def _locate_skill_provenance(
    terms: List[str],
    rec: JobPostingRecord,
) -> Tuple[str, str, float, str, List[str]]:
    """
    Find the best matching section, exact source quote, confidence score, certainty label,
    and per-item ambiguity flags for a skill or group of terms in `rec`.
    """
    patterns = [re.compile(rf"(?<!\w){re.escape(t)}(?!\w)", re.IGNORECASE) for t in terms]
    amb_flags: List[str] = []

    def _scan_lines(line_list: List[str]) -> Optional[str]:
        for ln in line_list:
            if any(p.search(ln) for p in patterns):
                return ln
        return None

    q_req = _scan_lines(rec.required_qualifications)
    if q_req:
        conf = 0.94
        if re.search(r"\b(?:familiarity|exposure|nice\s+to\s+have|plus|preferred)\b", q_req, re.IGNORECASE):
            conf = 0.78
            amb_flags.append("hedged_proficiency_in_required_section")
        cert = "HIGH" if conf >= 0.85 else "MODERATE"
        return "required_section", q_req, conf, cert, amb_flags

    q_pref = _scan_lines(rec.preferred_qualifications)
    if q_pref:
        return "preferred_section", q_pref, 0.88, "HIGH", amb_flags

    q_resp = _scan_lines(rec.responsibilities)
    if q_resp:
        amb_flags.append("inferred_from_responsibilities_not_qualifications")
        return "responsibilities_section", q_resp, 0.72, "MODERATE", amb_flags

    all_lines = [ln.strip() for ln in rec.full_description_text.splitlines() if ln.strip()]
    q_any = _scan_lines(all_lines)
    amb_flags.append("inferred_from_narrative_summary")
    return "summary_narrative", (q_any or ""), 0.58, "LOW_INFERRED", amb_flags


def build_ats_config_from_record(rec: JobPostingRecord) -> Dict[str, Any]:
    """
    Generate the `--jd-config` JSON structure consumed by `offline_ats_auditor.py`,
    enriched with per-requirement confidence, certainty, extraction_status ('INFERRED'),
    source_quote, provenance_section, provenance_rule, and ambiguity_flags.
    """
    requirements: List[Dict[str, Any]] = []
    seen: set[str] = set()

    req_text_block = "\n".join(rec.required_qualifications)
    pref_text_block = "\n".join(rec.preferred_qualifications)

    # Pre-mark generic 'six sigma' as seen if a specific Six Sigma belt or Lean Six Sigma is present
    all_detected_lower = {s.lower() for s in rec.hard_skills + rec.certifications_licenses}
    if "six sigma" in all_detected_lower and any(
        b in all_detected_lower for b in ("six sigma black belt", "six sigma green belt", "lean six sigma")
    ):
        seen.add("six sigma")

    # 0. JD-offered alternatives ("NICE, Verint, or Genesys"): one requirement, any listed option earns full credit
    for group in rec.alternative_groups:
        members = [m for m in group if m.lower() not in seen]
        if len(members) < 2:
            continue
        member_keys = {m.lower() for m in members}
        in_req = any(re.search(re.escape(m), req_text_block, re.IGNORECASE) for m in members)
        in_pref = any(re.search(re.escape(m), pref_text_block, re.IGNORECASE) for m in members)
        syns: List[str] = []
        for m in members:
            for s in SKILL_SYNONYM_MAP.get(m.lower(), []):
                if s.lower() not in member_keys and s not in syns:
                    syns.append(s)
        seen.update(member_keys)
        # Also mark bidirectional acronym synonyms as seen to avoid duplicate extraction
        for s in syns:
            if s.lower() in SKILL_SYNONYM_MAP and members[0].lower() in [x.lower() for x in SKILL_SYNONYM_MAP[s.lower()]]:
                seen.add(s.lower())

        prov_sec, quote, conf, cert, item_amb = _locate_skill_provenance(members, rec)
        requirements.append(
            {
                "name": members[0],
                "category": "hard_skill",
                "tier": "preferred" if (in_pref and not in_req) else "required",
                "synonyms": syns,
                "alternatives": members[1:],
                "is_knockout_gate": False,
                "confidence": conf,
                "certainty": cert,
                "extraction_status": "INFERRED",
                "source_quote": quote,
                "provenance_section": prov_sec,
                "provenance_rule": "alternative_chain_or",
                "ambiguity_flags": item_amb,
            }
        )

    # 1. Add matched hard skills with tier detection and parenthetical acronym deduplication
    for hs in rec.hard_skills:
        key = hs.lower()
        if key in seen:
            continue
        in_pref_only = bool(re.search(re.escape(hs), pref_text_block, re.IGNORECASE)) and not bool(
            re.search(re.escape(hs), req_text_block, re.IGNORECASE)
        )
        tier = "preferred" if in_pref_only else "required"
        cat = "title_scope" if hs in ("P&L Ownership", "Budgeting") else "hard_skill"
        syns = list(SKILL_SYNONYM_MAP.get(key, []))
        seen.add(key)
        # Prevent double-extracting a full term and its own bidirectional acronym (e.g. 'Average Speed of Answer (ASA)')
        for s in syns:
            s_low = s.lower()
            if s_low in SKILL_SYNONYM_MAP and key in [x.lower() for x in SKILL_SYNONYM_MAP[s_low]]:
                seen.add(s_low)

        prov_sec, quote, conf, cert, item_amb = _locate_skill_provenance([hs], rec)
        requirements.append(
            {
                "name": hs,
                "category": cat,
                "tier": tier,
                "synonyms": syns,
                "is_knockout_gate": False,
                "confidence": conf,
                "certainty": cert,
                "extraction_status": "INFERRED",
                "source_quote": quote,
                "provenance_section": prov_sec,
                "provenance_rule": "taxonomy_bullet_match",
                "ambiguity_flags": item_amb,
            }
        )

    # 2. Add certifications
    for cert_name in rec.certifications_licenses:
        key = cert_name.lower()
        if key in seen:
            continue
        in_pref = bool(re.search(re.escape(cert_name), pref_text_block, re.IGNORECASE)) and not bool(
            re.search(re.escape(cert_name), req_text_block, re.IGNORECASE)
        )
        seen.add(key)
        prov_sec, quote, conf, cert_lbl, item_amb = _locate_skill_provenance([cert_name], rec)
        requirements.append(
            {
                "name": cert_name,
                "category": "hard_skill",
                "tier": "preferred" if in_pref else "required",
                "synonyms": SKILL_SYNONYM_MAP.get(key, []),
                "is_knockout_gate": False,
                "confidence": conf,
                "certainty": cert_lbl,
                "extraction_status": "INFERRED",
                "source_quote": quote,
                "provenance_section": prov_sec,
                "provenance_rule": "certification_match",
                "ambiguity_flags": item_amb,
            }
        )

    # 3. Add soft skills
    for ss in rec.soft_skills:
        key = ss.lower()
        if key in seen:
            continue
        seen.add(key)
        prov_sec, quote, conf, cert_lbl, item_amb = _locate_skill_provenance([ss], rec)
        requirements.append(
            {
                "name": ss,
                "category": "soft_skill",
                "tier": "required",
                "synonyms": SKILL_SYNONYM_MAP.get(key, []),
                "is_knockout_gate": False,
                "confidence": conf,
                "certainty": cert_lbl,
                "extraction_status": "INFERRED",
                "source_quote": quote,
                "provenance_section": prov_sec,
                "provenance_rule": "soft_skill_match",
                "ambiguity_flags": item_amb,
            }
        )

    # 4. Add degree requirement (check equivalency & optionality before marking as hard knockout gate)
    edu_joined = " ".join(rec.education_requirements)
    if re.search(
        r"\b(?:bachelor|b\.?s\.?|b\.?a\.?|licenciatura|grado\s+universitario|dipl[oô]me\s+d'ing[eé]nieur|bac\s*\+\s*[345]|licence|hochschulabschluss|studium)\b",
        edu_joined,
        re.IGNORECASE,
    ) and "b.s." not in seen:
        seen.add("b.s.")
        edu_quote = rec.education_requirements[0] if rec.education_requirements else ""
        has_equiv = bool(
            re.search(
                r"\b(?:or\s+(?:an?\s+)?equivalent|equivalent\s+(?:combination|practical|work|experience)"
                r"|o\s+experiencia\s+equivalente|ou\s+exp[eé]rience\s+[eé]quivalente|oder\s+(?:vergleichbare|gleichwertige))\b",
                edu_joined,
                re.IGNORECASE,
            )
        )
        is_pref_edu = bool(re.search(r"\b(?:preferred|desired|optional|pursuing)\b", edu_joined, re.IGNORECASE)) and not bool(
            re.search(r"\b(?:bachelor|b\.?s\.?|licenciatura|licence|studium)\b", req_text_block, re.IGNORECASE)
        )
        deg_amb: List[str] = []
        if has_equiv:
            deg_amb.append("degree_has_equivalent_experience_path")
        if is_pref_edu:
            deg_amb.append("degree_marked_preferred_or_in_progress")
        requirements.append(
            {
                "name": "B.S.",
                "category": "hard_skill",
                "tier": "preferred" if is_pref_edu else "required",
                "synonyms": ["Bachelor", "Bachelor's", "B.A.", "Master's", "M.S.", "MBA", "Licenciatura", "Licence", "Hochschulabschluss"],
                "is_knockout_gate": not (has_equiv or is_pref_edu),
                "confidence": 0.90 if not deg_amb else 0.78,
                "certainty": "HIGH" if not deg_amb else "MODERATE",
                "extraction_status": "INFERRED",
                "source_quote": edu_quote,
                "provenance_section": "preferred_section" if is_pref_edu else "required_section",
                "provenance_rule": "degree_gate_regex",
                "ambiguity_flags": deg_amb,
            }
        )

    # 5. If heuristic bullet phrases exist that weren't in KNOWN_HARD_SKILLS, extract top clean phrases as LOW_INFERRED
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
                            "confidence": 0.58,
                            "certainty": "LOW_INFERRED",
                            "extraction_status": "INFERRED",
                            "source_quote": line,
                            "provenance_section": "preferred_section" if tier_name == "preferred" else "required_section",
                            "provenance_rule": "heuristic_phrase_fallback",
                            "ambiguity_flags": ["unlisted_heuristic_phrase_confirm_in_jd_config"],
                        }
                    )

    # 6. Populate extraction ledger and overall extraction confidence summary on `rec`
    rec.extraction_ledger = [dict(r) for r in requirements]
    rec.confirmed_requirements_count = sum(1 for r in requirements if r.get("extraction_status") == "CONFIRMED")
    rec.inferred_requirements_count = sum(1 for r in requirements if r.get("extraction_status") == "INFERRED")

    if requirements:
        mean_item_conf = sum(float(r.get("confidence", 0.75)) for r in requirements) / len(requirements)
    else:
        mean_item_conf = 0.40

    # Structural modifiers for overall extraction confidence
    struct_penalty = 0.0
    if not rec.required_qualifications:
        struct_penalty += 0.15
    if not rec.preferred_qualifications:
        struct_penalty += 0.05
    if any(r.get("certainty") == "LOW_INFERRED" for r in requirements):
        struct_penalty += 0.08
    if any(a.get("severity") == "medium" for a in rec.ambiguity_diagnostics):
        struct_penalty += 0.06

    overall_conf = round(max(0.10, min(1.0, mean_item_conf - struct_penalty)), 3)
    rec.extraction_confidence_score = overall_conf
    if overall_conf >= 0.80:
        rec.extraction_confidence_band = "HIGH (>=0.80)"
    elif overall_conf >= 0.65:
        rec.extraction_confidence_band = "MODERATE (0.65-0.79)"
    else:
        rec.extraction_confidence_band = "LOW (<0.65 — Confirm --jd-config Required)"

    rec.recommend_confirmed_jd_config = (
        overall_conf < 0.80
        or any(r.get("certainty") == "LOW_INFERRED" for r in requirements)
        or any(a.get("severity") == "medium" for a in rec.ambiguity_diagnostics)
    )

    return {
        "extraction_metadata": {
            "detected_language": rec.detected_language,
            "extraction_confidence_score": rec.extraction_confidence_score,
            "extraction_confidence_band": rec.extraction_confidence_band,
            "recommend_confirmed_jd_config": rec.recommend_confirmed_jd_config,
            "confirmed_requirements_count": rec.confirmed_requirements_count,
            "inferred_requirements_count": rec.inferred_requirements_count,
            "ambiguity_diagnostics": rec.ambiguity_diagnostics,
            "negative_exclusions": rec.negative_exclusions,
        },
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

    if mode == "ashby_api":
        data = fetch_res["data"]
        board = str(fetch_res.get("board") or data.get("departmentName") or "")
        content_text = (str(data.get("descriptionPlain") or "")).strip() or html_fragment_to_text(str(data.get("descriptionHtml") or ""))
        comp_obj = data.get("compensation") or {}
        comp_summary = str(comp_obj.get("scrapeableCompensationSalarySummary") or comp_obj.get("compensationTierSummary") or "").strip()
        header_block = (
            f"Job Title: {data.get('title', '')}\n"
            f"Company: {board}\n"
            f"Location: {data.get('location', '')}\n"
            f"Workplace Type: {data.get('workplaceType', '')}\n"
            f"Job ID: {data.get('id', '')}\n"
            + (f"Salary: {comp_summary}\n" if comp_summary else "")
            + f"\n{content_text}"
        )
        rec = parse_job_posting_text(header_block, url=url, platform_hint="Ashby ATS")
        rec.job_id = str(data.get("id") or rec.job_id)
        rec.company_name = board or rec.company_name
        rec.workplace_type = str(data.get("workplaceType") or rec.workplace_type)
        rec.date_posted = str(data.get("publishedAt") or rec.date_posted)
        rec.apply_url = str(data.get("applyUrl") or data.get("jobUrl") or url)
        if comp_summary and not rec.salary.raw_text:
            rec.salary.raw_text = comp_summary
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
            "",
            "## 7. Extraction Provenance & Certainty Ledger (Confirmed vs. Inferred)",
            "",
            f"- **Detected Language:** `{rec.detected_language}`",
            f"- **Overall Extraction Confidence:** `{rec.extraction_confidence_score:.1%}` (`{rec.extraction_confidence_band}`)",
            f"- **Confirmed vs. Inferred Split:** `{rec.confirmed_requirements_count}` CONFIRMED | `{rec.inferred_requirements_count}` INFERRED",
            f"- **Recommend Confirmed `--jd-config` Review:** `{'YES — review & lock --jd-config before scoring candidates' if rec.recommend_confirmed_jd_config else 'No (high structural certainty)'}`",
            "",
        ]
    )

    if rec.extraction_ledger:
        lines.extend([
            "| # | Requirement | Tier | Status | Certainty (`Conf`) | Provenance Section (`Rule`) | Source Quote |",
            "| :-: | :--- | :--- | :--- | :-: | :--- | :--- |",
        ])
        for idx, item in enumerate(rec.extraction_ledger, start=1):
            alts = item.get("alternatives") or []
            disp = f"{item['name']} (or {', '.join(alts)})" if alts else item["name"]
            q_str = (item.get("source_quote") or "").replace("|", "\\|")
            if len(q_str) > 95:
                q_str = q_str[:92] + "..."
            lines.append(
                f"| {idx} | **{disp}** | `{item.get('tier', 'required')}` | `{item.get('extraction_status', 'INFERRED')}` | "
                f"**{item.get('certainty', 'HIGH')}** (`{float(item.get('confidence', 1.0)):.0%}`) | "
                f"`{item.get('provenance_section', '')}` (`{item.get('provenance_rule', '')}`) | \"{q_str}\" |"
            )
        lines.append("")

    if rec.ambiguity_diagnostics or rec.negative_exclusions:
        lines.append("## 8. Ambiguity & Negative-Context Diagnostics")
        lines.append("")
        for amb in rec.ambiguity_diagnostics:
            lines.append(
                f"- **[{amb['rule_id']}] {amb['category']} ({amb['severity']}):** {amb['detail']} — Quote: \"{amb['quote']}\""
            )
        for neg in rec.negative_exclusions:
            lines.append(
                f"- **[NEGATIVE EXCLUSION: `{neg['category']}`] `{neg['term']}`:** {neg['reason']} — Quote: \"{neg['quote']}\""
            )
        lines.append("")

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

DEFAULT_USER_AGENT = "ops-requisition-auditor/1.5.0 (public-API-only collector)"

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
    parser = argparse.ArgumentParser(prog="ops-req-collect", description="ops-requisition-auditor: job posting collector & structured requisition parser")
    parser.add_argument("--url", type=str, help="Employer-published job posting URL (Greenhouse, Lever, Ashby, career page)")
    parser.add_argument("--input-file", "--jd-file", "--jd-text", dest="input_file", type=str, help="Path to saved HTML or plain-text job posting file")
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
        in_path = args.input_file
        if not os.path.exists(in_path):
            repo_cand = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), in_path)
            if os.path.exists(repo_cand):
                in_path = repo_cand
        with open(in_path, "r", encoding="utf-8") as f:
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
