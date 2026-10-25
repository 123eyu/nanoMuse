"""Fixed offline GoalEval benchmark contract and runner checks."""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

import pytest

from nanomuse.goals.evaluation import canonical, decide, evaluate, fingerprint
from tests.goal_eval_benchmark import (
    EXPECTED_IDS,
    _contract_for_case,
    _evaluate_case,
    _evidence_for_frame,
    _fixture_sha,
    _sha,
    _source_status,
    _validate_fixture_digest,
    _write_output,
    load_fixture,
    oracle_truth,
    replay,
    run_benchmark,
)


@pytest.fixture(scope="module")
def fixture_data() -> dict[str, Any]:
    return load_fixture()


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("goal-eval") / "archive"
    run_benchmark(output)
    return output


def _copy_archive(archive: Path, tmp_path: Path) -> Path:
    target = tmp_path / "run"
    shutil.copytree(archive, target)
    return target


@pytest.mark.parametrize("case_id", EXPECTED_IDS)
def test_all_fixed_scenarios_match_independent_gold(case_id: str, archive: Path) -> None:
    case = next(item for item in load_fixture()["cases"] if item["id"] == case_id)
    result = json.loads((archive / "results" / f"{case_id}.json").read_text())
    assert result["verdict"] == case["expected_verdict"]
    assert result["decision"] == case["expected_decision"]
    assert result["oracle_actual"] == case["outcome_truth"]
    assert result["expected_outcome_truth"] == case["outcome_truth"]
    assert result["source_status"] == case["expected_source_status"]
    assert result["submission_count"] == case["expected_submission_count"]
    assert all(result["matches_gold"].values())


