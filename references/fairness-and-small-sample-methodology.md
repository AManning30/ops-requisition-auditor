# Fairness, Adverse-Impact & Small-Sample Statistical Methodology

*Reference specification for `scripts/fairness_adverse_impact_auditor.py` (`ops-req-fairness`), covering subgroup adverse-impact evaluation across race/ethnicity, sex, age proxies, disability status, resume language, and intersectional cohorts.*

---

## 1. Why Blind Screening Alone Does Not Prove Equitable Outcomes

Blind-mode redaction (`redact_resume_for_blind_screening()`) removes direct demographic identifiers and proxies (candidate names, email addresses, phone numbers, URLs, 5/9-digit ZIP codes under Illinois HB 3773, and education graduation years under ADEA) prior to scoring with `0.00 pts` score drift. However, blind redaction only prevents **direct treatment disparity** at the text-input boundary. Disproportionate impact can still arise from:

1. **Unnecessary Requisition Gates:** Requiring a 4-year degree without an equivalent-experience pathway or requiring excessive experience years disproportionately screens out qualified STARs (*Skilled Through Alternative Routes*).
2. **Parser & Multilingual Extraction Bias:** A heuristic parser that only recognizes English section headers or exact English skill names could systematically under-extract qualifications from bilingual or non-English (`es`, `fr`, `de`) resumes or assistive-technology-formatted resumes.
3. **Human Reviewer Override Disparities:** Even when an engine score is neutral, human reviewers could apply upward overrides (`Borderline -> ADVANCE`) unevenly across demographic groups.

To audit all three layers, `ops-req-fairness` evaluates **both** the deterministic engine cutoff and the human post-override decisions across six dimensions:
- `race_ethnicity`
- `sex`
- `age_proxy_band` (`Under 40 (<40)` vs. `40 and Over (>=40, ADEA Protected)`)
- `disability_status` (`Disclosed Disability / Accommodation` vs. `No Disclosed Disability`)
- `resume_language` (`en`, `es`, `fr`, `de`)
- `intersectional_race_sex` (NYC Local Law 144 / 6 RCNY § 5-301 cross-tabulated `race_ethnicity × sex` subgroups)

---

## 2. Small-Sample Statistical Methodology

### 2.1 The Small-Sample Instability Problem in Four-Fifths (`0.80`) Ratios

Under the EEOC Uniform Guidelines on Employee Selection Procedures (`29 CFR § 1607.4(D)`), a selection rate for any racial, ethnic, or sex subgroup that is less than four-fifths (`4/5` or `80%`) of the rate for the group with the highest selection rate is generally regarded as evidence of adverse impact:

$$\text{Impact Ratio (IR)}_g = \frac{\text{Selection Rate}_g}{\max_{k} (\text{Selection Rate}_k)}$$

However, when a subgroup has a small sample size ($n < 5$ or $5 \le n < 30$), a single applicant's outcome shifts the subgroup selection rate by $\frac{1}{n}$ (e.g., $\pm 33.3\%$ at $n=3$, or $\pm 25.0\%$ at $n=4$). Relying solely on a raw point estimate of $\text{IR}_g < 0.80$ in small cohorts produces high false-positive rates.

### 2.2 Three-Tier Sample-Size Classification & Exact Tests

`ops-req-fairness` implements a three-tier small-sample methodology using exact standard-library combinatorics (`math.comb`):

| Sample Tier | Condition | Statistical Metrics Computed | Four-Fifths (`0.80`) Decision Rule |
| :--- | :---: | :--- | :--- |
| **Tier 1: `CRITICAL_SMALL_SAMPLE`** | $n < 5$ | • Exact **Fisher's Exact Test** two-sided $p$-value<br>• **Wilson Score 95% Confidence Interval**<br>• **EEOC Q&A #21 Single-Case Flip Test** ($\text{Flip-IR}_g = \frac{(s_g + 1) / n}{\text{SR}_{\max}}$) | Emits explicit `SMALL_SAMPLE_CAUTION`. If $\text{IR}_g < 0.80$ but flipping 1 applicant brings $\text{Flip-IR}_g \ge 0.80$ and Fisher $p \ge 0.05$, classifies as `SMALL_SAMPLE_NO_STAT_SIG_DISPARITY`. |
| **Tier 2: `MODERATE_SAMPLE`** | $5 \le n < 30$ | • Exact **Fisher's Exact Test** two-sided $p$-value<br>• **Wilson Score 95% Confidence Interval**<br>• Raw & Flip-Adjusted Impact Ratio | Flags `< 0.80` alongside exact hypergeometric Fisher $p$-value and Wilson 95% CI bounds. |
| **Tier 3: `POWERED_SAMPLE`** | $n \ge 30$ | • Impact Ratio ($\text{IR}_g$)<br>• Wilson 95% Confidence Interval<br>• Fisher's Exact Test $p$-value | Standard four-fifths (`0.80`) and statistical significance ($p < 0.05$) threshold enforcement. |

