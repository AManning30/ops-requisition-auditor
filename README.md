# ops-requisition-auditor

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)

![Deterministic Scoring](https://img.shields.io/badge/Scoring-Deterministic%20%26%20Explainable-indigo.svg)
![License: AGPL-3.0 or Commercial](https://img.shields.io/badge/License-AGPL--3.0%20%7C%20Commercial-orange.svg)

**Deterministic, explainable requisition-alignment scoring for operations, Workforce Management (WFM), and Business Intelligence (BI) roles. Every point is backed by a quoted line from the resume, and no rewrite invents experience.**

ops-requisition-auditor has two sides:

- **Candidate side: JD Alignment audit.** Compares a resume with a requisition (described below).
- **Employer side: Requisition Quality audit.** Scores the requisition itself and suggests edits that widen the qualified pool (see [Requisition Quality audit](#requisition-quality-audit-employer-edition)).

The JD Alignment audit compares a resume with a job requisition and produces:

- a transparent **100-point JD Alignment Score** with the fraction math shown;
- a **line-by-line audit ledger** that quotes the resume evidence (or marks it `MISSING`) for every requirement;
- **knockout-gate verification** (degree, license, certification, clearance);
- a **ranked point-recovery plan** and **grounded rewrites**: it never invents metrics, tools, titles, or seniority.

Its scoring engines run on the Python 3 standard library and uses **no language model in the scoring path**, so the same inputs always give the same score.

> [!IMPORTANT]
> **What the score is, and is not.** The JD Alignment Score is a transparent, repeatable *heuristic for finding fixable gaps between a resume and a requisition*. It is **not** any applicant tracking system's algorithm, and it does not predict a specific employer's or vendor's decision. Most ATS platforms parse and rank resumes but do not auto-reject them; recruiters and hiring managers make the decisions. Optimize for the human reader, and never add a term that isn't true.

---

## Why deterministic?

- **Explainable:** every point traces back to a requirement, a weight, a match credit, a placement multiplier, and a quoted resume line.
- **Reproducible:** pass `--reference-date` and you get identical results on any machine, on any day, which makes it easy to audit.
- **Private:** it runs offline with no third-party model providers, so resumes (personal data) never leave the machine.
- **Grounded:** the companion agent skill ([SKILL.md](SKILL.md)) enforces zero-fabrication rewrite rules and asks the candidate about true gaps instead of filling them in.

---

## Components

| Path | Purpose |
| :--- | :--- |
| `scripts/offline_ats_auditor.py` | Deterministic 100-point scoring engine; `.docx`/`.pdf`/`.txt`/`.md` resume loader; Markdown and JSON scorecards; `.docx` export of accepted rewrites |
| `scripts/job_posting_collector.py` | Parses pasted or saved postings, or fetches compliant public sources, into a structured `JobPostingRecord` and a ready-to-use `--jd-config` |
| `scripts/requisition_quality_auditor.py` | **Employer side (Module 1):** 100-point Requisition Quality Score for job requisitions, single or whole public board, with automatic and manual edit suggestions |
| `scripts/explainable_screening_assistant.py` | **Employer side (Module 2):** human-in-the-loop batch screening (`ops-req-screen`), blind-mode PII/ZIP/graduation-year redaction, Colorado SB 24-205 / NYC LL144 candidate factor notices, and EEOC four-fifths selection-rate tables |
| `scripts/internal_mobility_matcher.py` | **Employer & Outplacement side (Module 3):** multi-role internal mobility & outplacement matcher (`ops-req-mobility`) with deterministic 30/60/90-day reskilling roadmaps and employee-initiated privacy controls |
| `scripts/tos_compliant_collector.py` | Minimal bulk collector for Greenhouse, Lever, Ashby, and USAJobs public APIs, plus pasted text |
| `SKILL.md` | Agent skill (`ops-requisition-auditor`): workflow, WFM/ops domain rules, grounding and privacy rules |
| `references/` | Scoring rubric, WFM taxonomy, compliant endpoint reference, maintainer notes |
| `demo.html`, `docs/index.html` | Standalone interactive score simulator (GitHub Pages-ready from `docs/`) |
| `examples/` | Synthetic resume, JD config, replacements map, and baseline/optimized scorecards |
| `tests/` | `unittest` suite, run in CI on Python 3.10–3.12 |

> **Installed commands** (after `pip install .`): `ops-req-audit` (scoring engine), `ops-req-collect` (posting parser/collector), `ops-req-api-collect` (bulk public-API collector), `ops-req-jd-audit` (requisition quality audit), `ops-req-screen` (explainable batch screening assistant), and `ops-req-mobility` (internal mobility & outplacement matcher). The scripts can also be run directly with `python scripts/...` as shown below. The pre-1.5.0 command names `ats-auditor`, `job-collector`, and `tos-collector` have been retired.

---

## The 100-point JD Alignment Score

$$\text{JD Alignment Score} = C_1 + C_2 + C_3 + C_4 + C_5 \in [0, 100]$$

| # | Component | Max | How it is computed |
| :-: | :--- | :-: | :--- |
| 1 | **Requirement coverage** | 45 | Max weight = base weight (hard skill/tool/method/cert `3`, title/scope `2`, soft skill `1`) × tier (`Required 2.0x`, `Preferred 1.0x`). Match credit: exact term or JD-offered alternative `1.0`, same-capability synonym `0.5`, missing `0.0` |
| 2 | **Placement & recency** | 20 | Best occurrence × placement: recent role (≤5 yrs) `1.0x`, older/undated role `0.8x`, Skills list or Summary only `0.4x` |
| 3 | **Title, tenure & operational scale** | 15 | Title match (5), net tenure after merging overlapping date ranges (5), quantified operational scope (5) |
| 4 | **Knockout gates** | 10 | Required degree, license, certification, or clearance verified on the resume. A missing gate is reported as *not verified*, not as an auto-rejection |
| 5 | **Document & parse hygiene** | 10 | Deductions for non-standard headers, missing/irregular dates, unexpanded obscure acronyms |

Two views are always reported:

- **Semantic view:** verified same-capability synonyms earn half credit.
- **Literal-only view:** exact requisition terms (or alternatives the JD itself offers) only, approximating strict keyword filters.

Verdict bands (heuristic): **Strong** ≥ 80, **Borderline** 65–79, **Weak** < 65. The full specification is in [references/ats-scoring-rubric.md](references/ats-scoring-rubric.md).

### Domain rules for WFM, ops, and BI

- **Generic is satisfied by specific, never the reverse:** "WFM platform" is met by NICE; "Verint" is **not** met by NICE. "BI tool" is met by Power BI; "Power BI" is **not** met by Tableau.
- **Alternatives the JD offers** ("NICE, Verint, or Genesys") count as one requirement, met at full credit by any listed option.
- **Competing products are never synonyms.** Synonym credit is reserved for the same capability worded differently (for example "schedule adherence" ↔ "real-time adherence").

See [references/wfm-taxonomy.md](references/wfm-taxonomy.md).

---

## Quickstart

Python 3.10+ is enough for scoring and terminal reports. Encrypted requisition exports use the optional `cryptography` package. Optionally run `pip install .` to get the `ops-req-audit`, `ops-req-collect`, `ops-req-api-collect`, and `ops-req-jd-audit` commands.

```bash
# 1. Built-in verification demo
python scripts/offline_ats_auditor.py --demo

# 2. Audit a resume against a confirmed requirement list (most accurate)
python scripts/offline_ats_auditor.py --resume examples/sample_resume.txt \
  --jd-config examples/sample_ats_config.json --reference-date 2026-11-01 \
  --output scorecard.md --json-out scorecard.json

# 3. Audit against a pasted or saved job description (works for any source)
python scripts/offline_ats_auditor.py --resume path/to/Resume.docx \
  --jd-file job_description.txt --output audit_report.md

# 4. Audit against an employer-published posting (Greenhouse, Lever, Ashby, or a career page with JobPosting metadata)
python scripts/offline_ats_auditor.py --resume path/to/Resume.docx \
  --jd-url "https://boards.greenhouse.io/<board>/jobs/<id>" --output audit_report.md

# 5. Rank a resume across a public board's open roles
python scripts/offline_ats_auditor.py --resume path/to/Resume.docx \
  --greenhouse <board_token> --title-filter "analyst|planner" --top 5 --output matches.md

# 6. Turn a posting into structured fields and a reusable jd_config.json
python scripts/job_posting_collector.py --input-file job_description.txt \
  --output posting.md --ats-config-out jd_config.json

# 7. Export accepted rewrites to a .docx that keeps Word paragraph styles
python scripts/offline_ats_auditor.py --resume path/to/Resume.docx \
  --jd-config jd_config.json --replacements-json replacements.json \
  --export-docx path/to/Resume_Updated.docx

# 8. Run the test suite
python -m unittest discover -s tests -v
```

---

## Requisition Quality audit (Employer Edition)

Scores the **job requisition itself**, not a candidate, with a deterministic 100-point **Requisition Quality Score (RQS)**. Every finding quotes the requisition line it comes from and comes with a suggested change.

| Component | Max | Examples of what it checks |
| :--- | :-: | :--- |
| Gate necessity | 25 | Degree required with no equivalency path, years above the level norm, required certifications, GPA/prestige and employment-gap screens |
| Requirement load | 20 | Too many must-haves, too many named tools, competing products all required (NICE **and** Verint **and** Genesys) |
| Clarity & tiering | 20 | No Required/Preferred split, clichés, compound requirements, missing responsibilities |
| Title / level / scope | 15 | Missing or coded titles, leadership titles with no stated scope |
| Language & transparency | 20 | Pay range, unexpanded acronyms, potentially exclusionary wording, accommodation statement |

Bands: **Clear & inclusive** ≥ 85, **Needs tuning** 70–84, **High hidden-worker risk** < 70. The tool also applies only the concrete edits (for example adding an equivalency clause) to a draft copy and re-scores it as a **projected RQS**; it never fills in placeholders such as pay ranges or year counts. Full rubric: [references/requisition-quality-rubric.md](references/requisition-quality-rubric.md).

Markdown, JSON, CSV, and revised requisition exports use authenticated encryption. Install the optional dependency with `python -m pip install cryptography`. Generate a Fernet key with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`, store it securely, and set `OPS_AUDITOR_OUTPUT_KEY` to that key in your environment before exporting. Keep the key to read the files later.

An audit with no export flags prints its report to the terminal. When you request an encrypted export, the report is written to the requested file and omitted from the terminal output.

```bash
# Audit one requisition (text or saved HTML) and write a draft with the automatic edits applied
python scripts/requisition_quality_auditor.py --jd-file examples/sample_requisition_unicorn.txt \
  --output rqs_report.md.fernet --json-out rqs_report.json.fernet --improved-out requisition_draft.txt.fernet

# Audit every open role on a public Greenhouse, Lever, or Ashby board (portfolio summary, lowest scores first)
python scripts/requisition_quality_auditor.py --greenhouse <board_token> --title-filter "analyst|planner" --output portfolio.md.fernet

# Use your own leveling guide instead of the default year benchmarks
python scripts/requisition_quality_auditor.py --jd-file req.txt --level-benchmarks my_levels.json

# Read an encrypted report to standard output with the same key
python scripts/requisition_quality_auditor.py --decrypt rqs_report.md.fernet
```

| Example (fictional employer) | RQS | Band |
| :--- | :-: | :--- |
| `examples/sample_requisition_unicorn.txt` | **36.0** (projected 56.0 after automatic edits) | High hidden-worker risk |
| `examples/sample_requisition_improved.txt` | **100.0** | Clear & inclusive |

> The RQS is a heuristic for finding fixable issues. It is not legal advice, it does not predict applicant volume, and the default level benchmarks should be replaced with your own leveling guide.

---

## Explainable Screening Assistant (Employer Edition, Module 2)

Ranks a batch of candidate resumes against a recruiter-confirmed requirement configuration (`--jd-config` or `--jd-file`) using the deterministic 100-point JD Alignment engine, while enforcing five governance guardrails:

1. **Never auto-rejects:** every candidate defaults to `PENDING_HUMAN_REVIEW` until a human reviewer records a decision (`--decisions-json` with reviewer ID and override reason).
2. **Blind-mode redaction (on by default):** strips candidate name headers, email, phone, personal URLs, 5/9-digit ZIP codes (Illinois HB 3773 proxy protection), and education graduation dates (ADEA age-proxy mitigation) before scoring, while preserving work-experience date ranges for tenure math.
3. **Immutable audit trail:** logs engine version, SHA-256 hashes of the requirement config, raw resume, and blind-redacted text, 5-component scores, and human override notes.
4. **Candidate Factor & Gap Disclosure Notices (`--notices-dir`):** generates plain-language per-candidate notices (supporting Colorado SB 24-205, NYC LL144, and Illinois HB 3773 workflows) listing principal scoring factors, verified qualifications with quotes, partially verified items, and unverified qualifications with a right-to-clarify path.
5. **Adverse-Impact & Four-Fifths (`80%`) Selection-Rate Tables (`--demographics-json`):** computes per-group selection rates and EEOC impact ratios post-scoring from separately stored voluntary self-ID records.

```bash
# Run the built-in 4-candidate synthetic screening demo (shortlist + 4/5ths table + disclosure notices)
python scripts/explainable_screening_assistant.py --demo

# Screen a folder of resumes in blind mode and emit per-candidate disclosure notices
python scripts/explainable_screening_assistant.py --resumes-dir path/to/resumes \
  --jd-config examples/sample_ats_config.json --reference-date 2026-11-01 \
  --notices-dir out/notices --output batch_report.md --json-out batch_audit.json
```

Full governance & compliance specification: [references/explainable-screening-governance.md](references/explainable-screening-governance.md). Sample outputs: [batch shortlist & 4/5ths report](examples/sample_screening_report.md), [candidate factor notice (`CAND-003`)](examples/sample_candidate_factor_notice.md), [reviewer decisions JSON](examples/sample_screening_decisions.json), and [segregated demographics JSON](examples/sample_screening_demographics.json).

---

## Internal Mobility & Outplacement Matcher (Employer Edition, Module 3)

Scores an employee's or displaced worker's profile against a portfolio of open internal requisitions (`--reqs-dir`) or public job boards (`--greenhouse`, `--lever`, `--ashby`) and builds a deterministic **30/60/90-day reskilling and transition plan** for each target role:

- **Days 1–30 (Honest Profile Alignment):** surfaces Tier 1 synonym swaps and Tier 2 Skills-to-Experience elevations already supported by the employee's profile, computing a **Projected 30-Day Score** without fabricating any missing skills.
- **Days 31–60 (Targeted Upskilling & Shadowing):** maps missing WFM, BI, analytics, and operations skills to concrete learning modules and hands-on shadow/project milestones from the built-in WFM/BI reskilling catalog.
- **Days 61–90 (Credential & Gate Verification):** tracks degree equivalency, certifications, or scope milestones needed for Stretch roles.
- **Employee-Initiated Privacy by Default:** defaults to `share_with_manager: false` so internal exploration is never disclosed to the employee's current manager unless `--share-with-manager` is explicitly passed. Supports `--outplacement-pack` for workforce boards and outplacement counselors.

```bash
# Run the built-in 3-role WFM/Analytics career-ladder mobility demo
python scripts/internal_mobility_matcher.py --demo

# Match an employee against a folder of open internal requisitions or JD configs
python scripts/internal_mobility_matcher.py --resume examples/sample_resume.txt \
  --reqs-dir path/to/open_reqs --output mobility_plan.md --json-out mobility_plan.json

# Generate a Workforce Transition & Outplacement Pack against a public board
python scripts/internal_mobility_matcher.py --resume path/to/Resume.docx \
  --greenhouse <board_token> --title-filter "analyst|planner|operations" \
  --outplacement-pack --output outplacement_pack.md
```

Full specification & WFM/BI reskilling catalog: [references/internal-mobility-reskilling.md](references/internal-mobility-reskilling.md). Sample outputs: [3-role internal mobility plan](examples/sample_internal_mobility_plan.md), [outplacement transition packet](examples/sample_outplacement_transition_pack.md), and [career-ladder role bundle JSON](examples/sample_mobility_career_ladder.json).

---

## Requisition sources and compliance

| Source | How it is used |
| :--- | :--- |
| **Pasted text or saved file** | Always works, and is the only route for restricted boards |
| **Greenhouse, Lever (US + EU), Ashby** | Documented public job-board APIs |
| **USAJobs** | Official API; requires `USAJOBS_API_KEY` and `USAJOBS_USER_AGENT`; OPM attribution preserved |
| **Employer career pages** | Reads embedded `Schema.org/JobPosting` JSON-LD after checking `robots.txt` |
| **LinkedIn, Indeed, SimplyHired, ZipRecruiter, Glassdoor, Monster, CareerBuilder, Dice, Built In, Wellfound/AngelList** | **Never fetched.** Their terms restrict automated access, so the tools decline these URLs before any network request (exit code `2`) and ask the user to paste the description |

Requests send an honest `User-Agent`. The tools never attempt to bypass CAPTCHAs, login walls, or rate limits.

---

## Worked example (synthetic data)

The examples folder contains a **synthetic** candidate scored against a 12-requirement *Senior Analytics Manager* requisition (60.0 weight units).

| Run | JD Alignment Score | Literal-only view |
| :--- | :-: | :-: |
| `sample_resume.txt` (baseline) | **88.3 / 100** | 82.9% |
| `sample_resume_optimized.txt` (after accepted fixes) | **100.0 / 100** | 100.0% |

The six fixes fall into the three recovery tiers the skill uses:

| Requirement | Baseline issue | Fix tier | Gain |
| :--- | :--- | :--- | :-: |
| Workforce Capacity Modeling | Same-capability synonym ("capacity planning") | Tier 1: synonym → requisition term | +3.25 |
| P&L Ownership | Same-capability synonym ("operating budget") | Tier 1: synonym → requisition term | +2.17 |
| SAP S/4HANA | Listed only in Skills | Tier 2: moved into the role where it was used | +1.20 |
| Power BI | Listed only in Skills | Tier 2: moved into the role where it was used | +1.20 |
| Kubernetes | Listed only in Skills | Tier 2: moved into the role where it was used | +0.60 |
| Six Sigma Black Belt | Missing | Tier 3: added **only after the (synthetic) candidate confirmed holding it** | +3.25 |

In real use, Tier 3 gaps are always questions to the candidate, never generated bullets. Full reports: [baseline](examples/sample_scorecard_baseline.md), [optimized](examples/sample_scorecard_optimized_100.md).

---

## Known limitations

- Extracting requirements from plain-text postings is heuristic. A confirmed `--jd-config` is more accurate.
- `--export-docx` rewrites paragraph by paragraph and can lose mixed formatting inside a paragraph (for example a bold lead-in). Check the output visually.
- The Component 3 "scale" check looks for quantified scope anywhere in the resume, not per role.
- The acronym exemption list leans toward IT, finance, healthcare, and WFM terms, so other fields may see false deductions.
- The score has not yet been validated against real-world interview or hiring outcomes.

---

## Privacy

Resumes contain personal data. The engine runs locally and stores nothing beyond the output files you request. Public examples and fixtures must use synthetic resumes only (see [references/maintainer-notes.md](references/maintainer-notes.md)).

---

## License

ops-requisition-auditor is **dual-licensed**:

- **[GNU AGPL-3.0](LICENSE):** free to use, modify, and share. If you modify it and offer it to others over a network (SaaS, internal portal, API), you must make your modified source available under the AGPL.
- **[Commercial license](COMMERCIAL-LICENSE.md):** for proprietary products, closed-source integrations, hosted services, or enterprise support.

Copyright © 2026 Alexandru Manning. See [NOTICE](NOTICE). Versions 1.4.0 and earlier were released under the MIT License and remain available under MIT to anyone who received them.

*Product and company names are trademarks of their respective owners and are used only to describe compatibility or job-requirement terms. This project is not affiliated with or endorsed by any of them.*
