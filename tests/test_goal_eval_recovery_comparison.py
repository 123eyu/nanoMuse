"""Source-to-source regressions for the fixed recovery comparison."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
import socket
from pathlib import Path
from typing import Any

import goal_eval_recovery as benchmark
import goal_eval_recovery_comparison as source_comparison
import pytest

from nanomuse.schema import LLMResponse

HERE = Path(__file__).resolve().parent


def _deny_network(*_args: Any, **_kwargs: Any) -> None:
    raise AssertionError("source comparison tests must not open network connections")


@pytest.fixture(scope="module")
def comparison_run(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    data = source_comparison.dataset()
    output = tmp_path_factory.mktemp("recovery-comparison") / "run"
    baseline = HERE / "fixtures/goal_eval/recovery_baseline_v1.py"
    candidate = HERE / "goal_eval_recovery_policy.py"
    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(socket.socket, "connect", _deny_network)
        patcher.setattr(socket.socket, "connect_ex", _deny_network)
        patcher.setattr(socket, "create_connection", _deny_network)
        summary = asyncio.run(source_comparison.run(output, baseline, candidate))
    entries = {
        (variant, case_id): json.loads(
            (output / "runs" / variant / case_id / "trial.json").read_text(encoding="utf-8")
        )
        for variant in source_comparison.VARIANTS
        for case_id in data["inventory"]
    }
    return {
        "output": output,
        "summary": summary,
        "data": data,
        "entries": entries,
        "baseline": baseline,
        "candidate": candidate,
    }


def _entry(run: dict[str, Any], variant: str, case_id: str) -> dict[str, Any]:
    return run["entries"][(variant, case_id)]


def _copy_archive(run: dict[str, Any], tmp_path: Path) -> Path:
    target = tmp_path / "archive"
    shutil.copytree(run["output"], target)
    return target


def _rewrite_manifest_file(archive: Path, relative: str) -> None:
    target = archive / relative
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][relative] = benchmark.digest(target)
    benchmark.write_json(manifest_path, manifest)


def test_original_cases_pass_for_both_sources_with_identical_visible_traces(
    comparison_run: dict[str, Any],
) -> None:
    original = comparison_run["data"]["groups"]["original"]
    for case_id in original:
        baseline = _entry(comparison_run, "baseline", case_id)
        candidate = _entry(comparison_run, "candidate", case_id)
        assert baseline["oracle"]["passed"], (case_id, baseline["oracle"]["violations"])
        assert candidate["oracle"]["passed"], (case_id, candidate["oracle"]["violations"])
        assert baseline["record"]["events"] == candidate["record"]["events"], case_id
        assert baseline["record"]["reports"] == candidate["record"]["reports"], case_id
        for identity in ("condition_digest", "judge_identity", "environment_identity"):
            assert baseline["record"][identity] == candidate["record"][identity], (
                case_id,
                identity,
            )

    group = comparison_run["summary"]["groups"]["original"]
    assert group["metrics"]["baseline"]["handling_passed"] == len(original)
    assert group["metrics"]["candidate"]["handling_passed"] == len(original)
    assert group["changes"] == {"unchanged": len(original)}


def test_extension_candidate_passes_and_baseline_failures_are_detected(
    comparison_run: dict[str, Any],
) -> None:
    extension = comparison_run["data"]["groups"]["extension"]
    expected_changes = comparison_run["data"]["expected_changes"]
    assert set(expected_changes) == set(comparison_run["data"]["inventory"])
    for case_id in extension:
        candidate = _entry(comparison_run, "candidate", case_id)
        assert candidate["oracle"]["passed"], (case_id, candidate["oracle"]["violations"])

    for case_id, expected_change in expected_changes.items():
        row = next(
            row for row in comparison_run["summary"]["all"]["rows"] if row["case_id"] == case_id
        )
        assert row["change"] == expected_change, (case_id, row)
        if expected_change == "improved":
            baseline = _entry(comparison_run, "baseline", case_id)
            candidate = _entry(comparison_run, "candidate", case_id)
            assert not baseline["oracle"]["passed"], case_id
            assert candidate["oracle"]["passed"], case_id
        elif case_id in extension:
            baseline = _entry(comparison_run, "baseline", case_id)
            candidate = _entry(comparison_run, "candidate", case_id)
            assert baseline["oracle"]["passed"] is candidate["oracle"]["passed"], case_id

    assert comparison_run["summary"]["all"]["regression_gate_passed"] is True


def test_same_source_comparison_is_unchanged_and_candidate_regressions_are_visible(
    comparison_run: dict[str, Any],
) -> None:
    inventory = comparison_run["data"]["inventory"]
    baseline_entries = [
        copy.deepcopy(_entry(comparison_run, "baseline", case_id)) for case_id in inventory
    ]
    same_source: list[dict[str, Any]] = []
    for baseline_entry in baseline_entries:
        left = copy.deepcopy(baseline_entry)
        right = copy.deepcopy(baseline_entry)
        left["variant"] = "baseline"
        right["variant"] = "candidate"
        same_source.extend((left, right))
    unchanged = source_comparison.comparison(same_source, inventory)
    assert unchanged["changes"] == {"unchanged": len(inventory)}
    # The comparison detects no regressions, while the baseline itself still
    # misses the newly added extension gold cases required by the gate.
    assert unchanged["regression_gate_passed"] is False

    regressed_entries = copy.deepcopy(list(comparison_run["entries"].values()))
    target = next(
        entry
        for entry in regressed_entries
        if entry["variant"] == "candidate" and entry["record"]["case_id"] == "normal_ack"
    )
    target["oracle"]["passed"] = False
    regressed = source_comparison.comparison(regressed_entries, inventory)
    row = next(row for row in regressed["rows"] if row["case_id"] == "normal_ack")
    assert row["change"] == "regressed"
    assert regressed["changes"]["regressed"] == 1
    assert regressed["regression_gate_passed"] is False


def test_missing_incomparable_and_duplicate_trials_keep_pairing_honest(
    comparison_run: dict[str, Any],
) -> None:
    inventory = comparison_run["data"]["inventory"]
    entries = copy.deepcopy(list(comparison_run["entries"].values()))
    missing = [
        entry
        for entry in entries
        if not (entry["variant"] == "candidate" and entry["record"]["case_id"] == "normal_ack")
    ]
    result = source_comparison.comparison(missing, inventory)
    assert result["planned_pairs"] == len(inventory)
    assert result["eligible_pairs"] == len(inventory) - 1
    assert result["changes"]["missing"] == 1
    assert (
        next(row for row in result["rows"] if row["case_id"] == "normal_ack")["change"] == "missing"
    )

    incomparable = copy.deepcopy(entries)
    candidate = next(
        entry
        for entry in incomparable
        if entry["variant"] == "candidate" and entry["record"]["case_id"] == "normal_ack"
    )
    candidate["record"]["judge_identity"]["oracle_sha256"] = "changed"
    result = source_comparison.comparison(incomparable, inventory)
    assert result["planned_pairs"] == len(inventory)
    assert result["eligible_pairs"] == len(inventory) - 1
    assert result["changes"]["incomparable"] == 1

    with pytest.raises(ValueError, match="duplicate trial"):
        source_comparison.comparison(entries + [copy.deepcopy(entries[0])], inventory)


def test_idempotency_budgets_hidden_outcomes_revocation_and_receipts(
    comparison_run: dict[str, Any],
) -> None:
    cap = _entry(comparison_run, "candidate", "idempotent_final_permission_cap")
    plus_one = _entry(comparison_run, "candidate", "idempotent_permission_cap_plus_one")
    assert cap["record"]["reports"][0]["handling"] == "unresolved"
    assert len(cap["record"]["events"]) == 3
    assert plus_one["record"]["reports"][0]["handling"] == "completed"
    assert len(plus_one["record"]["events"]) == 4
    assert plus_one["record"]["events"][-1]["op"] == "commit"

    no_effect = _entry(comparison_run, "candidate", "idempotent_lookup_down_exhausted")
    hidden_effect = _entry(
        comparison_run, "candidate", "idempotent_lookup_down_exhausted_committed"
    )
    assert no_effect["oracle"]["world_completed"] is False
    assert hidden_effect["oracle"]["world_completed"] is True
    assert no_effect["record"]["reports"][0]["handling"] == "unresolved"
    assert hidden_effect["record"]["reports"][0]["handling"] == "unresolved"

    for case_id in ("idempotent_lookup_down_revoked", "idempotent_lookup_down_denied"):
        entry = _entry(comparison_run, "candidate", case_id)
        assert entry["record"]["reports"][0]["handling"] == "blocked"
        assert entry["oracle"]["assertions"]["no_unauthorized_commit"] is True

    for case_id in (
        "idempotent_lookup_down_bad_receipt_binding",
        "idempotent_lookup_down_bad_receipt_effect",
        "receipt_effect_conflict_existing_path",
    ):
        entry = _entry(comparison_run, "candidate", case_id)
        assert entry["record"]["reports"][0]["handling"] in ("unresolved", "conflict")
        assert entry["record"]["reports"][0]["handling"] != "completed"
        assert entry["oracle"]["passed"]


def test_replay_never_loads_an_actor_or_executes_an_agent(
    comparison_run: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        source_comparison,
        "load_actor",
        lambda *_args, **_kwargs: pytest.fail("replay loaded an actor"),
    )
    monkeypatch.setattr(
        benchmark,
        "execute_case",
        lambda *_args, **_kwargs: pytest.fail("replay executed an agent"),
    )
    assert source_comparison.replay(comparison_run["output"]) == {
        "mode": "evaluator_rescore",
        "consistent": True,
        "trials": len(comparison_run["data"]["inventory"]) * len(source_comparison.VARIANTS),
    }


@pytest.mark.parametrize("failure", ["constructor", "bad_json", "first_trigger"])
def test_candidate_actor_errors_are_saved_as_failed_trials_and_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    monkeypatch.setattr(socket.socket, "connect", _deny_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _deny_network)
    monkeypatch.setattr(socket, "create_connection", _deny_network)
    full = source_comparison.dataset()
    case_id = "duplicate_obligation" if failure == "first_trigger" else "normal_ack"
    case = next(case for case in full["cases"] if case["id"] == case_id)
    monkeypatch.setattr(
        source_comparison,
        "dataset",
        lambda: {
            "inventory": [case_id],
            "groups": {"original": [case_id], "extension": []},
            "cases": [case],
        },
    )
    normal_loader = source_comparison.load_actor

    class ConstructorCrash:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("candidate actor construction failed")

    class BadJSON:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def next(self, _messages: list[Any]) -> LLMResponse:
            return LLMResponse(content="not-json", finish_reason="stop")

    class FailFirstTrigger:
        constructions = 0

        def __init__(self, name: str, task: dict[str, Any], prior: set[str]) -> None:
            type(self).constructions += 1
            if type(self).constructions == 1:
                raise RuntimeError("first trigger actor failed")
            self.delegate = benchmark.ScriptedPolicy(name, task, prior)

        def next(self, messages: list[Any]) -> LLMResponse:
            return self.delegate.next(messages)

    fault_actors = {
        "constructor": ConstructorCrash,
        "bad_json": BadJSON,
        "first_trigger": FailFirstTrigger,
    }

    def load_actor(source: Path) -> Any:
        if source.name == "candidate.py":
            return fault_actors[failure]
        return normal_loader(source)

    monkeypatch.setattr(source_comparison, "load_actor", load_actor)
    output = tmp_path / f"run-{failure}"
    summary = asyncio.run(
        source_comparison.run(
            output,
            HERE / "fixtures/goal_eval/recovery_baseline_v1.py",
            HERE / "goal_eval_recovery_policy.py",
        )
    )
    baseline = json.loads(
        (output / "runs/baseline" / case_id / "trial.json").read_text(encoding="utf-8")
    )
    candidate = json.loads(
        (output / "runs/candidate" / case_id / "trial.json").read_text(encoding="utf-8")
    )
    assert baseline["oracle"]["passed"]
    assert not candidate["oracle"]["passed"]
    assert candidate["oracle"]["assertions"]["execution_succeeded"] is False
    assert candidate["record"]["execution_errors"][0]["error_type"] == (
        "JSONDecodeError" if failure == "bad_json" else "RuntimeError"
    )
    assert candidate["record"]["reports"][0]["handling"] == "unresolved"
    assert "harness_error" in candidate["record"]["reports"][0]["reason"]
    row = next(row for row in summary["all"]["rows"] if row["case_id"] == case_id)
    assert row["change"] == "regressed"
    assert summary["all"]["regression_gate_passed"] is False
    if failure == "bad_json":
        assert candidate["record"]["model_calls"] == 1
    elif failure == "constructor":
        assert candidate["record"]["model_calls"] == 0
    else:
        assert len(candidate["record"]["reports"]) == 2
        assert candidate["record"]["reports"][1]["handling"] == "completed"
        assert candidate["record"]["model_calls"] > 0
    assert source_comparison.replay(output)["consistent"] is True


@pytest.mark.parametrize("damage", ["missing_source", "changed_actor", "trial_identity", "summary"])
def test_replay_rejects_source_actor_trial_and_summary_tampering(
    comparison_run: dict[str, Any], tmp_path: Path, damage: str
) -> None:
    archive = _copy_archive(comparison_run, tmp_path)
    manifest_path = archive / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if damage == "missing_source":
        manifest["sources"].pop(next(iter(manifest["sources"])))
        benchmark.write_json(manifest_path, manifest)
    elif damage == "changed_actor":
        actor = archive / manifest["actors"]["candidate"]["path"]
        actor.write_text(actor.read_text(encoding="utf-8") + "\n# changed\n", encoding="utf-8")
    elif damage == "trial_identity":
        relative = "runs/candidate/normal_ack/trial.json"
        target = archive / relative
        entry = json.loads(target.read_text(encoding="utf-8"))
        entry["record"]["condition_digest"] = "changed"
        benchmark.write_json(target, entry)
        _rewrite_manifest_file(archive, relative)
    else:
        relative = "summary.json"
        target = archive / relative
        summary = json.loads(target.read_text(encoding="utf-8"))
        summary["all"]["metrics"]["candidate"]["handling_passed"] = 0
        benchmark.write_json(target, summary)
        _rewrite_manifest_file(archive, relative)

    with pytest.raises(ValueError):
        source_comparison.replay(archive)


def test_mixed_actor_versions_are_rejected(comparison_run: dict[str, Any]) -> None:
    entries = copy.deepcopy(list(comparison_run["entries"].values()))
    target = next(e for e in entries if e["variant"] == "candidate")
    target["record"]["actor_source_sha256"] = "a-different-code-version"
    with pytest.raises(ValueError, match="mixed actor source identity"):
        source_comparison.comparison(entries, comparison_run["data"]["inventory"])


@pytest.mark.parametrize("identity", ["condition_digest", "environment_identity"])
def test_different_conditions_or_runtime_make_pair_incomparable(
    comparison_run: dict[str, Any], identity: str
) -> None:
    entries = copy.deepcopy(list(comparison_run["entries"].values()))
    target = next(e for e in entries if e["variant"] == "candidate")
    target["record"][identity] = "different"
    result = source_comparison.comparison(entries, comparison_run["data"]["inventory"])
    assert result["planned_pairs"] == len(comparison_run["data"]["inventory"])
    assert result["eligible_pairs"] == len(comparison_run["data"]["inventory"]) - 1
    assert result["changes"]["incomparable"] == 1
    assert result["regression_gate_passed"] is False


def test_extension_gold_cannot_be_changed_silently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "extension.json"
    value = json.loads(source_comparison.EXTENSION.read_text(encoding="utf-8"))
    value["cases"][0]["expected_handling"] = ["unresolved"]
    benchmark.write_json(target, value)
    monkeypatch.setattr(source_comparison, "EXTENSION", target)
    with pytest.raises(ValueError, match="extension gold changed"):
        source_comparison.dataset()


@pytest.mark.parametrize("failure", ["syntax", "step_limit", "after_commit"])
def test_actual_faulty_source_keeps_failure_and_partial_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    data = source_comparison.dataset()
    case = next(c for c in data["cases"] if c["id"] == "normal_ack")
    monkeypatch.setattr(
        source_comparison,
        "dataset",
        lambda: {
            "inventory": [case["id"]],
            "groups": {"original": [case["id"]], "extension": []},
            "cases": [case],
        },
    )
    faulty = tmp_path / "faulty.py"
    if failure == "syntax":
        faulty.write_text("def broken(:\n", encoding="utf-8")
    elif failure == "after_commit":
        source = (HERE / "goal_eval_recovery_policy.py").read_text(encoding="utf-8")
        source = source.replace(
            '        if self.stage == "commit" and self.valid_status(data):',
            '        if self.stage == "commit":\n'
            '            raise RuntimeError("injected failure after real effect")\n'
            '        if self.stage == "commit" and self.valid_status(data):',
        )
        faulty.write_text(source, encoding="utf-8")
    else:
        faulty.write_text(
            """from nanomuse.schema import LLMResponse, ToolCall, Function
from nanomuse.goals.evaluation import canonical
class ScriptedPolicy:
    def __init__(self, name, task, prior):
        self.request = task["obligation"]
    def next(self, messages):
        return LLMResponse(tool_calls=[ToolCall(function=Function(
            name="recovery_action", arguments=canonical({"op":"permission", **self.request})
        ))], finish_reason="tool_calls")
""",
            encoding="utf-8",
        )
    output = tmp_path / "run"
    result = asyncio.run(
        source_comparison.run(
            output,
            HERE / "fixtures/goal_eval/recovery_baseline_v1.py",
            faulty,
        )
    )
    trial = json.loads(
        (output / "runs/candidate/normal_ack/trial.json").read_text(encoding="utf-8")
    )
    assert result["all"]["changes"] == {"regressed": 1}
    assert not result["all"]["regression_gate_passed"]
    assert trial["record"]["execution_errors"]
    assert not trial["oracle"]["assertions"]["execution_succeeded"]
    assert trial["oracle"]["world_completed"] is (failure == "after_commit")
    assert trial["oracle"]["effect_count"] == (1 if failure == "after_commit" else 0)
    assert source_comparison.replay(output)["consistent"]
