# Internal Mobility & 30/60/90-Day Reskilling Specification (`ops-req-mobility`)

This document specifies the multi-role career-ladder matching model, readiness bands, zero-fabrication 30/60/90-day reskilling horizons, WFM/BI/operations upskilling catalog, and employee-privacy defaults implemented in `scripts/internal_mobility_matcher.py` (**Employer & Outplacement Edition, Module 3**).

> **Important boundary:** `ops-req-mobility` is a deterministic career-navigation, internal-mobility, and outplacement planning aid. Scores and 30/60/90-day roadmaps are transparent heuristics, not automated promotion decisions, performance evaluations, or legal advice.

---

## 1. Use Cases & Channels

1. **Enterprise Internal Mobility & Retention:** Matches current employees against open internal requisitions (`--reqs-dir` or `--from-json`) so employees and Talent Mobility / L&D teams can see which roles are immediate lateral/step-up fits (`Ready Now`) and which roles can be unlocked with a 30-to-60-day bridge plan (`Near-Ready`).
2. **Workforce Transition & Outplacement (`--outplacement-pack`):** Matches displaced employees or state/local workforce-board participants against open requisitions across public Greenhouse, Lever, or Ashby boards (`--greenhouse`, `--lever`, `--ashby`) and prints a portable **Outplacement & Career Transition Packet** with gap-confirmation questions and concrete upskilling steps.

---

## 2. Readiness Bands & Projected 30-Day Alignment Math

Every target role is scored using the deterministic 100-point JD Alignment engine (`scripts/offline_ats_auditor.py`) and classified into one of three readiness bands:

| Readiness Band | Current Score Range | Meaning |
| :--- | :-: | :--- |
| **Ready Now (Immediate Transfer / Application)** | $\ge 80.0$ | Meets core role requirements today; focus on Days 1–30 profile alignment before applying or interviewing. |
| **Near-Ready (30-to-60-Day Bridge)** | $65.0 \text{ – } 79.9$ | Strong foundational overlap, with 1–3 bridgeable tool or methodology gaps that can be closed via a 30–60 day shadowing/learning sprint. |
| **Stretch Role (90-Day Reskilling Path)** | $< 65.0$ | Requires multi-month upskilling, broader operational scope, or verification/completion of a required credential gate. |

### Why `Projected 30-Day Score` Never Fabricates Missing Skills

For every evaluated role, `ops-req-mobility` reports two scores side-by-side:
- **Current Score:** Exact 100-point JD Alignment Score on the employee's current resume.
- **Projected 30-Day Score (Honest Profile Alignment Only):**
  $$\text{Projected}_{30\text{d}} = \min\!\left(100.0,\; \text{Score}_{\text{current}} + \sum_{r \in \text{Tier } 1 \cup \text{Tier } 2} \Delta\text{pts}(r)\right)$$
  where:
  - **Tier 1 (`0.5x -> 1.0x` Synonym Alignment):** The employee's profile already evidences the exact same capability under a verified synonym (for example `capacity planning` $\to$ `Workforce Capacity Modeling`).
  - **Tier 2 (`0.4x -> 1.0x` Contextual Elevation):** The employee already lists the exact tool or skill in their `Technical & Core Skills` section (`0.4x`), and documents how they used it inside a recent work-experience bullet (`1.0x`).
  - **Tier 3 (`0.0x` True Gaps):** Skills not found on the resume **contribute `0.0` points to the 30-day projection**. Instead, they generate an honest **Gap-Confirmation Question** and are routed to the **Days 31–60** or **Days 61–90** reskilling horizons.

---

## 3. The 30 / 60 / 90-Day Reskilling Horizons

| Horizon | Gap Category | What Happens in This Phase |
| :--- | :--- | :--- |
| **Days 1–30** | **Tier 1 Synonym Alignment & Tier 2 Contextual Elevation** | Align existing profile terminology to the target requisition's language and move skills already listed in the `Skills` section into the work-experience bullets where they were used, quoting the original resume line. |
| **Days 31–60** | **Targeted Upskilling & Operational Shadowing (`RESKILLING_CATALOG`)** | Complete structured training modules on missing non-gate tools/methods (`day_60_learning`) paired with a concrete shadowing or parallel-run operational milestone (`day_90_project`). |
| **Days 61–90** | **Required Gate / Credential Verification & Scope Milestones** | Verify whether the employee holds an unverified knockout gate (`B.S.`, licensure, security certification) or qualifies under an employer equivalent-experience pathway, and complete shadowed deliverables. |

