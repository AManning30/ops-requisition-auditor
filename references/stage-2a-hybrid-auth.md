# Stage 2A: hybrid authentication foundation

Baseline: v1.6.0, commit a88486f18f96940e3a44a28dc8d509dcbc1c418e. Initial implementation for RL-003 and RL-007, not a pilot-ready release.

The new `hybrid_auth.ReviewService` is a trusted application boundary, using one shared authorization and transactional state model for local and enterprise identity providers. Local accounts use operator-provisioned salted scrypt verifiers. Enterprise integration is a dependency-injected verifier contract, **not an implemented OIDC/SSO integration**. The production verifier must verify signatures, issuer, audience, time claims, revocation/session policy and interactive human identity; never accept unverified claims.

Roles come from trusted, explicit (issuer, subject) grants scoped to tenant, requisition and candidate. No default disposition role. The service reauthenticates every submission and derives reviewer, role and timestamp itself. Case registration and grant provisioning are trusted administrative operations, not public endpoints. The database must be protected by the hosting process and filesystem; the review events are transactionally appended, not externally anchored or immutable. The existing governance validator supplies score/gate override and negative-disposition rationale checks. No user-supplied secondary approver is accepted. When secondary approval is required, overrides fail closed until a separately authenticated dual-approval workflow exists.

Allowed transitions: PENDING_HUMAN_REVIEW to HOLD, ADVANCE or DO_NOT_ADVANCE; HOLD to ADVANCE or DO_NOT_ADVANCE. Final dispositions cannot be reopened by this service; a separately authorized correction workflow is future work. Stale revisions fail without updating the case or event history. This is a proposed transition policy requiring product review.

## Merge and pilot blockers

- All existing CI and new security regression tests must pass.
- Bind the real screening CLI, direct Python API, pilot and integration/webhook decision ingestion to this boundary; current legacy validators accept asserted identities and are **not** an authenticated production route.
- Implement and independently verify a real enterprise identity provider integration; credential enrollment, revocation, rate limiting, secret storage, session handling and administrative grants require deployment design. No demo credentials may be provisioned in production.
- Complete employee/manager access and consent authorization, auditor access, separate secondary approval, independent audit anchoring, and threat-model/security review. The current service supports scoped hiring dispositions only.
- No real candidate data, automatic final decisions or release-readiness claims from this foundation.

Run `python -B -m unittest discover -s tests -v` from the repository root. Tests use synthetic credentials and an in-memory SQLite database. The enterprise tests use a fake verifier, not an external identity provider.
