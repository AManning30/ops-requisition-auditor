# JD Alignment Scorecard

*A transparent heuristic for finding fixable gaps. It is not any ATS vendor's algorithm or a prediction of a specific system's decision.*

## 1. Executive Scorecard Summary
- **JD Alignment Score:** `88.3 / 100`
  - *Semantic view (synonyms earn 0.5x credit):* `88.3%`
  - *Literal-only view (exact terms only, approximates strict keyword filters):* `82.9%` *(excludes 0.5x synonym credits)*
  - *Knockout Gate Status:* `PASS`
  - *Alignment Verdict:* **`Strong match`** — High alignment across taxonomy and context; minor lexical polish remaining.
  - *Effective Organization Config:* `default-baseline-org` v`1.5.0` (`APPROVED` | Weights `45/20/15/10/10` | Cutoffs `Strong>=80, Borderline>=65`)

---

## 2. 5-Component Breakdown Table

| Component | Max Points | Underlying Formula / Fraction Math | Earned Points |
| :--- | :---: | :--- | :---: |
| **1. Weighted Keyword & Taxonomy** | 45.0 | `45 * (52.00 / 60.00)` *(Literal: `47.00/60.00`)* | **39.00** |
| **2. Contextual Placement & Recency** | 20.0 | `20 * (43.00 / 60.00)` | **14.33** |
| **3. Title, Seniority & Tenure Alignment** | 15.0 | Title Taxonomy Match: `5.0/5`; Calculated Domain Tenure (8.3 yrs vs 6.0 yrs req): `5.0/5`; Seniority & Quantified Enterprise Scale: `5.0/5` | **15.00** |
| **4. Hard Knockout / Minimum Gate** | 10.0 | `All mandatory gates verified` | **10.00** |
| **5. HR-XML Parseability & Schema** | 10.0 | `Zero schema defects (10.0)` | **10.00** |
| **TOTAL COMPOSITE SCORE** | **100.0** | **Sum of Components 1–5** | **`88.3 / 100`** |

---

## 3. Line-by-Line Audit Ledger

| JD Requirement | Tier | Base Wt | Max Wt | Match (`1.0`/`0.5`/`0.0`) | Placement (`1.0x`/`0.8x`/`0.4x`) | Earned Wt | Exact Resume Quote & Location | Composite Pts Lost |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- | :---: |
| **Python** | Required (2.0x) | 3 | 6.0 | `1.0` | `1.0x` | `6.00` | *Recent Work Experience (<=5 yrs):* "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue." | `-0.00` |
| **SQL** | Required (2.0x) | 3 | 6.0 | `1.0` | `1.0x` | `6.00` | *Recent Work Experience (<=5 yrs):* "- Automated weekly freight reconciliation reporting using SQL and Excel, cutting manual reporting cycle time by 12 hours per week." | `-0.00` |
| **Snowflake** | Required (2.0x) | 3 | 6.0 | `1.0` | `1.0x` | `6.00` | *Recent Work Experience (<=5 yrs):* "- Built automated data pipelines in Snowflake and Tableau to track supplier SLA compliance for 1,200+ global vendors." | `-0.00` |
| **SAP S/4HANA** | Required (2.0x) | 3 | 6.0 | `1.0` | `0.4x` | `2.40` | *Skills Section Only (Unverified 0.4x):* "SQL, Python, Snowflake, Power BI, SAP S/4HANA, Kubernetes, Cross-Functional Leadership" | `-1.20` |
| **Power BI** | Required (2.0x) | 3 | 6.0 | `1.0` | `0.4x` | `2.40` | *Skills Section Only (Unverified 0.4x):* "SQL, Python, Snowflake, Power BI, SAP S/4HANA, Kubernetes, Cross-Functional Leadership" | `-1.20` |
| **Workforce Capacity Modeling** | Required (2.0x) | 3 | 6.0 | `0.5` | `1.0x` | `3.00` | *Recent Work Experience (<=5 yrs):* "- Partnered with cross-functional leadership on capacity planning and annual operating budget reviews." | `-3.25` |
| **Demand Forecasting** | Required (2.0x) | 3 | 6.0 | `1.0` | `1.0x` | `6.00` | *Recent Work Experience (<=5 yrs):* "- Spearheaded enterprise demand forecasting models in Python, reducing stockout rates by 18.4% across 420 distribution centers and protecting $14.2M in annual revenue." | `-0.00` |
| **P&L Ownership** | Required (2.0x) | 2 | 4.0 | `0.5` | `1.0x` | `2.00` | *Recent Work Experience (<=5 yrs):* "- Partnered with cross-functional leadership on capacity planning and annual operating budget reviews." | `-2.17` |
| **Kubernetes** | Preferred (1.0x) | 3 | 3.0 | `1.0` | `0.4x` | `1.20` | *Skills Section Only (Unverified 0.4x):* "SQL, Python, Snowflake, Power BI, SAP S/4HANA, Kubernetes, Cross-Functional Leadership" | `-0.60` |
| **Six Sigma Black Belt** | Preferred (1.0x) | 3 | 3.0 | `0.0` | `0.0x` | `0.00` | `"MISSING"` | `-3.25` |
| **Cross-Functional Leadership** | Required (2.0x) | 1 | 2.0 | `1.0` | `1.0x` | `2.00` | *Recent Work Experience (<=5 yrs):* "- Partnered with cross-functional leadership on capacity planning and annual operating budget reviews." | `-0.00` |
| **B.S.** | Required (2.0x) | 3 | 6.0 | `1.0` | `1.0x` | `6.00` | *Education Section:* "B.S. in Industrial Engineering - University of Missouri (05/2018)" | `-0.00` |

