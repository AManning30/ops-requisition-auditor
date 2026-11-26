#!/usr/bin/env python3
"""
Integration & contract tests for the ATS-Neutral JSON Contract (`ats-neutral-contract-1.0`),
OpenAPI 3.1.0 specification, bidirectional Vendor Adapters (Greenhouse, Lever, Ashby,
Workday HRIS, HR-Open 4.x), HMAC-SHA256 Signed Webhooks, Batch-Job Pipeline, and
Outbound Human-Review & Audit Governance Preservation (`integration_adapters_and_webhooks.py`).
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import integration_adapters_and_webhooks as iaw  # noqa: E402


class TestIntegrationsAndWebhooks(unittest.TestCase):
    def test_ats_neutral_contract_and_bidirectional_adapters_preserve_governance(self) -> None:
        bundle = iaw.get_sample_integration_bundle()
        batch_res = iaw.run_batch_integration_job(bundle["batch_jobs"], reference_date="2026-11-01")

        self.assertEqual(batch_res["total_jobs"], 3)
        self.assertEqual(batch_res["succeeded_count"], 3)
        self.assertEqual(batch_res["failed_count"], 0)
        self.assertTrue(batch_res["all_governance_contracts_verified"])

        by_vendor = {r["source_system"]: r for r in batch_res["results"]}
        self.assertEqual(set(by_vendor.keys()), {"greenhouse", "lever", "ashby"})

        # 1. Greenhouse adapter preserves score, plausible interval, and blocks automated stage transitions
        gh_res = by_vendor["greenhouse"]
        self.assertEqual(gh_res["composite_score"], 100.0)
        self.assertEqual(gh_res["disposition_status"], "ADVANCE")
        self.assertTrue(gh_res["vendor_export"]["automated_stage_transition_blocked"])
        self.assertEqual(
            gh_res["vendor_export"]["custom_fields"]["ops_req_audit_chain_sha256"],
            gh_res["chain_entry_sha256"],
        )

        # 2. Lever adapter defaults unreviewed application to PENDING_HUMAN_REVIEW
        lev_res = by_vendor["lever"]
        self.assertEqual(lev_res["composite_score"], 88.3)
        self.assertEqual(lev_res["plausible_score_interval"], [82.9, 96.8])
        self.assertEqual(lev_res["disposition_status"], "PENDING_HUMAN_REVIEW")
        self.assertIn("ops-req-status:pending_human_review", lev_res["vendor_export"]["add_tags"])

        # 3. Ashby adapter preserves human override reason, reviewer_id, and plausibleScoreInterval
        ash_res = by_vendor["ashby"]
        self.assertEqual(ash_res["disposition_status"], "ADVANCE")
        self.assertTrue(ash_res["vendor_export"]["governance_audit"]["is_override"])
        self.assertIn(
            "verified capacity planning",
            ash_res["vendor_export"]["governance_audit"]["override_reason"],
        )

    def test_outbound_governance_gate_blocks_missing_audit_fields_or_auto_reject(self) -> None:
        bundle = iaw.get_sample_integration_bundle()
        gh_item = bundle["batch_jobs"][0]
        req_env = iaw.GreenhouseAdapter.import_requisition(gh_item["requisition"])
        app_env = iaw.GreenhouseAdapter.import_application(gh_item["application"])
        neutral_env = iaw.evaluate_to_neutral_envelope(req_env, app_env)
        valid_export = iaw.GreenhouseAdapter.export_evaluation(neutral_env)

        # 1. Tamper with auto_rejection_permitted -> must raise ContractGovernanceViolationError
        bad1 = copy.deepcopy(valid_export)
        bad1["governance_audit"]["auto_rejection_permitted"] = True
        with self.assertRaises(iaw.ContractGovernanceViolationError):
            iaw.verify_outbound_governance_contract(bad1, adapter_name="greenhouse")

        # 2. Drop chain_entry_sha256 -> must raise ContractGovernanceViolationError
        bad2 = copy.deepcopy(valid_export)
        bad2["governance_audit"]["chain_entry_sha256"] = "short_invalid_hash"
        with self.assertRaises(iaw.ContractGovernanceViolationError):
            iaw.verify_outbound_governance_contract(bad2, adapter_name="greenhouse")

        # 3. Non-initial disposition with bot reviewer_id -> must raise ContractGovernanceViolationError
        bad3 = copy.deepcopy(valid_export)
        bad3["governance_audit"]["reviewer_id"] = "auto_screening_bot_v1"
        with self.assertRaises(iaw.ContractGovernanceViolationError):
            iaw.verify_outbound_governance_contract(bad3, adapter_name="greenhouse")

    def test_signed_webhook_event_processing_and_replay_protection(self) -> None:
        bundle = iaw.get_sample_integration_bundle()
        secret = "test-webhook-secret-key-2026"
        ts = "1793534400"
        raw_body = json.dumps(bundle["sample_webhook_event"])
        sig = iaw.compute_webhook_signature(secret, ts, raw_body)

        res = iaw.process_signed_webhook_event(
            raw_body_str=raw_body,
            signature_header=sig,
            timestamp_header=ts,
            webhook_secret=secret,
            reference_epoch=1793534450,
        )
        self.assertEqual(res["status"], "PROCESSED")
        self.assertEqual(res["neutral_envelope"]["composite_score"], 88.3)
        self.assertEqual(res["neutral_envelope"]["disposition_status"], "PENDING_HUMAN_REVIEW")

        # Invalid signature must raise WebhookSignatureError
        with self.assertRaises(iaw.WebhookSignatureError):
            iaw.process_signed_webhook_event(
                raw_body_str=raw_body,
                signature_header="sha256=0000000000000000000000000000000000000000000000000000000000000000",
                timestamp_header=ts,
                webhook_secret=secret,
            )

        # Replay attack outside 300s tolerance window must raise WebhookSignatureError
        with self.assertRaises(iaw.WebhookSignatureError):
            iaw.process_signed_webhook_event(
                raw_body_str=raw_body,
                signature_header=sig,
                timestamp_header=ts,
                webhook_secret=secret,
                reference_epoch=1793535500,
            )

        # Duplicate replay nonce within tolerance window must raise WebhookSignatureError when enforce_nonce_dedup=True
        iaw.reset_webhook_nonce_cache()
        iaw.process_signed_webhook_event(
            raw_body_str=raw_body,
            signature_header=sig,
            timestamp_header=ts,
            webhook_secret=secret,
            reference_epoch=1793534450,
            enforce_nonce_dedup=True,
        )
        with self.assertRaises(iaw.WebhookSignatureError):
            iaw.process_signed_webhook_event(
                raw_body_str=raw_body,
                signature_header=sig,
                timestamp_header=ts,
                webhook_secret=secret,
                reference_epoch=1793534455,
                enforce_nonce_dedup=True,
            )

        # Test requisition.updated, mobility.profile_submitted, and privacy.erasure_requested webhooks
        for ev_payload, expected_key in (
            ({"event_id": "E2", "event_type": "requisition.updated", "source_system": "greenhouse"}, "rqs_score"),
            (
                {
                    "event_id": "E3",
                    "event_type": "mobility.profile_submitted",
                    "source_system": "workday_hris",
                    "worker": {"worker_id": "EMP-009", "share_with_manager": False},
                },
                "hris_export",
            ),
            (
                {"event_id": "E4", "event_type": "privacy.erasure_requested", "subject_id": "CAND-002"},
                "deletion_receipt",
            ),
        ):
            ev_str = json.dumps(ev_payload)
            ev_sig = iaw.compute_webhook_signature(secret, ts, ev_str)
            ev_res = iaw.process_signed_webhook_event(
                raw_body_str=ev_str,
                signature_header=ev_sig,
                timestamp_header=ts,
                webhook_secret=secret,
                reference_epoch=1793534450,
            )
            self.assertEqual(ev_res["status"], "PROCESSED")
            self.assertIn(expected_key, ev_res)

        # Edge Case 1 verification: custom vault_json_path inside scripts/vaults/ with SegregatedDataVault
        vault_dir = os.path.join(iaw.SCRIPT_DIR, "vaults")
        os.makedirs(vault_dir, exist_ok=True)
        vault_file = os.path.join(vault_dir, "test_webhook_erasure_vault.json")
        try:
            demo_vault, _, _ = iaw.pdm.run_privacy_demo(reference_date="2026-11-01")
            iaw.gov.atomic_write_json(vault_file, iaw.dataclasses.asdict(demo_vault))
            erasure_body = json.dumps({"event_id": "E4-FILE", "event_type": "privacy.erasure_requested", "subject_id": "CAND-002"})
            erasure_sig = iaw.compute_webhook_signature(secret, ts, erasure_body)
            file_res = iaw.process_signed_webhook_event(
                raw_body_str=erasure_body,
                signature_header=erasure_sig,
                timestamp_header=ts,
                webhook_secret=secret,
                reference_epoch=1793534450,
                vault_json_path="test_webhook_erasure_vault.json",
            )
            self.assertEqual(file_res["status"], "PROCESSED")
            self.assertEqual(file_res["deletion_receipt"]["verification_status"], "VERIFIED_PURGED")
            with open(vault_file, "r", encoding="utf-8") as vfh:
                persisted_vault = json.load(vfh)
            tok_002 = iaw.pdm.compute_pseudonym_token("CAND-002")
            self.assertNotIn(tok_002, persisted_vault["pii_vault"])
        finally:
            if os.path.exists(vault_file):
                os.remove(vault_file)
            if os.path.isdir(vault_dir) and not os.listdir(vault_dir):
                os.rmdir(vault_dir)

    def test_openapi_router_and_cli_jsonl_exports(self) -> None:
        code_h, body_h = iaw.handle_integration_api_request("GET", "/healthz")
        self.assertEqual(code_h, 200)
        self.assertFalse(body_h["auto_rejection_permitted"])

        code_oa, body_oa = iaw.handle_integration_api_request("GET", "/openapi.json")
        self.assertEqual(code_oa, 200)
        self.assertEqual(body_oa["openapi"], "3.1.0")
        self.assertIn("NeutralEvaluationAndGovernanceEnvelope", body_oa["components"]["schemas"])

        with tempfile.TemporaryDirectory() as tmpdir:
            oa_path = os.path.join(tmpdir, "openapi.json")
            bundle_path = os.path.join(tmpdir, "bundle.json")
            jsonl_path = os.path.join(tmpdir, "out.jsonl")
            md_path = os.path.join(tmpdir, "report.md")

            self.assertEqual(
                iaw.main(["--export-openapi", oa_path, "--export-sample-bundle", bundle_path]),
                0,
            )
            self.assertTrue(os.path.isfile(oa_path))
            self.assertTrue(os.path.isfile(bundle_path))

            rc = iaw.main([
                "--batch-job", bundle_path,
                "--output", md_path,
                "--jsonl-out", jsonl_path,
            ])
            self.assertEqual(rc, 0)
            with open(jsonl_path, "r", encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
            self.assertEqual(len(lines), 3)
            for env_obj in lines:
                self.assertTrue(iaw.verify_outbound_governance_contract(env_obj))


if __name__ == "__main__":
    unittest.main()
