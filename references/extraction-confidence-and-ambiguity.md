# Requisition Extraction Confidence, Provenance Ledger & Ambiguity Specification

This specification documents the per-requirement certainty taxonomy, extraction provenance ledger, ambiguity detectors, negative-context filters, and multilingual parser patterns implemented in `scripts/job_posting_collector.py` and `scripts/offline_ats_auditor.py`.

---

## 1. Confirmed vs. Inferred Requirements & Certainty Taxonomy

Every `JDRequirement` in `offline_ats_auditor.py` and every item in `JobPostingRecord.extraction_ledger` carries seven provenance and certainty fields:

| Field | Allowed Values | Meaning |
| :--- | :--- | :--- |
| `extraction_status` | `"CONFIRMED"` \| `"INFERRED"` | `"CONFIRMED"` when loaded from a recruiter-approved `--jd-config` JSON; `"INFERRED"` when extracted from plain-text or HTML job postings. |
| `certainty` | `"CONFIRMED"` \| `"HIGH"` \| `"MODERATE"` \| `"LOW_INFERRED"` | Certainty tier assigned to the requirement based on section structure and hedging language. |
| `confidence` | `0.00 – 1.00` | Numeric extraction confidence score (`1.00` confirmed config; `0.94` explicit Required bullet; `0.88` explicit Preferred bullet; `0.78` hedged Required bullet or equivalency degree gate; `0.72` Responsibilities bullet; `0.58` narrative summary or heuristic phrase fallback). |
| `source_quote` | `str` | Exact line from the job requisition from which the requirement was extracted. |
| `provenance_section` | `"confirmed_config"` \| `"required_section"` \| `"preferred_section"` \| `"responsibilities_section"` \| `"summary_narrative"` | Which section of the requisition the requirement came from. |
| `provenance_rule` | `"manual_config"` \| `"taxonomy_bullet_match"` \| `"alternative_chain_or"` \| `"degree_gate_regex"` \| `"certification_match"` \| `"soft_skill_match"` \| `"heuristic_phrase_fallback"` | Which parser rule produced the requirement. |
| `ambiguity_flags` | `List[str]` | Per-item ambiguity annotations (e.g., `inferred_from_responsibilities_not_qualifications`, `degree_has_equivalent_experience_path`, `hedged_proficiency_in_required_section`). |

---

## 2. When to Prefer Confirmed `--jd-config` (`recommend_confirmed_jd_config`)

`build_ats_config_from_record(rec)` computes an overall **Requisition Extraction Confidence Score** (`rec.extraction_confidence_score` $\in [0.10, 1.00]$) from the mean item confidence minus structural penalties (missing `Required` section `-0.15`, missing `Preferred` section `-0.05`, presence of `LOW_INFERRED` heuristic phrases `-0.08`, medium-severity ambiguity flags `-0.06`).

`rec.recommend_confirmed_jd_config` is automatically set to `True` whenever:
1. `extraction_confidence_score < 0.80`, OR
2. Any requirement has `certainty == "LOW_INFERRED"`, OR
3. Any `medium`-severity ambiguity flag is triggered (`AMB-EXP-RANGE`, `AMB-CLR-CONDITIONAL`, `AMB-LOC-CONFLICT`).

---

## 3. Ambiguity Detectors (`detect_requisition_ambiguities`)