def test_fixture_digest_normalizes_newlines_and_rejects_content_changes(
    tmp_path: Path,
) -> None:
    fixture_text = load_fixture()
    source_text = (Path(__file__).parent / "fixtures/goal_eval/benchmark.json").read_text(
        encoding="utf-8"
    )
    lf_path = tmp_path / "benchmark-lf.json"
    crlf_path = tmp_path / "benchmark-crlf.json"
    lf_path.write_bytes(source_text.encode("utf-8"))
    crlf_path.write_bytes(source_text.replace("\n", "\r\n").encode("utf-8"))
    assert _fixture_sha(lf_path) == _fixture_sha(crlf_path)
    assert _validate_fixture_digest(crlf_path) == _fixture_sha(lf_path)

    changed = copy.deepcopy(fixture_text)
    changed["cases"][0]["expected_decision"] = "continue"
    changed_path = tmp_path / "benchmark-changed.json"
    changed_path.write_text(json.dumps(changed, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="fixture contents changed"):
        _validate_fixture_digest(changed_path)


def test_crash_reconcile_uses_interrupted_attempt_then_fresh_attempt_two(archive: Path) -> None:
    input_record = json.loads((archive / "inputs/crash_reconcile.json").read_text())
    result_record = json.loads((archive / "results/crash_reconcile.json").read_text())
    first, second = input_record["invocations"]
    assert first["input"]["run"]["state"] == "interrupted"
    assert first["input"]["run"]["attempt"] == 1
    assert first["input"]["evidence"] == []
    assert first["evaluation"]["status"] == "unknown"
    assert first["decision_reason"] == "reconcile_before_retry"
    assert second["input"]["run"]["state"] == "completed"
    assert second["input"]["run"]["attempt"] == 2
    assert second["input"]["evidence"]
    assert all(item["attempt"] == 2 for item in second["input"]["evidence"])
    assert all(item["observed_at"] == 10001 for item in second["input"]["evidence"])
    assert result_record["verdict"] == "verified"
    assert result_record["submission_count"] == 1


def test_duplicate_trigger_reuses_evidence_without_new_progress(archive: Path) -> None:
    input_record = json.loads((archive / "inputs/duplicate_trigger.json").read_text())
    first, second = input_record["invocations"]
    assert first["input"]["now"] == 10000
    assert second["input"]["now"] == 10001
    assert first["input"]["evidence"] == second["input"]["evidence"]
    assert first["input"]["run"] == second["input"]["run"]
    assert second["evaluation"]["status"] == "verified"
    assert second["evaluation"]["progress"] == "stalled"
    assert second["evaluation"]["delta"] == 0
    assert second["decision"] == "escalate"
    result = json.loads((archive / "results/duplicate_trigger.json").read_text())
    assert result["submission_count"] == 1
    assert result["frames"] == result["invocation_count"] == 2


def test_inventory_labels_and_oracle_are_independent(fixture_data: dict[str, Any]) -> None:
    cases = fixture_data["cases"]
    assert len(cases) == 12
    false_cases = [case for case in cases if case["outcome_truth"] is False]
    assert len(false_cases) == 8
    assert sum(case["expected_verdict"] == "verified" for case in cases) == 3
    assert sum(case["expected_verdict"] == "unknown" for case in cases) == 2
    assert (
        next(case for case in cases if case["id"] == "unsupported_done")["outcome_truth"] is False
    )
    assert next(case for case in cases if case["id"] == "stale_evidence")["outcome_truth"] is True
    altered = copy.deepcopy(cases[0])
    altered["expected_verdict"] = "contradicted"
    altered["frames"][0]["service"]["available"] = False
    assert oracle_truth(altered, fixture_data) is True


def test_unavailable_service_hides_service_evidence_but_not_hidden_truth(
    fixture_data: dict[str, Any],
) -> None:
    from nanomuse.goals.evaluation import Run, contract_from_dict

    case = copy.deepcopy(
        next(case for case in fixture_data["cases"] if case["id"] == "unsupported_done")
    )
    frame = case["frames"][0]
    frame["artifact"] = {"title": "Weekly report", "items": ["Reviewed milestones"]}
    frame["service"].update(
        {"available": False, "accepted": True, "window_open": True, "successful_submissions": 1}
    )
    frame["observation"] = "observe"
    case["criteria"] = ["report_valid", "accepted"]
    assert oracle_truth(case, fixture_data) is True
    contract = contract_from_dict(_contract_for_case(case, fixture_data))
    evidence = _evidence_for_frame(
        contract,
        Run("run:unavailable"),
        frame,
        case["id"],
        0,
        fixture_data["business_contract"]["report_schema"],
        fixture_data["business_contract"]["successful_submission_maximum"],
        [],
    )
    assert [item.criterion_id for item in evidence] == ["report_valid"]


def test_all_skipped_really_uses_goalstore_auto_completion(archive: Path) -> None:
    result = json.loads((archive / "results/all_skipped.json").read_text())
    assert result["source_status"] == "done"
    assert result["source_step_statuses"] == ["skipped", "skipped"]
    assert result["verdict"] == "partial"
    assert result["submission_count"] == 0


def test_snapshot_replay_is_complete_and_manifested(archive: Path) -> None:
    report = json.loads((archive / "report.json").read_text())
    assert report["metrics"]["replay_count"] == 12
    assert report["metrics"]["all_gold_assertions_match"] is True
    assert replay(archive)["replay_count"] == 12
    assert report["metrics"]["baseline_false_completion"] == {"count": 7, "total": 8}
    assert report["metrics"]["false_completion"] == {"count": 0, "total": 8}
    assert report["metrics"]["unsupported_verified"] == {"count": 0, "total": 9}
    assert report["metrics"]["unknown_handling"] == {
        "unknown": 2,
        "total": 2,
        "oracle_actual_false": 1,
        "oracle_actual_true": 1,
    }
    assert report["metrics"]["replay_frame_count"] == 20
    assert report["metrics"]["replay_invocation_count"] == 20
    manifest = json.loads((archive / "manifest.json").read_text())
    assert len(manifest["files"]) == 38
    assert manifest["case_ids"] == list(EXPECTED_IDS)
    assert manifest["schema_version"] == 1
    assert manifest["dataset_version"] == "goal-eval.offline.v1"
    assert set(manifest["source_sha256"]) == {"runner", "evaluation", "goal_store"}


def test_missing_snapshot_fails_replay(archive: Path, tmp_path: Path) -> None:
    output = _copy_archive(archive, tmp_path)
    (output / "results/success.json").unlink()
    with pytest.raises(ValueError, match="missing or contains"):
        replay(output)


def test_tampered_snapshot_fails_replay(archive: Path, tmp_path: Path) -> None:
    output = _copy_archive(archive, tmp_path)
    path = output / "inputs/success.json"
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="snapshot changed"):
        replay(output)


def test_orphan_snapshot_fails_replay(archive: Path, tmp_path: Path) -> None:
    output = _copy_archive(archive, tmp_path)
    (output / "results/orphan.json").write_text("{}")
    with pytest.raises(ValueError, match="missing or contains"):
        replay(output)


def test_changed_summary_fails_even_with_updated_manifest(archive: Path, tmp_path: Path) -> None:
    output = _copy_archive(archive, tmp_path)
    report_path = output / "report.json"
    report = json.loads(report_path.read_text())
    report["metrics"]["false_completion"]["count"] = 1
    report_path.write_text(json.dumps(report))
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["report.json"] = _sha(report_path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="summary report differs"):
        replay(output)


def test_reduced_inventory_fails_replay(archive: Path, tmp_path: Path) -> None:
    output = _copy_archive(archive, tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["case_ids"].pop()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="inventory"):
        replay(output)


