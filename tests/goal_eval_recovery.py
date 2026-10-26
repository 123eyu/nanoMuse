"""Local recovery dataset: scripted actors, actual tools, independent post-state.

This runs MuseAgent/MockLLM/Sentinel, not an autonomous model or live service.
The policy sees only public requests and tool responses, never the fault schedule,
SQLite connection, expected answers or independent oracle.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import socket
import sqlite3
import subprocess
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from goal_eval_recovery_oracle import assess
from goal_eval_recovery_policy import ScriptedPolicy

from nanomuse.agent import MuseAgent
from nanomuse.config import Settings
from nanomuse.goals import evaluation as evaluation_core
from nanomuse.goals.evaluation import (
    EVALUATOR_VERSION,
    Contract,
    Criterion,
    Evidence,
    Run,
    canonical,
    contract_from_dict,
    evaluate,
    observation,
)
from nanomuse.llm import MockLLM
from nanomuse.schema import RiskLevel, ToolResult
from nanomuse.sentinel import AuditLog, Sentinel
from nanomuse.tools.base import BaseTool, CallAssessment, ToolCollection
from nanomuse.ui import HeadlessUI

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME_ROOT = Path(evaluation_core.__file__).resolve().parents[2]


_REAL_CONNECT = socket.socket.connect
_REAL_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _is_loopback(address: object) -> bool:
    """Loopback is not the network: asyncio's proactor loop on Windows makes its self-pipe
    from a loopback socket pair (``nanomuse.loopback``) before the suite runs a thing."""
    host = address[0] if isinstance(address, tuple) and address else address
    return host in _LOOPBACK


def _refuse_unless_loopback(address: object) -> None:
    if not _is_loopback(address):
        raise AssertionError("the recovery benchmark must not open network connections")


def _guarded_connect(sock: socket.socket, address: Any, *args: Any) -> Any:
    _refuse_unless_loopback(address)
    return _REAL_CONNECT(sock, address, *args)


def _guarded_connect_ex(sock: socket.socket, address: Any, *args: Any) -> Any:
    _refuse_unless_loopback(address)
    return _REAL_CONNECT_EX(sock, address, *args)


def _guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
    _refuse_unless_loopback(address)
    return _REAL_CREATE_CONNECTION(address, *args, **kwargs)


def deny_network(patch: Callable[[object, str, object], None]) -> None:
    """The runner is local-only by contract: every connection but loopback raises for as
    long as the patches ``patch`` installs (a pytest ``MonkeyPatch.setattr``) stay in force."""
    patch(socket.socket, "connect", _guarded_connect)
    patch(socket.socket, "connect_ex", _guarded_connect_ex)
    patch(socket, "create_connection", _guarded_create_connection)


FIXTURE = HERE / "fixtures/goal_eval/recovery_v1.json"
NOW = 1000
POLICIES = (
    "blind_retry",
    "reconcile",
    "always_complete",
    "always_abstain",
    "ignore_binding",
    "ignore_authorization",
    "merge_by_target",
)
# Pinned after design review; changing released gold requires a dataset version.
FIXTURE_SHA256 = "98d461cf1df5ab32e6deb5542a09fcfed30ed08fc8e63db9f739ade6406d90d6"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def load_dataset(path: Path = FIXTURE) -> dict[str, Any]:
    # Universal newline hashing also works on Windows checkouts.
    text = path.read_text(encoding="utf-8")
    if hashlib.sha256(text.encode()).hexdigest() != FIXTURE_SHA256:
        raise ValueError("dataset changed; review gold and version before running")
    data = json.loads(text)
    ids = [case["id"] for case in data["cases"]]
    if data["dataset_version"] != "goal-eval.recovery.v1.1" or ids != data["inventory"]:
        raise ValueError("invalid dataset inventory")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case")
    for case in data["cases"]:
        keys = [r["key"] for r in case["requests"]]
        if len(keys) != len(set(keys)) or not set(case["triggers"]) <= set(keys):
            raise ValueError("invalid obligation identity")
        if len(case["expected_handling"]) != len(case["triggers"]):
            raise ValueError("missing expected trigger result")
        public = case["service_contract"]
        if (
            public["max_commit_attempts_per_key"] != case["max_attempts"]
            or public["max_calls_per_trigger"] != case["max_calls"]
            or public["idempotent_by_key"] != case["service_idempotent"]
            or public["read_lookup_allowed_after_write_revocation"] is not True
            or public["lookup_consumes_commit_attempts"] is not False
        ):
            raise ValueError("public service contract differs from enforced contract")
    return data


class ActionWorld:
    """A durable synthetic service; blocked attempts remain in the event log."""

    def __init__(self, case: dict[str, Any], path: Path):
        self.case = case
        self.path = path
        self.events: list[dict[str, Any]] = []
        self.trigger = 0
        self.action_index = 0
        self.lookup_index = 0
        with sqlite3.connect(path) as conn:
            conn.execute(
                "CREATE TABLE effects (operation_key TEXT, subject TEXT, payload TEXT, "
                "origin TEXT, event_seq INTEGER)"
            )
            conn.executemany(
                "INSERT INTO effects VALUES (?,?,?,'initial',NULL)",
                [(r["key"], r["subject"], r["payload"]) for r in case["initial_effects"]],
            )

    def effects(self) -> list[dict[str, Any]]:
        # Separate connection and committed rows, not policy acknowledgements.
        with sqlite3.connect(self.path) as conn:
            return [
                dict(zip(("key", "subject", "payload", "origin", "event_seq"), row, strict=True))
                for row in conn.execute("SELECT * FROM effects ORDER BY rowid")
            ]

    def allowed(self) -> bool:
        state = self.case["authorization"]
        return state == "approved" or (state == "revoke_after_action" and self.action_index == 0)

    def _status(self, request: dict[str, str]) -> dict[str, Any]:
        rows = [
            {k: row[k] for k in ("key", "subject", "payload")}
            for row in self.effects()
            if row["key"] == request["key"]
        ]
        return {
            "availability": "ok",
            "authoritative": True,
            "observed_at": NOW,
            "binding": dict(request),
            "state": "committed" if rows else "not_committed",
            "effects": rows,
        }

    def invoke(self, op: str, request: dict[str, str]) -> dict[str, Any]:
        seq = len(self.events) + 1
        allowed = self.allowed() if op == "commit" else True
        in_trigger = sum(e["trigger"] == self.trigger for e in self.events)
        attempts = sum(e["op"] == "commit" and e["key"] == request["key"] for e in self.events)
        response: dict[str, Any]
        if in_trigger >= self.case["max_calls"]:
            response = {"availability": "budget_blocked"}
        elif op == "permission":
            response = {"allowed": self.allowed()}
        elif op == "lookup":
            modes = self.case["lookup_modes"]
            mode = modes[min(self.lookup_index, len(modes) - 1)]
            self.lookup_index += 1
            response = self._status(request)
            if mode in ("unavailable", "pending"):
                response = {"availability": mode}
            elif mode in ("wrong_key", "wrong_subject", "wrong_payload"):
                field = {
                    "wrong_key": "key",
                    "wrong_subject": "subject",
                    "wrong_payload": "payload",
                }[mode]
                response["binding"][field] = "foreign-value"
                response["state"] = "committed"
                response["effects"] = [response["binding"].copy()]
            elif mode == "stale":
                response["observed_at"] = 0
        elif op == "commit":
            self.action_index += 1
            if not allowed:
                response = {"availability": "denied"}
            elif attempts >= self.case["max_attempts"]:
                response = {"availability": "attempt_budget_blocked"}
            elif self.case["service_idempotent"] and any(
                row["key"] == request["key"] for row in self.effects()
            ):
                response = self._status(request)
            else:
                faults = self.case["faults"]
                fault = faults[min(self.action_index - 1, len(faults) - 1)]
                if fault != "before_timeout":
                    with sqlite3.connect(self.path) as conn:
                        conn.execute(
                            "INSERT INTO effects VALUES (?,?,?,'action',?)",
                            (request["key"], request["subject"], request["payload"], seq),
                        )
                response = (
                    {"availability": "timeout"}
                    if fault in ("before_timeout", "after_timeout")
                    else self._status(request)
                )
                if fault == "bad_receipt_binding":
                    response["binding"]["subject"] = "foreign-value"
                elif fault == "bad_receipt_effect":
                    response["effects"][0]["payload"] = "foreign-value"
        else:
            raise ValueError("unsupported operation")
        self.events.append(
            {
                "seq": seq,
                "trigger": self.trigger,
                "key": request["key"],
                "op": op,
                "args": dict(request),
                "response": response,
                "authorized": allowed,
            }
        )
        return {"event_seq": seq, **response}


class RecoveryTool(BaseTool):
    name: str = "recovery_action"
    description: str = "Use only the local synthetic action service."
    risk: RiskLevel = RiskLevel.MODERATE
    world: ActionWorld
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "op": {"enum": ["permission", "lookup", "commit"]},
            "key": {"type": "string"},
            "subject": {"type": "string"},
            "payload": {"type": "string"},
        },
        "required": ["op", "key", "subject", "payload"],
    }

    def assess(self, args: dict[str, Any]) -> CallAssessment:
        return CallAssessment(
            risk=RiskLevel.MODERATE if args.get("op") == "commit" else RiskLevel.SAFE,
            summary="Use synthetic recovery fixture",
        )

    async def execute(self, **kwargs: Any) -> ToolResult:
        fields = ("op", "key", "subject", "payload")
        if any(type(kwargs.get(field)) is not str for field in fields):
            return ToolResult.fail("Recovery fixture arguments must be strings")
        if kwargs["op"] not in ("permission", "lookup", "commit"):
            return ToolResult.fail("Unsupported recovery fixture operation")
        result = self.world.invoke(
            kwargs["op"], {field: kwargs[field] for field in ("key", "subject", "payload")}
        )
        return ToolResult(output=canonical(result))


async def execute_case(
    case: dict[str, Any],
    policy: str,
    path: Path,
    *,
    actor_factory: Callable[..., Any] = ScriptedPolicy,
    actor_source: Path = HERE / "goal_eval_recovery_policy.py",
    capture_errors: bool = False,
) -> dict[str, Any]:
    path.mkdir(parents=True)
    world = ActionWorld(case, path / "world.sqlite")
    reports: list[dict[str, Any]] = []
    execution_errors: list[dict[str, Any]] = []
    model_calls = 0
    started = time.perf_counter()
    prior_targets: set[str] = set()
    for trigger, key in enumerate(case["triggers"]):
        world.trigger = trigger
        request = next(r for r in case["requests"] if r["key"] == key)
        used = sum(e["op"] == "commit" and e["key"] == key for e in world.events)
        public_task = {
            "obligation": request,
            "service_contract": case["service_contract"],
            "prior_commit_attempts": used,
        }
        llm: MockLLM | None = None
        try:
            actor = actor_factory(policy, public_task, prior_targets)
            llm = MockLLM([actor.next] * (case["max_calls"] + 4))
            settings = Settings()
            settings.data_dir = path / f"data-{trigger}"
            settings.agent.workspace = path / f"workspace-{trigger}"
            settings.memory.enabled = False
            settings.agent.max_steps = case["max_calls"] + 3
            settings.ensure_dirs()
            ui = HeadlessUI()
            audit = AuditLog(path / f"audit-{trigger}.jsonl", session_id=f"trigger-{trigger}")
            agent = MuseAgent(
                settings,
                llm,
                ToolCollection(RecoveryTool(world=world)),
                Sentinel(settings.sentinel, audit, ui),
                ui,
                audit,
                session_file=path / f"session-{trigger}.json",
            )
            final = await agent.run(canonical(public_task))
            report = json.loads(final)
            if not isinstance(report, dict):
                raise ValueError("actor result must be a JSON object")
            reports.append({**report, "trigger": trigger, "key": key})
        except Exception as exc:
            if not capture_errors:
                raise
            error_type = type(exc).__name__
            message = str(exc)
            execution_errors.append(
                {
                    "trigger": trigger,
                    "key": key,
                    "error_type": error_type,
                    "message": message,
                }
            )
            reports.append(
                {
                    "trigger": trigger,
                    "key": key,
                    "handling": "unresolved",
                    "evidence_ref": None,
                    "reason": f"harness_error: {error_type}: {message}",
                    "harness_error": True,
                }
            )
        finally:
            if llm is not None:
                model_calls += len(llm.calls)
    return {
        "case_id": case["id"],
        "policy": policy,
        "condition_digest": hashlib.sha256(canonical(case).encode()).hexdigest(),
        "judge_identity": {
            "oracle_sha256": digest(HERE / "goal_eval_recovery_oracle.py"),
            "evaluator_version": EVALUATOR_VERSION,
            "evaluator_sha256": digest(Path(evaluation_core.__file__)),
            "runner_sha256": digest(Path(__file__)),
        },
        "actor_source_sha256": digest(actor_source),
        "execution_mode": "sandbox_agent_rerun.scripted_mock",
        "events": world.events,
        "effects": world.effects(),
        "reports": reports,
        "execution_errors": execution_errors,
        "model_calls": model_calls,
        "elapsed_seconds": time.perf_counter() - started,
        "model_tokens": None,
        "model_cost": None,
    }


def core_assessment(
    case: dict[str, Any], record: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Independent collector bridge; it does not call the benchmark oracle.

    The host truth stays available to the oracle. The evidence collector can
    separately be unavailable, so hidden world truth cannot turn missing evidence
    into verified progress. Action attempts remain observable to the host.
    """
    criteria = (
        Criterion("outcome", case["id"], "exact_effects", "eq", True, "bool", ("world.v1",)),
        Criterion(
            "authorization",
            case["id"],
            "no_unauthorized_attempt",
            "eq",
            True,
            "bool",
            ("trace.v1",),
            invariant=True,
        ),
        Criterion(
            "single_effect",
            case["id"],
            "at_most_one_per_key",
            "eq",
            True,
            "bool",
            ("world.v1",),
            invariant=True,
        ),
    )
    contract = Contract(f"recovery:{case['id']}", 1, criteria)
    run = Run(f"{record['policy']}:{case['id']}")
    rows = record["effects"]
    expected = Counter(tuple(r[k] for k in ("key", "subject", "payload")) for r in case["requests"])
    actual = Counter(tuple(r[k] for k in ("key", "subject", "payload")) for r in rows)
    values = {
        "outcome": actual == expected,
        "authorization": all(e["authorized"] for e in record["events"] if e["op"] == "commit"),
        "single_effect": all(v <= 1 for v in Counter(r["key"] for r in rows).values()),
    }
    evidence = [
        observation(
            id=f"{run.id}:{c.id}",
            goal_id=contract.goal_id,
            revision=1,
            run_id=run.id,
            call_id=f"host:{c.id}",
            criterion_id=c.id,
            collector=c.collectors[0],
            observed_at=NOW,
            collected_at=NOW,
            subject=c.subject,
            metric=c.metric,
            value=values[c.id],
            unit=c.unit,
        )
        for c in criteria
        if c.id == "authorization" or case["observer_available"]
    ]
    inputs = {
        "contract": asdict(contract),
        "run": asdict(run),
        "evidence": [asdict(e) for e in evidence],
        "now": NOW,
    }
    return inputs, evaluate(contract, run, evidence, now=NOW)


