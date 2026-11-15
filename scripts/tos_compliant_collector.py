#!/usr/bin/env python3
"""
ops-requisition-auditor: Public-API Job Posting Collector & Terms-of-Service Router.
Permitted automated APIs: Greenhouse, Lever (US + EU), Ashby, USAJobs.
Blocked from automated HTTP (manual paste/file only):
  Monster, CareerBuilder, Dice, Built In, Wellfound, SimplyHired, Indeed.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import json
import os
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_USER_AGENT = "ops-requisition-auditor/1.5.0 (public-API-only collector)"

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
    decoded = html.unescape(html.unescape(raw_html))
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


# ---------------------------------------------------------------------------
# 1. GREENHOUSE (Public Job Board API - GET requires no authentication)
# ---------------------------------------------------------------------------
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
                compensation="",  # Greenhouse embeds pay bands inside HTML content when posted
                url=job.get("absolute_url") or "",
                published_at=job.get("updated_at") or job.get("first_published") or "",
                description_text=html_to_plain_text(job.get("content")),
            )
        )
    return results


# ---------------------------------------------------------------------------
# 2. LEVER (Public Postings API - US + automatic EU instance fallback)
# ---------------------------------------------------------------------------
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
                time.sleep(1.0)  # Polite paging delay
            return postings
        except urllib.error.HTTPError as exc:
            if exc.code == 404 and idx == 0:
                continue  # Board may live on the other regional Lever instance
            raise
    return []


# ---------------------------------------------------------------------------
# 3. ASHBY (Public Job Postings API - unauthenticated per-board JSON)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 4. USAJOBS (Official Federal Open Data REST API - requires free API key)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 5. URL ROUTER & MANUAL FALLBACK (for ToS-restricted boards)
# ---------------------------------------------------------------------------
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


def main() -> int:
    ap = argparse.ArgumentParser(prog="ops-req-api-collect", description="ops-requisition-auditor: public-API job posting collector (restricted boards are paste-only)")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--url", help="Greenhouse, Lever, or Ashby job/board URL (blocks restricted boards)")
    grp.add_argument("--greenhouse", metavar="BOARD_TOKEN", help="Fetch all public jobs for a Greenhouse board token")
    grp.add_argument("--lever", metavar="SITE_SLUG", help="Fetch all public jobs for a Lever site slug")
    grp.add_argument("--ashby", metavar="BOARD_NAME", help="Fetch all public jobs for an Ashby board name")
    grp.add_argument("--usajobs", metavar="KEYWORD", help="Search USAJobs API by keyword (needs env vars)")
    grp.add_argument("--jd-file", metavar="PATH", help="Read manually copied JD text from local file (for restricted boards)")
    grp.add_argument("--stdin", action="store_true", help="Read manually copied JD text from standard input")

    ap.add_argument("--location", default="", help="Optional location filter (USAJobs only)")
    ap.add_argument("--out", metavar="JSON_PATH", help="Optional path to save normalized JSON array")
    args = ap.parse_args()

    try:
        if args.url:
            jobs = collect_from_url(args.url)
        elif args.greenhouse:
            jobs = fetch_greenhouse(args.greenhouse)
        elif args.lever:
            jobs = fetch_lever(args.lever)
        elif args.ashby:
            jobs = fetch_ashby(args.ashby)
        elif args.usajobs:
            jobs = fetch_usajobs(args.usajobs, location=args.location)
        elif args.jd_file:
            with open(args.jd_file, "r", encoding="utf-8") as fh:
                jobs = [load_manual_jd(fh.read())]
        else:
            jobs = [load_manual_jd(sys.stdin.read())]
    except ComplianceBlockError as exc:
        print(f"[COMPLIANCE GATE] {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    serialized = json.dumps([j.to_dict() for j in jobs], indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(serialized + "\n")
        print(f"Saved {len(jobs)} posting(s) to {args.out}", file=sys.stderr)
    else:
        print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
