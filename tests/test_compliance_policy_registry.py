#!/usr/bin/env python3
"""
Unit tests for Compliance Policy Versioning & Jurisdiction Registry (`compliance_policy_registry.py`)
and organization-selectable policy packs (`--policy-pack`) in `explainable_screening_assistant.py`.
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
import compliance_policy_registry as cpr  # noqa: E402
import explainable_screening_assistant as esa  # noqa: E402


class TestCompliancePolicyRegistry(unittest.TestCase):
    def test_default_policy_pack_validates_and_has_six_counsel_approved_rules(self) -> None:
        pack = cpr.load_policy_pack()
        self.assertEqual(pack.policy_pack_id, cpr.DEFAULT_POLICY_PACK_ID)
        self.assertEqual(pack.policy_pack_version, cpr.DEFAULT_POLICY_PACK_VERSION)
        self.assertEqual(len(pack.policy_pack_sha256), 64)
        self.assertEqual(len(pack.rules), 6)

        rule_ids = {r.rule_id for r in pack.rules}
        self.assertEqual(
            rule_ids,
            {
                "POL-CO-SB24-205",
                "POL-NYC-LL144",
                "POL-IL-HB3773",
                "POL-FED-EEOC-UGESP",
                "POL-FED-ADEA-GRAD-YEAR",
                "POL-US-PAY-TRANSPARENCY",
            },
        )
        for rule in pack.rules:
            self.assertEqual(rule.legal_review_status, "COUNSEL_APPROVED")
            self.assertTrue(rule.official_source_url.startswith("https://"))
            self.assertGreater(len(rule.engine_controls), 0)
            self.assertGreater(len(rule.notice_clause), 20)

    def test_schema_validation_rejects_bad_dates_urls_and_statuses(self) -> None:
        base = copy.deepcopy(cpr.get_default_policy_pack_dict())
        base["rules"][0]["official_source_url"] = "ftp://invalid-source.org"
        with self.assertRaises(cpr.PolicyPackError):
            cpr.validate_and_build_policy_pack(base)

        base2 = copy.deepcopy(cpr.get_default_policy_pack_dict())
        base2["rules"][0]["legal_review_status"] = "UNKNOWN_STATUS"
        with self.assertRaises(cpr.PolicyPackError):
            cpr.validate_and_build_policy_pack(base2)

        base3 = copy.deepcopy(cpr.get_default_policy_pack_dict())
        base3["rules"][0]["last_reviewed_date"] = "2026-10-15"
        base3["rules"][0]["next_review_due_date"] = "2026-09-01"
        with self.assertRaises(cpr.PolicyPackError):
            cpr.validate_and_build_policy_pack(base3)

    def test_effective_date_and_geography_resolution(self) -> None:
        pack = cpr.load_policy_pack()

        # On 2025-06-01: IL HB 3773 (2026-01-01) and CO SB 24-205 (2026-06-30) are not yet effective
        active_2025 = cpr.resolve_applicable_rules(pack, reference_date="2025-06-01")
        active_2025_ids = {r.rule_id for r in active_2025}
        self.assertNotIn("POL-IL-HB3773", active_2025_ids)
        self.assertNotIn("POL-CO-SB24-205", active_2025_ids)
        self.assertIn("POL-NYC-LL144", active_2025_ids)

        # On 2026-11-01 for "Denver, CO": includes CO + federal/multi-state rules, excludes NYC LL144 and IL HB3773
        denver_rules = cpr.resolve_applicable_rules(pack, reference_date="2026-11-01", location="Denver, CO")
        denver_ids = {r.rule_id for r in denver_rules}
        self.assertIn("POL-CO-SB24-205", denver_ids)
        self.assertIn("POL-FED-EEOC-UGESP", denver_ids)
        self.assertIn("POL-FED-ADEA-GRAD-YEAR", denver_ids)
        self.assertIn("POL-US-PAY-TRANSPARENCY", denver_ids)
        self.assertNotIn("POL-NYC-LL144", denver_ids)
        self.assertNotIn("POL-IL-HB3773", denver_ids)

        # On 2026-11-01 for "New York, NY": includes NYC LL144, excludes CO and IL
        nyc_rules = cpr.resolve_applicable_rules(pack, reference_date="2026-11-01", location="New York, NY")
        nyc_ids = {r.rule_id for r in nyc_rules}
        self.assertIn("POL-NYC-LL144", nyc_ids)
        self.assertNotIn("POL-CO-SB24-205", nyc_ids)
        self.assertNotIn("POL-IL-HB3773", nyc_ids)

        # On 2026-11-01 for "Chicago, IL": includes IL HB3773, excludes NYC and CO
        il_rules = cpr.resolve_applicable_rules(pack, reference_date="2026-11-01", location="Chicago, IL")
        il_ids = {r.rule_id for r in il_rules}
        self.assertIn("POL-IL-HB3773", il_ids)
        self.assertNotIn("POL-NYC-LL144", il_ids)
        self.assertNotIn("POL-CO-SB24-205", il_ids)

    def test_require_counsel_approved_blocks_unreviewed_or_stale_packs(self) -> None:
        pack = cpr.load_policy_pack()
        # Past next_review_due_date (2027-04-15), require_counsel_approved must raise PolicyPackError
        with self.assertRaises(cpr.PolicyPackError):
            cpr.resolve_applicable_rules(
                pack,
                reference_date="2028-01-01",
                require_counsel_approved=True,
            )

        # Unreviewed draft rule raises PolicyPackError when require_counsel_approved=True
        draft_raw = copy.deepcopy(cpr.get_default_policy_pack_dict())
        draft_raw["rules"][0]["legal_review_status"] = "DRAFT_UNREVIEWED"
        draft_pack = cpr.validate_and_build_policy_pack(draft_raw)
        with self.assertRaises(cpr.PolicyPackError):
            cpr.resolve_applicable_rules(
                draft_pack,
                reference_date="2026-11-01",
                require_counsel_approved=True,
            )

    def test_custom_organization_policy_pack_decouples_screening_notices(self) -> None:
        custom_raw = copy.deepcopy(cpr.get_default_policy_pack_dict())
        custom_raw["policy_pack_id"] = "acme-enterprise-policy-pack"
        custom_raw["policy_pack_version"] = "2026.11.99"
        custom_raw["organization"] = "Acme Global Logistics Legal Dept"
        custom_raw["default_disclaimer"] = "*ACME CUSTOM LEGAL DISCLAIMER: Human review mandatory.*"
        custom_raw["right_to_clarify_template"] = "Contact acme-recruiting-appeals@example.com within 10 business days to clarify unverified items:"
        custom_raw["rules"][0]["notice_clause"] = "ACME COLORADO CUSTOM CLAUSE: Verified by Acme Employment Counsel."

        with tempfile.TemporaryDirectory() as tmpdir:
            pack_path = os.path.join(tmpdir, "acme_pack.json")
            with open(pack_path, "w", encoding="utf-8") as fh:
                json.dump(custom_raw, fh, indent=2)

            loaded_pack = cpr.load_policy_pack(pack_path)
            candidates, reqs, align_cfg, decisions, demographics = esa.get_demo_batch()
            batch = esa.screen_batch(
                candidate_inputs=candidates,
                reqs=reqs,
                align_cfg=align_cfg,
                reference_date=datetime(2026, 11, 1),
                decisions_map=decisions,
                demographics_map=demographics,
                policy_pack=loaded_pack,
                location="Denver, CO",
                require_counsel_approved=True,
            )
            self.assertEqual(batch.policy_pack_id, "acme-enterprise-policy-pack")
            self.assertEqual(batch.policy_pack_version, "2026.11.99")
            self.assertIn("POL-CO-SB24-205", batch.active_policy_rules)
            self.assertNotIn("POL-NYC-LL144", batch.active_policy_rules)

            notice_md = esa.render_candidate_notice(batch, batch.candidates[-1], policy_pack=loaded_pack)
            self.assertIn("ACME CUSTOM LEGAL DISCLAIMER", notice_md)
            self.assertIn("acme-recruiting-appeals@example.com", notice_md)
            self.assertIn("ACME COLORADO CUSTOM CLAUSE", notice_md)
            self.assertNotIn("POL-NYC-LL144", notice_md)


if __name__ == "__main__":
    unittest.main()
