#!/usr/bin/env python3
"""Unit tests for Human-Approval & Anti-Automation Governance Middleware (ops-req-governance)."""

from __future__ import annotations

import copy
import dataclasses
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import governance_middleware as gov  # noqa: E402
import explainable_screening_assistant as esa  # noqa: E402
import offline_ats_auditor as oaa  # noqa: E402


class TestGovernanceMiddleware(unittest.TestCase):
    """Tests for PENDING_HUMAN_REVIEW enforcement, anti-automation blocks, RBAC, schema validation, and hash-chain immutability."""

    def setUp(self) -> None:
        self.cfg_path = ROOT / "examples" / "sample_ats_config.json"
        self.reqs, self.align_cfg = oaa.load_jd_config(str(self.cfg_path))
        self.manifest = gov.build_version_manifest(self.reqs, self.align_cfg)

    def test_enforces_pending_human_review_by_default(self) -> None:
        """Unreviewed candidates must always default to PENDING_HUMAN_REVIEW and never auto-reject."""
        dec = gov.validate_and_seal_decision(
            candidate_id="CAND-LOW-01",
            raw_decision=None,
            composite_score=22.5,
            knockout_passed=False,
            config_sha256=self.manifest.config_sha256,
        )
        self.assertEqual(dec.status, "PENDING_HUMAN_REVIEW")
        self.assertFalse(dec.is_override)
        self.assertEqual(len(dec.decision_sha256), 64)

    def test_blocks_prohibited_cli_flags_and_api_modes(self) -> None:
        """Blocks CLI flags and API keys that attempt auto-rejection or autonomous hiring recommendations."""
        for bad_flag in ("--auto-reject", "--auto-advance", "--autonomous-decision", "--hiring-recommendation", "--skip-human-review=true"):
            with self.assertRaises(gov.AutomationPolicyViolationError):
                gov.enforce_anti_automation_cli([bad_flag])

        # Verify CLI exit code 2 on ops-req-screen and ops-req-governance when prohibited flag is passed
        self.assertEqual(esa.main(["--demo", "--auto-reject"]), 2)
        self.assertEqual(gov.main(["--demo", "--hiring-recommendation"]), 2)

        for bad_key in ("auto_reject", "auto_advance", "autonomous_decision", "hiring_recommendation"):
            with self.assertRaises(gov.AutomationPolicyViolationError):
                gov.enforce_anti_automation_api_params({bad_key: True})

    def test_rejects_bot_or_system_reviewer_identities_and_missing_timestamps(self) -> None:
        """Non-pending decisions must be signed by a human reviewer with a valid ISO-8601 timestamp."""
        for bot_id in ("auto_screener", "bot-1", "system", "AI_Agent", "cron_job", "pipeline", "unattended"):
            with self.assertRaises(gov.AutomationPolicyViolationError):
                gov.validate_and_seal_decision(
                    candidate_id="CAND-001",
                    raw_decision={
                        "status": "ADVANCE",
                        "reviewer": bot_id,
                        "timestamp": "2026-11-15T09:00:00Z",
                    },
                    composite_score=95.0,
                    knockout_passed=True,
                )

        # Missing reviewer
        with self.assertRaises(gov.GovernanceValidationError):
            gov.validate_and_seal_decision(
                candidate_id="CAND-001",
                raw_decision={"status": "ADVANCE", "reviewer": "", "timestamp": "2026-11-15T09:00:00Z"},
                composite_score=95.0,
                knockout_passed=True,
            )

        # Invalid timestamp
        with self.assertRaises(gov.GovernanceValidationError):
            gov.validate_and_seal_decision(
                candidate_id="CAND-001",
                raw_decision={"status": "ADVANCE", "reviewer": "recruiter_j_smith", "timestamp": "yesterday"},
                composite_score=95.0,
                knockout_passed=True,
            )

    def test_requires_override_reason_on_overrides_and_non_advancements(self) -> None:
        """Requires a substantive human override_reason when overriding score tiers or recording DO_NOT_ADVANCE."""
        # Advancing a candidate below 80.0 threshold without an override_reason must fail
        with self.assertRaises(gov.GovernanceValidationError):
            gov.validate_and_seal_decision(
                candidate_id="CAND-003",
                raw_decision={
                    "status": "ADVANCE",
                    "reviewer": "recruiter_j_smith",
                    "timestamp": "2026-11-15T09:12:00Z",
                    "override_reason": "",
                },
                composite_score=74.0,
                knockout_passed=True,
            )

        # Recording DO_NOT_ADVANCE without a documented human rationale must fail
        with self.assertRaises(gov.GovernanceValidationError):
            gov.validate_and_seal_decision(
                candidate_id="CAND-004",
                raw_decision={
                    "status": "DO_NOT_ADVANCE",
                    "reviewer": "recruiter_j_smith",
                    "timestamp": "2026-11-15T09:15:00Z",
                    "override_reason": "too short",
                },
                composite_score=33.0,
                knockout_passed=False,
            )

        # Valid override succeeds and marks is_override=True
        valid_override = gov.validate_and_seal_decision(
            candidate_id="CAND-003",
            raw_decision={
                "status": "ADVANCE",
                "reviewer": "recruiter_j_smith",
                "reviewer_role": "hiring_manager",
                "timestamp": "2026-11-15T09:12:00Z",
                "override_reason": "Strong Python/SQL/Snowflake in recent role; verify SAP S/4HANA in interview.",
            },
            composite_score=74.0,
            knockout_passed=True,
            config_sha256=self.manifest.config_sha256,
        )
        self.assertTrue(valid_override.is_override)
        self.assertEqual(valid_override.reviewer_role, "hiring_manager")

    def test_rbac_and_environment_level_safeguards(self) -> None:
        """Verifies RBAC blocks read-only roles from recording dispositions and env safeguards block auto-reject vars."""
        for restricted_role in ("read_only_auditor", "candidate", "mobility_counselor"):
            with self.assertRaises(gov.RBACPermissionError):
                gov.validate_and_seal_decision(
                    candidate_id="CAND-001",
                    raw_decision={
                        "status": "ADVANCE",
                        "reviewer": "auditor_a_lee",
                        "reviewer_role": restricted_role,
                        "timestamp": "2026-11-15T09:00:00Z",
                    },
                    composite_score=95.0,
                    knockout_passed=True,
                )

        # Prohibited environment variable must raise EnvironmentSafeguardError
        with self.assertRaises(gov.EnvironmentSafeguardError):
            gov.enforce_environment_safeguards(env={"OPS_REQ_AUTO_REJECT": "true"})
        with self.assertRaises(gov.EnvironmentSafeguardError):
            gov.enforce_environment_safeguards(env={"ATS_AUTO_DECIDE": "1"})

    def test_hash_chained_immutability_and_tamper_detection(self) -> None:
        """Verifies SHA-256 hash chain across batch screening and detects any post-sign-off tampering."""
        candidates, reqs, align_cfg, decisions, demographics = esa.get_demo_batch()
        batch = esa.screen_batch(
            candidates,
            reqs,
            align_cfg,
            datetime(2026, 11, 1),
            blind_mode=True,
            decisions_map=decisions,
            demographics_map=demographics,
        )
        # Verify version manifest logging
        self.assertEqual(batch.engine_version, "1.6.0")
        self.assertEqual(batch.jd_alignment_rubric_version, "jd-align-1.6")
        self.assertEqual(batch.rqs_rubric_version, "rqs-0.2")
        self.assertEqual(batch.governance_policy_version, "gov-hitl-1.0")
        self.assertEqual(len(batch.config_sha256), 64)
        self.assertEqual(len(batch.immutable_audit_chain), 4)

        # Untampered chain passes verification
        ok, errs = gov.verify_audit_chain_integrity(batch.immutable_audit_chain)
        self.assertTrue(ok)
        self.assertEqual(errs, [])

        # Tamper 1: mutate a candidate's composite_score after sign-off
        tampered_score = copy.deepcopy(batch.immutable_audit_chain)
        tampered_score[1]["composite_score"] = 99.9
        with self.assertRaises(gov.AuditImmutabilityError):
            gov.verify_audit_chain_integrity(tampered_score)

        # Tamper 2: mutate a candidate's disposition status from PENDING_HUMAN_REVIEW to DO_NOT_ADVANCE
        tampered_status = copy.deepcopy(batch.immutable_audit_chain)
        tampered_status[3]["decision"]["status"] = "DO_NOT_ADVANCE"
        with self.assertRaises(gov.AuditImmutabilityError):
            gov.verify_audit_chain_integrity(tampered_status)

        # Verify CLI --validate-decisions and --verify-audit-json
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_json = Path(tmpdir) / "batch_audit.json"
            audit_json.write_text(json.dumps(dataclasses.asdict(batch), indent=2), encoding="utf-8")
            self.assertEqual(
                gov.main(["--verify-audit-json", str(audit_json)]),
                0,
            )
            self.assertEqual(
                gov.main(["--validate-decisions", str(ROOT / "examples" / "sample_screening_decisions.json")]),
                0,
            )

    def test_sqlite_wal_audit_ledger_and_override_sod(self) -> None:
        """Verifies secondary approver SoD on score overrides and SQLite WAL append-only audit ledger immutability."""
        # 1. Self-approval on override must raise GovernanceValidationError
        with self.assertRaises(gov.GovernanceValidationError):
            gov.validate_and_seal_decision(
                candidate_id="CAND-003",
                raw_decision={
                    "status": "ADVANCE",
                    "reviewer": "recruiter_j_smith",
                    "secondary_approver_id": "recruiter_j_smith",
                    "timestamp": "2026-11-15T09:12:00Z",
                    "override_reason": "Strong domain skills verified in phone screen.",
                },
                composite_score=74.0,
                knockout_passed=True,
            )

        # 2. Independent secondary approver succeeds
        dec = gov.validate_and_seal_decision(
            candidate_id="CAND-003",
            raw_decision={
                "status": "ADVANCE",
                "reviewer": "recruiter_j_smith",
                "secondary_approver_id": "ta_lead_m_chen",
                "secondary_approver_role": "ta_lead",
                "require_secondary_approver": True,
                "timestamp": "2026-11-15T09:12:00Z",
                "override_reason": "Strong domain skills verified in phone screen.",
            },
            composite_score=74.0,
            knockout_passed=True,
            config_sha256=self.manifest.config_sha256,
        )
        self.assertEqual(dec.secondary_approver_id, "ta_lead_m_chen")

        # 3. SQLite WAL append-only ledger chains multiple batches and blocks UPDATE/DELETE via SQL triggers
        import sqlite3

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "audit_ledger.sqlite")
            e1 = gov.append_audit_chain_to_sqlite(
                db_path,
                [{"candidate_id": "CAND-003", "composite_score": 74.0, "knockout_passed": True, "evidence_tier": "Tier B", "decision": dec}],
                self.manifest,
            )
            e2 = gov.append_audit_chain_to_sqlite(
                db_path,
                [{"candidate_id": "CAND-001", "composite_score": 100.0, "knockout_passed": True, "evidence_tier": "Tier A", "decision": dec}],
                self.manifest,
            )
            self.assertEqual(e1[0].sequence_index, 1)
            self.assertEqual(e2[0].sequence_index, 2)
            self.assertEqual(e2[0].previous_entry_sha256, e1[0].entry_sha256)

            loaded = gov.load_and_verify_sqlite_audit_chain(db_path)
            self.assertEqual(len(loaded), 2)

            conn = sqlite3.connect(db_path)
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("UPDATE audit_chain_ledger SET composite_score = 99.0 WHERE sequence_index = 1")
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("DELETE FROM audit_chain_ledger WHERE sequence_index = 1")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
