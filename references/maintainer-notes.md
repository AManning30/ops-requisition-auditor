# Maintainer Notes (read only when modifying or debugging the scripts)

## CLI recipes
```bash
# Audit a resume against a pasted or saved job description (works for any source)
python scripts/offline_ats_auditor.py --resume path/to/Resume.docx --jd-text job_description.txt \
  --output audit_report.md --json-out audit_report.json

# Audit against an employer-published posting (Greenhouse, Lever, or a career page with JobPosting metadata)
python scripts/offline_ats_auditor.py --resume Resume.docx \
  --jd-url "https://boards.greenhouse.io/<board>/jobs/<id>" --output audit_report.md

# Audit against a confirmed JD config (most accurate: you control the requirement list)
python scripts/offline_ats_auditor.py --resume Resume.docx --jd-config jd_config.json \
  --reference-date 2026-11-01 --output audit_report.md

# Parse a pasted or saved posting into structured fields and a ready-made jd_config.json
python scripts/job_posting_collector.py --input-file job_description.txt \
  --output posting.md --ats-config-out jd_config.json

# Export an edited .docx that preserves Word paragraph styling
python scripts/offline_ats_auditor.py --resume Resume.docx --jd-config jd_config.json \
  --replacements-json replacements.json --export-docx Resume_Optimized.docx
```
`python scripts/offline_ats_auditor.py --demo` and `python scripts/job_posting_collector.py --demo` run built-in fixtures and are the quickest regression checks.

## Collector scope (changed in 1.3.0 / 1.4.0)
- Fetches only compliant public APIs and employer-published postings: Greenhouse (`boards-api.greenhouse.io`), Lever (US `api.lever.co` + EU `api.eu.lever.co`), Ashby (`api.ashbyhq.com`), and USAJobs (`data.usajobs.gov` with `USAJOBS_API_KEY` + `USAJOBS_USER_AGENT`), plus direct career-page fetches that read embedded `Schema.org/JobPosting` JSON-LD data. SmartRecruiters has a documented public API but is not implemented in the script; use pasted text or the page fetch.
- All 10 restricted job-board hosts (`linkedin.com`, `indeed.com`, `simplyhired.com`, `ziprecruiter.com`, `glassdoor.com`, `monster.com`, `careerbuilder.com`, `dice.com`, `builtin.com`, `wellfound.com`, `angel.co`) are declined pre-socket without any network request (`enforce_domain_compliance()` / `paste_only_platform()`, matched on hostname so an employer board slug named "linkedin" still works). The result carries `paste_required: True` (CLI exit code `2`), and the CLIs tell the user to paste the description.
- Requests send an honest `User-Agent` (`USER_AGENT` in the collector) instead of a browser string.
- Removed: LinkedIn guest-page fetching, the `--search`, `--location`, `--limit`, and `--resume` leaderboard mode, and the browser-mimicking headers.

## Parser pitfalls and design notes
- **Combined headers and implicit summary:** composite headers (e.g. `CORE COMPETENCIES & TECHNICAL SKILLS`) map to `skills`. Narrative paragraphs above the first section header are promoted to `summary` when there is no explicit Summary heading; otherwise skills and summary stay in `header` with 0 section characters.
- **Role-header acronym exemption:** uppercase tokens in `Company | Title | Dates` headers and standard domain abbreviations are excluded from the Component 5 acronym deduction, so company initials do not cause false -2.0 penalties.
- **Bidirectional synonyms:** `SKILL_SYNONYM_MAP` lives in `scripts/job_posting_collector.py`. Register synonyms in both directions (e.g. `schedule adherence` <-> `real-time adherence`) so Component 1 gives 0.5 credit whichever variant the JD uses. Only register true same-capability synonyms, never competing products.
- **Parenthetical examples become synonyms:** the plain-text extractor treats `Cloud Data Warehousing (Snowflake, Redshift)` as a requirement with two 0.5-credit synonyms. That is reasonable for matching, but a rewrite should not swap one listed product for another the candidate did not use.
- **`reference_date`:** pass a `datetime.datetime` (or `None` for `datetime.now()`) to `run_ats_audit(..., reference_date=...)`. A raw `(YYYY, MM)` tuple raises `TypeError` because `parse_resume()` reads `.year` and `.month`.
- **`AlignmentConfig` serialization:** use `dataclasses.asdict(align_cfg)`, not `dict(align_cfg)`. Include an ISO `reference_date` (`YYYY-MM-DD`) in `--jd-config` fixtures so CLI runs and CI produce identical tenure and recency scores on any date. `load_jd_config` drops that key before building `AlignmentConfig`.
- **Output field names** (`modern_semantic_score`, `legacy_literal_score`) are kept so existing JSON consumers keep working; the displayed labels are "semantic view" and "literal-only view".

## Known limitations
- `--export-docx` replaces text paragraph by paragraph and collapses runs into the first text node, so mixed formatting inside one paragraph (e.g. a bold lead-in) can be lost. Check the output visually.
- Component 3 "scale" is a presence check (any metric plus any ownership verb anywhere), not per-role analysis.
- The acronym exemption list leans toward IT, finance, healthcare, and workforce-management terms; other fields may see false deductions.
- Requirement extraction from plain text is heuristic. Prefer a confirmed `--jd-config` when accuracy matters.

## Public showcase packaging
When sharing audit artifacts or the repository publicly, generate fixtures only from `get_demo_fixture()` or anonymized synthetic resumes (`examples/sample_resume.txt` -> `examples/sample_resume_optimized.txt` + `examples/sample_replacements.json`), and scrub local user paths and personal contact details.

## Multi-board API & v1.4.0 Unified Pipeline Notes
- **Unified JD extraction (`extract_reqs_consistent` & `audit_resume_against_jd`):** Runs `job_posting_collector.parse_job_posting_text` across `--jd-text`/`--jd-file`, `--from-json`, `--url`/`--jd-url`, and board flags (`--greenhouse`, `--lever`, `--ashby`, `--usajobs`), prepending `Job Title: <title>` only when absent from the first 250 characters and excluding external attribution/disclaimer strings from scoring.
- **Multi-job ranking (`--top N`):** Multi-job runs sort `md_blocks` and `json_reports` descending by `scorecard.overall_score` and slice to `--top N` when specified.
- **Unified 10-board ToS compliance gate (`RESTRICTED_DOMAINS`):** `enforce_domain_compliance(url)` blocks automated requests to `linkedin.com`, `indeed.com`, `simplyhired.com`, `ziprecruiter.com`, `glassdoor.com`, `monster.com`, `careerbuilder.com`, `dice.com`, `builtin.com`, `wellfound.com`, and `angel.co` (raising `ComplianceBlockError` / CLI exit code `2`).
