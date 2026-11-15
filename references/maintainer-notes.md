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

# Employer Module 1: Requisition Quality Score (RQS rqs-0.2) on a single JD or public board
python scripts/requisition_quality_auditor.py --jd-file examples/sample_requisition_unicorn.txt \
  --output rqs_report.md --improved-out requisition_draft.txt

# Employer Module 2: Explainable batch screening in blind mode + factor notices + 4/5ths table
python scripts/explainable_screening_assistant.py --demo

# Employer Module 3: Multi-role internal mobility & 30/60/90-day reskilling matcher
python scripts/internal_mobility_matcher.py --demo
```
`python scripts/offline_ats_auditor.py --demo`, `python scripts/job_posting_collector.py --demo`, `python scripts/requisition_quality_auditor.py --jd-file examples/sample_requisition_unicorn.txt`, `python scripts/explainable_screening_assistant.py --demo`, and `python scripts/internal_mobility_matcher.py --demo` run built-in fixtures and are the quickest regression checks.

## Collector scope (changed in 1.3.0 / 1.4.0)
- Fetches only compliant public APIs and employer-published postings: Greenhouse (`boards-api.greenhouse.io`), Lever (US `api.lever.co` + EU `api.eu.lever.co`), Ashby (`api.ashbyhq.com`), and USAJobs (`data.usajobs.gov` with `USAJOBS_API_KEY` + `USAJOBS_USER_AGENT`), plus direct career-page fetches that read embedded `Schema.org/JobPosting` JSON-LD data. SmartRecruiters has a documented public API but is not implemented in the script; use pasted text or the page fetch.
- Direct career-page fetches first read the site's `robots.txt` (`robots_allows()`): disallowed, or a 401/403 on `robots.txt`, returns `paste_required` with `reason: "robots_txt"`. A missing `robots.txt` (404) or an unreachable site counts as allowed. Greenhouse, Lever, Ashby, and USAJobs API calls skip this check because those APIs are documented as public.
- All 10 restricted job-board hosts (`linkedin.com`, `indeed.com`, `simplyhired.com`, `ziprecruiter.com`, `glassdoor.com`, `monster.com`, `careerbuilder.com`, `dice.com`, `builtin.com`, `wellfound.com`, `angel.co`) are declined pre-socket without any network request (`enforce_domain_compliance()` / `paste_only_platform()`, matched on hostname with country-code variants so `indeed.co.uk` is covered and an employer board slug named "monster" still works). The result carries `paste_required: True` (CLI exit code `2`), and the CLIs tell the user to paste the description.
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
- **Unified JD extraction (`extract_reqs_consistent` & `audit_resume_against_jd`):** Runs `job_posting_collector.parse_job_posting_text` across `--jd-text`/`--jd-file`, `--from-json`, `--url`/`--jd-url`, and board flags (`--greenhouse`, `--lever`, `--ashby`, `--usajobs`), prepending `Job Title: <title>` only when absent from the first 250 characters and excluding external attribution/disclaimer strings from scoring. Render USAJobs `attribution` as a blockquote (`> Data source: USAJOBS...`) in the top header block above `# JD Alignment Scorecard`, never inside the `## 3. Line-by-Line Audit Ledger` table.
- **Multi-job ranking (`--top N`):** Multi-job runs sort `md_blocks` and `json_reports` descending by `scorecard.overall_score` and slice to `--top N` when specified.
- **Unified 10-board ToS compliance gate (`RESTRICTED_DOMAINS`):** `enforce_domain_compliance(url)` blocks automated requests to `linkedin.com`, `indeed.com`, `simplyhired.com`, `ziprecruiter.com`, `glassdoor.com`, `monster.com`, `careerbuilder.com`, `dice.com`, `builtin.com`, `wellfound.com`, and `angel.co` pre-socket (zero HTTP calls, raising `ComplianceBlockError` and exiting with CLI code `2` and `[COMPLIANCE GATE] BLOCKED` on stderr in both `job_posting_collector.py` and `offline_ats_auditor.py`).
- **WFM taxonomy, JD-offered alternatives & ambiguous skill spans:** `JDRequirement.alternatives` holds options the JD itself offers (`"NICE, Verint, or Genesys"`), earning full `1.0` credit in `evaluate_requirement` and rendering via `display_name` in the ledger and recovery plan. `job_posting_collector.py` detects alternative chains via `find_alternative_groups()`, drops shorter skill spans contained inside longer ones (`_drop_contained_spans()`), and enforces context/case boundaries on `AMBIGUOUS_SKILLS` and `CASE_SENSITIVE_SKILLS`.
- **Clean skill directory & bytecode suppression:** Invoke bundled scripts with `python -B` (or `sys.dont_write_bytecode = True`) when running in-place tests so `scripts/__pycache__` does not pollute the skill tree or `.skill` archive.

## v1.5.0 Employer Edition Notes (Modules 1, 2, and 3)
- **Module 1 (`requisition_quality_auditor.py`, rubric `rqs-0.2`):** Calibrated across 113 live public postings from 15 employers (Tech, Contact-Center/WFM, Healthcare, Retail, Mid-Market/Logistics). Rule `L1` relaxes must-have count thresholds to `> 10` (-4 pts) / `> 14` (-8 pts); rule `P2` exempts `STANDARD_CERT_ACRONYMS` (`CPA`, `CISSP`, `RN`, `BSN`, `PMP`, etc.) and uses `SPECIALIST_DOMAIN_ALLOWLISTS` so accounting/security/clinical acronyms are not penalized on specialist roles; rule `G3` uses `NON_EXPERIENCE_YEARS_RE` so age requirements (`"18 years of age"`) and company-history phrases (`"For 20 years, we've..."`) are never mistaken for experience gates.
- **Module 2 (`explainable_screening_assistant.py`, `ops-req-screen`):** `redact_resume_for_blind_screening()` replaces emails with `redacted@redacted.invalid` (preserving the `@` character on pipe-delimited contact lines so `offline_ats_auditor.parse_resume()` never triggers a false -3.0 pt ASCII-table deduction) and uses lowercase redaction tokens (`[redacted-name]`, `[redacted-zip]`, `([redacted-grad-date])`) so the uppercase acronym detector is never triggered. This guarantees **100% score parity** (`Score_blind == Score_raw`).
- **Module 3 (`internal_mobility_matcher.py`, `ops-req-mobility`):** `projected_30d_score` sums only Tier 1 (`0.5x -> 1.0x` synonym) and Tier 2 (`0.4x -> 1.0x` skills-list elevation) gains already evidenced on the employee's resume; Tier 3 (`0.0x`) gaps contribute `0.0` points to the 30-day projection and map to `RESKILLING_CATALOG` learning and shadowing milestones in Days 31–60 / 61–90.
- **Demo parity (`demo.html` == `docs/index.html`):** Always keep `demo.html` and `docs/index.html` byte-identical when updating any of the 4 interactive workbench tabs.
