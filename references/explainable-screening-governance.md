# Explainable Screening Governance & Compliance Specification (`ops-req-screen`)

This document specifies the deterministic governance rules, blind-mode redaction mechanics, candidate factor disclosure structure, and EEOC four-fifths ($80\%$) selection-rate calculations implemented in `scripts/explainable_screening_assistant.py` (**Employer Edition, Module 2**).

> **Important boundary:** `ops-req-screen` is a transparent, human-in-the-loop screening and documentation aid. It **never** auto-rejects a candidate, is **not** legal advice, and does **not** replace an independent third-party bias audit where required by statute (such as NYC Local Law 144).

---

## 1. Five Core Governance Guardrails

| # | Guardrail | Implementation in `scripts/explainable_screening_assistant.py` |
| :-: | :--- | :--- |
| 1 | **Human-in-the-Loop Disposition (Never Auto-Rejects)** | Every candidate defaults to `PENDING_HUMAN_REVIEW`. Even when a candidate scores below the advisory review threshold (`--threshold`, default `80.0`) or has an unverified knockout gate, the status remains `PENDING_HUMAN_REVIEW` until a human reviewer supplies a signed decision record via `--decisions-json`. |
| 2 | **Blind-Mode Redaction with 100% Score Parity** | Enabled by default (`--no-blind-mode` to disable). Strips name headers, email addresses, phone numbers, personal URLs, 5/9-digit ZIP codes, and education graduation years before `oaa.parse_resume()` runs, while preserving work-history date ranges (`MM/YYYY - MM/YYYY`) so Component 2 recency and Component 3 net tenure math remain 100% identical to unredacted scoring. |
| 3 | **Immutable Cryptographic Audit Trail** | Every batch run records the engine version (`1.5.0`), reference date, SHA-256 hash of the canonical requirement config, SHA-256 hash of each candidate's raw resume text, SHA-256 hash of the blind-redacted text, exact redaction counts by category, all 5 component scores, and any human reviewer override notes. |
| 4 | **Candidate Factor & Gap Disclosure Notices** | Generates a plain-language Markdown notice per candidate (`--notices-dir`) detailing the 5 scoring components, every qualification verified at `1.0x` with its exact quote, partially verified qualifications (`0.5x` synonym or `0.4x` skills-list placement), and unverified qualifications with an explicit **Right to Clarify / Correct** checklist. |
| 5 | **Segregated Adverse-Impact & Four-Fifths (`80%`) Tables** | Voluntary EEO self-identification data (`--demographics-json`) is never passed to the scoring engine. Selection rates and impact ratios are computed **post-scoring** from human `ADVANCE` decisions (or the advisory review threshold when human decisions are not yet recorded). |

---

## 2. Blind-Mode Redaction Rules & Score-Parity Engineering

Automated redaction can accidentally penalize candidates if replacement tokens trigger document-hygiene deductions in Component 5 of `offline_ats_auditor.py`. Specifically:
- `offline_ats_auditor.parse_resume()` checks whether a line containing pipe characters (`|`) also contains `@` to distinguish a standard contact header (`City, ST | email@domain.com | phone`) from a multi-column ASCII table (`-3.0 pts`).
- Uppercase placeholder tokens (such as `[REDACTED_EMAIL]`) can be flagged by the unexpanded-acronym detector (`-2.0 pts`).

To guarantee **exact score parity** ($\text{Score}_{\text{blind}} \equiv \text{Score}_{\text{raw}}$), `redact_resume_for_blind_screening()` applies six targeted rules using lowercase tokens and an `@`-preserving RFC 2606 `.invalid` email placeholder:

| Redaction Category | Regulatory / Bias Rationale | Pattern Replaced | Replacement Token |
| :--- | :--- | :--- | :--- |
| `name_header` | Mitigates name-based demographic proxy bias | First non-empty line before the first section header (when it contains no `|` or `@` and $\le 5$ words) | `[redacted-name]` |
| `email` | Direct PII removal while preserving contact-line `@` detection | Standard email regex | `redacted@redacted.invalid` |
| `phone` | Direct PII removal | North American & international phone patterns | `[redacted-phone]` |
| `url` | Removes personal websites, LinkedIn/GitHub profiles, and portfolio links | `https?://...`, `linkedin.com/...`, `github.com/...` | `[redacted-url]` |
| `zip_code` | **Illinois HB 3773** (effective Jan 1, 2026) prohibits using ZIP codes as a proxy for protected classes in AI/automated employment tools | `City, ST 12345(-6789)` or standalone ZIP following a state abbreviation | `[redacted-zip]` |
| `education_grad_date` | **ADEA** age-proxy mitigation | `(MM/YYYY)`, `(YYYY)`, or `Class of YYYY` **only inside the `Education` / `Academic` section** | `([redacted-grad-date])` / `Class of [redacted-grad-date]` |