def test_changed_previous_snapshot_fails_even_with_updated_manifest(
    archive: Path, tmp_path: Path
) -> None:
    output = _copy_archive(archive, tmp_path)
    input_path = output / "inputs/revoked_acceptance.json"
    saved = json.loads(input_path.read_text())
    saved["invocations"][1]["input"]["previous"]["status"] = "partial"
    input_path.write_text(canonical(saved) + "\n")
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["files"]["inputs/revoked_acceptance.json"] = _sha(input_path)
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="recomputed input differs"):
        replay(output)


def test_nested_manifest_is_an_orphan_snapshot(archive: Path, tmp_path: Path) -> None:
    output = _copy_archive(archive, tmp_path)
    nested = output / "nested"
    nested.mkdir()
    (nested / "manifest.json").write_text("{}")
    with pytest.raises(ValueError, match="missing or contains"):
        replay(output)


def test_source_digest_change_fails_replay(archive: Path, tmp_path: Path) -> None:
    output = _copy_archive(archive, tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_sha256"]["evaluation"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="source changed"):
        replay(output)


def test_manifest_schema_and_dataset_versions_are_checked(archive: Path, tmp_path: Path) -> None:
    output = _copy_archive(archive, tmp_path)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 99
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="schema version"):
        replay(output)
    manifest["schema_version"] = 1
    manifest["dataset_version"] = "different"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="dataset version"):
        replay(output)


def _rehash(result: dict[str, Any]) -> dict[str, Any]:
    result["id"] = fingerprint({key: value for key, value in result.items() if key != "id"})
    return result


def _always_unknown(
    contract: Any, run: Any, evidence: list[Any], *, now: int, previous: Any = None
) -> dict[str, Any]:
    result = evaluate(contract, run, evidence, now=now, previous=previous)
    result["status"] = "unknown"
    result["criteria"] = {criterion.id: "unknown" for criterion in contract.criteria}
    result["met"] = []
    result["outcome_met"] = []
    result["constraint_met"] = []
    result["observed_values"] = {}
    result["lost"] = []
    result["violated"] = []
    result["evidence"] = {criterion.id: [] for criterion in contract.criteria}
    result["progress"] = "unknown"
    result["delta"] = None
    return _rehash(result)


def _always_verified(
    contract: Any, run: Any, evidence: list[Any], *, now: int, previous: Any = None
) -> dict[str, Any]:
    result = evaluate(contract, run, evidence, now=now, previous=previous)
    result["status"] = "verified"
    result["criteria"] = {criterion.id: "met" for criterion in contract.criteria}
    result["met"] = sorted(result["criteria"])
    result["outcome_met"] = sorted(c.id for c in contract.criteria if not c.invariant)
    result["constraint_met"] = sorted(c.id for c in contract.criteria if c.invariant)
    result["observed_values"] = {c.id: c.target for c in contract.criteria}
    result["lost"] = []
    result["violated"] = []
    result["evidence"] = {c.id: [f"forged:{c.id}"] for c in contract.criteria}
    result["progress"] = "advanced"
    result["delta"] = result["outcome_required"]
    return _rehash(result)


@pytest.mark.parametrize("scorer", [_always_verified, _always_unknown])
def test_pathological_evaluator_cannot_pass_runner(scorer: Any, tmp_path: Path) -> None:
    with pytest.raises(AssertionError, match="independent gold"):
        run_benchmark(tmp_path / "run", scorer=scorer)


def test_public_replay_rejects_evaluator_results_that_miss_gold(archive: Path) -> None:
    with pytest.raises(ValueError, match="recomputed input differs"):
        replay(archive, scorer=_always_unknown)


def test_replay_rejects_a_consistent_archive_that_misses_fixed_gold(tmp_path: Path) -> None:
    fixture = load_fixture()
    inputs, results = [], []
    for case in fixture["cases"]:
        input_record, result_record = _evaluate_case(case, fixture, _always_unknown)
        inputs.append(input_record)
        results.append(result_record)
    output = tmp_path / "bad-archive"
    _write_output(output, fixture, inputs, results, replay_count=len(results))
    report = json.loads((output / "report.json").read_text())
    assert report["metrics"]["unknown_handling"] == {
        "unknown": 2,
        "total": 2,
        "oracle_actual_false": 1,
        "oracle_actual_true": 1,
    }
    assert report["metrics"]["actual_unknown_outputs"] == {
        "count": 12,
        "total": 12,
        "oracle_actual_false": 8,
        "oracle_actual_true": 4,
    }
    with pytest.raises(ValueError, match="did not match fixed gold"):
        replay(output, scorer=_always_unknown)


