#!/usr/bin/env python3
"""Unit tests for Module 3: Internal Mobility & Outplacement Matcher (ops-req-mobility)."""

from __future__ import annotations

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

import internal_mobility_matcher as imm  # noqa: E402


class TestInternalMobilityMatcher(unittest.TestCase):
    """Tests for 3-role career ladder matching, 30/60/90-day plans, and privacy flags."""

    def setUp(self) -> None:
        self.resume_path = ROOT / "examples" / "sample_resume.txt"
        self.resume_text, self.demo_roles = imm.get_demo_career_ladder()
        self.ref_date = datetime(2026, 11, 1)

    def test_career_ladder_ranking_and_readiness_bands(self) -> None:
        """Scores sample resume across the 3-role WFM career ladder and verifies readiness bands."""
        report = imm.match_resume_to_roles(
            resume_text=self.resume_text,
            role_entries=self.demo_roles,
            reference_date=self.ref_date,
        )
        self.assertEqual(report.roles_evaluated, 3)
        self.assertEqual(report.ready_now_count, 2)
        self.assertEqual(report.near_ready_count, 1)
        self.assertEqual(report.stretch_count, 0)

        # Ranked descending by current_score
        scores = [m.current_score for m in report.matches]
        self.assertEqual(scores, sorted(scores, reverse=True))

        top_match = report.matches[0]
        self.assertEqual(top_match.role_id, "INT-WFM-204")
        self.assertAlmostEqual(top_match.current_score, 88.3, places=1)
        self.assertAlmostEqual(top_match.projected_30d_score, 96.7, places=1)
        self.assertGreaterEqual(top_match.projected_30d_score, top_match.current_score)
        self.assertTrue(top_match.knockout_passed)

        stretch_bridge = report.matches[2]
        self.assertEqual(stretch_bridge.role_id, "INT-WFM-310")
        self.assertAlmostEqual(stretch_bridge.current_score, 67.0, places=1)
        self.assertAlmostEqual(stretch_bridge.projected_30d_score, 74.7, places=1)
        self.assertIn("Near-Ready", stretch_bridge.readiness_band)

    def test_30_60_90_reskilling_horizons_and_catalog(self) -> None:
        """Verifies Days 1-30 honest alignment vs Days 31-60 WFM catalog upskilling."""
        report = imm.match_resume_to_roles(
            resume_text=self.resume_text,
            role_entries=self.demo_roles,
            reference_date=self.ref_date,
        )
        # Check Forecasting & Capacity Planning Lead (INT-WFM-108)
        lead_match = next(m for m in report.matches if m.role_id == "INT-WFM-108")
        horizons = {s.requirement: s.horizon for s in lead_match.plan_30_60_90}
        gap_types = {s.requirement: s.gap_type for s in lead_match.plan_30_60_90}

        # Workforce Capacity Modeling is a Tier 1 synonym ("capacity planning") -> Days 1-30
        self.assertEqual(horizons["Workforce Capacity Modeling"], "Days 1-30")
        self.assertEqual(gap_types["Workforce Capacity Modeling"], "Tier 1 Synonym Alignment")

        # Erlang C and NICE IEX are missing -> Days 31-60 upskilling from RESKILLING_CATALOG
        self.assertEqual(horizons["Erlang C"], "Days 31-60")
        self.assertIn("WFM Staffing Math", gap_types["Erlang C"])
        self.assertEqual(horizons["NICE IEX (or Verint, Genesys Cloud)"], "Days 31-60")
        self.assertIn("Contact-Center WFM Platform", gap_types["NICE IEX (or Verint, Genesys Cloud)"])

        # Missing skills must also produce honest gap-confirmation questions
        self.assertEqual(len(lead_match.gap_questions), 2)
        self.assertTrue(any("Erlang C" in q for q in lead_match.gap_questions))

    def test_knockout_gate_triggers_days_61_90_horizon(self) -> None:
        """Unverified knockout gate maps to Days 61-90 and marks knockout_passed=False."""
        gate_role = [
            {
                "job_id": "INT-SEC-900",
                "title": "Security Operations Lead",
                "company": "Northstar Contact Solutions",
                "location": "Remote",
                "url": "",
                "alignment": {
                    "target_title": "Security Operations Lead",
                    "title_synonyms": ["Senior Analytics Manager"],
                    "min_years_required": 3.0,
                },
                "requirements": [
                    {
                        "name": "SQL",
                        "category": "hard_skill",
                        "tier": "required",
                        "synonyms": [],
                        "alternatives": [],
                        "is_knockout_gate": False,
                    },
                    {
                        "name": "CISSP",
                        "category": "certification",
                        "tier": "required",
                        "synonyms": [],
                        "alternatives": [],
                        "is_knockout_gate": True,
                    },
                ],
            }
        ]
        report = imm.match_resume_to_roles(
            resume_text=self.resume_text,
            role_entries=gate_role,
            reference_date=self.ref_date,
        )
        match = report.matches[0]
        self.assertFalse(match.knockout_passed)
        self.assertIn("CISSP", match.unverified_gates)
        cissp_step = next(s for s in match.plan_30_60_90 if s.requirement == "CISSP")
        self.assertEqual(cissp_step.horizon, "Days 61-90")
        self.assertEqual(cissp_step.gap_type, "Required Gate / Credential Verification")

    def test_privacy_defaults_and_outplacement_pack_rendering(self) -> None:
        """Verifies employee-private default and outplacement-pack Markdown rendering."""
        report_default = imm.match_resume_to_roles(
            resume_text=self.resume_text,
            role_entries=self.demo_roles,
            reference_date=self.ref_date,
        )
        self.assertTrue(report_default.employee_initiated)
        self.assertFalse(report_default.share_with_manager)
        md_default = imm.render_mobility_markdown(report_default)
        self.assertIn("share_with_manager: false", md_default)
        self.assertIn("# Internal Mobility & 30/60/90-Day Reskilling Plan", md_default)

        report_outplacement = imm.match_resume_to_roles(
            resume_text=self.resume_text,
            role_entries=self.demo_roles,
            reference_date=self.ref_date,
            mode="outplacement-pack",
            share_with_manager=True,
        )
        self.assertTrue(report_outplacement.share_with_manager)
        md_out = imm.render_mobility_markdown(report_outplacement)
        self.assertIn("# Outplacement & Career Transition Packet", md_out)
        self.assertIn("share_with_manager: true", md_out)

    def test_cli_demo_and_reqs_dir_execution(self) -> None:
        """Tests CLI --demo and --reqs-dir with JSON and Markdown output files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            reqs_dir = tmp / "reqs"
            reqs_dir.mkdir()
            # Copy sample_ats_config.json into reqs_dir
            sample_cfg = (ROOT / "examples" / "sample_ats_config.json").read_text(encoding="utf-8")
            (reqs_dir / "senior_analytics_manager.json").write_text(sample_cfg, encoding="utf-8")

            md_out = tmp / "mobility.md"
            json_out = tmp / "mobility.json"
            csv_out = tmp / "mobility.csv"
            rc = imm.main(
                [
                    "--resume",
                    str(self.resume_path),
                    "--reqs-dir",
                    str(reqs_dir),
                    "--reference-date",
                    "2026-11-01",
                    "--output",
                    str(md_out),
                    "--json-out",
                    str(json_out),
                    "--csv-out",
                    str(csv_out),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(md_out.exists())
            self.assertTrue(json_out.exists())
            self.assertTrue(csv_out.exists())
            csv_txt = csv_out.read_text(encoding="utf-8")
            self.assertIn("rank,role_id,title,company", csv_txt)
            self.assertIn("Senior Analytics Manager", csv_txt)
            payload = json.loads(json_out.read_text(encoding="utf-8"))
            self.assertEqual(payload["roles_evaluated"], 1)
            self.assertAlmostEqual(payload["matches"][0]["current_score"], 88.3, places=1)

    def test_employee_confirmation_mentor_approvals_outcomes_and_catalog_governance(self) -> None:
        """Verifies Workstream 11: employee skill confirmation, prerequisite chains, mentor/manager privacy gate, and 90d outcomes."""
        ok, cat_sha = imm.validate_reskilling_catalog_governance()
        self.assertTrue(ok)
        self.assertEqual(len(cat_sha), 64)

        conf_path = ROOT / "examples" / "sample_mobility_confirmation_and_outcomes.json"
        conf_data = json.loads(conf_path.read_text(encoding="utf-8"))
        emp_confs = conf_data["employee_confirmations"]
        approvals = {
            rid: imm.MentorAndManagerApprovalRecord(**ap)
            for rid, ap in conf_data["approvals_by_role"].items()
        }
        outcomes = {
            rid: imm.MobilityPlanOutcomeRecord(**out)
            for rid, out in conf_data["outcomes_by_role"].items()
        }

        report = imm.match_resume_to_roles(
            resume_text=self.resume_text,
            role_entries=self.demo_roles,
            reference_date=self.ref_date,
            share_with_manager=False,
            employee_confirmations=emp_confs,
            approvals_by_role=approvals,
            outcomes_by_role=outcomes,
        )
        top = next(m for m in report.matches if m.role_id == "INT-WFM-204")
        # Baseline is 88.3, Projected (all 5 Day 1-30 items) is 96.7, Confirmed (only 3 confirmed items) is strictly between 88.3 and 96.7!
        self.assertEqual(top.current_score, 88.3)
        self.assertGreater(top.confirmed_30d_score, top.current_score)
        self.assertLess(top.confirmed_30d_score, top.projected_30d_score)
        self.assertEqual(top.confirmed_30d_items_count, 3)
        self.assertEqual(top.unconfirmed_30d_items_count, 2)
        self.assertEqual(top.approvals.mentor_approval_status, "APPROVED")
        self.assertEqual(top.outcome_tracking.transition_outcome, "INTERNAL_TRANSFER_COMPLETED")
        self.assertTrue(top.outcome_tracking.retained_at_90_days)
        self.assertGreaterEqual(len(top.credential_or_prerequisite_alerts), 1)

        # Attempting to record a manager approval while share_with_manager=False must fail closed
        bad_approvals = {
            "INT-WFM-204": imm.MentorAndManagerApprovalRecord(
                manager_id="mgr_unconsented",
                manager_approval_status="APPROVED",
            )
        }
        with self.assertRaises(Exception):
            imm.match_resume_to_roles(
                resume_text=self.resume_text,
                role_entries=self.demo_roles,
                reference_date=self.ref_date,
                share_with_manager=False,
                approvals_by_role=bad_approvals,
            )


if __name__ == "__main__":
    unittest.main()
