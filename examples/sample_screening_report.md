# Explainable Screening Shortlist & Audit Report: Senior Analytics Manager

*A deterministic, human-in-the-loop screening aid. It never auto-rejects candidates, is not legal advice, and does not replace an independent bias audit where required by law.*

## 1. Governance & Run Summary

- **Module:** `explainable-screening` (Engine v1.5.0)
- **Reference Date:** `2026-11-01`
- **Requirement Config SHA-256:** `41e6c538dacece73d58cf5bac501470da32ece3b2d102a131b297b4309b52bed`
- **Blind Mode Redaction:** `ENABLED (PII, ZIP codes, URLs, and education graduation years stripped before scoring)`
- **Shortlist Review Threshold:** `80.0 / 100` *(advisory only; no candidate is auto-rejected)*
- **Candidates Evaluated:** `4` (2 recommended for interview review, 1 borderline, 1 below threshold)

## 2. Ranked Candidate Shortlist (Human-in-the-Loop)

| Rank | Candidate ID | Score | Literal | Gates | Verified / Partial / Unverified | Recommendation Band | Human Reviewer Decision |
| :-: | :--- | :-: | :-: | :-: | :-: | :--- | :--- |
| 1 | `CAND-001` | **100.0** | 100.0% | PASS | 12 / 0 / 0 | Recommended for human interview review | ADVANCE (recruiter_j_smith) |
| 2 | `CAND-002` | **88.3** | 82.9% | PASS | 6 / 5 / 1 | Recommended for human interview review | ADVANCE (recruiter_j_smith) |
| 3 | `CAND-003` | **76.4** | 71.0% | PASS | 6 / 3 / 3 | Borderline — human review recommended | ADVANCE (recruiter_j_smith) — *Human override: strong Python/SQL/Snowflake/Demand Forecasting in recent role; verify Power BI and SAP in phone screen.* |
| 4 | `CAND-004` | **33.0** | 29.8% | NOT VERIFIED (B.S.) | 2 / 2 / 8 | Below threshold — human review required before disposition | PENDING_HUMAN_REVIEW |

## 3. Adverse-Impact & Four-Fifths (`80%`) Selection-Rate Table

*Computed post-scoring from separately stored voluntary self-identification records. Demographic fields are never passed to the scoring engine.*

| Dimension | Group | Applicants | Selected | Selection Rate | Impact Ratio | Four-Fifths (`0.80`) Status |
| :--- | :--- | :-: | :-: | :-: | :-: | :--- |
| `gender` | Female | 2 | 2 | 100.0% | 1.00 | PASS (small sample n=2) |
| `gender` | Male | 2 | 1 | 50.0% | 0.50 | FLAG (<0.80) (small sample n=2) |
| `race_ethnicity` | Asian | 1 | 1 | 100.0% | 1.00 | PASS (small sample n=1) |
| `race_ethnicity` | Black/African American | 1 | 0 | 0.0% | 0.00 | FLAG (<0.80) (small sample n=1) |
| `race_ethnicity` | Hispanic/Latino | 1 | 1 | 100.0% | 1.00 | PASS (small sample n=1) |
| `race_ethnicity` | White | 1 | 1 | 100.0% | 1.00 | PASS (small sample n=1) |

## 4. Per-Candidate Factor & Gap Disclosure Notices

---

# Candidate Factor & Gap Disclosure Notice: `CAND-001`

*A deterministic, human-in-the-loop screening aid. It never auto-rejects candidates, is not legal advice, and does not replace an independent bias audit where required by law.*

## 1. Automated Assessment & Human Oversight Disclosure

- **Requisition:** Senior Analytics Manager
- **Tool & Version:** `ops-requisition-auditor` (`explainable-screening` v1.5.0, deterministic rule engine — no LLM or generative AI)
- **Reference Date:** `2026-11-01`
- **Configuration Hash (SHA-256):** `41e6c538dacece73…`
- **Application Hash (SHA-256):** `a85a6522395d2c57…`
- **Blind Mode Applied:** `Yes (name, email, phone, ZIP code, URLs, and education graduation years stripped before scoring)`
- **JD Alignment Score:** `100.0 / 100` (Literal-only view: `100.0%`)
- **Screening Band:** **Recommended for human interview review**
- **Human Reviewer Status:** `ADVANCE` (Reviewer: `recruiter_j_smith`)

## 2. Principal Factors & Component Breakdown

