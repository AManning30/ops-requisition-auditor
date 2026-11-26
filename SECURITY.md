# Security & Responsible Disclosure Policy (`SECURITY.md`)

`ops-requisition-auditor` processes sensitive employment documents (resumes, job requisitions, internal mobility plans, and voluntary demographic self-ID manifests). We treat **both** traditional software security vulnerabilities and **algorithmic fairness / privacy / governance bypasses** as security-critical issues.

---

## 1. Supported Versions

| Version | Supported | Rubric & Contract Versions |
| :--- | :---: | :--- |
| **`1.6.x`** | **Yes** | `jd-align-1.6`, `rqs-0.2`, `ats-neutral-contract-1.0`, `shared-vectors-1.0`, `wfm-reskill-cat-2.0` |
| **`1.5.x`** | Security Fixes Only | `rqs-0.2` |
| `< 1.5.0` | Upgrade to `1.6.0+` | Legacy versions prior to Workstreams 1–13 governance hardening |

---

## 2. Scope of Responsible Disclosure

We welcome responsible disclosure reports covering:

1. **Security & Privacy Vulnerabilities:**
   - Path traversal, XML/ZIP bomb handling in `.docx`/`.pdf` parsers, or SSRF in `job_posting_collector.py`.
   - Cryptographic or redaction bypasses in `privacy_data_minimization.py` (`ops-req-privacy`), including PII leakage in logs or failure of 3-vault separation (`PIIVaultRecord` vs. `ScoringVaultRecord` vs. `DemographicVaultRecord`).
   - HMAC-SHA256 webhook signature verification or replay-window bypasses in `integration_adapters_and_webhooks.py` (`ops-req-integrate`).
2. **Algorithmic Fairness, Blind-Mode & Governance Bypasses:**
   - Any input where blind-mode PII/ZIP/graduation-year redaction causes non-zero score drift (`blind_score != raw_score`).
   - Bypasses of Separation-of-Duties (SoD), human-in-the-loop anti-automation checks, or hash-chain integrity verification in `governance_middleware.py` (`ops-req-governance`) or `org_customization_registry.py` (`ops-req-org-config`).

---

## 3. How to Report a Vulnerability or Governance Defect

- **GitHub Security Advisories (Preferred):** Open a private security advisory at `https://github.com/AManning30/ops-requisition-auditor/security/advisories/new`.
- **What to Include:**
  - A minimal, synthetic reproduction command or fixture (**never include real candidate PII in a bug report**).
  - The affected CLI module (`ops-req-audit`, `ops-req-screen`, `ops-req-privacy`, etc.) and Python version.
  - Expected vs. observed behavior.

---

## 4. Disclosure Timeline & Release Process

- **Acknowledgment:** Within **48 hours** of receipt.
- **Triage & Deterministic Reproduction:** Within **5 business days**, including a new regression unit test in `tests/`.
- **Patch & Coordinated Release:** Within **14 days** for critical privacy/governance issues, documented in `CHANGELOG.md` with a corresponding policy-pack or rubric version bump if scoring behavior is affected.
