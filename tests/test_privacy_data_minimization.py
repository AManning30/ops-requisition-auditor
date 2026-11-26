#!/usr/bin/env python3
"""
Unit tests for Privacy, Data Minimization, Three-Vault Segregation, Retention,
Deletion API, and Authenticated Export Encryption (`privacy_data_minimization.py`).
"""
from __future__ import annotations

import copy
from datetime import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import explainable_screening_assistant as esa  # noqa: E402
import internal_mobility_matcher as imm  # noqa: E402
import privacy_data_minimization as pdm  # noqa: E402


class TestPrivacyDataMinimization(unittest.TestCase):
    def test_explicit_consent_enforcement_batch_and_mobility(self) -> None:
        consent_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "examples",
            "sample_consent_records.json",
        )
        cmap = pdm.load_consent_records(consent_path)
        rec = pdm.verify_explicit_consent(
            "CAND-001",
            "batch_screening",
            consent_map=cmap,
            reference_date="2026-11-01",
            require_explicit_consent=True,
        )
        self.assertTrue(rec.consented)
        self.assertFalse(rec.revoked)
        self.assertEqual(len(rec.consent_sha256), 64)

        # Missing consent record blocks when require_explicit_consent=True
        with self.assertRaises(pdm.PrivacyConsentError):
            pdm.verify_explicit_consent(
                "CAND-999",
                "batch_screening",
                consent_map=cmap,
                reference_date="2026-11-01",
                require_explicit_consent=True,
            )

        # Revoked consent blocks even when require_explicit_consent=False
        revoked_cmap = dict(cmap)
        revoked_cmap["CAND-002:batch_screening"] = pdm.build_consent_record(
            {
                "subject_id": "CAND-002",
                "flow_type": "batch_screening",
                "consented": True,
                "consent_scope": ["batch_screening"],
                "consent_timestamp": "2026-11-01T08:00:00Z",
                "consent_method": "applicant_portal_opt_in",
                "revoked": True,
                "revoked_timestamp": "2026-11-02T10:00:00Z",
            }
        )
        candidates, reqs, align_cfg, decisions, demographics = esa.get_demo_batch()
        with self.assertRaises(pdm.PrivacyConsentError):
            esa.screen_batch(
                candidates,
                reqs,
                align_cfg,
                datetime(2026, 11, 1),
                decisions_map=decisions,
                demographics_map=demographics,
                consent_map=revoked_cmap,
            )

        # Internal mobility blocks unconsented manager sharing when consent record has share_with_manager=False
        resume_txt, roles = imm.get_demo_career_ladder()
        with self.assertRaises(pdm.PrivacyConsentError):
            imm.match_resume_to_roles(
                resume_text=resume_txt,
                role_entries=roles,
                reference_date=datetime(2026, 11, 1),
                share_with_manager=True,
                subject_id="EMP-001",
                consent_map=cmap,
                require_explicit_consent=True,
            )

    def test_three_vault_segregation_and_isolation_proof(self) -> None:
        vault, policy, _ = pdm.run_privacy_demo(reference_date="2026-11-01")
        self.assertTrue(vault.isolation_verified)
        self.assertEqual(len(vault.isolation_proof_sha256), 64)
        self.assertEqual(policy.processing_mode, "ephemeral_in_memory")
        self.assertEqual(policy.mobility_profile_retention_days, 0)

        # CAND-004 was erased from PII & demographics vaults in the demo while CAND-001..003 remain
        self.assertEqual(len(vault.pii_vault), 3)
        self.assertEqual(len(vault.demographics_vault), 3)
        self.assertEqual(len(vault.scoring_vault), 4)

        # Verify RBAC access boundaries across the 3 vaults
        scoring_view = pdm.read_vault_with_access_boundary(vault, "scoring_vault", "hiring_manager")
        self.assertEqual(len(scoring_view), 4)
        with self.assertRaises(pdm.PrivacyAccessDeniedError):
            pdm.read_vault_with_access_boundary(vault, "pii_vault", "hiring_manager")
        with self.assertRaises(pdm.PrivacyAccessDeniedError):
            pdm.read_vault_with_access_boundary(vault, "demographics_vault", "recruiter")
        demo_view = pdm.read_vault_with_access_boundary(vault, "demographics_vault", "hr_compliance")
        self.assertEqual(len(demo_view), 3)

    def test_vault_isolation_detects_injected_pii_or_demographics(self) -> None:
        vault, _, _ = pdm.run_privacy_demo(reference_date="2026-11-01")

        # Inject a demographic field into scoring_vault -> must raise PrivacyIsolationError
        bad_vault1 = copy.deepcopy(vault)
        first_tok = next(iter(bad_vault1.scoring_vault))
        bad_vault1.scoring_vault[first_tok]["gender"] = "Female"
        with self.assertRaises(pdm.PrivacyIsolationError):
            pdm.verify_vault_isolation(bad_vault1)

        # Inject an email address into scoring_vault quote -> must raise PrivacyIsolationError
        bad_vault2 = copy.deepcopy(vault)
        bad_vault2.scoring_vault[first_tok]["leaked_note"] = "Contact candidate at leaked.user@example.com"
        with self.assertRaises(pdm.PrivacyIsolationError):
            pdm.verify_vault_isolation(bad_vault2)

        # Inject a score into demographics_vault -> must raise PrivacyIsolationError
        bad_vault3 = copy.deepcopy(vault)
        demo_tok = next(iter(bad_vault3.demographics_vault))
        bad_vault3.demographics_vault[demo_tok]["composite_score"] = 88.3
        with self.assertRaises(pdm.PrivacyIsolationError):
            pdm.verify_vault_isolation(bad_vault3)

    def test_configurable_retention_ttl_and_right_to_erasure(self) -> None:
        vault, _, _ = pdm.run_privacy_demo(reference_date="2026-11-01")
        # Purge CAND-001 across all 3 vaults (including scoring_vault)
        receipt = pdm.purge_subject_across_vaults(
            vault,
            subject_id_or_token="CAND-001",
            purge_scoring_vault=True,
        )
        self.assertEqual(receipt.verification_status, "VERIFIED_PURGED")
        self.assertEqual(sorted(receipt.vaults_purged), ["demographics_vault", "pii_vault", "scoring_vault"])
        tok1 = pdm.compute_pseudonym_token("CAND-001")
        self.assertNotIn(tok1, vault.pii_vault)
        self.assertNotIn(tok1, vault.scoring_vault)
        self.assertNotIn(tok1, vault.demographics_vault)

        # Advance date to 2026-12-15 (past 30-day PII retention TTL, within 180-day demographics & 365-day score TTL)
        ttl_receipts = pdm.enforce_retention_schedule(vault, evaluation_date="2026-12-15")
        self.assertEqual(len(ttl_receipts), 2)  # remaining 2 PII records expired and purged
        self.assertEqual(len(vault.pii_vault), 0)
        self.assertEqual(len(vault.demographics_vault), 2)
        self.assertEqual(len(vault.scoring_vault), 3)

        # Test secure_delete_file overwrites and unlinks
        with tempfile.TemporaryDirectory() as tmpdir:
            secret_file = os.path.join(tmpdir, "temp_resume_pii.txt")
            with open(secret_file, "w", encoding="utf-8") as fh:
                fh.write("JORDAN VANCE | jordan.vance@example.com | (314) 555-0142 | SSN 123-45-6789")
            f_receipt = pdm.secure_delete_file(secret_file, passes=2)
            self.assertFalse(os.path.exists(secret_file))
            self.assertEqual(f_receipt.verification_status, "VERIFIED_UNLINKED")
            self.assertEqual(f_receipt.overwrite_passes, 2)
            self.assertGreater(f_receipt.bytes_overwritten, 100)

    def test_authenticated_export_encryption_and_tamper_detection(self) -> None:
        secret_payload = json.dumps({"candidate": "SUBJ-001", "score": 88.3, "notice": "Confidential export"})
        passphrase = "correct-horse-battery-staple-2026"

        envelope = pdm.encrypt_persistent_export(
            secret_payload,
            passphrase=passphrase,
            associated_metadata={"module": "explainable-screening"},
        )
        self.assertEqual(envelope["schema"], pdm.ENCRYPTED_EXPORT_SCHEMA)
        self.assertNotIn("Confidential export", envelope["ciphertext_b64"])

        recovered = pdm.decrypt_persistent_export(envelope, passphrase=passphrase)
        self.assertEqual(recovered, secret_payload)

        # Wrong passphrase must fail closed
        with self.assertRaises(pdm.PrivacyEncryptionError):
            pdm.decrypt_persistent_export(envelope, passphrase="wrong-passphrase-12345")

        # Tampered metadata or ciphertext must fail closed (Encrypt-then-MAC verification)
        tampered = copy.deepcopy(envelope)
        tampered["metadata"]["module"] = "tampered-module"
        with self.assertRaises(pdm.PrivacyEncryptionError):
            pdm.decrypt_persistent_export(tampered, passphrase=passphrase)

    def test_log_redaction_scrubs_pii_and_secrets(self) -> None:
        raw_log = (
            "Error parsing resume for jane.doe@example.com, phone (312) 555-0199, "
            "address Chicago, IL 60601-2345, profile https://linkedin.com/in/janedoe, "
            "SSN 123-45-6789, auth Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        )
        redacted, counts = pdm.redact_log_message(raw_log)
        self.assertNotIn("jane.doe@example.com", redacted)
        self.assertNotIn("555-0199", redacted)
        self.assertNotIn("60601", redacted)
        self.assertNotIn("linkedin.com/in/janedoe", redacted)
        self.assertNotIn("123-45-6789", redacted)
        self.assertNotIn("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9", redacted)
        self.assertEqual(counts["email"], 1)
        self.assertEqual(counts["phone"], 1)
        self.assertEqual(counts["zip_code"], 1)
        self.assertEqual(counts["url"], 1)
        self.assertEqual(counts["ssn"], 1)
        self.assertEqual(counts["secret"], 1)


if __name__ == "__main__":
    unittest.main()
