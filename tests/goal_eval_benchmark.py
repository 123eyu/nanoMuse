"""Deterministic offline benchmark for the GoalEval snapshot evaluator."""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from nanomuse.goals.evaluation import (
    EVALUATOR_VERSION,
    Contract,
    Evidence,
    Run,
    canonical,
    contract_from_dict,
    decide,
    evaluate,
    observation,
)
from nanomuse.goals.store import GoalStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/goal_eval/benchmark.json"
RUNNER = Path(__file__).resolve()
EXPECTED_IDS = (
    "success",
    "all_skipped",
    "unsupported_done",
    "busy_no_progress",
    "window_closed",
    "approval_denied",
    "revoked_acceptance",
    "crash_naive_retry",
    "crash_reconcile",
    "malformed_artifact",
    "stale_evidence",
    "duplicate_trigger",
)
EXPECTED_VERDICTS = {"success", "crash_reconcile", "duplicate_trigger"}
EXPECTED_FALSE_IDS = {
    "all_skipped",
    "unsupported_done",
    "busy_no_progress",
    "window_closed",
    "approval_denied",
    "revoked_acceptance",
    "crash_naive_retry",
    "malformed_artifact",
}
EXPECTED_UNKNOWN_IDS = {"unsupported_done", "stale_evidence"}
EXPECTED_NOT_VERIFIED_IDS = set(EXPECTED_IDS) - EXPECTED_VERDICTS
EXPECTED_FIXTURE_SHA256 = "ea1bf9902df828bec30388a33c43bff16a52b670a8d9b66d977a816fa6dcaf8d"


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture_sha(path: Path) -> str:
    with path.open("r", encoding="utf-8", newline=None) as stream:
        content = stream.read()
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _validate_fixture_digest(path: Path) -> str:
    digest = _fixture_sha(path)
    if digest != EXPECTED_FIXTURE_SHA256:
        raise ValueError("fixture contents changed; introduce a new dataset version")
    return digest


def load_fixture() -> dict[str, Any]:
    _validate_fixture_digest(FIXTURE)
    fixture = _json(FIXTURE)
    ids = [case.get("id") for case in fixture.get("cases", [])]
    if tuple(ids) != EXPECTED_IDS or fixture.get("inventory") != list(EXPECTED_IDS):
        raise ValueError("benchmark inventory changed; create a new dataset version")
    if fixture.get("schema_version") != 1:
        raise ValueError("unsupported fixture schema version")
    if fixture.get("dataset_version") != "goal-eval.offline.v1":
        raise ValueError("benchmark dataset version changed; create a new dataset version")
    return fixture


def _valid_report(artifact: Any, schema: dict[str, Any]) -> bool:
    if not isinstance(artifact, dict):
        return False
    title = artifact.get("title")
    items = artifact.get("items")
    return (
        isinstance(title, str)
        and bool(title.strip())
        and isinstance(items, list)
        and len(items) >= schema["items_minimum"]
        and all(isinstance(item, str) and item.strip() for item in items)
    )


def oracle_truth(case: dict[str, Any], fixture: dict[str, Any]) -> bool:
    """Independent oracle over raw artifact/service facts, never evaluator output."""
    final = case["frames"][-1]
    service = final["service"]
    return (
        _valid_report(final["artifact"], fixture["business_contract"]["report_schema"])
        and service["accepted"] is True
        and service["window_open"] is True
        and service["successful_submissions"]
        <= fixture["business_contract"]["successful_submission_maximum"]
    )


def _contract_for_case(case: dict[str, Any], fixture: dict[str, Any]) -> dict[str, Any]:
    shared = fixture["contract"]
    criteria = {item["id"]: item for item in shared["criteria"]}
    selected = case["criteria"]
    if not selected or any(criterion_id not in criteria for criterion_id in selected):
        raise ValueError(f"invalid criteria subset for {case['id']}")
    return {
        "goal_id": shared["goal_id"],
        "revision": shared["revision"],
        "criteria": [criteria[item] for item in selected],
    }


