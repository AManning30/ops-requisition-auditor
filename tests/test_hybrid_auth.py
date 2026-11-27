"""Stage 2A security regression tests using synthetic identities and cases."""
import pathlib
import sqlite3
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import governance_middleware as gov
from hybrid_auth import (AuthenticationError, CaseKey, EnterpriseIdentityProvider,
                         Grant, Identity, LocalIdentityProvider, PasswordVerifier,
                         ReviewService)


class HybridAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "synthetic-password-only"
        cls.local = LocalIdentityProvider({"reviewer-one": PasswordVerifier.provision(cls.password)})

    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.case = CaseKey("tenant-a", "req-1", "CAND-001")
        self.other = CaseKey("tenant-b", "req-1", "CAND-001")
        def verify(token):
            if token == "test-valid":
                return Identity("test-idp", "reviewer-two", True)
            if token == "test-machine":
                return Identity("test-idp", "reviewer-two", False)
            if token == "test-wrong-issuer":
                return Identity("other-idp", "reviewer-two", True)
            if token == "test-unassigned":
                return Identity("test-idp", "unassigned", True)
            raise ValueError("invalid")
        self.service = ReviewService(
            self.db, {"local": self.local, "enterprise": EnterpriseIdentityProvider("test-idp", verify)},
            {("local", "reviewer-one"): frozenset({Grant("recruiter", self.case)}),
             ("test-idp", "reviewer-two"): frozenset({Grant("hiring_manager", self.case),
                                                   Grant("read_only_auditor", self.other)})})
        for case in (self.case, self.other):
            self.service.register_case(case, score=90, gate=True, threshold=80, config_hash="a"*64)

    def submit(self, **kwargs):
        args = dict(provider="local", credential=("reviewer-one", self.password),
                    case=self.case, status="ADVANCE", expected_revision=0)
        args.update(kwargs)
        return self.service.record_decision(**args)

    def test_local_credential_and_role_are_bound(self):
        decision = self.submit()
        self.assertEqual((decision.reviewer, decision.reviewer_role, decision.status),
                         ("reviewer-one", "recruiter", "ADVANCE"))
        self.assertTrue(decision.timestamp.endswith("+00:00"))

    def test_invalid_credentials_and_spoofed_fields_fail(self):
        for credential in (("reviewer-one", "incorrect"), ("unknown", self.password), {"role": "recruiter"}):
            with self.assertRaises(AuthenticationError):
                self.submit(credential=credential)
        with self.assertRaises(TypeError):
            self.submit(reviewer_role="hiring_manager")

    def test_enterprise_adapter_uses_verified_identity(self):
        decision = self.submit(provider="enterprise", credential="test-valid")
        self.assertEqual((decision.reviewer, decision.reviewer_role), ("reviewer-two", "hiring_manager"))

    def test_enterprise_machine_wrong_issuer_and_unknown_token_rejected(self):
        for token in ("test-machine", "test-wrong-issuer", "fake"):
            with self.assertRaises(AuthenticationError):
                self.submit(provider="enterprise", credential=token)
        with self.assertRaises(gov.RBACPermissionError):
            self.submit(provider="enterprise", credential="test-unassigned")

    def test_cross_tenant_and_auditor_denied(self):
        with self.assertRaises(gov.RBACPermissionError):
            self.submit(case=self.other)
        with self.assertRaises(gov.RBACPermissionError):
            self.submit(provider="enterprise", credential="test-valid", case=self.other)

    def test_initial_pending_and_stale_transition(self):
        self.assertEqual(self.db.execute("SELECT DISTINCT status FROM authenticated_review_cases").fetchone()[0],
                         gov.MANDATORY_INITIAL_STATE)
        self.submit(status="HOLD")
        with self.assertRaises(gov.GovernanceValidationError):
            self.submit()
        self.submit(expected_revision=1)
        with self.assertRaises(gov.GovernanceValidationError):
            self.submit(status="DO_NOT_ADVANCE", expected_revision=2, reason="Job related evidence")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM authenticated_review_events").fetchone()[0], 2)

    def test_invalid_status_and_negative_reason_rollback(self):
        with self.assertRaises(gov.GovernanceValidationError):
            self.submit(status="HIRED")
        with self.assertRaises(gov.GovernanceValidationError):
            self.submit(status="DO_NOT_ADVANCE", reason="short")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM authenticated_review_events").fetchone()[0], 0)
        self.submit(status="DO_NOT_ADVANCE", reason="Required job evidence not verified")

    def test_no_self_declared_secondary_approver(self):
        with patch.dict("os.environ", {"OPS_REQ_REQUIRE_OVERRIDE_SOD": "1"}):
            with self.assertRaises(gov.GovernanceValidationError):
                self.submit(status="DO_NOT_ADVANCE", reason="Required job evidence not verified")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM authenticated_review_events").fetchone()[0], 0)

    def test_cannot_replace_case_to_reset_state(self):
        self.submit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.register_case(self.case, score=90, gate=True, threshold=80, config_hash="a"*64)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM authenticated_review_events").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