| Factor | Earned | Max | What This Measures |
| :--- | :-: | :-: | :--- |
| **1. Requirement Coverage** | 45.00 | 45.00 | Presence of job-related skills, tools, methods, and qualifications from the requisition |
| **2. Contextual Placement & Recency** | 20.00 | 20.00 | Whether matched skills are demonstrated in recent work experience (<=5 yrs: 1.0x) vs. skills list only (0.4x) |
| **3. Title, Tenure & Operational Scale** | 15.00 | 15.00 | Target title alignment, net relevant tenure, and quantified operational scope |
| **4. Knockout Gate Verification** | 10.00 | 10.00 | Verification of required degrees, licenses, or certifications on the submitted text |
| **5. Document & Parse Hygiene** | 10.00 | 10.00 | Standard section headings and parseable work-history date ranges |
| **Total JD Alignment Score** | **100.0** | **100.0** | Deterministic sum of Components 1–5 |

## 3. Qualifications Verified at Full Credit (`1.0x` match in recent work experience)

- **Python** (required): matched `Python` in *Recent Work Experience (<=5 yrs)* — Quote: "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue, integrating SAP S/4HANA, Power BI, Kubernetes, and Six Sigma Black Belt DMAIC controls."
- **SQL** (required): matched `SQL` in *Recent Work Experience (<=5 yrs)* — Quote: "- Automated weekly freight reconciliation reporting using SQL and Excel, cutting manual reporting cycle time by 12 hours per week."
- **Snowflake** (required): matched `Snowflake` in *Recent Work Experience (<=5 yrs)* — Quote: "- Built automated data pipelines in Snowflake and Tableau to track supplier SLA compliance for 1,200+ global vendors."
- **SAP S/4HANA** (required): matched `SAP S/4HANA` in *Recent Work Experience (<=5 yrs)* — Quote: "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue, integrating SAP S/4HANA, Power BI, Kubernetes, and Six Sigma Black Belt DMAIC controls."
- **Power BI** (required): matched `Power BI` in *Recent Work Experience (<=5 yrs)* — Quote: "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue, integrating SAP S/4HANA, Power BI, Kubernetes, and Six Sigma Black Belt DMAIC controls."
- **Workforce Capacity Modeling** (required): matched `Workforce Capacity Modeling` in *Recent Work Experience (<=5 yrs)* — Quote: "- Partnered with cross-functional leadership on Workforce Capacity Modeling and annual P&L Ownership ($18M operating budget) reviews."
- **Demand Forecasting** (required): matched `Demand Forecasting` in *Recent Work Experience (<=5 yrs)* — Quote: "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue, integrating SAP S/4HANA, Power BI, Kubernetes, and Six Sigma Black Belt DMAIC controls."
- **P&L Ownership** (required): matched `P&L Ownership` in *Recent Work Experience (<=5 yrs)* — Quote: "- Partnered with cross-functional leadership on Workforce Capacity Modeling and annual P&L Ownership ($18M operating budget) reviews."
- **Kubernetes** (preferred): matched `Kubernetes` in *Recent Work Experience (<=5 yrs)* — Quote: "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue, integrating SAP S/4HANA, Power BI, Kubernetes, and Six Sigma Black Belt DMAIC controls."
- **Six Sigma Black Belt** (preferred): matched `Six Sigma Black Belt` in *Recent Work Experience (<=5 yrs)* — Quote: "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue, integrating SAP S/4HANA, Power BI, Kubernetes, and Six Sigma Black Belt DMAIC controls."
- **Cross-Functional Leadership** (required): matched `Cross-Functional Leadership` in *Recent Work Experience (<=5 yrs)* — Quote: "- Partnered with cross-functional leadership on Workforce Capacity Modeling and annual P&L Ownership ($18M operating budget) reviews."
- **B.S.** (required): matched `B.S.` in *Education Section* — Quote: "B.S. in Industrial Engineering - University of Missouri ([redacted-grad-date])"

## 4. Qualifications Partially Verified (Synonym Match or Skills-List Placement)

- *(None)*

## 5. Qualifications Not Verified on Submitted Application (Right to Clarify / Correct)

- All requisition requirements were verified on your submitted application.

---

# Candidate Factor & Gap Disclosure Notice: `CAND-002`

*A deterministic, human-in-the-loop screening aid. It never auto-rejects candidates, is not legal advice, and does not replace an independent bias audit where required by law.*