---

## 4. Built-In WFM, BI & Operations Reskilling Catalog

When a target role requires a WFM, contact-center, BI/analytics, or operations competency not present on the employee's profile, `RESKILLING_CATALOG` maps the gap to a domain-specific learning action and a hands-on verification milestone:

| Skill / Requirement Key | Domain | Days 31–60 Learning Action | Verification / Project Milestone |
| :--- | :--- | :--- | :--- |
| `erlang c` | WFM Staffing Math | Complete queueing theory & Erlang C staffing module (arrival rate, AHT, occupancy cap, shrinkage, and service-level 80/20 trade-offs). | Build a parallel Erlang C interval staffing workbook for one inbound queue and compare against actuals for 2 weeks. |
| `nice iex` | Contact-Center WFM Platform | Complete NICE IEX administrator/scheduler training (forecaster, schedule bidding, exception codes, and intraday RTA views). | Shadow a WFM scheduler on NICE IEX shift generation and intraday exception management for two scheduling cycles. |
| `verint` | Contact-Center WFM Platform | Complete Verint Workforce Management forecasting and shift-bid configuration training. | Build a sample multi-skill shift bid and adherence report in Verint with a senior planner. |
| `genesys cloud` | Contact-Center WFM Platform | Complete Genesys Cloud CX Workforce Engagement Management (WEM) forecasting and scheduling fundamentals. | Configure a test forecast and intraday monitoring view in Genesys Cloud CX. |
| `workforce capacity modeling` | Strategic Capacity Planning | Complete multi-month FTE capacity modeling training (hiring classes, nesting curves, attrition, shrinkage, and multi-skill efficiency). | Co-author a 13-week rolling FTE capacity outlook with the capacity planning lead. |
| `demand forecasting` | Forecasting & Time-Series | Complete time-series forecasting training (seasonality decomposition, ARIMA/ETS baselines, holiday arrival patterns, and MAPE/WAPE tracking). | Back-test a 12-week volume forecast and publish a WAPE/bias accuracy scorecard. |
| `power bi` | BI & Executive Reporting | Complete Power BI data modeling (Star Schema), DAX measures, and operational dashboard design training. | Publish an interactive Power BI adherence & SLA dashboard from warehouse data. |
| `tableau` | BI & Executive Reporting | Complete Tableau calculated fields, LOD expressions, and operational KPI workbook training. | Build and peer-review a Tableau operational KPI workbook with drill-down by queue/site. |
| `sql` | Data Querying & Analytics | Complete intermediate SQL training (`JOIN`s, CTEs, window functions `ROW_NUMBER`/`LAG`, and interval aggregations). | Write and validate 3 production SQL queries extracting interval volume, AHT, and adherence metrics. |
| `python` | Analytics & Automation | Complete Python data analysis track (`pandas`, `numpy`, automated CSV/warehouse ingestion, and forecast error calculation). | Automate one recurring manual spreadsheet report into a reproducible Python script. |
| `snowflake` | Cloud Data Warehouse | Complete Snowflake SQL, virtual warehouse sizing, and staging/pipeline fundamentals. | Migrate one operational reporting query or view into Snowflake with documented lineage. |
| `sap s/4hana` | ERP & Enterprise Systems | Complete SAP S/4HANA operational reporting & master-data navigation overview. | Partner with finance/supply-chain ops to reconcile one weekly SAP S/4HANA data extract. |
| `p&l ownership` | Financial & Operational Scope | Complete operational finance & budget variance training (cost per contact, labor budget vs. actuals, unit economics). | Co-present the monthly labor variance and overtime cost analysis with the operations manager. |
| `six sigma black belt` | Process Improvement | Complete Lean Six Sigma DMAIC project methodology and root-cause analysis coursework. | Lead a scoped DMAIC continuous-improvement project targeting shrinkage, AHT, or reporting cycle time. |
| `kubernetes` | Cloud & Container Infrastructure | Complete container orchestration & Kubernetes fundamentals for analytics/data services. | Shadow the platform team on a containerized batch-job deployment. |

