"""Regression checks for the local recovery benchmark's evidence and archive."""

from __future__ import annotations

import asyncio
import copy
import json
import shutil
from pathlib import Path
from typing import Any

import goal_eval_recovery as benchmark
import pytest
from goal_eval_recovery_oracle import assess

from nanomuse.schema import Message

HERE = Path(__file__).resolve().parent


@pytest.fixture(scope="module")
def suite(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    output = tmp_path_factory.mktemp("recovery-suite") / "run"
    # The runner is local-only by contract. Keep the full execution hermetic even
    # if a future import or tool accidentally adds a network connection.
    with pytest.MonkeyPatch.context() as patcher:
        benchmark.deny_network(patcher.setattr)
        summary = asyncio.run(benchmark.run_suite(output))
    data = benchmark.load_dataset()
    cases = {case["id"]: case for case in data["cases"]}
    entries = {
        (policy, case_id): json.loads(
            (output / "runs" / policy / case_id / "trial.json").read_text(encoding="utf-8")
        )
        for policy in benchmark.POLICIES
        for case_id in data["inventory"]
    }
    return {"output": output, "summary": summary, "cases": cases, "entries": entries}


def _entry(suite: dict[str, Any], policy: str, case_id: str) -> dict[str, Any]:
    return suite["entries"][(policy, case_id)]


def test_reconcile_matches_every_fixed_gold_case(suite: dict[str, Any]) -> None:
    cases = suite["cases"]
    for case_id in benchmark.load_dataset()["inventory"]:
        entry = _entry(suite, "reconcile", case_id)
        result = assess(cases[case_id], entry["record"])
        assert result["passed"], (case_id, result["violations"])
        assert result == entry["oracle"]


def test_injected_controls_are_caught_by_the_relevant_gold_assertions(
    suite: dict[str, Any],
) -> None:
    cases = suite["cases"]

    blind = assess(
        cases["commit_lost_ack"], _entry(suite, "blind_retry", "commit_lost_ack")["record"]
    )
    assert not blind["passed"]
    assert not blind["assertions"]["retry_reconciliation"]
    assert not blind["assertions"]["no_duplicate_requested_effects"]

    false_completion = assess(
        cases["unavailable_uncommitted"],
        _entry(suite, "always_complete", "unavailable_uncommitted")["record"],
    )
    assert not false_completion["passed"]
    assert not false_completion["assertions"]["completed_requires_world_and_evidence"]

    abstention = assess(
        cases["normal_ack"], _entry(suite, "always_abstain", "normal_ack")["record"]
    )
    assert not abstention["passed"]
    assert not abstention["assertions"]["handling_matches_expected"]

    wrong_binding = assess(
        cases["foreign_subject_evidence"],
        _entry(suite, "ignore_binding", "foreign_subject_evidence")["record"],
    )
    assert not wrong_binding["passed"]
    assert not wrong_binding["assertions"]["completed_requires_world_and_evidence"]

    denied = assess(
        cases["denied_authorization"],
        _entry(suite, "ignore_authorization", "denied_authorization")["record"],
    )
    assert not denied["passed"]
    assert not denied["assertions"]["no_unauthorized_commit"]

    merged = assess(
        cases["distinct_obligations"],
        _entry(suite, "merge_by_target", "distinct_obligations")["record"],
    )
    assert not merged["passed"]
    assert not merged["assertions"]["expected_effects"]


def test_declared_native_idempotency_allows_same_binding_retry(suite: dict[str, Any]) -> None:
    case = suite["cases"]["native_idempotency"]
    # The blind policy retries; the service's explicit idempotency guarantee
    # makes that same-key retry safe and still yields exactly one effect.
    record = _entry(suite, "blind_retry", "native_idempotency")["record"]
    assert case["service_idempotent"] is True
    commits = [event for event in record["events"] if event["op"] == "commit"]
    assert len(commits) == 2
    assert commits[0]["args"] == commits[1]["args"]
    result = assess(case, record)
    assert result["passed"], result["violations"]
    assert result["effect_count"] == 1


def test_unknown_cases_separate_hidden_world_truth_from_evidence_handling(
    suite: dict[str, Any],
) -> None:
    committed = _entry(suite, "reconcile", "unavailable_committed")
    uncommitted = _entry(suite, "reconcile", "unavailable_uncommitted")
    assert committed["oracle"]["world_completed"] is True
    assert uncommitted["oracle"]["world_completed"] is False
    assert committed["oracle"]["passed"] is True
    assert uncommitted["oracle"]["passed"] is True
    assert committed["evaluation"]["criteria"]["outcome"] == "unknown"
    assert uncommitted["evaluation"]["criteria"]["outcome"] == "unknown"
    assert committed["record"]["reports"][0]["handling"] == "unresolved"
    assert uncommitted["record"]["reports"][0]["handling"] == "unresolved"


@pytest.mark.parametrize(
    ("case_id", "mutation", "assertion"),
    [
        ("normal_ack", "drop_report", "report_inventory"),
        ("distinct_obligations", "cross_reference", "completed_requires_world_and_evidence"),
        ("denied_authorization", "unauthorized_commit", "no_unauthorized_commit"),
        ("normal_ack", "duplicate_seq", "event_authorization_and_binding"),
        ("normal_ack", "reverse_events", "event_sequence_integrity"),
        ("normal_ack", "missing_event", "event_sequence_integrity"),
        ("duplicate_obligation", "trigger_rollback", "event_sequence_integrity"),
        ("normal_ack", "call_budget", "call_budget"),
        ("normal_ack", "attempt_budget", "commit_attempt_budget"),
    ],
)
def test_independent_oracle_rejects_tampered_records(
    suite: dict[str, Any], case_id: str, mutation: str, assertion: str
) -> None:
    case = copy.deepcopy(suite["cases"][case_id])
    policy = "ignore_authorization" if mutation == "unauthorized_commit" else "reconcile"
    record = copy.deepcopy(_entry(suite, policy, case_id)["record"])

    if mutation == "drop_report":
        record["reports"].pop()
    elif mutation == "cross_reference":
        record["reports"][1]["evidence_ref"] = next(
            event["seq"]
            for event in record["events"]
            if event["trigger"] == 0 and event["response"].get("state") == "committed"
        )
    elif mutation == "unauthorized_commit":
        assert any(
            event["op"] == "commit" and event["authorized"] is False for event in record["events"]
        )
    elif mutation == "duplicate_seq":
        duplicate = copy.deepcopy(record["events"][-1])
        duplicate["seq"] = record["events"][0]["seq"]
        record["events"].append(duplicate)
    elif mutation == "reverse_events":
        record["events"].reverse()
    elif mutation == "missing_event":
        record["events"].pop(1)
    elif mutation == "trigger_rollback":
        record["events"][-1]["trigger"] = 0
    elif mutation == "call_budget":
        case["max_calls"] = 0
    elif mutation == "attempt_budget":
        case["max_attempts"] = 0

    result = assess(case, record)
    assert result["passed"] is False
    assert result["assertions"][assertion] is False


def test_core_outcome_evidence_tracks_world_truth_only_when_observer_available(
    suite: dict[str, Any],
) -> None:
    for case_id, case in suite["cases"].items():
        for policy in benchmark.POLICIES:
            entry = _entry(suite, policy, case_id)
            criteria = entry["evaluation"]["criteria"]
            world_completed = entry["oracle"]["world_completed"]
            if case["observer_available"]:
                assert (criteria["outcome"] == "met") is world_completed, (case_id, policy)
            else:
                assert criteria["outcome"] == "unknown", (case_id, policy, criteria)
                assert entry["evaluation"]["status"] != "verified", (case_id, policy)


def test_unknown_world_outcomes_are_not_counted_as_report_false_completions(
    suite: dict[str, Any],
) -> None:
    for case_id in ("unavailable_uncommitted", "unavailable_committed"):
        entry = _entry(suite, "reconcile", case_id)
        assert entry["oracle"]["false_completion_count"] == 0
        assert entry["oracle"]["world_completed"] is (case_id == "unavailable_committed")
    reports = [
        report
        for case_id in suite["cases"]
        for report in _entry(suite, "reconcile", case_id)["record"]["reports"]
    ]
    assert len(reports) == sum(len(case["triggers"]) for case in suite["cases"].values())
    assert suite["summary"]["policies"]["reconcile"]["false_completion_count"] == sum(
        _entry(suite, "reconcile", case_id)["oracle"]["false_completion_count"]
        for case_id in suite["cases"]
    )


def test_attempt_exhaustion_does_not_hide_an_already_committed_effect(
    suite: dict[str, Any],
) -> None:
    for case_id in ("attempt_budget_committed", "revoked_after_commit"):
        entry = _entry(suite, "reconcile", case_id)
        assert entry["oracle"]["passed"]
        assert entry["record"]["reports"][0]["handling"] == "completed"
        assert entry["oracle"]["commit_attempts"] == 1
        assert entry["oracle"]["effect_count"] == 1
    ambiguous = _entry(suite, "reconcile", "attempt_budget_unavailable")
    assert ambiguous["record"]["reports"][0]["handling"] == "unresolved"
    blind = _entry(suite, "blind_retry", "attempt_budget")
    assert not blind["oracle"]["assertions"]["exhausted_status_supported"]


def test_final_permitted_call_can_supply_valid_completion_evidence(
    suite: dict[str, Any],
) -> None:
    entry = _entry(suite, "reconcile", "lookup_at_call_limit")
    assert len(entry["record"]["events"]) == suite["cases"]["lookup_at_call_limit"]["max_calls"]
    assert entry["record"]["events"][-1]["op"] == "lookup"
    assert entry["record"]["reports"][0]["handling"] == "completed"
    assert entry["oracle"]["passed"]


def test_actor_and_independent_observer_can_have_different_knowledge(
    suite: dict[str, Any],
) -> None:
    entry = _entry(suite, "reconcile", "independent_observer_available")
    assert entry["oracle"]["passed"]
    assert entry["record"]["reports"][0]["handling"] == "unresolved"
    assert entry["evaluation"]["status"] == "verified"


def test_actor_sees_enforced_service_contract_but_not_gold_or_fault_schedule(
    suite: dict[str, Any],
) -> None:
    for case_id in ("native_idempotency", "commit_lost_ack", "attempt_budget"):
        session = suite["output"] / "runs/reconcile" / case_id / "session-0.json"
        first_user = next(
            message
            for message in json.loads(session.read_text(encoding="utf-8"))["messages"]
            if message["role"] == "user"
        )
        task = json.loads(first_user["content"])
        assert task["service_contract"] == suite["cases"][case_id]["service_contract"]
        assert set(task) == {"obligation", "service_contract", "prior_commit_attempts"}
        assert "faults" not in task and "expected_handling" not in task


def test_oracle_derives_authorization_independently_of_service_flag(
    suite: dict[str, Any],
) -> None:
    case = suite["cases"]["denied_authorization"]
    record = copy.deepcopy(_entry(suite, "ignore_authorization", case["id"])["record"])
    for event in record["events"]:
        if event["op"] == "commit":
            event["authorized"] = True
    result = assess(case, record)
    assert not result["assertions"]["no_unauthorized_commit"]
    assert not result["assertions"]["event_authorization_and_binding"]


def test_oracle_rejects_permission_claim_after_authorization_revocation(
    suite: dict[str, Any],
) -> None:
    case = suite["cases"]["revoked_before_retry"]
    record = copy.deepcopy(_entry(suite, "reconcile", case["id"])["record"])
    permission_events = [event for event in record["events"] if event["op"] == "permission"]
    assert len(permission_events) >= 2
    assert permission_events[-1]["response"]["allowed"] is False
    # Rewriting the readback after the first commit attempt cannot extend the
    # authority window recorded by the independent oracle.
    permission_events[-1]["response"]["allowed"] = True
    result = assess(case, record)
    assert not result["assertions"]["event_authorization_and_binding"]
    assert result["assertions"]["event_sequence_integrity"]


def test_blocked_report_requires_a_denial_reference(suite: dict[str, Any]) -> None:
    case = suite["cases"]["denied_authorization"]
    record = copy.deepcopy(_entry(suite, "reconcile", case["id"])["record"])
    record["reports"][0]["evidence_ref"] = None
    result = assess(case, record)
    assert not result["assertions"]["blocked_status_supported"]


@pytest.mark.parametrize("availability", ["budget_blocked", "attempt_budget_blocked"])
def test_service_budget_refusal_cannot_establish_absent_effect(
    suite: dict[str, Any],
    availability: str,
) -> None:
    case = suite["cases"]["normal_ack"]
    actor = benchmark.ScriptedPolicy(
        "reconcile",
        {
            "obligation": case["requests"][0],
            "service_contract": case["service_contract"],
            "prior_commit_attempts": 0,
        },
        set(),
    )
    actor.stage = "commit"
    response = actor.next(
        [
            Message.tool(
                json.dumps({"availability": availability, "event_seq": 1}),
                "fixture-call",
                "recovery_action",
            )
        ]
    )
    assert json.loads(response.content or "{}")["handling"] == "unresolved"


def test_compare_keeps_missing_case_policy_pair_in_planned_denominator(
    suite: dict[str, Any],
) -> None:
    entries = [
        entry
        for entry in suite["entries"].values()
        if not (
            entry["record"]["policy"] == "reconcile" and entry["record"]["case_id"] == "normal_ack"
        )
    ]
    comparison = benchmark.compare(entries, list(suite["cases"]))
    assert comparison["planned_pairs"] == len(suite["cases"])
    assert comparison["changes"]["missing"] == 1
    row = next(row for row in comparison["rows"] if row["case_id"] == "normal_ack")
    assert row["change"] == "missing"
    assert row["candidate"] is None


@pytest.mark.parametrize("identity", ["condition_digest", "judge_identity"])
def test_compare_refuses_changed_conditions_or_judge(suite: dict[str, Any], identity: str) -> None:
    entries = copy.deepcopy(list(suite["entries"].values()))
    candidate = next(
        entry
        for entry in entries
        if entry["record"]["policy"] == "reconcile" and entry["record"]["case_id"] == "normal_ack"
    )
    candidate["record"][identity] = "changed"
    result = benchmark.compare(entries, list(suite["cases"]))
    assert result["changes"]["incomparable"] == 1
    assert result["eligible_pairs"] == result["planned_pairs"] - 1
    row = next(row for row in result["rows"] if row["case_id"] == "normal_ack")
    assert row["change"] == "incomparable"


def test_independent_world_disagrees_with_a_plausible_completion_receipt(
    suite: dict[str, Any],
) -> None:
    case = suite["cases"]["normal_ack"]
    record = copy.deepcopy(_entry(suite, "reconcile", "normal_ack")["record"])
    # A matching cited receipt cannot override actual absent effects.
    record["effects"] = []
    result = assess(case, record)
    assert result["world_completed"] is False
    assert result["false_completion_count"] == 1
    assert not result["assertions"]["completed_requires_world_and_evidence"]


def test_fixture_gold_change_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    changed = tmp_path / "recovery_v1.json"
    data = json.loads(benchmark.FIXTURE.read_text(encoding="utf-8"))
    data["cases"][0]["expected_handling"][0] = "unresolved"
    changed.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setattr(benchmark, "FIXTURE", changed)
    with pytest.raises(ValueError, match="dataset changed"):
        benchmark.load_dataset(changed)


def test_replay_recomputes_without_running_agent(
    suite: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        benchmark, "execute_case", lambda *_args, **_kwargs: pytest.fail("Agent reran")
    )
    result = benchmark.replay(suite["output"])
    assert result["consistent"] is True
    assert result["trials"] == len(benchmark.load_dataset()["inventory"]) * len(benchmark.POLICIES)


@pytest.mark.parametrize("damage", ["missing_trial", "changed_bytes"])
def test_replay_rejects_missing_or_modified_archive_files(
    suite: dict[str, Any], tmp_path: Path, damage: str
) -> None:
    damaged = tmp_path / damage
    shutil.copytree(suite["output"], damaged)
    target = damaged / "runs/reconcile/normal_ack/trial.json"
    if damage == "missing_trial":
        target.unlink()
    else:
        target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError, match="archive file inventory mismatch|archive changed"):
        benchmark.replay(damaged)


