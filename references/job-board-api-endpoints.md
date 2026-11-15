# Employer Requisition Endpoints for Posting Collection

These are the sources `scripts/job_posting_collector.py --url` reads. They are published by employers or their job-board vendor for reading job postings, need no login, and are used with an honest User-Agent. Keep use low-volume and personal.

## Not fetched (10 Paste-Only Boards): LinkedIn, Indeed, SimplyHired, ZipRecruiter, Glassdoor, Monster, CareerBuilder, Dice, Built In, Wellfound/AngelList
Those sites' terms restrict automated access (`linkedin.com`, `indeed.com`, `simplyhired.com`, `ziprecruiter.com`, `glassdoor.com`, `monster.com`, `careerbuilder.com`, `dice.com`, `builtin.com`, `wellfound.com`, `angel.co`), so neither the scripts nor the skill fetch them (`enforce_domain_compliance(url)` blocks them pre-socket with `ComplianceBlockError` / exit code 2). Ask the user to paste the description (or save the page and use `--input-file`). If a web search tool is available, a search for the job title plus company can often find the same posting on the employer's own careers page or Greenhouse/Lever/Ashby board, which can then be fetched below. Never attempt to solve, click through, wait out, or work around CAPTCHAs, login walls, or rate limits.

## 1. Greenhouse Board API (implemented)
`https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}?questions=true` (single job) and `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true` (board listing)
- Converts from `boards.greenhouse.io/{board_token}[/jobs/{job_id}]` or `job-boards.greenhouse.io/{board_token}[/jobs/{job_id}]`, or via `--greenhouse {board_token}`.
- Fields: `title`, `company_name`, `location.name`, `updated_at`, `content` (HTML-escaped full JD), `pay_input_ranges` (structured min/max/currency), `departments`, `offices`.

## 2. Lever Postings API — US & EU (implemented)
`https://api.lever.co/v0/postings/{company}/{job_id}?mode=json` (US) and `https://api.eu.lever.co/v0/postings/{company}/{job_id}?mode=json` (EU)
- Converts from `jobs.lever.co/{company}[/{job_id}]` or `jobs.eu.lever.co/{company}[/{job_id}]`, or via `--lever {company}` (automatically falls back from US to EU on HTTP 404).
- Fields: `text` (title), `categories` (`location`, `department`, `commitment`, `team`), `workplaceType`, `descriptionPlain`, `lists` (array of `{text, content}` requirement sections), `salaryRange` (`min`, `max`, `currency`, `interval`), `applyUrl`.

## 3. Ashby Public Job Board API (implemented)
`https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true`
- Converts from `jobs.ashbyhq.com/{board_name}[/{job_id}]`, or via `--ashby {board_name}`.
- Fields: `jobs[]` (`id`, `title`, `location`, `workplaceType`, `publishedAt`, `jobUrl`, `descriptionPlain`, `descriptionHtml`, `compensation.compensationTierSummary`).

## 4. USAJobs Official Federal Open Data API (implemented)
`https://data.usajobs.gov/api/search?Keyword={kw}&LocationName={loc}&ResultsPerPage=25&Page=1&Fields=full`
- Invoked via `--usajobs "{keyword}" [--location "{loc}"]`. Requires `USAJOBS_API_KEY` and `USAJOBS_USER_AGENT` environment variables and preserves mandatory OPM attribution.

## 5. Embedded `Schema.org/JobPosting` (implemented as the page-fetch fallback)
Many employer career pages (including Workday and iCIMS-hosted pages) embed `<script type="application/ld+json">` with `title`, `hiringOrganization.name`, `jobLocation`, `baseSalary`, `employmentType`, `datePosted`, `validThrough`, and `description`. The collector reads this when it fetches a public career page, and only if that site's `robots.txt` allows the fetch (`robots_allows()`; otherwise it declines and asks for pasted text).

## 6. SmartRecruiters (documented, not implemented)
`https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}` is a public posting API. The script does not call it yet; use pasted text or the page fetch above.
