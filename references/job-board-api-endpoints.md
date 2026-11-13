# Employer ATS Endpoints for Posting Collection

These are the sources `scripts/job_posting_collector.py --url` reads. They are published by employers or their ATS vendor for reading job postings, need no login, and are used with an honest User-Agent. Keep use low-volume and personal.

## Not fetched: LinkedIn, Indeed, ZipRecruiter, Glassdoor
Those sites' terms restrict automated access, so neither the scripts nor the skill fetch them. Ask the user to paste the description (or save the page and use `--input-file`). If a web search tool is available, a search for the job title plus company can often find the same posting on the employer's own careers page or Greenhouse/Lever board, which can then be fetched below. Never attempt to solve, click through, wait out, or work around CAPTCHAs, login walls, or rate limits.

## 1. Greenhouse Board API (implemented)
`https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}?questions=true`
- Converts from `boards.greenhouse.io/{board_token}/jobs/{job_id}` or `job-boards.greenhouse.io/{board_token}/jobs/{job_id}`.
- Fields: `title`, `company_name`, `location.name`, `updated_at`, `content` (HTML-escaped full JD), `pay_input_ranges` (structured min/max/currency), `departments`, `offices`.

## 2. Lever Postings API (implemented)
`https://api.lever.co/v0/postings/{company}/{job_id}?mode=json`
- Converts from `jobs.lever.co/{company}/{job_id}`.
- Fields: `text` (title), `categories` (`location`, `department`, `commitment`, `team`), `workplaceType`, `descriptionPlain`, `lists` (array of `{text, content}` requirement sections), `salaryRange` (`min`, `max`, `currency`, `interval`), `applyUrl`.

## 3. Embedded `Schema.org/JobPosting` (implemented as the page-fetch fallback)
Many employer career pages (including Ashby, Workday, and iCIMS-hosted pages) embed `<script type="application/ld+json">` with `title`, `hiringOrganization.name`, `jobLocation`, `baseSalary`, `employmentType`, `datePosted`, `validThrough`, and `description`. The collector reads this when it fetches a public career page.

## 4. SmartRecruiters (documented, not implemented)
`https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}` is a public posting API. The script does not call it yet; use pasted text or the page fetch above.

## 5. Ashby Public Job Board API (implemented in v1.4.0)
`https://api.ashbyhq.com/posting-api/job-board/{board_name}?includeCompensation=true`
- Converts from `jobs.ashbyhq.com/{board_name}[/{job_id}]`.

## 6. USAJobs Official Federal Open Data API (implemented in v1.4.0)
`https://data.usajobs.gov/api/search?Keyword={kw}&LocationName={loc}&ResultsPerPage=25&Page=1&Fields=full`
- Requires `USAJOBS_API_KEY` and `USAJOBS_USER_AGENT` environment variables and preserves mandatory OPM attribution.