| Category | Rule ID | Severity | Trigger Pattern & Downstream Handling |
| :--- | :--- | :-: | :--- |
| `AMBIGUOUS_EXPERIENCE` | `AMB-EXP-RANGE` | `medium` | Wide experience span ($\ge 3$ years difference, e.g., `"2-8 years of experience"`). Prompts recruiter to lock exact `min_years_required` floor in `--jd-config`. |
| `AMBIGUOUS_EXPERIENCE` | `AMB-EXP-SOFT` | `low` | Hedged year phrasing (`"ideally 5+ years"`, `"up to 4 years"`, `"or equivalent experience"`). |
| `AMBIGUOUS_DEGREE` | `AMB-DEG-EQUIV` | `info` | Degree line includes an equivalency clause (`"Bachelor's degree or equivalent practical experience"`, `"o experiencia equivalente"`, `"ou expérience équivalente"`, `"oder vergleichbare Qualifikation"`). Automatically sets `is_knockout_gate = False`. |
| `AMBIGUOUS_DEGREE` | `AMB-DEG-OPTIONAL` | `low` | Degree is marked preferred or in-progress (`"Bachelor's degree preferred"`, `"currently pursuing a B.S."`). Sets `is_knockout_gate = False`. |
| `AMBIGUOUS_CLEARANCE` | `AMB-CLR-CONDITIONAL` | `medium` | Security clearance phrased as future eligibility (`"ability to obtain a Public Trust or Secret clearance"`, `"eligible for clearance if required"`). |
| `AMBIGUOUS_LOCATION` | `AMB-LOC-CONFLICT` | `medium` | Header/location states `"Remote"` while body text requires in-office days, hybrid attendance, or a commuting radius (`"in-office 3 days/week"`, `"must reside within 30 miles"`). |

---

## 4. Negative-Context Exclusions & Parenthetical Acronym Deduplication

To prevent false requirement extraction:
- **Negated Skills & Certifications (`is_negated_skill_mention`):** Mentions such as `"No SQL experience required"`, `"Python is not required"`, `"Sin experiencia en Tableau"`, `"Aucun besoin de Kubernetes"`, or `"Kein SAP erforderlich"` are excluded from `hard_skills` and logged in `rec.negative_exclusions`.
- **Negated & Non-Educational Degree Mentions (`_NEGATED_DEGREE_RE`, `_NON_DEGREE_IDIOM_RE`):** `"No college degree required"` and idioms like `"high degree of ownership"` or `"360-degree view"` are excluded from `education_requirements`.
- **Negated & Non-Security Clearances (`_NEGATED_CLEARANCE_RE`, `_NON_SECURITY_CLEARANCE_RE`):** `"No security clearance required"`, `"inventory clearance"`, `"customs clearance"`, and `"medical clearance"` are excluded from `security_clearance`.
- **Verb-Homograph `"Excel in / Excel at"` Guard (`AMBIGUOUS_SKILLS`):** `"Excel in a fast-paced environment"` is ignored unless `Excel` appears in a skills-list or tool context (`Microsoft Excel`, `Excel and SQL`, `Excel macros/PivotTables`).
- **Bidirectional Acronym & Belt Deduplication:** When a requisition says `"Average Speed of Answer (ASA)"` or `"Average Handle Time (AHT)"` or `"Six Sigma Green Belt"`, the extractor absorbs the child acronym/generic substring into `synonyms` rather than creating duplicate requirements.

---

## 5. Multilingual Requisition Coverage (`en`, `es`, `fr`, `de`)

`detect_posting_language()` and `_SECTION_HEADER_MAP` support English (`en`), Spanish (`es`), French (`fr`), and German (`de`) postings:
- **Section Headers:**
  - *Required:* `Requisitos`, `Requisitos mínimos`, `Calificaciones requeridas`, `Qualifications requises`, `Profil recherché`, `Compétences requises`, `Anforderungen`, `Erforderliche Qualifikationen`, `Was du mitbringst`.
  - *Preferred:* `Requisitos deseables`, `Deseable`, `Atouts`, `Souhaité`, `Wünschenswert`.
  - *Responsibilities:* `Responsabilidades`, `Funciones`, `Missions`, `Vos missions`, `Aufgaben`, `Deine Aufgaben`.
- **Alternative Connectors:** `_ALT_SEP_RE` and `_ALT_OR_RE` group `"Power BI o Tableau"` (`es`), `"NICE IEX ou Verint"` (`fr`), and `"Power BI oder Tableau"` (`de`) as single-requirement alternative groups (`1.0x` credit for any listed option).
