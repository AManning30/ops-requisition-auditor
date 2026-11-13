# Public Job Board & Enterprise ATS Endpoints

Use these compliant, unauthenticated endpoints and search-index pivots to retrieve structured job postings when aggregator HTML pages serve anti-bot challenges.

## 1. LinkedIn Public Guest APIs

LinkedIn exposes unauthenticated guest endpoints that return clean HTML fragments without login walls:

- **Job Search Endpoint:**
  `https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={urlencoded_keywords}&location={urlencoded_location}&start={offset}`
  - Returns `<li>` job cards containing `data-entity-urn="urn:li:jobPosting:{job_id}"`.
  - Key selectors: `.base-search-card__title` (title), `.base-search-card__subtitle` (company), `.job-search-card__location` (location), `.job-search-card__salary-info` (salary badge), `<time datetime="...">` (posted date).

- **Single Job Posting Detail Endpoint:**
  `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}`
  - Extract `job_id` from `/jobs/view/{slug}-{job_id}`, `/jobs/view/{job_id}`, or `?currentJobId={job_id}`.
  - Key selectors:
    - Title: `h2.top-card-layout__title`
    - Company: `a.topcard__org-name-link`
    - Location: `span.topcard__flavor--bullet`
    - Posted age: `span.posted-time-ago__text`
    - Compensation badge: `.compensation__salary`
    - Full JD HTML: `div.show-more-less-html__markup`
    - Criteria items (Seniority level, Employment type, Job function, Industries): `h3.description__job-criteria-subheader` paired with `span.description__job-criteria-text`

## 2. Public Enterprise ATS JSON APIs

Most roles syndicated on Indeed, LinkedIn, and ZipRecruiter originate from an employer ATS whose public API returns full JSON with structured compensation:

- **Greenhouse Board API:**
  `https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}?questions=true`
  - Converts from `boards.greenhouse.io/{board_token}/jobs/{job_id}` or `job-boards.greenhouse.io/{board_token}/jobs/{job_id}`.
  - Fields: `title`, `company_name`, `location.name`, `updated_at`, `content` (HTML-escaped full JD), `pay_input_ranges` (structured min/max/currency), `departments`, `offices`.

- **Lever Postings API:**
  `https://api.lever.co/v0/postings/{company}/{job_id}?mode=json`
  - Converts from `jobs.lever.co/{company}/{job_id}`.
  - Fields: `text` (title), `categories` (`location`, `department`, `commitment`, `team`), `workplaceType`, `descriptionPlain`, `lists` (array of `{text, content}` requirement sections), `salaryRange` (`min`, `max`, `currency`, `interval`), `applyUrl`.

- **SmartRecruiters Public Posting API:**
  `https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}`

- **Ashby / Workday / iCIMS:**
  Check embedded `<script type="application/ld+json">` for `Schema.org/JobPosting` (`title`, `hiringOrganization.name`, `jobLocation`, `baseSalary`, `employmentType`, `datePosted`, `validThrough`, `description`).

## 3. Cross-Board Resolution for Gated Aggregators (Indeed / ZipRecruiter / Glassdoor)

When a direct URL on Indeed (`indeed.com/viewjob?jk=...`) or ZipRecruiter (`ziprecruiter.com/c/...`) is gated by Cloudflare Turnstile/403:

1. **Never attempt to solve, click, wait out, or bypass CAPTCHA/Turnstile challenges.**
2. **Query Search Indexes First:** Run `web_search` with the exact job key or URL slug (e.g., `"{jk_id}" site:indeed.com` or `site:indeed.com/viewjob "{role_title}" "{company}"` / `site:ziprecruiter.com/c/ "{role_title}"`). Search snippets frequently preserve the title, company, location, salary badge, and core qualifications.
3. **Pivot to Canonical ATS or LinkedIn Guest API:** Once `(Job Title, Company Name)` is known from the URL slug, search snippet, or user prompt, query `site:boards.greenhouse.io OR site:jobs.lever.co "{company}" "{job_title}"` or search the LinkedIn Guest Search API (`seeMoreJobPostings/search`) to pull the complete unblocked job description.
