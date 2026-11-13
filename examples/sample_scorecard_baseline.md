# JD Alignment Scorecard

*A transparent heuristic for finding fixable gaps. It is not any ATS vendor's algorithm or a prediction of a specific system's decision.*

## 1. Executive Scorecard Summary
- **JD Alignment Score:** `88.3 / 100`
  - *Semantic view (synonyms earn 0.5x credit):* `88.3%`
  - *Literal-only view (exact terms only, approximates strict keyword filters):* `82.9%` *(excludes 0.5x synonym credits)*
  - *Knockout Gate Status:* `PASS`
  - *Alignment Verdict:* **`Strong match`** — High alignment across taxonomy and context; minor lexical polish remaining.

---

## 2. 5-Component Breakdown Table

| ATS Component | Max Points | Underlying Formula / Fraction Math | Earned Points |
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

| Rank | JD Requirement | Fix Strategy Tier | Recoverable ATS Points | Actionable Instruction |
| :---: | :--- | :--- | :---: | :--- |
| 1 | **Workforce Capacity Modeling** (Required (2.0x)) | Tier 1: Synonym-to-Literal Swap (0.5x -> 1.0x Match Credit) | **`+3.25 pts`** | If 'capacity planning' and 'Workforce Capacity Modeling' name the same capability, use the JD's exact term; if they are different tools or methods, do not swap - ask the candidate. |
| 2 | **Six Sigma Black Belt** (Preferred (1.0x)) | Tier 3: True Gap Interrogation (0.0x Missing Requirement) | **`+3.25 pts`** | Ask whether the candidate has hands-on experience with 'Six Sigma Black Belt'; write a bullet only from what they confirm. |
| 3 | **P&L Ownership** (Required (2.0x)) | Tier 1: Synonym-to-Literal Swap (0.5x -> 1.0x Match Credit) | **`+2.17 pts`** | If 'operating budget' and 'P&L Ownership' name the same capability, use the JD's exact term; if they are different tools or methods, do not swap - ask the candidate. |
| 4 | **SAP S/4HANA** (Required (2.0x)) | Tier 2: Contextual Elevation (0.4x -> 1.0x Placement) | **`+1.20 pts`** | Move 'SAP S/4HANA' from Skills Section Only (Unverified 0.4x) into a recent Work Experience bullet with action + metric. |
| 5 | **Power BI** (Required (2.0x)) | Tier 2: Contextual Elevation (0.4x -> 1.0x Placement) | **`+1.20 pts`** | Move 'Power BI' from Skills Section Only (Unverified 0.4x) into a recent Work Experience bullet with action + metric. |
| 6 | **Kubernetes** (Preferred (1.0x)) | Tier 2: Contextual Elevation (0.4x -> 1.0x Placement) | **`+0.60 pts`** | Move 'Kubernetes' from Skills Section Only (Unverified 0.4x) into a recent Work Experience bullet with action + metric. |