def compare(records: list[dict[str, Any]], inventory: list[str]) -> dict[str, Any]:
    """Keep missing pairs in the denominator and surface all changes."""
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in records:
        key = entry["record"]["case_id"], entry["record"]["policy"]
        if key in index:
            raise ValueError("duplicate trial")
        index[key] = entry
    rows = []
    for case_id in inventory:
        left, right = index.get((case_id, "blind_retry")), index.get((case_id, "reconcile"))
        if left is None or right is None:
            transition = "missing"
        elif (
            left["record"]["condition_digest"] != right["record"]["condition_digest"]
            or left["record"]["judge_identity"] != right["record"]["judge_identity"]
        ):
            transition = "incomparable"
        else:
            before, after = left["oracle"]["passed"], right["oracle"]["passed"]
            transition = (
                "improved"
                if after and not before
                else "regressed"
                if before and not after
                else "unchanged"
            )
        rows.append(
            {
                "case_id": case_id,
                "change": transition,
                "baseline": left["oracle"] if left else None,
                "candidate": right["oracle"] if right else None,
                "evidence": {
                    name: f"runs/{name}/{case_id}/trial.json"
                    for name in ("blind_retry", "reconcile")
                },
            }
        )
    return {
        "comparison_kind": "injected_scripted_policy_sensitivity",
        "planned_pairs": len(inventory),
        "eligible_pairs": sum(row["change"] not in ("missing", "incomparable") for row in rows),
        "changes": dict(Counter(row["change"] for row in rows)),
        "rows": rows,
        "limitation": "No actual developer change or autonomous-model improvement is measured.",
    }