Work-experience date ranges (`03/2021 - Present`) outside the Education section are never redacted so Component 2 (`<= 5 yrs` recency multiplier) and Component 3 (interval-merged net years of experience) compute the exact same values.

---

## 3. Regulatory Alignment Matrix

| Framework | Effective / Scope | How `ops-req-screen` Supports Compliance Workflows |
| :--- | :--- | :--- |
| **Colorado AI Act (SB 24-205)** | High-risk AI systems in employment decisions | Produces per-candidate **Factor & Gap Disclosure Notices** explaining the principal factors (`Components 1–5`), the exact quoted evidence or missing items, human reviewer oversight status, and the candidate's statutory right to correct inaccurate data and trigger a deterministic re-score. |
| **NYC Local Law 144 (AEDT)** | Automated Employment Decision Tools used in NYC | Provides deterministic reference scoring and built-in **selection-rate and impact-ratio tables** across sex/gender and race/ethnicity cohorts (`compute_selection_rate_tables`) to prepare data for an independent bias auditor. |
| **Illinois HB 3773** | AI in employment (effective Jan 1, 2026) | Discloses automated assessment factors and strips 5- and 9-digit ZIP codes in blind mode (`zip_code` redaction) so geographic codes are never used as a proxy for race or protected classes. |
| **EEOC Uniform Guidelines (29 CFR § 1607.4D)** | Federal four-fifths ($80\%$) adverse-impact rule | Computes group selection rates $\text{SR}_g = \text{Selected}_g / \text{Applicants}_g$ and impact ratios $\text{IR}_g = \text{SR}_g / \max_k(\text{SR}_k)$, flagging any group with $\text{IR}_g < 0.80$ and annotating small sample sizes ($n < 5$). |

---

## 4. EEOC Four-Fifths (`80%`) Selection-Rate Math

Given a set of screened candidates $C$ and a separately stored demographic map $D: C \to \{\text{dimension} \to \text{group}\}$:

1. **Selection Definition:**
   - If any candidate in the batch has an explicit human decision of `ADVANCE`, `SELECTED`, or `INTERVIEW`, a candidate $c$ is counted as **Selected** if and only if $\text{decision}(c) \in \{\text{ADVANCE}, \text{SELECTED}, \text{INTERVIEW}\}$.
   - Otherwise (prior to human review), selection is simulated using the advisory shortlist threshold: $\text{composite\_score}(c) \ge \tau$ (default $\tau = 80.0$).
2. **Group Selection Rate:**
   $$\text{SR}_{d,g} = \frac{|\{c \in C_{d,g} : c \text{ is selected}\}|}{|C_{d,g}|}$$
3. **Highest Group Rate within Dimension $d$:**
   $$\text{SR}_{d,\max} = \max_{g} \text{SR}_{d,g}$$
4. **Impact Ratio & Status:**
   $$\text{IR}_{d,g} = \frac{\text{SR}_{d,g}}{\text{SR}_{d,\max}} \quad (\text{when } \text{SR}_{d,\max} > 0)$$
   - If $\text{IR}_{d,g} \ge 0.80$: `PASS (>=0.80)` (or `PASS (small sample n=...)` when $|C_{d,g}| < 5$).
   - If $\text{IR}_{d,g} < 0.80$: `FLAG (<0.80 four-fifths rule)` (or `FLAG (<0.80) (small sample n=...)` when $|C_{d,g}| < 5$).

---

## 5. Input & Output JSON Schemas

### 5.1 `--decisions-json` (`examples/sample_screening_decisions.json`)
Records human reviewer dispositions and override reasons:
```json
{
  "CAND-001": {
    "status": "ADVANCE",
    "reviewer": "recruiter_j_smith",
    "timestamp": "2026-11-15T09:00:00Z",
    "override_reason": ""
  },
  "CAND-003": {
    "status": "ADVANCE",
    "reviewer": "recruiter_j_smith",
    "timestamp": "2026-11-15T09:12:00Z",
    "override_reason": "Human override: strong Python/SQL/Snowflake/Demand Forecasting in recent role; verify Power BI and SAP in phone screen."
  }
}
```

### 5.2 `--demographics-json` (`examples/sample_screening_demographics.json`)
Stored separately from resumes and joined by `candidate_id` only after scoring is complete:
```json
{
  "CAND-001": { "gender": "Female", "race_ethnicity": "Hispanic/Latino" },
  "CAND-002": { "gender": "Male", "race_ethnicity": "White" },
  "CAND-003": { "gender": "Female", "race_ethnicity": "Asian" },
  "CAND-004": { "gender": "Male", "race_ethnicity": "Black/African American" }
}
```
