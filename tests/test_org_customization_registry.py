#!/usr/bin/env python3
"""
Unit tests for Organization-Native Customization, Leveling Guides, Gate Equivalency,
Custom Component Weights & Thresholds, Dual-Signoff SoD Approval Workflow, and
Hash-Chained Configuration Audit History (`org_customization_registry.py`).
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
import offline_ats_auditor as oaa  # noqa: E402
import org_customization_registry as ocr  # noqa: E402
import requisition_quality_auditor as rqa  # noqa: E402


class TestOrgCustomizationRegistry(unittest.TestCase):
    def test_baseline_and_northstar_profiles_validate_and_diff_cleanly(self) -> None:
        base_prof = ocr.validate_and_build_org_profile(ocr.get_default_org_profile_dict(), require_approved=True)
        ns_prof = ocr.validate_and_build_org_profile(ocr.get_northstar_enterprise_org_profile_dict(), require_approved=True)

        self.assertEqual(base_prof.org_id, "default-baseline-org")
        self.assertEqual(ns_prof.org_id, "northstar-contact-solutions")
        self.assertEqual(len(ns_prof.config_sha256), 64)
        self.assertNotEqual(base_prof.config_sha256, ns_prof.config_sha256)

        diffs = ocr.compute_org_profile_diff(base_prof, ns_prof)
        self.assertGreaterEqual(len(diffs), 8)
        diff_params = {d["parameter"] for d in diffs}
        self.assertIn("comp1_keyword_max", diff_params)
        self.assertIn("comp2_context_max", diff_params)
        self.assertIn("jd_strong_match_threshold", diff_params)
        self.assertIn("bachelor_equivalency_years", diff_params)
        self.assertIn("level_2.max_benchmark_years", diff_params)

    def test_schema_validation_rejects_unbalanced_weights_and_inverted_thresholds(self) -> None:
        bad_weights = copy.deepcopy(ocr.get_default_org_profile_dict())
        bad_weights["scoring_and_thresholds"]["comp1_keyword_max"] = 50.0  # Sum = 105.0 != 100.0
        with self.assertRaises(ocr.OrgSchemaValidationError):
            ocr.validate_and_build_org_profile(bad_weights)

        bad_thresh = copy.deepcopy(ocr.get_default_org_profile_dict())
        bad_thresh["scoring_and_thresholds"]["jd_strong_match_threshold"] = 60.0
        bad_thresh["scoring_and_thresholds"]["jd_borderline_threshold"] = 70.0
        with self.assertRaises(ocr.OrgSchemaValidationError):
            ocr.validate_and_build_org_profile(bad_thresh)

        bad_gate = copy.deepcopy(ocr.get_default_org_profile_dict())
        bad_gate["gate_and_equivalency_policy"]["allowed_hard_gate_types"] = ["gpa_prestige_gate"]
        with self.assertRaises(ocr.OrgSchemaValidationError):
            ocr.validate_and_build_org_profile(bad_gate)

    def test_approval_workflow_enforces_dual_signoff_and_separation_of_duties(self) -> None:
        # 1. Self-approval violation (author == ta_or_comp_approver_id)
        sod_violation = copy.deepcopy(ocr.get_northstar_enterprise_org_profile_dict())
        sod_violation["approval_workflow"]["ta_or_comp_approver_id"] = sod_violation["approval_workflow"]["author_id"]
        with self.assertRaises(ocr.OrgApprovalWorkflowError):
            ocr.validate_and_build_org_profile(sod_violation)

        # 2. Same person signing off as both TA and Compliance approver
        sod_violation2 = copy.deepcopy(ocr.get_northstar_enterprise_org_profile_dict())
        sod_violation2["approval_workflow"]["compliance_or_legal_approver_id"] = (
            sod_violation2["approval_workflow"]["ta_or_comp_approver_id"]
        )
        with self.assertRaises(ocr.OrgApprovalWorkflowError):
            ocr.validate_and_build_org_profile(sod_violation2)

        # 3. Unapproved DRAFT blocked when require_approved=True
        draft_raw = copy.deepcopy(ocr.get_northstar_enterprise_org_profile_dict())
        draft_raw["approval_workflow"]["approval_status"] = "DRAFT"
        draft_prof = ocr.validate_and_build_org_profile(draft_raw, require_approved=False)
        self.assertEqual(draft_prof.approval_workflow.approval_status, "DRAFT")
        with self.assertRaises(ocr.OrgApprovalWorkflowError):
            ocr.validate_and_build_org_profile(draft_raw, require_approved=True)

    def test_hash_chained_configuration_audit_history_and_tamper_detection(self) -> None:
        base_prof = ocr.validate_and_build_org_profile(ocr.get_default_org_profile_dict(), require_approved=True)
        ns_prof = ocr.validate_and_build_org_profile(ocr.get_northstar_enterprise_org_profile_dict(), require_approved=True)
        chain = ocr.build_config_audit_history_chain([base_prof, ns_prof])
        self.assertEqual(len(chain), 2)

        chain_dicts = [
            {
                "seq": e.seq,
                "org_id": e.org_id,
                "config_version": e.config_version,
                "previous_version": e.previous_version,
                "approval_status": e.approval_status,
                "author_id": e.author_id,
                "ta_or_comp_approver_id": e.ta_or_comp_approver_id,
                "compliance_or_legal_approver_id": e.compliance_or_legal_approver_id,
                "approved_timestamp": e.approved_timestamp,
                "change_rationale": e.change_rationale,
                "parameter_diffs": e.parameter_diffs,
                "config_sha256": e.config_sha256,
                "previous_entry_sha256": e.previous_entry_sha256,
                "entry_sha256": e.entry_sha256,
            }
            for e in chain
        ]
        ok, msg = ocr.verify_org_config_audit_history(chain_dicts)
        self.assertTrue(ok, msg)

        # Tamper with change_rationale in seq 2 -> verification must fail
        tampered = copy.deepcopy(chain_dicts)
        tampered[1]["change_rationale"] = "Tampered rationale"
        ok_bad, _ = ocr.verify_org_config_audit_history(tampered)
        self.assertFalse(ok_bad)

    def test_effective_org_configuration_applied_and_displayed_in_reports(self) -> None:
        ns_prof = ocr.validate_and_build_org_profile(ocr.get_northstar_enterprise_org_profile_dict(), require_approved=True)
        resume_txt, reqs, align_cfg = oaa.get_demo_fixture()
        ref_dt = datetime(2026, 11, 1)

        rep_base = oaa.run_ats_audit(resume_txt, reqs, align_cfg, reference_date=ref_dt)
        rep_ns = oaa.run_ats_audit(resume_txt, reqs, align_cfg, reference_date=ref_dt, org_profile=ns_prof)

        self.assertEqual(rep_base.effective_org_config["org_id"], "default-baseline-org")
        self.assertEqual(rep_ns.effective_org_config["org_id"], "northstar-contact-solutions")
        self.assertEqual(rep_ns.effective_org_config["comp1_keyword_max"], 40.0)
        self.assertEqual(rep_ns.effective_org_config["comp2_context_max"], 25.0)
        self.assertEqual(rep_ns.effective_org_config["jd_strong_match_threshold"], 78.0)

        md_ns = oaa.render_markdown_report(rep_ns)
        self.assertIn("northstar-contact-solutions", md_ns)
        self.assertIn("Weights `40/25/15/10/10`", md_ns)
        self.assertIn("Cutoffs `Strong>=78, Borderline>=62`", md_ns)

        # Also test Explainable Screening Assistant with org_profile
        candidates, s_reqs, s_align, decisions, demographics = esa.get_demo_batch()
        batch = esa.screen_batch(
            candidates,
            s_reqs,
            s_align,
            ref_dt,
            decisions_map=decisions,
            demographics_map=demographics,
            org_profile=ns_prof,
        )
        self.assertEqual(batch.org_profile_id, "northstar-contact-solutions")
        self.assertEqual(batch.shortlist_threshold, 78.0)
        batch_md = esa.render_batch_markdown(batch, include_notices=False)
        self.assertIn("northstar-contact-solutions", batch_md)


if __name__ == "__main__":
    unittest.main()
