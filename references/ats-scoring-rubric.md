# Enterprise ATS 100-Point Mathematical Scoring Rubric

This reference documents the deterministic 5-component scoring model implemented in `scripts/offline_ats_auditor.py`.

## 1. Composite Score Formula (0 - 100 Points)

- **Component 1 — Weighted Keyword & Taxonomy Coverage (45.0 pts):**
  Category base weights (`hard_skill = 3.0`, `title_scope = 2.0`, `soft_skill = 1.0`) multiplied by tier (`required = 2.0x`, `preferred = 1.0x`). Exact boundary match = `1.0x` credit; verified domain synonym = `0.5x` credit (`0.0x` in Legacy Literal mode).
- **Component 2 — Contextual Placement & Recency Multiplier (20.0 pts):**
  Recent Experience (`<= 5` yrs) or Verified Education Gate = `1.0x`; Executive Summary or Older Experience (`> 5` yrs) = `0.8x`; Standalone Skills/Certifications section only = `0.4x`.
- **Component 3 — Title, Seniority & Quantified Impact (15.0 pts):**
  Target title alignment (`5.0` pts) + non-overlapping merged role tenure vs. required years (`5.0` pts) + quantified metric bullet density (`5.0` pts for `>= 5` metric bullets).
- **Component 4 — Hard Knockout Gates (10.0 pts):**
  Verifies mandatory degree, certification, licensure, or clearance gates (`10.0` PASS vs `0.0` AUTO-FILTER RISK).
- **Component 5 — HR-XML Schema & Parse Integrity (10.0 pts):**
  Evaluates section header recognition, parseable `MM/YYYY` date intervals, contact info presence, table/pipe hazards, and unexpanded obscure acronyms.