---

## 5. Employee-Initiated Privacy Governance

Internal career exploration frequently stalls when employees worry their current manager will be notified before they are ready to apply. `ops-req-mobility` enforces privacy-by-default:
- `employee_initiated`: `true`
- `share_with_manager`: defaults to `false` (`Private to employee/candidate`). It only switches to `true` when the user explicitly passes `--share-with-manager` on the CLI.
- All scoring runs locally on the Python standard library with zero external API calls unless the user explicitly queries a public Greenhouse, Lever, or Ashby board.

---

## 6. Employee Skill Confirmation, Mentor/Manager Approvals & 90-Day Outcome Tracking (Workstream 11)

Static 30/60/90-day transition plans can fail when they assume unconfirmed skills, ignore prerequisite or credential gates, or lack milestone governance. Workstream 11 upgrades `ops-req-mobility` (`scripts/internal_mobility_matcher.py`) with five production safeguards:

### 6.1 Employee Confirmation of Skill Evidence (`Confirmed 30d` vs. `Projected 30d`)
- Every Days 1–30 Tier 1 synonym or Tier 2 contextual-elevation step starts as `UNCONFIRMED_BY_EMPLOYEE`.
- `ops-req-mobility` computes two distinct 30-day scores for every role:
  - **`projected_30d_score` (Upper Bound if All Tier 1/2 Confirmed):** Score if the employee confirms every detected synonym and Skills-section item.
  - **`confirmed_30d_score` (Verified Score):** Credits **only** the Days 1–30 steps where the employee has recorded `CONFIRMED` in `--confirmations-json`. If an employee marks a synonym or skill as `DECLINED` (or leaves it unconfirmed), it receives `0.0` confirmed points.

### 6.2 Credential & Prerequisite Chain Detection
- Each `RESKILLING_CATALOG` entry (`RESKILLING_CATALOG_VERSION = "wfm-reskill-cat-2.0"`) declares:
  - `learning_resources`: Structured learning modules with provider, title, format, and estimated hours.
  - `prerequisites`: Upstream skills or operational foundations required before starting the module (e.g., `erlang c` and `demand forecasting` before `workforce capacity modeling`).
  - `requires_credential_or_prerequisite`: Flags formal certifications, degrees, or multi-skill prerequisites (`six sigma black belt`, `p&l ownership`, `workforce capacity modeling`) and surfaces explicit alerts in `credential_or_prerequisite_alerts`.
  - `verification_artifact_type`, `catalog_owner`, and `last_reviewed_date`: Enforced programmatically via `validate_reskilling_catalog_governance()`.

### 6.3 Mentor & Manager Approval Tracking (Respecting Employee Privacy)
- `MentorAndManagerApprovalRecord` tracks mentor verification (`mentor_id`, `mentor_status`, `verified_milestones`) and manager transition sign-off (`manager_id`, `manager_status`).
- **Privacy Guardrail:** If `share_with_manager` is `false` (employee-private exploration) and a payload attempts to record a manager approval (`manager_status: "APPROVED"`), `evaluate_employee_mobility()` raises a `ValueError` blocking manager sign-off until the employee explicitly enables `share_with_manager: true`.

### 6.4 90-Day Role Transition & Outcome Measurement
- `MobilityPlanOutcomeRecord` measures longitudinal plan effectiveness across:
  - `baseline_score` $\to$ `day_30_score` $\to$ `day_60_score` $\to$ `day_90_score` (`score_delta_90d`)
  - `transition_status` (`APPLIED`, `INTERVIEWED`, `TRANSITIONED`, `upskilling_in_progress`)
  - `day_90_retention_status` and `verified_milestones_completed`
- Run with confirmation & outcome tracking:
  ```bash
  ops-req-mobility --demo --confirmations-json examples/sample_mobility_confirmation_and_outcomes.json --output mobility_confirmed.md
  ```