def _evidence_for_frame(
    contract: Contract,
    run: Run,
    frame: dict[str, Any],
    case_id: str,
    index: int,
    schema: dict[str, Any],
    maximum_submissions: int,
    prior: list[Evidence],
) -> list[Evidence]:
    mode = frame["observation"]
    if mode == "reuse":
        return prior
    if mode == "missing":
        return []
    artifact = frame["artifact"]
    service = frame["service"]
    facts = {"report_valid": _valid_report(artifact, schema)}
    if service["available"] is True:
        facts.update(
            {
                "accepted": service["accepted"],
                "window_open": service["window_open"],
                "single_submission": service["successful_submissions"] <= maximum_submissions,
            }
        )
    return [
        observation(
            id=f"ev:{case_id}:{index}:{criterion.id}",
            goal_id=contract.goal_id,
            revision=contract.revision,
            run_id=run.id,
            attempt=run.attempt,
            call_id=f"call:{case_id}:{index}:{criterion.id}",
            criterion_id=criterion.id,
            collector="fixture.business.v1",
            observed_at=frame["at"],
            collected_at=frame["at"],
            subject=criterion.subject,
            metric=criterion.metric,
            value=facts[criterion.id],
            unit=criterion.unit,
        )
        for criterion in contract.criteria
        if criterion.id in facts
    ]


def _source_status(case: dict[str, Any], tmp: Path) -> dict[str, Any]:
    """Exercise the real GoalStore baseline and project only stable status fields."""
    store = GoalStore(tmp / "goals.sqlite")
    try:
        goal = store.create(
            "Produce and submit the weekly report",
            steps=["Prepare report", "Submit report"],
        )
        for operation in case["source_operations"]:
            if operation["op"] == "update_step":
                updated = store.update_step(goal.id, operation["index"], status=operation["status"])
                if updated is None:
                    raise ValueError("GoalStore did not return the updated goal")
                goal = updated
            elif operation["op"] == "set_status":
                updated = store.set_status(goal.id, operation["status"])
                if updated is None:
                    raise ValueError("GoalStore did not return the updated goal")
                goal = updated
            else:
                raise ValueError(f"unsupported GoalStore operation: {operation['op']}")
        assert goal is not None
        return {"status": goal.status, "steps": [step.status for step in goal.steps]}
    finally:
        store.close()


