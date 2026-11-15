# Requisition Quality Score (RQS): Rubric `rqs-0.2`

Implemented in `scripts/requisition_quality_auditor.py` (command `ops-req-jd-audit`). The RQS scores the **job requisition itself**, not a candidate. It starts at 100. Each finding deducts points inside its component, and each component cannot lose more than its maximum. Every finding quotes the requisition line it comes from.

> The RQS is a transparent heuristic for finding fixable issues. It is not legal advice, it does not predict applicant volume, and the level benchmarks below are defaults that an employer should replace with its own leveling guide (`--level-benchmarks`).

## Bands

| RQS | Band |
| :-: | :--- |
| ≥ 85 | Clear & inclusive |
| 70–84 | Needs tuning |
| < 70 | High hidden-worker risk |

## 1. Gate necessity (25)

| Rule | Deduction | Trigger | Auto-edit |
| :-: | :-: | :--- | :-: |
| G1 | 10 | Required degree with no equivalency path ("or equivalent experience", "in lieu of", "combination of education and experience", "degree preferred") anywhere in the posting. Needs education context (bachelor's, MBA, "degree in"…), so "a high degree of ownership" does not count. Enrollment requirements for internships are skipped | Appends an equivalency clause |
| G1P | 0 (info) | Degree requirement in labor-certification wording ("Minimum Requirements for Position", "foreign equivalent"); these requirements are often fixed by an immigration filing | — |
| G2 | 4 | That degree is a master's/MBA/PhD, unless the line marks it optional ("MBA a plus", "PhD preferred") | — |
| G3 | 5 or 8 (3 if level is ambiguous) | Required years exceed the level benchmark (8 when ≥ 3 years over). Lines with soft wording ("ideally", "preferably", "a plus"), age requirements ("18 years of age or older"), or company-history statements ("For nearly 20 years, we've…") are excluded. When the level comes from an ambiguous title word ("Lead", or no level word at all) the finding is low severity, 3 points | — (placeholder only) |
| G4 | 3 each, max 6 | Non-licensure certification in the required list. Licenses (RN, CPA, PE, CDL, "license") are listed as info with no deduction | — |
| G5 | 3 | GPA or school-prestige screen | — |
| G6 | 6 | Employment-gap screen ("no gaps in employment", "currently employed only") | Removes the line |

**Default level benchmarks (maximum years):** entry 2 · level I 2 · level II 4 · level III 6 · mid (no level word) 5 · senior 8 · lead 8 · staff/principal 10 · manager 8 · senior manager 10 · director 12 · executive 15.

## 2. Requirement load (20)

| Rule | Deduction | Trigger | Auto-edit |
| :-: | :-: | :--- | :-: |
| L1 | 4 / 8 | More than 10 / more than 14 required items | — |
| L2 | 3 / 6 | More than 6 / more than 8 named tools or skills required. Options the posting offers as alternatives ("Power BI or Tableau") count as one | — |
| L3 | 4 per product family, max 8 | Two or more competing products from one family all required (WFM platforms, BI tools, cloud warehouses, clouds, CRM, ERP, statistics packages, CCaaS) and not offered as alternatives | Adds "experience with any one of these is sufficient" when they share one line |
| L4 | 2 | More than 6 required items and no Preferred section (not applied when L1 already fired) | — |

## 3. Clarity & tiering (20)

| Rule | Deduction | Trigger |
| :-: | :-: | :--- |
| C1 | 8 | No Required/Preferred sections detected |
| C2 | 1 each, max 5 | Vague clichés (rockstar, ninja, guru, unicorn, wear many hats, hit the ground running, fast-paced, self-starter, thrives in ambiguity…) |
| C3 | 1 each, max 3 | Compound requirement line (more than 40 words) |
| C4 | 4 | No responsibilities listed |

## 4. Title / level / scope (15)

| Rule | Deduction | Trigger |
| :-: | :-: | :--- |
| T1 | 5 | No job title found |
| T2 | 3 / 1 | Internal codes in the title (L4, IC3, Band 5, REQ numbers): 3. Very long title (more than 80 characters): 1. Team names in titles are not penalised |
| T3 | 5 | Manager/director/executive title with no stated scope (team, direct reports, budget, hiring, P&L) |

## 5. Language & transparency (20)

| Rule | Deduction | Trigger | Auto-edit |
| :-: | :-: | :--- | :-: |
| P1 | 8 | No pay range detected (any common currency and format, e.g. "$105,400—$155,000 USD", "€114.000—€142.000 EUR"). Flagged as high severity when the location may be covered by a pay-transparency law (verify current law) | — (placeholder only) |
| P2 | 2 / 4 | 5–9 / 10+ acronyms never spelled out (currencies, regions, common business terms, standard credentials like CPA/CISSP/PMP/RN, and specialist domain acronyms on security, accounting/compliance, and clinical roles excluded) | — |
| P3 | 3 each (culture fit 1), max 9 | Potentially exclusionary wording: age-coded (digital native, recent graduate, young/energetic), national origin (native English speaker), gendered (salesman, chairman, manpower, he/she), disability (able-bodied), fair-chance (no criminal record), "culture fit" | Direct replacements where a neutral term exists |
| P4 | 2 (0 for board audits) | No equal-opportunity or accommodation statement. Board audits deduct nothing, because job boards often add this statement outside the posting text | Appends a standard statement (review with counsel) |
| P5 | 2 / 3 | Very long (> 2,000 words) / very short (< 120 words) posting | — |

## Projected RQS

The tool applies **only the concrete automatic edits** above to a copy of the requisition and re-scores it. Placeholder suggestions (`[INSERT: …]`) are never inserted automatically, so the projected score never assumes information the employer hasn't provided.

## Change log

- **rqs-0.2** (field test on 46 public postings from 5 employers, Nov 2026): degree detection needs education context; internship enrollment and labor-certification wording no longer count as degree gates; optional advanced degrees are not flagged; soft year wording is not a gate and ambiguous levels get low severity; compound-line threshold raised from 22 to 40 words; title codes only (no penalty for team names); pay ranges detected in any common currency; acronym thresholds raised and currencies/regions excluded; length threshold raised to 2,000 words; L4 no longer stacks with L1; EEO is info-only in board audits. Shared parser: curly apostrophes, 20+ common section headings ("Your Expertise", "A Typical Day", "Compensation", "Where You'll Be"), legal boilerplate filtering, and a plain paragraph after a bullet list now ends that list.
- **rqs-0.1**: initial rubric.

## Hidden-worker risk flag (🔸)

Applied to findings that are likely to screen out qualified candidates: G1–G6, L1–L3. This follows Harvard Business School & Accenture, *Hidden Workers: Untapped Talent* (2021).
