# Workbench Parity, Shared Test Vectors & UI Accessibility Specification (`ops-req-workbench`)

This reference documents the **Shared Scoring Test Vector Contract (`shared-vectors-1.0`)**, Python-to-HTML non-divergence verification, and WCAG 2.1 AA / WAI-ARIA accessibility architecture implemented in `scripts/workbench_parity_and_a11y.py`, `demo.html`, and `docs/index.html` (**Workstream 12**).

---

## 1. Preventing Python and HTML Workbench Divergence

A common failure mode in dual CLI + browser tools is silent drift between the Python scoring engine and the browser workbench. `ops-req-workbench` prevents divergence through three mechanisms:

1. **Canonical Shared Test Vectors (`examples/shared_scoring_test_vectors.json`):**
   Generated directly from live executions of `offline_ats_auditor.py`, `requisition_quality_auditor.py`, `explainable_screening_assistant.py`, and `internal_mobility_matcher.py`:
   - **Candidate JD Alignment (`VEC-02`, `VEC-03`):** Baseline composite score `88.3 / 100`, literal score `82.9%`, plausible score band `[82.9, 96.8]`, evidence completeness `71.7%`, and optimized score `100.0 / 100` across `60.0` max weight units.
   - **Employer Requisition Quality (`VEC-04`):** Unicorn WFM JD score `36.0 / 100` (`8` hidden-worker flags, `13` findings), deterministic auto-edit projection `56.0 / 100`, and calibrated JD score `100.0 / 100` under rubric `rqs-0.2`.
   - **Explainable Batch Screening (`VEC-05`):** 4-candidate synthetic batch scores `[100.0, 88.3, 76.4, 33.0]` with `0.0` blind-mode score drift.
   - **Internal Mobility & 30/60/90-Day Reskilling (`VEC-06`):** 3-role WFM/Analytics career ladder current scores `[88.33, 81.40, 67.00]` and honest 30-day projections `[96.75, 85.55, 74.74]`.
2. **Automated Parity Verification (`VEC-01` – `VEC-07`):**
   `verify_workbench_parity_and_accessibility()` parses `demo.html` and asserts that every embedded constant, component cap, and test vector matches the live Python output and that `demo.html` and `docs/index.html` share an identical SHA-256 digest.

---

## 2. UI Accessibility, Keyboard Navigation & Form Validation (`A11Y-01` – `A11Y-07`)

| Check ID | WCAG 2.1 / ARIA Criterion | Implementation in `demo.html` & `docs/index.html` |
| :--- | :--- | :--- |
| **`A11Y-01`** | **Synthetic Demo Mode Labeling** | Top status bar `#synthetic-demo-banner` (`role="status" aria-live="polite"`) explicitly states **`SYNTHETIC DEMO MODE (ZERO REAL PII)`** and lists the fictional fixtures. |
| **`A11Y-02`** | **Bypass Blocks (WCAG 2.4.1)** | `<a href="#main-stage" class="skip-link">Skip to main content</a>` becomes visible on keyboard focus and jumps directly to `<main class="stage" id="main-stage">`. |
| **`A11Y-03`** | **WAI-ARIA 1.2 Tab Pattern** | `<nav class="rail" role="tablist" aria-label="Auditor Workbench Views">` pairs 7 `role="tab"` buttons (`aria-selected`, `aria-controls`, roving `tabindex`) with 7 `role="tabpanel"` sections (`aria-labelledby`). |
| **`A11Y-04`** | **Keyboard Operation (WCAG 2.1.1)** | `handleTabKeydown(event)` supports `ArrowRight`, `ArrowDown`, `ArrowLeft`, `ArrowUp`, `Home`, and `End` keys to move focus and activate tabs without a mouse. |
| **`A11Y-05`** | **Info & Relationships (WCAG 1.3.1)** | Every `<textarea>` and `<input type="number">` control (`#live-jd-input`, `#org-c1`..`#org-bachelor-yrs`) is bound to an explicit `<label for="...">`. |
| **`A11Y-06`** | **Error Identification (WCAG 3.3.1)** | `#live-jd-validation-banner` (`role="alert"`) warns when pasted JD text is empty or `< 30` characters; `#org-validation-banner` (`role="status"`) validates that component weights sum to `100.0` and cutoffs satisfy `0 < Borderline < Strong <= 100`. |
| **`A11Y-07`** | **Reflow & Focus Visible (WCAG 1.4.10 / 2.4.7)** | High-contrast `:focus-visible` outlines plus `@media (max-width: 1024px)` and `@media (max-width: 640px)` breakpoints stack the 3-column layout and scroll wide tables horizontally on mobile/tablet screens. |

---

## 3. CLI Usage

```bash
# Sync demo.html -> docs/index.html, export shared vectors, and run the parity & accessibility audit
ops-req-workbench --demo --sync-docs --export-vectors examples/shared_scoring_test_vectors.json
```