def _evaluate_case(
    case: dict[str, Any],
    fixture: dict[str, Any],
    scorer: Callable[..., dict[str, Any]] = evaluate,
) -> tuple[dict[str, Any], dict[str, Any]]:
    contract_dict = _contract_for_case(case, fixture)
    contract = contract_from_dict(contract_dict)
    base_run = Run(f"run:{case['id']}")
    schema = fixture["business_contract"]["report_schema"]
    previous: dict[str, Any] | None = None
    previous_evidence: list[Evidence] = []
    invocations = []
    final_result: dict[str, Any] | None = None
    stalled_runs = 0
    for index, frame in enumerate(case["frames"]):
        run_override = frame.get("run", {})
        run = Run(
            base_run.id,
            attempt=run_override.get("attempt", base_run.attempt),
            state=run_override.get("state", base_run.state),
        )
        evidence = _evidence_for_frame(
            contract,
            run,
            frame,
            case["id"],
            index,
            schema,
            fixture["business_contract"]["successful_submission_maximum"],
            previous_evidence,
        )
        if frame["observation"] != "reuse":
            previous_evidence = evidence
        args = {
            "contract": asdict(contract),
            "run": asdict(run),
            "evidence": [asdict(item) for item in evidence],
            "now": frame["at"],
            "previous": previous,
        }
        result = scorer(contract, run, evidence, now=frame["at"], previous=previous)
        stalled_runs = stalled_runs + 1 if result["progress"] == "stalled" else 0
        decision = decide(result, stalled_runs=stalled_runs)
        invocations.append(
            {
                "input": args,
                "evaluation": result,
                "decision": decision[0],
                "decision_reason": decision[1],
                "stalled_runs": stalled_runs,
            }
        )
        previous = result
        final_result = result
    assert final_result is not None
    with tempfile.TemporaryDirectory(prefix="goal-eval-store-") as directory:
        actual_source = _source_status(case, Path(directory))
    truth = oracle_truth(case, fixture)
    expected = {
        key: case[key]
        for key in (
            "expected_verdict",
            "expected_decision",
            "expected_submission_count",
            "expected_source_status",
            "outcome_truth",
            "requires_completion_review",
            "gold_rationale",
        )
    }
    result_record = {
        "case_id": case["id"],
        "frames": len(case["frames"]),
        "verdict": final_result["status"],
        "decision": invocations[-1]["decision"],
        "decision_reason": invocations[-1]["decision_reason"],
        "evaluation": final_result,
        "source_status": actual_source["status"],
        "source_step_statuses": actual_source["steps"],
        "submission_count": case["frames"][-1]["service"]["successful_submissions"],
        "outcome_truth": truth,
        "oracle_actual": truth,
        "expected_outcome_truth": case["outcome_truth"],
        "invocation_count": len(invocations),
        "expected": expected,
        "matches_gold": {
            "verdict": final_result["status"] == case["expected_verdict"],
            "decision": invocations[-1]["decision"] == case["expected_decision"],
            "outcome_truth": truth == case["outcome_truth"],
            "source_status": actual_source["status"] == case["expected_source_status"],
            "submission_count": case["frames"][-1]["service"]["successful_submissions"]
            == case["expected_submission_count"],
            "completion_review": (final_result["status"] == "verified")
            == case["requires_completion_review"],
        },
    }
    input_record = {
        "case_id": case["id"],
        "frames": case["frames"],
        "contract": contract_dict,
        "run": asdict(base_run),
        "invocations": invocations,
    }
    return input_record, result_record


def _aggregate(results: list[dict[str, Any]], replay_count: int = 0) -> dict[str, Any]:
    by_id = {item["case_id"]: item for item in results}
    false_cases = [by_id[case_id] for case_id in EXPECTED_FALSE_IDS]
    positives = [by_id[case_id] for case_id in EXPECTED_VERDICTS]
    unknown_gold = [by_id[case_id] for case_id in EXPECTED_UNKNOWN_IDS]
    oracle_matches = all(item["matches_gold"]["outcome_truth"] for item in results)
    return {
        "case_count": len(results),
        "frame_count": sum(item["frames"] for item in results),
        "invocation_count": sum(item["invocation_count"] for item in results),
        "baseline_false_completion": {
            "count": sum(item["source_status"] == "done" for item in false_cases),
            "total": len(EXPECTED_FALSE_IDS),
        },
        "false_completion": {
            "count": sum(item["verdict"] == "verified" for item in false_cases),
            "total": len(EXPECTED_FALSE_IDS),
        },
        "positive_controls": {
            "verified": sum(item["verdict"] == "verified" for item in positives),
            "total": len(EXPECTED_VERDICTS),
        },
        "unsupported_verified": {
            "count": sum(
                by_id[case_id]["verdict"] == "verified" for case_id in EXPECTED_NOT_VERIFIED_IDS
            ),
            "total": len(EXPECTED_NOT_VERIFIED_IDS),
        },
        "unknown_handling": {
            "unknown": sum(item["verdict"] == "unknown" for item in unknown_gold),
            "total": len(EXPECTED_UNKNOWN_IDS),
            "oracle_actual_false": sum(
                item["oracle_actual"] is False
                for item in unknown_gold
                if item["verdict"] == "unknown"
            ),
            "oracle_actual_true": sum(
                item["oracle_actual"] is True
                for item in unknown_gold
                if item["verdict"] == "unknown"
            ),
        },
        "actual_unknown_outputs": {
            "count": sum(item["verdict"] == "unknown" for item in results),
            "total": len(results),
            "oracle_actual_false": sum(
                item["oracle_actual"] is False for item in results if item["verdict"] == "unknown"
            ),
            "oracle_actual_true": sum(
                item["oracle_actual"] is True for item in results if item["verdict"] == "unknown"
            ),
        },
        "oracle_actual": {
            "true": sum(item["oracle_actual"] is True for item in results),
            "false": sum(item["oracle_actual"] is False for item in results),
            "matches_fixed_gold": oracle_matches,
        },
        "replay_count": replay_count,
        "replay_frame_count": sum(item["frames"] for item in results) if replay_count else 0,
        "replay_invocation_count": sum(item["invocation_count"] for item in results)
        if replay_count
        else 0,
        "all_gold_assertions_match": all(all(item["matches_gold"].values()) for item in results),
    }