---

## 4. Highest-ROI Point Recovery Plan

| Rank | JD Requirement | Fix Strategy Tier | Recoverable Points | Actionable Instruction |
| :---: | :--- | :--- | :---: | :--- |
| 1 | **Workforce Capacity Modeling** (Required (2.0x)) | Tier 1: Synonym-to-Literal Swap (0.5x -> 1.0x Match Credit) | **`+3.25 pts`** | If 'capacity planning' and 'Workforce Capacity Modeling' name the same capability, use the JD's exact term; if they are different tools or methods, do not swap - ask the candidate. |
| 2 | **Six Sigma Black Belt** (Preferred (1.0x)) | Tier 3: True Gap Interrogation (0.0x Missing Requirement) | **`+3.25 pts`** | Ask whether the candidate has hands-on experience with 'Six Sigma Black Belt'; write a bullet only from what they confirm. |
| 3 | **P&L Ownership** (Required (2.0x)) | Tier 1: Synonym-to-Literal Swap (0.5x -> 1.0x Match Credit) | **`+2.17 pts`** | If 'operating budget' and 'P&L Ownership' name the same capability, use the JD's exact term; if they are different tools or methods, do not swap - ask the candidate. |
| 4 | **SAP S/4HANA** (Required (2.0x)) | Tier 2: Contextual Elevation (0.4x -> 1.0x Placement) | **`+1.20 pts`** | Move 'SAP S/4HANA' from Skills Section Only (Unverified 0.4x) into a recent Work Experience bullet with action + metric. |
| 5 | **Power BI** (Required (2.0x)) | Tier 2: Contextual Elevation (0.4x -> 1.0x Placement) | **`+1.20 pts`** | Move 'Power BI' from Skills Section Only (Unverified 0.4x) into a recent Work Experience bullet with action + metric. |
| 6 | **Kubernetes** (Preferred (1.0x)) | Tier 2: Contextual Elevation (0.4x -> 1.0x Placement) | **`+0.60 pts`** | Move 'Kubernetes' from Skills Section Only (Unverified 0.4x) into a recent Work Experience bullet with action + metric. |

---

## 5. Transparent Uncertainty: Score vs. Confidence vs. Evidence Completeness