@pytest.mark.parametrize("damage", ["summary", "collector"])
def test_replay_rederives_data_even_if_file_hash_is_replaced(
    suite: dict[str, Any], tmp_path: Path, damage: str
) -> None:
    damaged = tmp_path / damage
    shutil.copytree(suite["output"], damaged)
    relative = "summary.json" if damage == "summary" else "runs/reconcile/normal_ack/trial.json"
    target = damaged / relative
    data = json.loads(target.read_text(encoding="utf-8"))
    if damage == "summary":
        data["policies"]["always_complete"]["handling_passed"] = len(suite["cases"])
    else:
        data["evaluation_input"]["evidence"][0]["value"] = False
    benchmark.write_json(target, data)
    manifest = json.loads((damaged / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][relative] = benchmark.digest(target)
    benchmark.write_json(damaged / "manifest.json", manifest)
    with pytest.raises(ValueError, match="summary replay mismatch|collector input replay mismatch"):
        benchmark.replay(damaged)


@pytest.mark.parametrize("identity", ["dataset_version", "evaluator_version", "runtime_root"])
def test_replay_requires_the_recorded_runtime_and_judge(
    suite: dict[str, Any], tmp_path: Path, identity: str
) -> None:
    damaged = tmp_path / identity
    shutil.copytree(suite["output"], damaged)
    manifest = json.loads((damaged / "manifest.json").read_text(encoding="utf-8"))
    manifest[identity] = "different"
    benchmark.write_json(damaged / "manifest.json", manifest)
    with pytest.raises(ValueError, match="archive experiment identity mismatch"):
        benchmark.replay(damaged)


def test_replay_rejects_deleted_source_manifest_entry(
    suite: dict[str, Any], tmp_path: Path
) -> None:
    damaged = tmp_path / "missing-source"
    shutil.copytree(suite["output"], damaged)
    manifest = json.loads((damaged / "manifest.json").read_text(encoding="utf-8"))
    manifest["sources"].pop(next(iter(manifest["sources"])))
    benchmark.write_json(damaged / "manifest.json", manifest)
    with pytest.raises(ValueError, match="source inventory mismatch"):
        benchmark.replay(damaged)