def _case_markdown(record: dict[str, Any]) -> str:
    evaluation = record["evaluation"]
    lines = [
        f"# {record['case_id']}",
        "",
        f"- Result: `{record['verdict']}` / `{record['decision']}` ({record['decision_reason']})",
        f"- Gold: `{record['expected']['expected_verdict']}` / `{record['expected']['expected_decision']}`",
        f"- Hidden outcome truth: `{record['outcome_truth']}`",
        f"- GoalStore baseline: `{record['source_status']}`; steps `{', '.join(record['source_step_statuses'])}`",
        f"- Successful submissions: `{record['submission_count']}`",
        f"- Frames / evaluator invocations: `{record['frames']}` / `{record['invocation_count']}`",
        f"- Evaluator evidence refs: `{json.dumps(evaluation['evidence'], sort_keys=True)}`",
        f"- Gold rationale: {record['expected']['gold_rationale']}",
        "",
        "The hidden outcome oracle reads the fixture's artifact and service facts independently. Its expected label does not enter evaluator inputs.",
        "",
    ]
    return "\n".join(lines)


def _summary_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        "# Offline GoalEval benchmark report",
        "",
        f"Dataset: `{summary['dataset_version']}`. Cases: {summary['metrics']['case_count']}/12.",
        "",
        f"- GoalStore baseline false completion: {summary['metrics']['baseline_false_completion']['count']}/{summary['metrics']['baseline_false_completion']['total']} fixed false-gold cases.",
        f"- Evaluator false completion: {summary['metrics']['false_completion']['count']}/{summary['metrics']['false_completion']['total']} fixed false-gold cases.",
        f"- Positive controls: {summary['metrics']['positive_controls']['verified']}/{summary['metrics']['positive_controls']['total']} verified.",
        f"- Unknown handling: {summary['metrics']['unknown_handling']['unknown']}/{summary['metrics']['unknown_handling']['total']} fixed unknown-gold cases; actual truth false {summary['metrics']['unknown_handling']['oracle_actual_false']}, true {summary['metrics']['unknown_handling']['oracle_actual_true']}. Unknown is not counted as business false.",
        f"- All unknown outputs: {summary['metrics']['actual_unknown_outputs']['count']}/{summary['metrics']['actual_unknown_outputs']['total']} cases; actual truth false {summary['metrics']['actual_unknown_outputs']['oracle_actual_false']}, true {summary['metrics']['actual_unknown_outputs']['oracle_actual_true']}.",
        f"- Verified on any fixed nonverified case: {summary['metrics']['unsupported_verified']['count']}/{summary['metrics']['unsupported_verified']['total']}.",
        f"- Replay: {summary['metrics']['replay_count']}/{len(results)} cases, {summary['metrics']['replay_frame_count']} frames, {summary['metrics']['replay_invocation_count']} evaluator invocations.",
        f"- Gold assertions match: `{summary['metrics']['all_gold_assertions_match']}`.",
        "",
        "This is a fixed offline rules regression set. It does not measure model error rates or establish production recovery, scheduling, approval enforcement, or deduplication.",
        "",
        "| Case | Eval | Decision | Hidden truth | GoalStore | Submissions | Frames | Invocations |",
        "|---|---|---|---:|---|---:|---:|---:|",
    ]
    lines.extend(
        f"| {r['case_id']} | {r['verdict']} | {r['decision']} | {str(r['outcome_truth']).lower()} | {r['source_status']} | {r['submission_count']} | {r['frames']} | {r['invocation_count']} |"
        for r in results
    )
    return "\n".join(lines) + "\n"