*The JD Alignment Score is a deterministic text-alignment heuristic, not an objective measurement of human capability or a prediction of job performance. Always interpret the point estimate alongside its Plausible Score Interval, Measurement Confidence, and Evidence Completeness.*

| Distinct Evaluation Pillar | Value / Band | What This Measures (and Does Not Measure) |
| :--- | :--- | :--- |
| **1. Heuristic Alignment Score & Plausible Band** | **`88.3 / 100`** (Plausible Band: **`[82.9, 96.8]`**, spread `13.9 pts` — `MODERATE_INTERPRETATION_SPREAD (Synonym / Placement Sensitivity)`) | Point estimate under active rubric weights. Lower bound (`82.9`) reflects strict literal-only filtering; upper bound (`96.8`) reflects full credit for synonyms & skills-list items already on the resume (excluding `0.0x` missing skills). |
| **2. Measurement & Parser Confidence** | **`100.0%`** (`HIGH_MEASUREMENT_CONFIDENCE (>=85%)` — Resume Parse: `100%`, JD Config: `100%`) | Technical certainty that resume sections, work-history dates, and requisition requirements were parsed without structural loss. High parser confidence does **not** mean the candidate meets all requirements. |
| **3. Evidence Completeness** | **`71.7%`** (`PARTIAL_EVIDENCE (65%-84% Weighted Contextual Verification — Clarify Gaps)` — `6` Full `1.0x` / `5` Partial / `1` Missing of `12`) | Share of weighted requisition requirements backed by direct `1.0x` recent-role evidence vs. partial (`0.5x` synonym / `0.4x` skills-list) or unverified (`0.0x`) items. |

### Side-by-Side Semantic vs. Literal View Comparison

| Component | Max Pts | Semantic View (`0.5x` Synonyms) | Literal-Only View (`1.0x` Exact Only) | Spread (`Semantic - Literal`) | Why the Views Agree or Diverge |
| :--- | :-: | :-: | :-: | :-: | :--- |
| **1. Requirement Coverage** | 45.0 | **39.00** | `35.25` | `+3.75 pts` | Semantic view credits verified same-capability synonyms at 0.5x; Literal view requires exact JD terms or JD-offered alternatives. |
| **2. Contextual Placement & Recency** | 20.0 | **14.33** | `12.67` | `+1.67 pts` | Measures whether matched terms appear in recent work experience (1.0x), older roles (0.8x), or skills list only (0.4x). |
| **3. Title, Tenure & Operational Scale** | 15.0 | **15.00** | `15.00` | `+0.00 pts` | Deterministic title alignment, merged net tenure vs. required years, and quantified operational scope. |
| **4. Knockout Gate Verification** | 10.0 | **10.00** | `10.00` | `+0.00 pts` | Binary verification of required degree, certification, license, or clearance gates on submitted text. |
| **5. Document & Parse Hygiene** | 10.0 | **10.00** | `10.00` | `+0.00 pts` | HR-XML section header standard compliance, date format parseability, and acronym expansion. |
| **Total Score Comparison** | **100.0** | **`88.3 / 100`** | **`82.9%`** | **`+5.4 pts`** | 2 requirement(s) matched via same-capability synonym |

### Explicit Missing & Partially Verified Evidence

**Unverified / Missing Requirements (`0.0x` — Never Auto-Inferred):**
- **Six Sigma Black Belt** (preferred, `3w`, `-3.25 pts`): No evidence or synonym detected anywhere in submitted resume text. *Action:* Do you hold hands-on experience or credentials in 'Six Sigma Black Belt' that were omitted from your resume?

