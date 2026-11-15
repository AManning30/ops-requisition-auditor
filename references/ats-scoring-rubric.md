# JD Alignment Scoring Rubric (0-100)

This documents the model implemented in `scripts/offline_ats_auditor.py`, so inline-mode math matches script-mode math. The weights are this project's own heuristic, not any vendor's algorithm.

## Item weights
- Base weight: `hard_skill = 3`, `title_scope = 2`, `soft_skill = 1`.
- Tier multiplier: `required = 2.0x`, `preferred = 1.0x`.
- Max item weight = base x tier. Total possible = sum of all max item weights (the shared denominator for Components 1 and 2).

## Component 1 - Keyword coverage (45)
`45 x sum(max weight x match credit) / sum(max weights)`. Match credit: exact term (or a JD-offered alternative in `alternatives`) = 1.0; listed synonym = 0.5; none = 0.0. The literal-only view scores synonyms as 0.0. Matching is case-insensitive and boundary-aware (handles `C++`, `B.S.`, `.NET`).

## Component 2 - Placement and recency (20)
`20 x sum(max weight x best (credit x placement)) / sum(max weights)`. Placement multiplier by where the best match sits:
- `1.0`: work-experience role ending within 5 years of the reference date; Education or Certifications section; a knockout-gate term found in Summary.
- `0.8`: role ending more than 5 years before the reference date, or an undated role.
- `0.4`: Summary (non-gate terms), Skills list only, or unstructured text only.

## Component 3 - Title, tenure, seniority (15)
- Title (5): target title in a recent role header or Summary = 5; target title elsewhere, or a title synonym = 3; partial token overlap (tokens of 4+ letters) = 1; else 0.
- Tenure (5): merged, non-overlapping role years vs. the JD minimum. Meets = 5; within 1 year = 3; else 0.
- Scale (5): the script checks for any quantified metric anywhere in the resume (%, $, or counts of users, clients, projects, etc.) and any configured ownership verb anywhere. Both = 5; one = 2.5; neither = 0. Inline mode should apply the same intent but can judge it per role.

## Component 4 - Knockout gates (10)
Gates are JD requirements flagged `is_knockout_gate` (the extractor flags Required items that mention a degree, bachelor, master, PhD, clearance, or license). All gates found on the resume = 10; any gate not found = 0 and the report shows "knockout gate not verified". Not found is not the same as not held: confirm with the candidate and honor "or equivalent experience". Work authorization and location are not scored.

## Component 5 - Parse hygiene (10)
Start at 10 and deduct, floored at 0:
- Non-standard section headers (e.g. "Where I've Been"): -2
- No standard Work Experience header: -2
- Table or pipe-column layout: -3
- Missing or unparseable dates (accepts `MM/YYYY`, `YYYY-MM`, `Month YYYY`): -3
- Unexpanded obscure acronyms: -2 (a built-in exemption list covers common IT, finance, healthcare, and workforce-management acronyms)

## Verdict
Strong match >= 80; Borderline 65-79; Weak match < 65. These cutoffs are heuristic. A knockout gate not found is called out beside the verdict.