def _source_hashes() -> dict[str, str]:
    evaluation_source = Path(evaluate.__code__.co_filename).resolve()
    store_source = Path(GoalStore.__init__.__code__.co_filename).resolve()
    return {
        "runner": _sha(RUNNER),
        "evaluation": _sha(evaluation_source),
        "goal_store": _sha(store_source),
    }


def _expected_files() -> list[str]:
    files = ["report.json", "report.md"]
    for case_id in EXPECTED_IDS:
        files.extend([f"inputs/{case_id}.json", f"results/{case_id}.json", f"reports/{case_id}.md"])
    return files


def _write_output(
    directory: Path,
    fixture: dict[str, Any],
    inputs: list[dict[str, Any]],
    results: list[dict[str, Any]],
    replay_count: int = 0,
) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "inputs").mkdir()
    (directory / "results").mkdir()
    (directory / "reports").mkdir()
    for record in inputs:
        (directory / "inputs" / f"{record['case_id']}.json").write_text(
            canonical(record) + "\n", encoding="utf-8"
        )
    for record in results:
        (directory / "results" / f"{record['case_id']}.json").write_text(
            canonical(record) + "\n", encoding="utf-8"
        )
        (directory / "reports" / f"{record['case_id']}.md").write_text(
            _case_markdown(record), encoding="utf-8"
        )
    report = {
        "schema_version": 1,
        "dataset_version": fixture["dataset_version"],
        "fixture_sha256": _fixture_sha(FIXTURE),
        "evaluator_version": results[0]["evaluation"]["evaluator_version"],
        "metrics": _aggregate(results, replay_count),
    }
    (directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (directory / "report.md").write_text(_summary_markdown(report, results), encoding="utf-8")
    inventory = _expected_files()
    manifest = {
        "schema_version": 1,
        "dataset_version": fixture["dataset_version"],
        "evaluation_version": results[0]["evaluation"]["evaluator_version"],
        "case_ids": list(EXPECTED_IDS),
        "files": {name: _sha(directory / name) for name in inventory},
        "source_sha256": _source_hashes(),
        "fixture_sha256": _fixture_sha(FIXTURE),
    }
    (directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def run_benchmark(output: Path, scorer: Callable[..., dict[str, Any]] = evaluate) -> dict[str, Any]:
    fixture = load_fixture()
    inputs, results = [], []
    for case in fixture["cases"]:
        input_record, result_record = _evaluate_case(case, fixture, scorer)
        inputs.append(input_record)
        results.append(result_record)
    report = _write_output(output, fixture, inputs, results)
    if (
        not report["metrics"]["all_gold_assertions_match"]
        or not report["metrics"]["oracle_actual"]["matches_fixed_gold"]
    ):
        raise AssertionError("benchmark output did not match all independent gold labels")
    replay_metrics = _replay(output, scorer, allow_initial_report=True)
    report["metrics"] = replay_metrics
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output / "report.md").write_text(_summary_markdown(report, results), encoding="utf-8")
    manifest_path = output / "manifest.json"
    manifest = _json(manifest_path)
    manifest["files"]["report.json"] = _sha(output / "report.json")
    manifest["files"]["report.md"] = _sha(output / "report.md")
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def _replay(
    directory: Path,
    scorer: Callable[..., dict[str, Any]],
    *,
    allow_initial_report: bool = False,
) -> dict[str, Any]:
    fixture = load_fixture()
    manifest = _json(directory / "manifest.json")
    names = _expected_files()
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported manifest schema version")
    if manifest.get("dataset_version") != fixture["dataset_version"]:
        raise ValueError("manifest dataset version mismatch")
    if manifest.get("case_ids") != list(EXPECTED_IDS) or set(manifest.get("files", {})) != set(
        names
    ):
        raise ValueError("manifest inventory is incomplete or contains unexpected snapshots")
    actual = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.relative_to(directory).as_posix() != "manifest.json"
    }
    if actual != set(names):
        raise ValueError("snapshot directory is missing or contains unregistered files")
    if manifest.get("fixture_sha256") != _fixture_sha(FIXTURE):
        raise ValueError("fixture changed since benchmark run")
    if manifest.get("source_sha256") != _source_hashes():
        raise ValueError("runner or evaluated source changed since benchmark run")
    if manifest.get("evaluation_version") != EVALUATOR_VERSION:
        raise ValueError("evaluation version mismatch")
    for name in names:
        if _sha(directory / name) != manifest["files"][name]:
            raise ValueError(f"snapshot changed: {name}")
    case_map = {case["id"]: case for case in fixture["cases"]}
    inputs, results = [], []
    for case_id in EXPECTED_IDS:
        saved_input = _json(directory / "inputs" / f"{case_id}.json")
        saved_result = _json(directory / "results" / f"{case_id}.json")
        if (
            saved_input.get("case_id") != case_id
            or saved_input.get("frames") != case_map[case_id]["frames"]
        ):
            raise ValueError(f"input snapshot does not match fixed case: {case_id}")
        input_record, result_record = _evaluate_case(case_map[case_id], fixture, scorer)
        if canonical(input_record) != canonical(saved_input):
            raise ValueError(f"recomputed input differs: {case_id}")
        if canonical(result_record) != canonical(saved_result):
            raise ValueError(f"replay result differs: {case_id}")
        if (directory / "reports" / f"{case_id}.md").read_text(encoding="utf-8") != _case_markdown(
            result_record
        ):
            raise ValueError(f"case report differs: {case_id}")
        inputs.append(input_record)
        results.append(result_record)
    report = {
        "schema_version": 1,
        "dataset_version": fixture["dataset_version"],
        "fixture_sha256": _fixture_sha(FIXTURE),
        "evaluator_version": EVALUATOR_VERSION,
        "metrics": _aggregate(results, replay_count=len(results)),
    }
    saved_report = _json(directory / "report.json")
    if allow_initial_report and saved_report.get("metrics", {}).get("replay_count") == 0:
        report["metrics"]["replay_count"] = 0
        report["metrics"]["replay_frame_count"] = 0
        report["metrics"]["replay_invocation_count"] = 0
    if canonical(saved_report) != canonical(report):
        raise ValueError("summary report differs")
    if (directory / "report.md").read_text(encoding="utf-8") != _summary_markdown(report, results):
        raise ValueError("summary report differs")
    report["metrics"]["replay_count"] = len(results)
    report["metrics"]["replay_frame_count"] = sum(item["frames"] for item in results)
    report["metrics"]["replay_invocation_count"] = sum(item["invocation_count"] for item in results)
    if (
        not report["metrics"]["all_gold_assertions_match"]
        or not report["metrics"]["oracle_actual"]["matches_fixed_gold"]
    ):
        raise ValueError("replay did not match fixed gold labels")
    return report["metrics"]


def replay(directory: Path, scorer: Callable[..., dict[str, Any]] = evaluate) -> dict[str, Any]:
    return _replay(directory, scorer)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--output", type=Path, help="new directory for snapshots and reports")
    group.add_argument(
        "--replay", type=Path, help="re-evaluate and compare a saved benchmark directory"
    )
    args = parser.parse_args()
    if args.output:
        report = run_benchmark(args.output)
        print(f"Wrote {report['metrics']['case_count']} cases to {args.output}")
        print(json.dumps(report["metrics"], sort_keys=True))
    else:
        metrics = replay(args.replay)
        print(json.dumps(metrics, sort_keys=True))


if __name__ == "__main__":
    main()