**Partially Verified Requirements (`0.5x` Synonym or `<1.0x` Placement):**
- **SAP S/4HANA** (required, `-1.20 pts`): Term 'SAP S/4HANA' appears only in Skills Section Only (Unverified 0.4x) (0.4x); not yet demonstrated in a recent Work Experience bullet (<=5 yrs, 1.0x).
- **Power BI** (required, `-1.20 pts`): Term 'Power BI' appears only in Skills Section Only (Unverified 0.4x) (0.4x); not yet demonstrated in a recent Work Experience bullet (<=5 yrs, 1.0x).
- **Workforce Capacity Modeling** (required, `-3.25 pts`): Matched synonym 'capacity planning' (0.5x) instead of exact JD term 'Workforce Capacity Modeling' (1.0x).
- **P&L Ownership** (required, `-2.17 pts`): Matched synonym 'operating budget' (0.5x) instead of exact JD term 'P&L Ownership' (1.0x).
- **Kubernetes** (preferred, `-0.60 pts`): Term 'Kubernetes' appears only in Skills Section Only (Unverified 0.4x) (0.4x); not yet demonstrated in a recent Work Experience bullet (<=5 yrs, 1.0x).

## 6. Complete Point-Deduction Explanations (Reconciling `100.0` -> `88.3`)

| # | Component | Requirement / Criterion | Deduction Type | C1 Lost | C2 Lost | C3–C5 Lost | Total Deducted | Plain-Language Explanation |
| :-: | :--- | :--- | :--- | :-: | :-: | :-: | :-: | :--- |
| 1 | `C1_C2_Requirement_Alignment` | **Six Sigma Black Belt** | `MISSING_REQUIREMENT_0.0X` | `-2.25` | `-1.00` | `-0.00` | **`-3.25 pts`** | 'Six Sigma Black Belt' (preferred, 3w) was not found on the resume (0.0x credit): -2.25 pts in C1 Coverage and -1.00 pts in C2 Placement. |
| 2 | `C1_C2_Requirement_Alignment` | **Workforce Capacity Modeling** | `SYNONYM_DILUTION_0.5X` | `-2.25` | `-1.00` | `-0.00` | **`-3.25 pts`** | 'Workforce Capacity Modeling' (required, 6w) matched via same-capability synonym 'capacity planning' (0.5x credit in Recent Work Experience (<=5 yrs)): lost -2.25 pts in C1 Coverage and -1.00 pts in C2 Placement. |
| 3 | `C1_C2_Requirement_Alignment` | **P&L Ownership** | `SYNONYM_DILUTION_0.5X` | `-1.50` | `-0.67` | `-0.00` | **`-2.17 pts`** | 'P&L Ownership' (required, 4w) matched via same-capability synonym 'operating budget' (0.5x credit in Recent Work Experience (<=5 yrs)): lost -1.50 pts in C1 Coverage and -0.67 pts in C2 Placement. |
| 4 | `C1_C2_Requirement_Alignment` | **Power BI** | `PLACEMENT_PENALTY_SKILLS_OR_HISTORICAL` | `-0.00` | `-1.20` | `-0.00` | **`-1.20 pts`** | 'Power BI' (required, 6w) earned full C1 Coverage (1.0x), but appears only in Skills Section Only (Unverified 0.4x) (0.4x multiplier): lost -1.20 pts in C2 Placement. |
| 5 | `C1_C2_Requirement_Alignment` | **SAP S/4HANA** | `PLACEMENT_PENALTY_SKILLS_OR_HISTORICAL` | `-0.00` | `-1.20` | `-0.00` | **`-1.20 pts`** | 'SAP S/4HANA' (required, 6w) earned full C1 Coverage (1.0x), but appears only in Skills Section Only (Unverified 0.4x) (0.4x multiplier): lost -1.20 pts in C2 Placement. |
| 6 | `C1_C2_Requirement_Alignment` | **Kubernetes** | `PLACEMENT_PENALTY_SKILLS_OR_HISTORICAL` | `-0.00` | `-0.60` | `-0.00` | **`-0.60 pts`** | 'Kubernetes' (preferred, 3w) earned full C1 Coverage (1.0x), but appears only in Skills Section Only (Unverified 0.4x) (0.4x multiplier): lost -0.60 pts in C2 Placement. |