In addition, any subgroup representing $< 2.0\%$ of the total applicant pool is annotated with `[NYC LL144 <2% Pool Share]` while still reporting its complete counts and rates.

### 2.3 Mathematical Definitions

1. **Wilson Score 95% Confidence Interval** ($z = 1.95996$):
   $$\text{CI}_{95\%} = \frac{\hat{p} + \frac{z^2}{2n} \pm z\sqrt{\frac{\hat{p}(1-\hat{p})}{n} + \frac{z^2}{4n^2}}}{1 + \frac{z^2}{n}}$$
2. **Exact Two-Sided Fisher's Exact Test $p$-value** for a $2 \times 2$ table $\begin{pmatrix} a & b \\ c & d \end{pmatrix}$ comparing subgroup $g$ ($a$ selected, $b$ unselected) against the reference group ($c$ selected, $d$ unselected):
   $$P(X = x) = \frac{\binom{a+b}{x}\binom{c+d}{(a+c)-x}}{\binom{a+b+c+d}{a+c}}, \quad p_{\text{two-sided}} = \sum_{x : P(X=x) \le P(X=a)} P(X = x)$$

---

## 3. Parser Error Rate, Rubric Calibration & Override Audits

Beyond selection rates, `ops-req-fairness` audits three diagnostic equity dimensions per subgroup:

1. **Parser & Multilingual Extraction Parity:**
   - **Mean Parser Confidence (`0–100%`)** and **Low-Confidence Flag Rate (`< 75%`)**.
   - **Parser Extraction Error Rate:** $\frac{\text{Ground-Truth Qualifications} - \text{Extracted Qualifications}}{\text{Ground-Truth Qualifications}}$. If any subgroup exceeds a `15%` extraction error rate or `25%` low-confidence rate, `ops-req-fairness` raises a `[PARSER DISPARITY ALERT]`.
2. **Rubric Calibration & Blind-Mode Invariance:**
   - **Mean Signed Rubric Bias:** $\frac{1}{n}\sum (\text{Engine Score} - \text{Validated Expert Score})$.
   - **Mean Absolute Error (`MAE`):** $\frac{1}{n}\sum |\text{Engine Score} - \text{Validated Expert Score}|$.
   - **Max Blind-Mode Parity Drift:** $\max |\text{Score}_{\text{raw}} - \text{Score}_{\text{blind}}|$, verifying `0.00 pts` drift when stripping names, ZIP codes, graduation years, and voluntary ADA accommodation notes.
3. **False-Positive / False-Negative Rates (`FPR` / `FNR`) & Reviewer Overrides:**
   - Computes per-subgroup `FPR` ($\frac{\text{FP}}{\text{FP} + \text{TN}}$) and `FNR` ($\frac{\text{FN}}{\text{FN} + \text{TP}}$) before and after human review.
   - Audits `upward_overrides` (`Borderline/Weak -> ADVANCE`), `downward_overrides` (`Strong -> DO_NOT_ADVANCE`), and `override_accuracy_gain` ($\text{Accuracy}_{\text{human}} - \text{Accuracy}_{\text{engine}}$). Any subgroup where downward overrides degrade accuracy triggers an `[OVERRIDE DISPARITY ALERT]`.

---

## 4. Reproducible CLI Exports (`ops-req-fairness`)

```bash
# 1. Run the built-in 12-case fairness & adverse-impact evaluation dashboard
ops-req-fairness --demo

# 2. Export the 12-case cohort manifest JSON and generate Markdown, JSON, and CSV reports
ops-req-fairness --manifest examples/sample_fairness_cohort_manifest.json \
  --output examples/sample_fairness_impact_report.md \
  --json-out fairness_audit.json \
  --csv-out fairness_subgroups.csv
```