## 1. Automated Assessment & Human Oversight Disclosure

- **Requisition:** Senior Analytics Manager
- **Tool & Version:** `ops-requisition-auditor` (`explainable-screening` v1.5.0, deterministic rule engine — no LLM or generative AI)
- **Reference Date:** `2026-11-01`
- **Configuration Hash (SHA-256):** `41e6c538dacece73…`
- **Application Hash (SHA-256):** `448629cc46cefb0f…`
- **Blind Mode Applied:** `Yes (name, email, phone, ZIP code, URLs, and education graduation years stripped before scoring)`
- **JD Alignment Score:** `88.3 / 100` (Literal-only view: `82.9%`)
- **Screening Band:** **Recommended for human interview review**
- **Human Reviewer Status:** `ADVANCE` (Reviewer: `recruiter_j_smith`)

## 2. Principal Factors & Component Breakdown

| Factor | Earned | Max | What This Measures |
| :--- | :-: | :-: | :--- |
| **1. Requirement Coverage** | 39.00 | 45.00 | Presence of job-related skills, tools, methods, and qualifications from the requisition |
| **2. Contextual Placement & Recency** | 14.33 | 20.00 | Whether matched skills are demonstrated in recent work experience (<=5 yrs: 1.0x) vs. skills list only (0.4x) |
| **3. Title, Tenure & Operational Scale** | 15.00 | 15.00 | Target title alignment, net relevant tenure, and quantified operational scope |
| **4. Knockout Gate Verification** | 10.00 | 10.00 | Verification of required degrees, licenses, or certifications on the submitted text |
| **5. Document & Parse Hygiene** | 10.00 | 10.00 | Standard section headings and parseable work-history date ranges |
| **Total JD Alignment Score** | **88.3** | **100.0** | Deterministic sum of Components 1–5 |

## 3. Qualifications Verified at Full Credit (`1.0x` match in recent work experience)

- **Python** (required): matched `Python` in *Recent Work Experience (<=5 yrs)* — Quote: "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue."
- **SQL** (required): matched `SQL` in *Recent Work Experience (<=5 yrs)* — Quote: "- Automated weekly freight reconciliation reporting using SQL and Excel, cutting manual reporting cycle time by 12 hours per week."
- **Snowflake** (required): matched `Snowflake` in *Recent Work Experience (<=5 yrs)* — Quote: "- Built automated data pipelines in Snowflake and Tableau to track supplier SLA compliance for 1,200+ global vendors."
- **Demand Forecasting** (required): matched `Demand Forecasting` in *Recent Work Experience (<=5 yrs)* — Quote: "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue."
- **Cross-Functional Leadership** (required): matched `Cross-Functional Leadership` in *Recent Work Experience (<=5 yrs)* — Quote: "- Partnered with cross-functional leadership on capacity planning and annual operating budget reviews."
- **B.S.** (required): matched `B.S.` in *Education Section* — Quote: "B.S. in Industrial Engineering - University of Missouri ([redacted-grad-date])"

## 4. Qualifications Partially Verified (Synonym Match or Skills-List Placement)

- **SAP S/4HANA** (required): listed in *Skills Section Only (Unverified 0.4x)* (0.4x) — Quote: "SQL, Python, Snowflake, Power BI, SAP S/4HANA, Kubernetes, Cross-Functional Leadership"
- **Power BI** (required): listed in *Skills Section Only (Unverified 0.4x)* (0.4x) — Quote: "SQL, Python, Snowflake, Power BI, SAP S/4HANA, Kubernetes, Cross-Functional Leadership"
- **Workforce Capacity Modeling** (required): same-capability synonym `capacity planning` (0.5x) — Quote: "- Partnered with cross-functional leadership on capacity planning and annual operating budget reviews."
- **P&L Ownership** (required): same-capability synonym `operating budget` (0.5x) — Quote: "- Partnered with cross-functional leadership on capacity planning and annual operating budget reviews."
- **Kubernetes** (preferred): listed in *Skills Section Only (Unverified 0.4x)* (0.4x) — Quote: "SQL, Python, Snowflake, Power BI, SAP S/4HANA, Kubernetes, Cross-Functional Leadership"

## 5. Qualifications Not Verified on Submitted Application (Right to Clarify / Correct)

The following job requirements were **not detected** on your submitted resume. If you hold any of these qualifications under a different term or omitted them from your resume, you may submit a clarification or updated resume to the recruiting team to trigger a deterministic re-score:

- **Six Sigma Black Belt** (preferred): `NOT VERIFIED` on submitted text

---

# Candidate Factor & Gap Disclosure Notice: `CAND-003`

*A deterministic, human-in-the-loop screening aid. It never auto-rejects candidates, is not legal advice, and does not replace an independent bias audit where required by law.*

## 1. Automated Assessment & Human Oversight Disclosure

- **Requisition:** Senior Analytics Manager
- **Tool & Version:** `ops-requisition-auditor` (`explainable-screening` v1.5.0, deterministic rule engine — no LLM or generative AI)
- **Reference Date:** `2026-11-01`
- **Configuration Hash (SHA-256):** `41e6c538dacece73…`
- **Application Hash (SHA-256):** `4abfd85ee8c0e82f…`
- **Blind Mode Applied:** `Yes (name, email, phone, ZIP code, URLs, and education graduation years stripped before scoring)`
- **JD Alignment Score:** `76.4 / 100` (Literal-only view: `71.0%`)
- **Screening Band:** **Borderline — human review recommended**
- **Human Reviewer Status:** `ADVANCE` (Reviewer: `recruiter_j_smith`)

## 2. Principal Factors & Component Breakdown

| Factor | Earned | Max | What This Measures |
| :--- | :-: | :-: | :--- |
| **1. Requirement Coverage** | 32.25 | 45.00 | Presence of job-related skills, tools, methods, and qualifications from the requisition |
| **2. Contextual Placement & Recency** | 13.13 | 20.00 | Whether matched skills are demonstrated in recent work experience (<=5 yrs: 1.0x) vs. skills list only (0.4x) |
| **3. Title, Tenure & Operational Scale** | 11.00 | 15.00 | Target title alignment, net relevant tenure, and quantified operational scope |
| **4. Knockout Gate Verification** | 10.00 | 10.00 | Verification of required degrees, licenses, or certifications on the submitted text |
| **5. Document & Parse Hygiene** | 10.00 | 10.00 | Standard section headings and parseable work-history date ranges |
| **Total JD Alignment Score** | **76.4** | **100.0** | Deterministic sum of Components 1–5 |

## 3. Qualifications Verified at Full Credit (`1.0x` match in recent work experience)

- **Python** (required): matched `Python` in *Recent Work Experience (<=5 yrs)* — Quote: "- Led enterprise demand forecasting models in Python and SQL across 180 fulfillment centers, improving inventory accuracy by 14%."
- **SQL** (required): matched `SQL` in *Recent Work Experience (<=5 yrs)* — Quote: "- Led enterprise demand forecasting models in Python and SQL across 180 fulfillment centers, improving inventory accuracy by 14%."
- **Snowflake** (required): matched `Snowflake` in *Recent Work Experience (<=5 yrs)* — Quote: "- Built Snowflake data pipelines and Tableau executive dashboards for capacity planning and operating budget reviews."
- **Demand Forecasting** (required): matched `Demand Forecasting` in *Recent Work Experience (<=5 yrs)* — Quote: "- Led enterprise demand forecasting models in Python and SQL across 180 fulfillment centers, improving inventory accuracy by 14%."
- **Cross-Functional Leadership** (required): matched `Cross-Functional Leadership` in *Recent Work Experience (<=5 yrs)* — Quote: "- Partnered with cross-functional leadership to streamline weekly staffing schedules."
- **B.S.** (required): matched `B.S.` in *Education Section* — Quote: "B.S. in Industrial Engineering — University of Illinois ([redacted-grad-date])"

## 4. Qualifications Partially Verified (Synonym Match or Skills-List Placement)

- **Power BI** (required): listed in *Skills Section Only (Unverified 0.4x)* (0.4x) — Quote: "Python, SQL, Snowflake, Power BI, Tableau, Capacity Planning, Cross-Functional Leadership"
- **Workforce Capacity Modeling** (required): same-capability synonym `capacity planning` (0.5x) — Quote: "- Built Snowflake data pipelines and Tableau executive dashboards for capacity planning and operating budget reviews."
- **P&L Ownership** (required): same-capability synonym `operating budget` (0.5x) — Quote: "- Built Snowflake data pipelines and Tableau executive dashboards for capacity planning and operating budget reviews."

## 5. Qualifications Not Verified on Submitted Application (Right to Clarify / Correct)

