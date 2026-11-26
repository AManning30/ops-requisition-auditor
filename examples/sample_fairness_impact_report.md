# Fairness, Bias & Adverse-Impact Evaluation Dashboard (`ops-req-fairness`)

*Evaluates selection rates, Wilson 95% confidence intervals, exact Fisher p-values, small-sample flip tests, parser/rubric error rates, FPR/FNR, and human reviewer overrides across Race/Ethnicity, Sex, Age Proxies (ADEA >=40), Disability/Accommodation Status, Resume Language (`en`/`es`/`fr`/`de`), and Intersectional (`Race x Sex`) subgroups.*

## 1. Cohort Summary & Equity Telemetry

- **Fairness Engine Version:** `fairness-eval-1.0` (Reference Date: `2026-11-01`)
- **Report Cryptographic Fingerprint (SHA-256):** `5c003d6fd0fb56f32691e5da979b759b3635970172d51560454a0df8b6b218ca`
- **Candidates Evaluated:** `12` across `6` dimensions (`race_ethnicity, sex, age_proxy_band, disability_status, resume_language, intersectional_race_sex`)
- **Advisory Shortlist Cutoff:** `>= 80.0 / 100` + Required Knockout Gates
- **Blind-Mode Score Parity Drift (Max `|Raw - Blind|`):** `0.00 pts` *(0.00 = exact invariance across name, ZIP, graduation year, and disability notes)*
- **Overall Classification Accuracy vs. Validated Outcomes:** Engine Cutoff `100.0%` -> Human-in-the-Loop Post-Override `100.0%`
- **Subgroups with Small-Sample Warnings (`n < 5`):** `16` *(evaluated with Fisher's Exact Test, Wilson 95% CI, and EEOC Q&A #21 single-case flip test)*
- **Statistically Significant Four-Fifths Flags (`< 0.80` after Small-Sample Flip Check):** Engine `2` | Human Post-Override `6`
- **Parser Disparity Alerts:** `0`
- **Reviewer Override Disparity Alerts:** `0`

## 2. Adverse-Impact, Small-Sample Confidence & Four-Fifths (`0.80`) Matrix

| Dimension | Subgroup | `n` (`Share`) | Sample Tier | Engine Sel Rate (`95% Wilson CI`) | Engine IR (`Flip IR`) | Fisher Exact `p` | Engine 4/5ths Status | Human Sel Rate (`Human IR`) |
| :--- | :--- | :-: | :--- | :-: | :-: | :-: | :--- | :-: |
| `race_ethnicity` | **Asian** | `3` (`25.0%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `66.7%` `[20.8%, 93.8%]` | `1.00` (`1.50`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `66.7%` (`1.00`) |
| `race_ethnicity` | **Black/African American** | `3` (`25.0%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `66.7%` `[20.8%, 93.8%]` | `1.00` (`1.50`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `66.7%` (`1.00`) |
| `race_ethnicity` | **Hispanic/Latino** | `3` (`25.0%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `66.7%` `[20.8%, 93.8%]` | `1.00` (`1.50`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `66.7%` (`1.00`) |
| `race_ethnicity` | **White** | `3` (`25.0%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `66.7%` `[20.8%, 93.8%]` | `1.00` (`1.50`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `66.7%` (`1.00`) |
| `sex` | **Female** | `6` (`50.0%`) | `MODERATE_SAMPLE (5<=n<30)` | `66.7%` `[30.0%, 90.3%]` | `1.00` (`1.25`) | `1.000` | PASS (>=0.80) | `66.7%` (`1.00`) |
| `sex` | **Male** | `6` (`50.0%`) | `MODERATE_SAMPLE (5<=n<30)` | `66.7%` `[30.0%, 90.3%]` | `1.00` (`1.25`) | `1.000` | PASS (>=0.80) | `66.7%` (`1.00`) |
| `age_proxy_band` | **40 and Over (>=40)** | `6` (`50.0%`) | `MODERATE_SAMPLE (5<=n<30)` | `66.7%` `[30.0%, 90.3%]` | `1.00` (`1.25`) | `1.000` | PASS (>=0.80) | `66.7%` (`1.00`) |
| `age_proxy_band` | **Under 40 (<40)** | `6` (`50.0%`) | `MODERATE_SAMPLE (5<=n<30)` | `66.7%` `[30.0%, 90.3%]` | `1.00` (`1.25`) | `1.000` | PASS (>=0.80) | `66.7%` (`1.00`) |
| `disability_status` | **Disclosed Disability / Accommodation** | `3` (`25.0%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `100.0%` `[43.9%, 100.0%]` | `1.00` (`1.00`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `100.0%` (`1.00`) |
| `disability_status` | **No Disclosed Disability** | `9` (`75.0%`) | `MODERATE_SAMPLE (5<=n<30)` | `55.6%` `[26.7%, 81.1%]` | `0.56` (`0.67`) | `0.491` | FLAG (<0.80, IR=0.56, p=0.491) | `55.6%` (`0.56`) |
| `resume_language` | **de** | `1` (`8.3%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `100.0%` `[20.6%, 100.0%]` | `1.00` (`1.00`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `100.0%` (`1.00`) |
| `resume_language` | **en** | `9` (`75.0%`) | `MODERATE_SAMPLE (5<=n<30)` | `55.6%` `[26.7%, 81.1%]` | `0.56` (`0.67`) | `1.000` | FLAG (<0.80, IR=0.56, p=1.000) | `55.6%` (`0.56`) |
| `resume_language` | **es** | `1` (`8.3%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `100.0%` `[20.6%, 100.0%]` | `1.00` (`1.00`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `100.0%` (`1.00`) |
| `resume_language` | **fr** | `1` (`8.3%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `100.0%` `[20.6%, 100.0%]` | `1.00` (`1.00`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `100.0%` (`1.00`) |
| `intersectional_race_sex` | **Asian × Female** | `1` (`8.3%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `100.0%` `[20.6%, 100.0%]` | `1.00` (`1.00`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `100.0%` (`1.00`) |
| `intersectional_race_sex` | **Asian × Male** | `2` (`16.7%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `50.0%` `[9.4%, 90.5%]` | `0.50` (`1.00`) | `1.000` | SMALL_SAMPLE_NO_STAT_SIG_DISPARITY (IR=0.50, Flip-IR=1.00, p=1.000) | `50.0%` (`0.50`) |
| `intersectional_race_sex` | **Black/African American × Female** | `2` (`16.7%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `50.0%` `[9.4%, 90.5%]` | `0.50` (`1.00`) | `1.000` | SMALL_SAMPLE_NO_STAT_SIG_DISPARITY (IR=0.50, Flip-IR=1.00, p=1.000) | `50.0%` (`0.50`) |
| `intersectional_race_sex` | **Black/African American × Male** | `1` (`8.3%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `100.0%` `[20.6%, 100.0%]` | `1.00` (`1.00`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `100.0%` (`1.00`) |
| `intersectional_race_sex` | **Hispanic/Latino × Female** | `2` (`16.7%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `50.0%` `[9.4%, 90.5%]` | `0.50` (`1.00`) | `1.000` | SMALL_SAMPLE_NO_STAT_SIG_DISPARITY (IR=0.50, Flip-IR=1.00, p=1.000) | `50.0%` (`0.50`) |
| `intersectional_race_sex` | **Hispanic/Latino × Male** | `1` (`8.3%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `100.0%` `[20.6%, 100.0%]` | `1.00` (`1.00`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `100.0%` (`1.00`) |
| `intersectional_race_sex` | **White × Female** | `1` (`8.3%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `100.0%` `[20.6%, 100.0%]` | `1.00` (`1.00`) | `1.000` | PASS (>=0.80) [Small Sample n<5] | `100.0%` (`1.00`) |
| `intersectional_race_sex` | **White × Male** | `2` (`16.7%`) | `CRITICAL_SMALL_SAMPLE (n<5)` | `50.0%` `[9.4%, 90.5%]` | `0.50` (`1.00`) | `1.000` | SMALL_SAMPLE_NO_STAT_SIG_DISPARITY (IR=0.50, Flip-IR=1.00, p=1.000) | `50.0%` (`0.50`) |

## 3. Parser Error Rate, Rubric Calibration & Blind-Mode Parity by Subgroup

| Dimension | Subgroup | `n` | Parser Conf | Low-Conf Rate (`<75%`) | Parser Error Rate | Mean Engine Score | Mean Expert Score | Signed Bias (`Eng - Exp`) | Rubric `MAE` | Blind Drift (`Δ`) |
| :--- | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| `race_ethnicity` | **Asian** | `3` | `100.0%` | `0.0%` | `0.0%` | `73.1` | `70.7` | `+2.4` | `2.4` | `0.00` |
| `race_ethnicity` | **Black/African American** | `3` | `100.0%` | `0.0%` | `0.0%` | `71.9` | `72.7` | `-0.7` | `2.7` | `0.00` |
| `race_ethnicity` | **Hispanic/Latino** | `3` | `100.0%` | `0.0%` | `0.0%` | `71.9` | `73.0` | `-1.1` | `2.5` | `0.00` |
| `race_ethnicity` | **White** | `3` | `100.0%` | `0.0%` | `0.0%` | `71.9` | `72.3` | `-0.4` | `3.1` | `0.00` |
| `sex` | **Female** | `6` | `100.0%` | `0.0%` | `0.0%` | `72.5` | `72.3` | `+0.2` | `2.2` | `0.00` |
| `sex` | **Male** | `6` | `100.0%` | `0.0%` | `0.0%` | `71.9` | `72.0` | `-0.1` | `3.1` | `0.00` |
| `age_proxy_band` | **40 and Over (>=40)** | `6` | `100.0%` | `0.0%` | `0.0%` | `71.9` | `72.5` | `-0.6` | `3.0` | `0.00` |
| `age_proxy_band` | **Under 40 (<40)** | `6` | `100.0%` | `0.0%` | `0.0%` | `72.5` | `71.8` | `+0.7` | `2.4` | `0.00` |
| `disability_status` | **Disclosed Disability / Accommodation** | `3` | `100.0%` | `0.0%` | `0.0%` | `87.6` | `87.7` | `-0.0` | `3.5` | `0.00` |
| `disability_status` | **No Disclosed Disability** | `9` | `100.0%` | `0.0%` | `0.0%` | `67.1` | `67.0` | `+0.1` | `2.4` | `0.00` |
| `resume_language` | **de** | `1` | `100.0%` | `0.0%` | `0.0%` | `80.7` | `86.0` | `-5.3` | `5.3` | `0.00` |
| `resume_language` | **en** | `9` | `100.0%` | `0.0%` | `0.0%` | `69.4` | `67.7` | `+1.7` | `1.9` | `0.00` |
| `resume_language` | **es** | `1` | `100.0%` | `0.0%` | `0.0%` | `80.7` | `86.0` | `-5.3` | `5.3` | `0.00` |
| `resume_language` | **fr** | `1` | `100.0%` | `0.0%` | `0.0%` | `80.7` | `85.0` | `-4.3` | `4.3` | `0.00` |

## 4. False-Positive / False-Negative Rates & Reviewer Override Audit

| Dimension | Subgroup | `n` | `TP / FP / TN / FN` (Engine) | Engine `FPR` | Engine `FNR` | Human `FPR` | Human `FNR` | Overrides (`Up / Down`) | Override Rate | Accuracy Gain from Human Review |
| :--- | :--- | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: | :-: |
| `race_ethnicity` | **Asian** | `3` | `2 / 0 / 1 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `race_ethnicity` | **Black/African American** | `3` | `2 / 0 / 1 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `race_ethnicity` | **Hispanic/Latino** | `3` | `2 / 0 / 1 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `race_ethnicity` | **White** | `3` | `2 / 0 / 1 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `sex` | **Female** | `6` | `4 / 0 / 2 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `sex` | **Male** | `6` | `4 / 0 / 2 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `age_proxy_band` | **40 and Over (>=40)** | `6` | `4 / 0 / 2 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `age_proxy_band` | **Under 40 (<40)** | `6` | `4 / 0 / 2 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `disability_status` | **Disclosed Disability / Accommodation** | `3` | `3 / 0 / 0 / 0` | `—` | `0.0%` | `—` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `disability_status` | **No Disclosed Disability** | `9` | `5 / 0 / 4 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `resume_language` | **de** | `1` | `1 / 0 / 0 / 0` | `—` | `0.0%` | `—` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `resume_language` | **en** | `9` | `5 / 0 / 4 / 0` | `0.0%` | `0.0%` | `0.0%` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `resume_language` | **es** | `1` | `1 / 0 / 0 / 0` | `—` | `0.0%` | `—` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
| `resume_language` | **fr** | `1` | `1 / 0 / 0 / 0` | `—` | `0.0%` | `—` | `0.0%` | `+0 / -0` | `0.0%` | `+0.0%` |