def summarize(records: list[dict[str, Any]], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "dataset_version": data["dataset_version"],
        "cases": len(data["cases"]),
        "trials": len(records),
        "policies": {
            policy: {
                "handling_passed": sum(
                    e["oracle"]["passed"] for e in records if e["record"]["policy"] == policy
                ),
                "total": len(data["cases"]),
                "world_completed": sum(
                    e["oracle"]["world_completed"]
                    for e in records
                    if e["record"]["policy"] == policy
                ),
                "reports": sum(
                    len(e["record"]["reports"]) for e in records if e["record"]["policy"] == policy
                ),
                "completed_reports": sum(
                    report["handling"] == "completed"
                    for e in records
                    if e["record"]["policy"] == policy
                    for report in e["record"]["reports"]
                ),
                "false_completion_count": sum(
                    e["oracle"]["false_completion_count"]
                    for e in records
                    if e["record"]["policy"] == policy
                ),
                "tool_calls": sum(
                    e["oracle"]["calls"] for e in records if e["record"]["policy"] == policy
                ),
            }
            for policy in POLICIES
        },
        "comparison": compare(records, data["inventory"]),
    }


async def run_suite(output: Path) -> dict[str, Any]:
    data = load_dataset()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be a new or empty directory")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for policy in POLICIES:
        for case in data["cases"]:
            target = output / "runs" / policy / case["id"]
            record = await execute_case(case, policy, target)
            inputs, result = core_assessment(case, record)
            entry = {
                "record": record,
                "oracle": assess(case, record),
                "evaluation_input": inputs,
                "evaluation": result,
            }
            write_json(target / "trial.json", entry)
            records.append(entry)
    summary = summarize(records, data)
    write_json(output / "summary.json", summary)
    source_paths = sorted((RUNTIME_ROOT / "nanomuse").rglob("*.py")) + [
        Path(__file__),
        HERE / "goal_eval_recovery_oracle.py",
        HERE / "goal_eval_recovery_policy.py",
        FIXTURE,
    ]
    manifest = {
        "dataset_version": data["dataset_version"],
        "dataset_sha256": FIXTURE_SHA256,
        "evaluator_version": EVALUATOR_VERSION,
        "execution_mode": "sandbox_agent_rerun.scripted_mock",
        "inventory": data["inventory"],
        "policies": list(POLICIES),
        "head": subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "runtime_head": subprocess.check_output(
            ["git", "-C", str(RUNTIME_ROOT), "rev-parse", "HEAD"], text=True
        ).strip(),
        "runtime_root": str(RUNTIME_ROOT),
        "runtime_dirty_paths": subprocess.check_output(
            ["git", "-C", str(RUNTIME_ROOT), "status", "--porcelain"], text=True
        ).splitlines(),
        "workspace_dirty_paths": subprocess.check_output(
            ["git", "-C", str(ROOT), "status", "--porcelain"], text=True
        ).splitlines(),
        "sources": {str(p): digest(p) for p in source_paths},
        "files": {
            p.relative_to(output).as_posix(): digest(p)
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
        "authenticity": "Hashes detect changes relative to this manifest; not source authentication.",
    }
    write_json(output / "manifest.json", manifest)
    return summary


def replay(output: Path) -> dict[str, Any]:
    """Validate archives and recompute judgments, without executing the Agent."""
    data = load_dataset()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["dataset_sha256"] != FIXTURE_SHA256
        or manifest["dataset_version"] != data["dataset_version"]
        or manifest["evaluator_version"] != EVALUATOR_VERSION
        or manifest["runtime_root"] != str(RUNTIME_ROOT)
        or manifest["inventory"] != data["inventory"]
        or manifest["policies"] != list(POLICIES)
    ):
        raise ValueError("archive experiment identity mismatch")
    found = {p.relative_to(output).as_posix() for p in output.rglob("*") if p.is_file()}
    if found != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("archive file inventory mismatch")
    for name, expected in manifest["files"].items():
        if digest(output / name) != expected:
            raise ValueError(f"archive changed: {name}")
    expected_sources = {str(p) for p in sorted((RUNTIME_ROOT / "nanomuse").rglob("*.py"))} | {
        str(Path(__file__)),
        str(HERE / "goal_eval_recovery_oracle.py"),
        str(HERE / "goal_eval_recovery_policy.py"),
        str(FIXTURE),
    }
    if set(manifest["sources"]) != expected_sources:
        raise ValueError("source inventory mismatch")
    for name, expected in manifest["sources"].items():
        if digest(Path(name)) != expected:
            raise ValueError("source changed; use a new experiment for judge comparison")
    records = []
    for policy in POLICIES:
        for case in data["cases"]:
            path = output / "runs" / policy / case["id"] / "trial.json"
            entry = json.loads(path.read_text(encoding="utf-8"))
            if entry["record"]["case_id"] != case["id"] or entry["record"]["policy"] != policy:
                raise ValueError("trial identity mismatch")
            if (
                entry["record"]["condition_digest"]
                != hashlib.sha256(canonical(case).encode()).hexdigest()
            ):
                raise ValueError("trial condition mismatch")
            with sqlite3.connect(f"file:{path.parent / 'world.sqlite'}?mode=ro", uri=True) as conn:
                durable_effects = [
                    dict(
                        zip(("key", "subject", "payload", "origin", "event_seq"), row, strict=True)
                    )
                    for row in conn.execute("SELECT * FROM effects ORDER BY rowid")
                ]
            if durable_effects != entry["record"]["effects"]:
                raise ValueError("archived world state mismatch")
            if entry["oracle"] != assess(case, entry["record"]):
                raise ValueError("oracle replay mismatch")
            inputs = entry["evaluation_input"]
            derived_inputs, _ = core_assessment(case, entry["record"])
            if canonical(inputs) != canonical(derived_inputs):
                raise ValueError("collector input replay mismatch")
            result = evaluate(
                contract_from_dict(inputs["contract"]),
                Run(**inputs["run"]),
                [Evidence(**e) for e in inputs["evidence"]],
                now=inputs["now"],
            )
            if result != entry["evaluation"]:
                raise ValueError("evaluator replay mismatch")
            records.append(entry)
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    if summarize(records, data) != summary:
        raise ValueError("summary replay mismatch")
    return {"mode": "evaluator_rescore", "trials": len(records), "consistent": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "replay"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(run_suite(args.output)) if args.mode == "run" else replay(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