The following job requirements were **not detected** on your submitted resume. If you hold any of these qualifications under a different term or omitted them from your resume, you may submit a clarification or updated resume to the recruiting team to trigger a deterministic re-score:

- **SAP S/4HANA** (required): `NOT VERIFIED` on submitted text
- **Kubernetes** (preferred): `NOT VERIFIED` on submitted text
- **Six Sigma Black Belt** (preferred): `NOT VERIFIED` on submitted text

---

# Candidate Factor & Gap Disclosure Notice: `CAND-004`

*A deterministic, human-in-the-loop screening aid. It never auto-rejects candidates, is not legal advice, and does not replace an independent bias audit where required by law.*

## 1. Automated Assessment & Human Oversight Disclosure

- **Requisition:** Senior Analytics Manager
- **Tool & Version:** `ops-requisition-auditor` (`explainable-screening` v1.5.0, deterministic rule engine — no LLM or generative AI)
- **Reference Date:** `2026-11-01`
- **Configuration Hash (SHA-256):** `41e6c538dacece73…`
- **Application Hash (SHA-256):** `7902888109a6ab6f…`
- **Blind Mode Applied:** `Yes (name, email, phone, ZIP code, URLs, and education graduation years stripped before scoring)`
- **JD Alignment Score:** `33.0 / 100` (Literal-only view: `29.8%`)
- **Screening Band:** **Below threshold — human review required before disposition**
- **Human Reviewer Status:** `PENDING_HUMAN_REVIEW` — **No automated rejection is ever performed.**

## 2. Principal Factors & Component Breakdown

| Factor | Earned | Max | What This Measures |
| :--- | :-: | :-: | :--- |
| **1. Requirement Coverage** | 12.75 | 45.00 | Presence of job-related skills, tools, methods, and qualifications from the requisition |
| **2. Contextual Placement & Recency** | 5.27 | 20.00 | Whether matched skills are demonstrated in recent work experience (<=5 yrs: 1.0x) vs. skills list only (0.4x) |
| **3. Title, Tenure & Operational Scale** | 5.00 | 15.00 | Target title alignment, net relevant tenure, and quantified operational scope |
| **4. Knockout Gate Verification** | 0.00 | 10.00 | Verification of required degrees, licenses, or certifications on the submitted text |
| **5. Document & Parse Hygiene** | 10.00 | 10.00 | Standard section headings and parseable work-history date ranges |
| **Total JD Alignment Score** | **33.0** | **100.0** | Deterministic sum of Components 1–5 |

## 3. Qualifications Verified at Full Credit (`1.0x` match in recent work experience)

- **SQL** (required): matched `SQL` in *Recent Work Experience (<=5 yrs)* — Quote: "- Built Power BI and SQL dashboards to monitor supplier compliance and weekly staffing across 85 support queues."
- **Power BI** (required): matched `Power BI` in *Recent Work Experience (<=5 yrs)* — Quote: "- Built Power BI and SQL dashboards to monitor supplier compliance and weekly staffing across 85 support queues."

## 4. Qualifications Partially Verified (Synonym Match or Skills-List Placement)

- **Workforce Capacity Modeling** (required): same-capability synonym `capacity planning` (0.5x) — Quote: "- Assisted leadership with capacity planning and cross-functional process improvement initiatives."
- **Cross-Functional Leadership** (required): listed in *Skills Section Only (Unverified 0.4x)* (0.4x) — Quote: "SQL, Power BI, Excel, Capacity Planning, Cross-Functional Leadership"

## 5. Qualifications Not Verified on Submitted Application (Right to Clarify / Correct)

The following job requirements were **not detected** on your submitted resume. If you hold any of these qualifications under a different term or omitted them from your resume, you may submit a clarification or updated resume to the recruiting team to trigger a deterministic re-score:

- **Python** (required): `NOT VERIFIED` on submitted text
- **Snowflake** (required): `NOT VERIFIED` on submitted text
- **SAP S/4HANA** (required): `NOT VERIFIED` on submitted text
- **Demand Forecasting** (required): `NOT VERIFIED` on submitted text
- **P&L Ownership** (required): `NOT VERIFIED` on submitted text
- **Kubernetes** (preferred): `NOT VERIFIED` on submitted text
- **Six Sigma Black Belt** (preferred): `NOT VERIFIED` on submitted text
- **B.S.** (required) **[Required Knockout Gate]**: `NOT VERIFIED` on submitted text