def test_copy_goalstore_status_as_evaluation_cannot_pass_runner(tmp_path: Path) -> None:
    by_id = {case["id"]: case for case in load_fixture()["cases"]}

    def copy_status(
        contract: Any, run: Any, evidence: list[Any], *, now: int, previous: Any = None
    ) -> dict[str, Any]:
        case = by_id[run.id.removeprefix("run:")]
        with tempfile.TemporaryDirectory() as directory:
            source_status = _source_status(case, Path(directory))["status"]
        if source_status == "done":
            return _always_verified(contract, run, evidence, now=now, previous=previous)
        return evaluate(contract, run, evidence, now=now, previous=previous)

    with pytest.raises(AssertionError, match="independent gold"):
        run_benchmark(tmp_path / "run", scorer=copy_status)


def test_refreshing_expired_evidence_is_caught_as_false_verification(tmp_path: Path) -> None:
    from dataclasses import asdict

    from nanomuse.goals.evaluation import observation

    def refresh_expired(
        contract: Any, run: Any, evidence: list[Any], *, now: int, previous: Any = None
    ) -> dict[str, Any]:
        refreshed = []
        for item in evidence:
            fields = asdict(item)
            fields.pop("payload_digest")
            fields["observed_at"] = now
            fields["collected_at"] = now
            refreshed.append(observation(**fields))
        return evaluate(contract, run, refreshed, now=now, previous=previous)

    output = tmp_path / "run"
    with pytest.raises(AssertionError, match="independent gold"):
        run_benchmark(output, scorer=refresh_expired)
    result = json.loads((output / "results/stale_evidence.json").read_text())
    assert result["verdict"] == "verified"
    assert result["expected"]["expected_verdict"] == "unknown"


def test_dropping_single_submission_constraint_is_caught(tmp_path: Path) -> None:
    from dataclasses import replace

    def drop_constraint(
        contract: Any, run: Any, evidence: list[Any], *, now: int, previous: Any = None
    ) -> dict[str, Any]:
        if run.id == "run:crash_naive_retry":
            contract = replace(
                contract,
                criteria=tuple(c for c in contract.criteria if c.id != "single_submission"),
            )
        return evaluate(contract, run, evidence, now=now, previous=previous)

    output = tmp_path / "run"
    with pytest.raises(AssertionError, match="independent gold"):
        run_benchmark(output, scorer=drop_constraint)
    result = json.loads((output / "results/crash_naive_retry.json").read_text())
    assert result["verdict"] == "verified"
    assert result["expected"]["expected_verdict"] == "contradicted"


def test_evidence_ttl_boundary_and_ordering_invariance(fixture_data: dict[str, Any]) -> None:
    from nanomuse.goals.evaluation import Run, contract_from_dict, observation

    case = fixture_data["cases"][0]
    shared = fixture_data["contract"]
    case_criteria = set(case["criteria"])
    contract = contract_from_dict(
        {
            "goal_id": shared["goal_id"],
            "revision": shared["revision"],
            "criteria": [
                criterion for criterion in shared["criteria"] if criterion["id"] in case_criteria
            ],
        }
    )
    criterion = contract.criteria[0]

    def evidence(observed_at: int, evidence_id: str) -> Any:
        return observation(
            id=evidence_id,
            goal_id=contract.goal_id,
            revision=contract.revision,
            run_id="boundary-run",
            attempt=1,
            call_id=f"call:{evidence_id}",
            criterion_id=criterion.id,
            collector="fixture.business.v1",
            observed_at=observed_at,
            collected_at=observed_at,
            subject=criterion.subject,
            metric=criterion.metric,
            value=True,
            unit=criterion.unit,
        )

    run = Run("boundary-run")
    at_ttl = evidence(10000, "at-ttl")
    beyond_ttl = evidence(9999, "beyond-ttl")
    one = evaluate(contract, run, [at_ttl], now=10060)
    two = evaluate(contract, run, [at_ttl], now=10061)
    assert one["criteria"][criterion.id] == "met"
    assert two["criteria"][criterion.id] == "unknown"
    a = evaluate(contract, run, [at_ttl, beyond_ttl], now=10000)
    b = evaluate(contract, run, [beyond_ttl, at_ttl], now=10000)
    assert a == b


def test_successful_evaluation_needs_completion_review() -> None:
    from nanomuse.goals.evaluation import Contract, Criterion, Run, observation

    criterion = Criterion(
        "c", "fixture://x", "accepted", "eq", True, "boolean", ("fixture.business.v1",)
    )
    contract = Contract("goal:x", 1, (criterion,))
    evidence = observation(
        id="e",
        goal_id="goal:x",
        revision=1,
        run_id="r",
        attempt=1,
        call_id="call",
        criterion_id="c",
        collector="fixture.business.v1",
        observed_at=10,
        collected_at=10,
        subject="fixture://x",
        metric="accepted",
        value=True,
        unit="boolean",
    )
    result = evaluate(contract, Run("r"), [evidence], now=10)
    assert decide(result) == ("escalate", "review_completion")
    assert decide(result, approved_result_id=result["id"]) == ("stop", "completion_approved")
