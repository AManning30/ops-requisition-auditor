# Enterprise ATS Auditor & Universal Job Posting Collector

![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)
![Zero External Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Stdlib%20Only)-success.svg)
![Deterministic Scoring](https://img.shields.io/badge/ATS%20Engine-100--Point%20Deterministic-indigo.svg)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

A deterministic, zero-hallucination **Enterprise Applicant Tracking System (ATS) Auditor**, **Multi-Platform Job Posting Collector**, and **Interactive Score Simulator Workbench**.

Built in pure Python 3 standard library (`offline_ats_auditor.py` & `job_posting_collector.py`) alongside an interactive browser workbench (`demo.html` / `docs/index.html`), this repository models how modern semantic ATS engines (**Workday, Eightfold.ai, Greenhouse, Lever, iCIMS, Textkernel**) and legacy literal Boolean parsers (**Taleo**) parse, weight, and rank candidate resumes against job descriptions.

---

## Key Capabilities

1. **Deterministic 100-Point ATS Mathematical Engine (`scripts/offline_ats_auditor.py`)**
   - Evaluates resumes across **5 weighted enterprise ATS components** with transparent fraction math and line-by-line quote provenance.
   - Computes both **Modern Semantic ATS Score** (awards `0.5x` credit for verified domain synonyms) and **Legacy Literal-Only ATS Score** (strict `1.0x` exact match only).
   - Supports native `.docx`, `.pdf`, `.txt`, and `.md` resume files (`load_resume_file`) and exports updated Word resumes preserving formatting (`--export-docx`).

2. **Compliant Multi-Board Job Posting Collector (`scripts/job_posting_collector.py`)**
   - Extracts structured `JobPostingRecord` metadata (company, title, location, workplace model, salary bounds, hard/soft skills, knockout gates, benefits) from public ATS APIs (**Greenhouse, Lever, SmartRecruiters, Workday, Ashby, LinkedIn Guest APIs**) and saved job postings without bypassing anti-bot or CAPTCHA gates.
   - Includes live multi-job search (`--search`) and automated batch resume ranking (`--resume`) that outputs a ranked Markdown + JSON match leaderboard.

3. **Interactive 12-Row Live ATS Score Simulator (`demo.html` & `docs/index.html`)**
   - Standalone zero-build HTML/JS workbench pre-loaded with the 12-item ATS audit ledger (`60.0` total weight units).
   - Demonstrates how combining the **6 locked foundation matches (`88.3 / 100` baseline)** with the **6 interactive point-recovery fixes (`+11.7` points)** achieves **`100.0 / 100` Composite** and **`100.0%` Legacy Literal** scores in real time.

---

## 100-Point Enterprise ATS Formula

$$	ext{Composite ATS Score} = C_1 + C_2 + C_3 + C_4 + C_5 \in [0, 100]$$

| Component | Weight | What It Measures | Scoring Mechanics |
| :--- | :---: | :--- | :--- |
| **1. Weighted Keyword & Taxonomy** | **45 pts** | Hard skills (`3`), title/scope (`2`), soft skills (`1`) x Tier (`Required 2.0x`, `Preferred 1.0x`) | Exact match = `1.0`, Synonym = `0.5`, Missing = `0.0` |
| **2. Contextual Placement & Recency** | **20 pts** | Where each matched skill is proven in the resume | Recent Experience (`<=5 yrs`) = `1.0x`, Summary/Older = `0.8x`, Skills-only trap = `0.4x` |
| **3. Title, Seniority & Tenure** | **15 pts** | Role title taxonomy (`5`), net non-overlapping tenure (`5`), quantified impact (`5`) | Merges overlapping date intervals (`_merge_intervals`) |
| **4. Hard Knockout Gates** | **10 pts** | Degree, licensure, clearance, work authorization | Verified = `10/10`; missing required gate triggers `AUTO-FILTER RISK` |
| **5. HR-XML Schema Integrity** | **10 pts** | Section headers, chronological dates, contact fields, acronym hygiene | Deducts for missing headers, date gaps, or unexpanded obscure acronyms |

---

## Repository Structure

```text
enterprise-ats-auditor/
├── README.md                                 # Project overview, math spec, and CLI quickstart
├── pyproject.toml                            # PEP 621 package metadata & CLI entry points (ats-auditor, job-collector)
├── LICENSE                                   # OSI-approved MIT License
├── SKILL.md                                  # Hermes Agent skill specification & grounding rules
├── demo.html                                 # Standalone 4-tab Interactive ATS Simulator Workbench
├── docs/
│   └── index.html                            # GitHub Pages-ready interactive workbench
├── scripts/
│   ├── offline_ats_auditor.py                # Deterministic 100-pt ATS scoring engine + .docx loader/exporter
│   └── job_posting_collector.py              # Multi-platform JD collector, parser & live batch ranker
├── references/
│   ├── ats-scoring-rubric.md                 # Full 5-component mathematical specification
│   └── job-board-api-endpoints.md            # Compliant public ATS & job board endpoint reference
├── examples/
│   ├── sample_resume.txt                     # Baseline candidate resume (88.3 / 100 Composite, 82.9% Literal)
│   ├── sample_resume_optimized.txt           # Post-rewrite resume with all 6 fixes applied (100.0 / 100)
│   ├── sample_ats_config.json                # Structured 12-requirement ATS config (60.0 weight units)
│   ├── sample_replacements.json              # Verified phrase replacement map (88.3 -> 100.0)
│   ├── sample_job_record.json                # Extracted structured JobPostingRecord fixture
│   ├── sample_scorecard_baseline.md          # Full baseline audit report + 3-Tier Point Recovery Plan
│   └── sample_scorecard_optimized_100.md     # Full post-fix 100.0 / 100 verification report
├── tests/
│   └── test_ats_auditor.py                   # Automated unittest suite (7 end-to-end verification tests)
└── .github/workflows/
    └── ci.yml                                # GitHub Actions CI pipeline (Python 3.10, 3.11, 3.12)
```

---

## Quickstart & CLI Usage

No `pip install` is required—everything runs out of the box with the Python 3 standard library.

### 1. Run the Built-In Verification Demo
```bash
python scripts/offline_ats_auditor.py --demo
```

### 2. Audit a Word (`.docx`), PDF, or Text Resume Against a Job Config or Live URL
```bash
# Audit against a structured JSON config
python scripts/offline_ats_auditor.py   --resume examples/sample_resume.txt   --jd-config examples/sample_ats_config.json   --output scorecard.md

# Audit a native .docx resume directly against a live job URL
python scripts/offline_ats_auditor.py   --resume path/to/Resume.docx   --jd-url "https://www.linkedin.com/jobs/view/4482008279"   --output live_audit.md
```

### 3. Live Multi-Job Search & Batch Resume Leaderboard
```bash
python scripts/job_posting_collector.py   --search "Workforce Management Analyst"   --location "United States"   --limit 5   --resume path/to/Resume.docx   --output leaderboard.md
```

### 4. Export an Updated `.docx` Resume Preserving Word Formatting
```bash
python scripts/offline_ats_auditor.py   --resume path/to/Resume.docx   --jd-config examples/sample_ats_config.json   --replacements-json examples/sample_replacements.json   --export-docx path/to/Resume_Optimized.docx
```

### 5. Run the Automated Test Suite
```bash
python -m unittest discover -s tests -v
```

---

## Verified Benchmark: 12-Requirement Ledger (`88.3` -> `100.0 / 100`)

| # | JD Requirement | Tier | Max Wt | Baseline State | Baseline Pts | Fix Strategy | Post-Fix State | Gain |
| :-: | :--- | :---: | :---: | :--- | :---: | :--- | :--- | :---: |
| 1 | **Python** | Req (`2.0x`) | `6.0` | `1.0x` Exact / `1.0x` Recent | Maxed | Locked Foundation | `1.0x` / `1.0x` | `+0.00` |
| 2 | **SQL** | Req (`2.0x`) | `6.0` | `1.0x` Exact / `1.0x` Recent | Maxed | Locked Foundation | `1.0x` / `1.0x` | `+0.00` |
| 3 | **Snowflake** | Req (`2.0x`) | `6.0` | `1.0x` Exact / `1.0x` Recent | Maxed | Locked Foundation | `1.0x` / `1.0x` | `+0.00` |
| 4 | **Demand Forecasting** | Req (`2.0x`) | `6.0` | `1.0x` Exact / `1.0x` Recent | Maxed | Locked Foundation | `1.0x` / `1.0x` | `+0.00` |
| 5 | **Cross-Functional Leadership** | Req (`2.0x`) | `2.0` | `1.0x` Exact / `1.0x` Recent | Maxed | Locked Foundation | `1.0x` / `1.0x` | `+0.00` |
| 6 | **B.S.** (Knockout Gate) | Req (`2.0x`) | `6.0` | `1.0x` Gate Verified | Maxed | Locked Foundation | `1.0x` / `1.0x` | `+0.00` |
| 7 | **Workforce Capacity Modeling** | Req (`2.0x`) | `6.0` | `0.5x` Synonym (`1.0x` Recent) | `-3.25` | **Tier 1:** Synonym-to-Literal Swap | `1.0x` / `1.0x` | **`+3.25`** |
| 8 | **Six Sigma Black Belt** | Pref (`1.0x`) | `3.0` | `0.0x` Missing (`0.0x`) | `-3.25` | **Tier 3:** Verified Gap Injection | `1.0x` / `1.0x` | **`+3.25`** |
| 9 | **P&L Ownership** | Req (`2.0x`) | `4.0` | `0.5x` Synonym (`1.0x` Recent) | `-2.17` | **Tier 1:** Synonym-to-Literal Swap | `1.0x` / `1.0x` | **`+2.17`** |
| 10 | **SAP S/4HANA** | Req (`2.0x`) | `6.0` | `1.0x` Exact in Skills (`0.4x`) | `-1.20` | **Tier 2:** Contextual Elevation | `1.0x` / `1.0x` | **`+1.20`** |
| 11 | **Power BI** | Req (`2.0x`) | `6.0` | `1.0x` Skills + `0.5x` Recent Syn | `-1.00` | **Tier 2:** Contextual Elevation | `1.0x` / `1.0x` | **`+1.00`** |
| 12 | **Kubernetes** | Pref (`1.0x`) | `3.0` | `1.0x` Exact in Skills (`0.4x`) | `-0.60` | **Tier 2:** Contextual Elevation | `1.0x` / `1.0x` | **`+0.60`** |

- **Baseline Score:** `88.3 / 100` Composite (`82.9%` Legacy Literal)
- **Post-Fix Score:** `100.0 / 100` Composite (`100.0%` Legacy Literal)

---

## Interactive Live Demo (GitHub Pages Ready)

Open `demo.html` locally in any browser—or enable **GitHub Pages** from the `docs/` folder on the `main` branch—to launch the interactive 4-tab workbench.

## License

Released under the [MIT License](LICENSE).
