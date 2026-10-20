"""Run: python tests/test_goal_evaluation.py.

These evaluator tests require the standard library only.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from nanomuse.goals.evaluation import (
    Contract,
    Criterion,
    Evidence,
    Ledger,
    Run,
    Scalar,
    collect_file,
    contract_from_dict,
    decide,
    evaluate,
    fingerprint,
    observation,
)

NOW = 10000


class GoalEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.criterion = Criterion(
            "accepted",
            "fixture://submission/1",
            "accepted",
            "eq",
            True,
            "boolean",
            ("fixture.external.v1",),
            max_age=60,
        )
        self.contract = Contract("g_1", 1, (self.criterion,))
        self.goal_run = Run("r_1")

    def evidence(self, value: Scalar = True, **overrides: Any) -> Evidence:
        fields: dict[str, Any] = dict(
            id="e_1",
            goal_id="g_1",
            revision=1,
            run_id="r_1",
            call_id="call_1",
            criterion_id="accepted",
            collector="fixture.external.v1",
            observed_at=NOW,
            collected_at=NOW,
            subject="fixture://submission/1",
            metric="accepted",
            value=value,
            unit="boolean",
        )
        fields.update(overrides)
        return observation(**fields)

    def test_task_completed_with_real_progress(self) -> None:
        before = evaluate(self.contract, self.goal_run, [self.evidence(False)], now=NOW)
        after = evaluate(self.contract, self.goal_run, [self.evidence()], now=NOW, previous=before)
        self.assertEqual((after["status"], after["delta"]), ("verified", 1))
        self.assertEqual(after["evidence"], {"accepted": ["e_1"]})
        self.assertEqual(decide(after), ("escalate", "review_completion"))
        self.assertEqual(decide(after, approved_result_id=after["id"])[0], "stop")
        self.assertNotEqual(decide(after, approved_result_id="another-result")[0], "stop")

    def test_task_succeeded_without_goal_progress(self) -> None:
        before = evaluate(self.contract, self.goal_run, [self.evidence(False)], now=NOW)
        after = evaluate(
            self.contract,
            Run("r_2"),
            [self.evidence(False, run_id="r_2")],
            now=NOW,
            previous=before,
        )
        self.assertEqual((after["status"], after["delta"]), ("partial", 0))
        self.assertEqual(after["met"], [])
        self.assertEqual(decide(after, stalled_runs=3)[0], "replan")

    def test_model_claim_without_evidence(self) -> None:
        claim = self.evidence(collector="llm.self_report")
        result = evaluate(self.contract, self.goal_run, [claim], now=NOW)
        self.assertEqual((result["status"], result["delta"]), ("unknown", None))
        self.assertEqual(decide(result), ("continue", "collect_evidence"))
        self.assertEqual(decide(result, unknown_runs=3)[0], "escalate")

    def test_external_condition_invalidates_plan(self) -> None:
        contract = replace(
            self.contract,
            criteria=(self.criterion, replace(self.criterion, id="window", invariant=True)),
        )
        result = evaluate(
            contract, self.goal_run, [self.evidence(False, criterion_id="window")], now=NOW
        )
        self.assertEqual(result["status"], "contradicted")
        self.assertEqual(result["violated"], ["window"])
        self.assertEqual(decide(result)[0], "replan")

    def test_interrupted_retry_and_reopen_do_not_double_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "eval.db"
            ledger = Ledger(path)
            before = ledger.record(
                self.contract, replace(self.goal_run, state="interrupted"), [], now=NOW
            )
            self.assertEqual(before["status"], "unknown")
            self.assertEqual(decide(before)[1], "reconcile_before_retry")
            ledger.close()
            ledger = Ledger(path)
            recovered = ledger.record(
                self.contract,
                replace(self.goal_run, attempt=2),
                [self.evidence(attempt=2)],
                now=NOW,
                previous=before,
            )
            self.assertEqual(recovered["status"], "verified")
            replay = ledger.record(
                self.contract,
                replace(self.goal_run, attempt=2),
                [self.evidence(attempt=2)],
                now=NOW,
                previous=before,
            )
            self.assertEqual(recovered, replay)
            self.assertEqual(
                ledger.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0], 2
            )
            with self.assertRaises(ValueError):
                ledger.record(self.contract, replace(self.goal_run, attempt=2), [], now=NOW)
            ledger.close()

    def test_real_artifact_recheck_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.txt"
            path.write_text("accepted report\n")
            criterion = Criterion(
                "file",
                path.resolve().as_uri(),
                "sha256",
                "eq",
                hashlib.sha256(path.read_bytes()).hexdigest(),
                "digest",
                ("file.sha256.v1",),
            )
            contract = Contract("g_1", 1, (criterion,))
            before = evaluate(
                contract,
                self.goal_run,
                [collect_file(contract, self.goal_run, criterion, path, NOW)],
                now=NOW,
            )
            self.assertEqual(before["status"], "verified")
            path.write_text("different report\n")
            after = evaluate(
                contract,
                self.goal_run,
                [collect_file(contract, self.goal_run, criterion, path, NOW + 1)],
                now=NOW + 1,
                previous=before,
            )
            self.assertEqual(after["status"], "contradicted")

    def test_unusable_evidence_never_verifies(self) -> None:
        for fields in (
            {"goal_id": "g_other"},
            {"revision": 2},
            {"run_id": "r_other"},
            {"subject": "fixture://other"},
            {"call_id": ""},
            {"unit": "percent"},
            {"metric": "tool_ok"},
            {"observed_at": NOW - 61},
            {"collected_at": NOW + 1},
            {"observed_at": NOW + 1},
            {"ok": False},
            {"allowed": False},
            {"value": 1},
            {"value": "true"},
        ):
            with self.subTest(fields=fields):
                result = evaluate(self.contract, self.goal_run, [self.evidence(**fields)], now=NOW)
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(result["met"], [])

    def test_digest_mismatch_is_unknown(self) -> None:
        e = replace(self.evidence(), value=False)
        self.assertEqual(evaluate(self.contract, self.goal_run, [e], now=NOW)["status"], "unknown")

    def test_duplicate_and_conflicting_ids(self) -> None:
        e = self.evidence()
        self.assertEqual(
            evaluate(self.contract, self.goal_run, [e, e], now=NOW)["met"], ["accepted"]
        )
        result = evaluate(self.contract, self.goal_run, [e, self.evidence(False)], now=NOW)
        self.assertEqual(result["status"], "unknown")

    def test_conflicting_independent_observations_require_review(self) -> None:
        result = evaluate(
            self.contract, self.goal_run, [self.evidence(), self.evidence(False, id="e_2")], now=NOW
        )
        self.assertEqual(result["status"], "contradicted")
        self.assertEqual(result["criteria"]["accepted"], "conflict")

    def test_latest_valid_observation_wins(self) -> None:
        old = self.evidence(False, id="old", observed_at=NOW - 1)
        result = evaluate(self.contract, self.goal_run, [old, self.evidence()], now=NOW)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(
            result, evaluate(self.contract, self.goal_run, [self.evidence(), old], now=NOW)
        )

    def test_partial_coverage_and_no_vacuous_success(self) -> None:
        contract = replace(
            self.contract, criteria=(self.criterion, replace(self.criterion, id="second"))
        )
        result = evaluate(contract, self.goal_run, [self.evidence()], now=NOW)
        self.assertEqual((result["status"], result["required"]), ("partial", 2))
        self.assertIsNone(result["delta"])
        with self.assertRaises(ValueError):
            evaluate(replace(contract, criteria=()), self.goal_run, [], now=NOW)
        with self.assertRaises(ValueError):
            evaluate(
                replace(contract, criteria=(replace(self.criterion, invariant=True),)),
                self.goal_run,
                [],
                now=NOW,
            )

    def test_revision_baseline_and_nan_are_rejected(self) -> None:
        before = evaluate(self.contract, self.goal_run, [], now=NOW)
        with self.assertRaises(ValueError):
            evaluate(
                replace(self.contract, revision=2), self.goal_run, [], now=NOW, previous=before
            )
        with self.assertRaises(ValueError):
            evaluate(self.contract, self.goal_run, [], now=NOW - 1, previous=before)
        with self.assertRaises(ValueError):
            evaluate(
                replace(self.contract, criteria=(replace(self.criterion, target=float("nan")),)),
                self.goal_run,
                [],
                now=NOW,
            )

    def test_sqlite_transaction_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "eval.db")
            with self.assertRaises(sqlite3.OperationalError), ledger.conn:
                ledger.conn.execute("INSERT INTO evaluations VALUES ('x','r',1,'{}','{}')")
                ledger.conn.execute("INSERT INTO missing_table VALUES (1)")
            self.assertEqual(
                ledger.conn.execute("SELECT count(*) FROM evaluations").fetchone()[0], 0
            )
            ledger.close()

    def test_numeric_predicate_and_units(self) -> None:
        criterion = replace(self.criterion, metric="count", op="gte", target=3, unit="items")
        contract = replace(self.contract, criteria=(criterion,))
        for value, expected in ((2, "partial"), (3, "verified"), (True, "unknown")):
            e = self.evidence(value=value, metric="count", unit="items")
            self.assertEqual(evaluate(contract, self.goal_run, [e], now=NOW)["status"], expected)

    def test_retry_requires_evidence_from_the_current_attempt(self) -> None:
        run = replace(self.goal_run, attempt=2)
        old = evaluate(self.contract, run, [self.evidence()], now=NOW)
        fresh = evaluate(self.contract, run, [self.evidence(attempt=2)], now=NOW)
        self.assertEqual((old["status"], fresh["status"]), ("unknown", "verified"))

    def test_json_flags_and_integer_fields_are_strict(self) -> None:
        invalid_overrides: tuple[dict[str, Any], ...] = (
            {"ok": "false"},
            {"allowed": "false"},
            {"revision": True},
            {"attempt": True},
            {"observed_at": True},
            {"id": []},
        )
        for overrides in invalid_overrides:
            with self.subTest(overrides=overrides):
                self.assertEqual(
                    evaluate(self.contract, self.goal_run, [self.evidence(**overrides)], now=NOW)[
                        "status"
                    ],
                    "unknown",
                )

    def test_modified_result_cannot_reuse_completion_approval(self) -> None:
        result = evaluate(self.contract, self.goal_run, [], now=NOW)
        altered = {**result, "status": "verified"}
        with self.assertRaises(ValueError):
            decide(altered, approved_result_id=result["id"])

    def test_baseline_integrity_and_evaluator_version_are_checked(self) -> None:
        result = evaluate(self.contract, self.goal_run, [self.evidence()], now=NOW)
        for field, value in (("met", []), ("evaluator_version", "other.v1")):
            altered = {**result, field: value}
            with self.assertRaises(ValueError):
                evaluate(self.contract, self.goal_run, [], now=NOW, previous=altered)
        altered = {**result, "evaluator_version": "other.v1"}
        altered["id"] = fingerprint({k: v for k, v in altered.items() if k != "id"})
        with self.assertRaises(ValueError):
            evaluate(self.contract, self.goal_run, [], now=NOW, previous=altered)

    def test_equivalent_numeric_observations_do_not_conflict(self) -> None:
        criterion = replace(self.criterion, metric="count", op="gte", target=1, unit="items")
        contract = replace(self.contract, criteria=(criterion,))
        observations = [
            self.evidence(value=v, metric="count", unit="items", id=str(v)) for v in (1, 1.0)
        ]
        result = evaluate(contract, self.goal_run, observations, now=NOW)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result, evaluate(contract, self.goal_run, observations[::-1], now=NOW))

    def test_constraint_recovery_is_not_outcome_progress(self) -> None:
        contract = replace(
            self.contract,
            criteria=(self.criterion, replace(self.criterion, id="window", invariant=True)),
        )
        before = evaluate(
            contract,
            self.goal_run,
            [self.evidence(False), self.evidence(False, id="w", criterion_id="window")],
            now=NOW,
        )
        after = evaluate(
            contract,
            self.goal_run,
            [self.evidence(False), self.evidence(True, id="w", criterion_id="window")],
            now=NOW,
            previous=before,
        )
        self.assertEqual(after["outcome_met"], [])
        self.assertEqual(after["constraint_met"], ["window"])
        self.assertEqual(after["delta"], 0)

    def test_subthreshold_growth_does_not_trigger_stagnation(self) -> None:
        criterion = replace(self.criterion, metric="count", op="gte", target=10, unit="items")
        contract = replace(self.contract, criteria=(criterion,))
        previous = None
        for count in (0, 3, 6, 9):
            evidence = self.evidence(value=count, metric="count", unit="items")
            result = evaluate(contract, self.goal_run, [evidence], now=NOW, previous=previous)
            if previous is not None:
                self.assertEqual(result["delta"], 0)  # Milestone coverage is unchanged.
                self.assertEqual(result["progress"], "advanced")
                self.assertEqual(decide(result, stalled_runs=3)[0], "continue")
            self.assertEqual(result["observed_values"], {"accepted": count})
            previous = result

    def test_partial_unknown_exhausts_verification_budget(self) -> None:
        contract = replace(
            self.contract,
            criteria=(self.criterion, replace(self.criterion, id="guard", invariant=True)),
        )
        previous = None
        for round_number in range(1, 6):
            result = evaluate(
                contract, self.goal_run, [self.evidence(False)], now=NOW, previous=previous
            )
            self.assertEqual(result["status"], "partial")
            self.assertIsNone(result["delta"])
            if previous is not None:
                self.assertEqual(result["progress"], "stalled")
            decision = decide(result, unknown_runs=round_number)
            self.assertEqual(decision[0], "escalate" if round_number >= 3 else "continue")
            previous = result

    def test_known_growth_is_visible_with_another_unknown_outcome(self) -> None:
        counter = replace(self.criterion, metric="count", op="gte", target=10, unit="items")
        contract = replace(
            self.contract, criteria=(counter, replace(self.criterion, id="unavailable"))
        )
        before = evaluate(
            contract, self.goal_run, [self.evidence(value=2, metric="count", unit="items")], now=NOW
        )
        result = evaluate(
            contract,
            self.goal_run,
            [self.evidence(value=5, metric="count", unit="items")],
            now=NOW,
            previous=before,
        )
        self.assertEqual((result["progress"], result["delta"]), ("advanced", None))
        self.assertEqual(result["status"], "partial")

    def test_numeric_regression_is_not_milestone_stagnation(self) -> None:
        counter = replace(self.criterion, metric="count", op="gte", target=10, unit="items")
        contract = replace(self.contract, criteria=(counter,))
        before = evaluate(
            contract, self.goal_run, [self.evidence(value=9, metric="count", unit="items")], now=NOW
        )
        result = evaluate(
            contract,
            self.goal_run,
            [self.evidence(value=6, metric="count", unit="items")],
            now=NOW,
            previous=before,
        )
        self.assertEqual((result["progress"], result["delta"]), ("regressed", 0))
        self.assertEqual(decide(result)[0], "replan")

    def test_changes_above_a_satisfied_threshold_are_not_goal_progress(self) -> None:
        counter = replace(
            self.criterion, id="count", metric="count", op="gte", target=10, unit="items"
        )
        contract = replace(self.contract, criteria=(self.criterion, counter))
        previous = None
        for count in (10, 20, 11):
            result = evaluate(
                contract,
                self.goal_run,
                [
                    self.evidence(False),
                    self.evidence(
                        value=count, id="count", criterion_id="count", metric="count", unit="items"
                    ),
                ],
                now=NOW,
                previous=previous,
            )
            if previous is not None:
                self.assertEqual(result["progress"], "stalled")
                self.assertEqual(decide(result, stalled_runs=3)[0], "replan")
            previous = result

    def test_out_of_scope_collision_cannot_suppress_valid_evidence(self) -> None:
        valid = self.evidence()
        for invalid in (
            self.evidence(False, goal_id="another_goal"),
            self.evidence(False, attempt=2),
            self.evidence(False, collector="untrusted"),
        ):
            for records in ([valid, invalid], [invalid, valid]):
                self.assertEqual(
                    evaluate(self.contract, self.goal_run, records, now=NOW)["status"], "verified"
                )

    def test_file_collection_ids_identify_observation_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.txt"
            path.write_text("x")
            criterion = replace(
                self.criterion,
                subject=path.resolve().as_uri(),
                metric="sha256",
                target=hashlib.sha256(b"x").hexdigest(),
                unit="digest",
                collectors=("file.sha256.v1",),
            )
            contract = replace(self.contract, criteria=(criterion,))
            old = collect_file(contract, self.goal_run, criterion, path, NOW)
            run = replace(self.goal_run, attempt=2)
            current = collect_file(contract, run, criterion, path, NOW + 1)
            newer = collect_file(contract, run, criterion, path, NOW + 2)
            self.assertEqual(len({old.id, current.id, newer.id}), 3)
            result = evaluate(contract, run, [old, current, newer], now=NOW + 2)
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["evidence"][criterion.id], [newer.id])

    def test_freshness_boundary_is_inclusive(self) -> None:
        evidence = self.evidence(observed_at=NOW - 60)
        self.assertEqual(
            evaluate(self.contract, self.goal_run, [evidence], now=NOW)["status"], "verified"
        )
        self.assertEqual(
            evaluate(self.contract, self.goal_run, [evidence], now=NOW + 1)["status"], "unknown"
        )

    def test_contract_json_boundary_rejects_coercion_and_extra_fields(self) -> None:
        data = json.loads(json.dumps(asdict(self.contract)))
        self.assertEqual(contract_from_dict(data), self.contract)
        for altered in ({**data, "revision": True}, {**data, "ignored": "field"}):
            with self.assertRaises(ValueError):
                contract_from_dict(altered)

    def test_result_schema_is_checked_even_when_digest_is_recomputed(self) -> None:
        result = evaluate(self.contract, self.goal_run, [self.evidence()], now=NOW)
        invalid_result_fields: tuple[dict[str, Any], ...] = (
            {"run_state": "invalid"},
            {"outcome_required": False},
            {"constraint_required": 999},
            {"evidence": {"accepted": True}},
            {"progress": "complete"},
            {"observed_values": {"accepted": []}},
            {"observed_values": {}},
            {"delta": "zero"},
        )
        for fields in invalid_result_fields:
            altered = {**result, **fields}
            altered["id"] = fingerprint({k: v for k, v in altered.items() if k != "id"})
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                decide(altered, approved_result_id=altered["id"])

    def test_baseline_observed_values_must_match_the_contract(self) -> None:
        previous = evaluate(self.contract, self.goal_run, [self.evidence()], now=NOW)
        for value in (False, "true", 1):
            altered = {**previous, "observed_values": {"accepted": value}}
            altered["id"] = fingerprint({k: v for k, v in altered.items() if k != "id"})
            with self.subTest(value=value), self.assertRaises(ValueError):
                evaluate(self.contract, self.goal_run, [], now=NOW, previous=altered)

    def test_baseline_partition_cannot_reclassify_constraints_as_outcomes(self) -> None:
        contract = replace(
            self.contract,
            criteria=(self.criterion, replace(self.criterion, id="guard", invariant=True)),
        )
        evidence = [self.evidence(), self.evidence(id="guard", criterion_id="guard")]
        previous = evaluate(contract, self.goal_run, evidence, now=NOW)
        previous.update(
            outcome_required=2,
            constraint_required=0,
            outcome_met=previous["met"],
            constraint_met=[],
        )
        previous["id"] = fingerprint({k: v for k, v in previous.items() if k != "id"})
        with self.assertRaises(ValueError):
            evaluate(contract, self.goal_run, evidence, now=NOW, previous=previous)

    def test_continuity_counters_reject_boolean_and_negative_inputs(self) -> None:
        result = evaluate(self.contract, self.goal_run, [], now=NOW)
        for counters in ({"stalled_runs": True}, {"unknown_runs": -1}):
            with self.subTest(counters=counters), self.assertRaises(ValueError):
                decide(result, **counters)


class UpstreamCharacterizationTests(unittest.TestCase):
    """Load the actual stdlib-only upstream store, with no application initialization."""

    def setUp(self) -> None:
        path = Path(__file__).resolve().parents[1] / "nanomuse/goals/store.py"
        spec = importlib.util.spec_from_file_location("goal_eval_upstream_store", path)
        assert spec and spec.loader
        self.module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.module
        spec.loader.exec_module(self.module)

    def test_skipping_all_steps_completes_upstream_goal_without_evidence(self) -> None:
        store = self.module.GoalStore(":memory:")
        try:
            goal = store.create("Deliver an accepted report", steps=["Write", "Submit"])
            store.update_step(goal.id, 1, status="skipped")
            goal = store.update_step(goal.id, 2, status="skipped")
            self.assertEqual((goal.progress, goal.status), ("2/2", "done"))
            self.assertEqual(goal.notes, "")
        finally:
            store.close()

    def test_explicit_done_needs_no_finished_steps(self) -> None:
        store = self.module.GoalStore(":memory:")
        try:
            goal = store.create("Deliver an accepted report", steps=["Write"])
            goal = store.set_status(goal.id, "done")
            self.assertEqual((goal.progress, goal.status), ("0/1", "done"))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
