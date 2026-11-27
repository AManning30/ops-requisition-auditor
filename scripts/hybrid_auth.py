"""Stage 2A authenticated review boundary (not a production identity server).

Only a trusted application may configure providers, grants, case registration and
the database connection. Never expose these administrative operations to clients.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import dataclasses
import hashlib
import hmac
import json
import math
import secrets
import sqlite3
from typing import Callable, Mapping, Protocol

import governance_middleware as gov


class AuthenticationError(gov.GovernanceError):
    """Credentials were missing or invalid."""


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    human: bool


class IdentityProvider(Protocol):
    def authenticate(self, credential: object) -> Identity: ...


@dataclass(frozen=True)
class PasswordVerifier:
    salt: bytes
    digest: bytes

    @classmethod
    def provision(cls, password: str) -> PasswordVerifier:
        if not isinstance(password, str) or len(password) < 14:
            raise ValueError("Local password must have at least 14 characters")
        salt = secrets.token_bytes(16)
        return cls(salt, cls._derive(password, salt))

    @staticmethod
    def _derive(password: str, salt: bytes) -> bytes:
        return hashlib.scrypt(password.encode("utf-8"), salt=salt,
                              n=2**15, r=8, p=3, maxmem=64 * 1024 * 1024)

    def matches(self, password: str) -> bool:
        return hmac.compare_digest(self.digest, self._derive(password, self.salt))


class LocalIdentityProvider:
    """Verify local credentials against operator-provisioned password verifiers."""

    def __init__(self, accounts: Mapping[str, PasswordVerifier]):
        self._accounts = dict(accounts)
        self._dummy = PasswordVerifier.provision(secrets.token_hex(32))

    def authenticate(self, credential: object) -> Identity:
        if (not isinstance(credential, tuple) or len(credential) != 2
                or not all(isinstance(x, str) for x in credential)):
            raise AuthenticationError("Invalid credentials")
        subject, password = credential
        if len(password) > 1024 or len(subject) > 256:
            raise AuthenticationError("Invalid credentials")
        verifier = self._accounts.get(subject, self._dummy)
        valid = verifier.matches(password)
        if not valid or subject not in self._accounts:
            raise AuthenticationError("Invalid credentials")
        return Identity("local", subject, True)


class EnterpriseIdentityProvider:
    """Adapter for a trusted external token verifier, not a JWT decoder.

    The injected verifier MUST check signature, trusted issuer, audience,
    expiration, revocation/session policy and interactive human identity.
    Never inject a parser that merely decodes caller-supplied claims.
    """

    def __init__(self, issuer: str, verify_token: Callable[[str], Identity]):
        if not issuer or issuer == "local":
            raise ValueError("Enterprise issuer must be distinct from local")
        self._issuer = issuer
        self._verify_token = verify_token

    def authenticate(self, credential: object) -> Identity:
        if not isinstance(credential, str) or not credential or len(credential) > 16384:
            raise AuthenticationError("Invalid enterprise credential")
        try:
            identity = self._verify_token(credential)
        except Exception:
            raise AuthenticationError("Invalid enterprise credential") from None
        if (not isinstance(identity, Identity) or identity.issuer != self._issuer
                or not identity.subject or identity.human is not True):
            raise AuthenticationError("Invalid enterprise identity")
        return identity


@dataclass(frozen=True)
class CaseKey:
    tenant: str
    requisition: str
    candidate: str

    def __post_init__(self):
        if any(not isinstance(x, str) or not x.strip() or len(x) > 256
               for x in dataclasses.astuple(self)):
            raise ValueError("Invalid case scope")


@dataclass(frozen=True)
class Grant:
    role: str
    case: CaseKey


class ReviewService:
    """Shared authorization and transactional review-state boundary.

    The connection must be exclusive to this trusted service, with no other
    application transactions active; provision separate connections per worker.
    Grants are server configuration, never token or request payload fields.
    """

    _transitions = {
        gov.MANDATORY_INITIAL_STATE: frozenset({"ADVANCE", "HOLD", "DO_NOT_ADVANCE"}),
        "HOLD": frozenset({"ADVANCE", "DO_NOT_ADVANCE"}),
        "ADVANCE": frozenset(),
        "DO_NOT_ADVANCE": frozenset(),
    }

    def __init__(self, db: sqlite3.Connection,
                 providers: Mapping[str, IdentityProvider],
                 grants: Mapping[tuple[str, str], frozenset[Grant]]):
        self._db = db
        self._providers = dict(providers)
        self._grants = {key: frozenset(value) for key, value in grants.items()}
        self._db.executescript("""
            CREATE TABLE IF NOT EXISTS authenticated_review_cases (
                tenant TEXT, requisition TEXT, candidate TEXT,
                score REAL NOT NULL, gate INTEGER NOT NULL, threshold REAL NOT NULL,
                config_hash TEXT NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL,
                PRIMARY KEY (tenant, requisition, candidate)
            );
            CREATE TABLE IF NOT EXISTS authenticated_review_events (
                tenant TEXT, requisition TEXT, candidate TEXT, revision INTEGER,
                identity_issuer TEXT NOT NULL, identity_subject TEXT NOT NULL,
                decision_json TEXT NOT NULL,
                PRIMARY KEY (tenant, requisition, candidate, revision)
            );
        """)

    def register_case(self, case: CaseKey, *, score: float, gate: bool,
                      threshold: float, config_hash: str) -> None:
        """Trusted ingestion only; never reset or replace an existing case."""
        if (not math.isfinite(score) or not 0 <= score <= 100
                or not math.isfinite(threshold) or not 0 <= threshold <= 100
                or type(gate) is not bool or not isinstance(config_hash, str)
                or len(config_hash) != 64
                or any(c not in "0123456789abcdef" for c in config_hash)):
            raise gov.GovernanceValidationError("Invalid scoring context")
        with self._db:
            self._db.execute(
                "INSERT INTO authenticated_review_cases VALUES (?,?,?,?,?,?,?,?,?)",
                (*dataclasses.astuple(case), score, int(gate), threshold,
                 config_hash, gov.MANDATORY_INITIAL_STATE, 0),
            )

    def _authorized_role(self, provider: str, credential: object,
                         case: CaseKey) -> tuple[Identity, str]:
        adapter = self._providers.get(provider)
        if adapter is None:
            raise AuthenticationError("Unknown identity provider")
        identity = adapter.authenticate(credential)
        if (not isinstance(identity, Identity) or identity.human is not True
                or not identity.issuer or not identity.subject
                or gov.PROHIBITED_BOT_REVIEWER_PATTERNS.match(identity.subject)):
            raise AuthenticationError("Human identity required")
        roles = {g.role for g in self._grants.get((identity.issuer, identity.subject), ())
                 if g.case == case
                 and gov.RBAC_ROLE_PERMISSIONS.get(g.role, {}).get("can_record_disposition")}
        if len(roles) != 1:
            raise gov.RBACPermissionError("No unambiguous disposition grant for case")
        return identity, next(iter(roles))

    def record_decision(self, provider: str, credential: object, case: CaseKey,
                        *, status: str, reason: str = "",
                        expected_revision: int) -> gov.ValidatedHumanDecision:
        """Authenticate each submission and bind identity and scope server-side."""
        identity, role = self._authorized_role(provider, credential, case)
        gov.enforce_environment_safeguards(active_role=role)
        if (type(status) is not str or type(reason) is not str
                or type(expected_revision) is not int or expected_revision < 0):
            raise gov.GovernanceValidationError("Invalid decision schema")
        if self._db.in_transaction:
            raise gov.GovernanceError("Review requires a dedicated transaction")
        self._db.execute("BEGIN IMMEDIATE")
        try:
            row = self._db.execute(
                "SELECT score,gate,threshold,config_hash,status,revision "
                "FROM authenticated_review_cases WHERE tenant=? AND requisition=? AND candidate=?",
                dataclasses.astuple(case)).fetchone()
            if row is None:
                raise gov.RBACPermissionError("Case not available")
            score, gate, threshold, config_hash, current, revision = row
            if revision != expected_revision or status not in self._transitions[current]:
                raise gov.GovernanceValidationError("Invalid or stale decision transition")
            # The legacy validator owns evidence-tier and override rules; this
            # boundary owns identity, timestamp, scope and state. Secondary
            # approval is deliberately unavailable until independently authenticated.
            decision = gov.validate_and_seal_decision(
                candidate_id=case.candidate,
                raw_decision={
                    "status": status,
                    "reviewer": identity.subject,
                    "reviewer_role": role,
                    "timestamp": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    "override_reason": reason,
                },
                composite_score=score, knockout_passed=bool(gate),
                shortlist_threshold=threshold, config_sha256=config_hash,
                enforced_role=role,
            )
            self._db.execute(
                "INSERT INTO authenticated_review_events VALUES (?,?,?,?,?,?,?)",
                (*dataclasses.astuple(case), revision + 1, identity.issuer,
                 identity.subject, json.dumps(dataclasses.asdict(decision), sort_keys=True)),
            )
            self._db.execute(
                "UPDATE authenticated_review_cases SET status=?,revision=? "
                "WHERE tenant=? AND requisition=? AND candidate=?",
                (status, revision + 1, *dataclasses.astuple(case)),
            )
            self._db.commit()
            return decision
        except BaseException:
            self._db.rollback()
            raise
