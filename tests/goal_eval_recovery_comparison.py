"""Compare trusted local recovery component source snapshots under fixed judges.

Actor modules implement ScriptedPolicy(name, public_task, prior_targets).next.
They receive public observations, not gold or a world reference. This is an API
boundary for trusted fixtures, not an adversarial Python security sandbox.
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import importlib.metadata
import importlib.util
import json
import platform
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import goal_eval_recovery as benchmark
from goal_eval_recovery_oracle import assess

HERE = Path(__file__).resolve().parent
EXTENSION = HERE / "fixtures/goal_eval/recovery_extension_v1.json"
EXTENSION_SHA256 = "65efb8ae5cdcf516096e1fd0b99deb93f5775194243794c4da34628a0c7806c2"
PROTOCOL = "goal-eval.source-comparison.v1"
VARIANTS = ("baseline", "candidate")


def dataset() -> dict[str, Any]:
    parent = benchmark.load_dataset()
    text = EXTENSION.read_text(encoding="utf-8")
    if hashlib.sha256(text.encode()).hexdigest() != EXTENSION_SHA256:
        raise ValueError("extension gold changed; review and version before execution")
    extension = json.loads(text)
    if (
        extension["dataset_version"] != "goal-eval.recovery-extension.v1.1"
        or extension["parent_dataset"] != parent["dataset_version"]
        or extension["inventory"] != [case["id"] for case in extension["cases"]]
    ):
        raise ValueError("invalid extension identity")
    cases = parent["cases"] + extension["cases"]
    inventory = [case["id"] for case in cases]
    if len(set(inventory)) != len(inventory):
        raise ValueError("duplicate case identity")
    return {
        "inventory": inventory,
        "groups": {"original": parent["inventory"], "extension": extension["inventory"]},
        "expected_changes": {
            **{key: "unchanged" for key in parent["inventory"]},
            **extension["expected_changes"],
        },
        "cases": cases,
    }


def sources() -> dict[str, str]:
    paths = list((benchmark.RUNTIME_ROOT / "nanomuse").rglob("*.py")) + [
        HERE / "goal_eval_recovery.py",
        HERE / "goal_eval_recovery_oracle.py",
        Path(__file__),
        benchmark.FIXTURE,
        EXTENSION,
    ]
    # Actor source intentionally excluded from judge/environment identity.
    return {str(p): benchmark.digest(p) for p in sorted(paths)}


def environment() -> dict[str, Any]:
    return {
        "python": sys.version,
        "runtime_root": str(benchmark.RUNTIME_ROOT),
        "platform": platform.platform(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("pydantic", "openai", "pytest", "loguru")
        },
        "runtime_sources": {
            str(p.relative_to(benchmark.RUNTIME_ROOT)): benchmark.digest(p)
            for p in sorted((benchmark.RUNTIME_ROOT / "nanomuse").rglob("*.py"))
        },
        "agent": "MuseAgent/MockLLM/Sentinel; same public task and per-case budgets",
    }


def load_actor(source: Path) -> Any:
    spec = importlib.util.spec_from_file_location(
        "recovery_subject_" + benchmark.digest(source), source
    )
    if spec is None or spec.loader is None:
        raise ValueError("cannot load actor source")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ScriptedPolicy


def comparison(entries: list[dict[str, Any]], inventory: list[str]) -> dict[str, Any]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in entries:
        key = entry["record"]["case_id"], entry["variant"]
        if key[0] not in inventory or key[1] not in VARIANTS or key in index:
            raise ValueError("unexpected or duplicate trial")
        index[key] = entry
    for variant in VARIANTS:
        hashes = {e["record"]["actor_source_sha256"] for e in entries if e["variant"] == variant}
        if len(hashes) > 1:
            raise ValueError("mixed actor source identity within one variant")
    rows = []
    for case_id in inventory:
        left, right = (index.get((case_id, variant)) for variant in VARIANTS)
        if left is None or right is None:
            change = "missing"
        elif any(
            left["record"][key] != right["record"][key]
            for key in ("condition_digest", "judge_identity", "environment_identity")
        ):
            change = "incomparable"
        else:
            before, after = left["oracle"]["passed"], right["oracle"]["passed"]
            change = (
                "improved"
                if after and not before
                else "regressed"
                if before and not after
                else "unchanged"
            )
        rows.append(
            {
                "case_id": case_id,
                "change": change,
                "baseline_passed": left["oracle"]["passed"] if left else None,
                "candidate_passed": right["oracle"]["passed"] if right else None,
                "tool_call_delta": right["oracle"]["calls"] - left["oracle"]["calls"]
                if left and right
                else None,
                "candidate_violations": right["oracle"]["violations"] if right else None,
                "evidence": {v: f"runs/{v}/{case_id}/trial.json" for v in VARIANTS},
            }
        )
    metrics = {
        variant: {
            "handling_passed": sum(
                e["oracle"]["passed"] for e in entries if e["variant"] == variant
            ),
            "planned_cases": len(inventory),
            "recorded_cases": sum(e["variant"] == variant for e in entries),
            "world_completed": sum(
                e["oracle"]["world_completed"] for e in entries if e["variant"] == variant
            ),
            "reports": sum(len(e["record"]["reports"]) for e in entries if e["variant"] == variant),
            "completed_claims": sum(
                r["handling"] == "completed"
                for e in entries
                if e["variant"] == variant
                for r in e["record"]["reports"]
            ),
            "false_completion_count": sum(
                e["oracle"]["false_completion_count"] for e in entries if e["variant"] == variant
            ),
            "tool_calls": sum(e["oracle"]["calls"] for e in entries if e["variant"] == variant),
            "mock_model_calls": sum(
                e["record"]["model_calls"] for e in entries if e["variant"] == variant
            ),
            "paid_model_tokens": None,
            "paid_model_cost": None,
        }
        for variant in VARIANTS
    }
    counts = dict(Counter(row["change"] for row in rows))
    eligible = sum(row["change"] not in ("missing", "incomparable") for row in rows)
    return {
        "protocol": PROTOCOL,
        "comparison_kind": "actual_local_mock_component_source_change",
        "planned_pairs": len(inventory),
        "eligible_pairs": eligible,
        "changes": counts,
        "metrics": metrics,
        "rows": rows,
        "regression_gate_passed": (
            eligible == len(inventory)
            and not counts.get("regressed", 0)
            and metrics["candidate"]["handling_passed"] == len(inventory)
            and metrics["candidate"]["false_completion_count"] == 0
        ),
        "limitation": "Trusted local scripted component; no autonomous model, production fix or developer productivity claim.",
    }


def summary(entries: list[dict[str, Any]], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "all": comparison(entries, data["inventory"]),
        "groups": {
            name: comparison([e for e in entries if e["record"]["case_id"] in ids], ids)
            for name, ids in data["groups"].items()
        },
    }


async def run(output: Path, baseline: Path, candidate: Path) -> dict[str, Any]:
    data = dataset()
    if output.exists() and any(output.iterdir()):
        raise ValueError("output must be new or empty")
    output.mkdir(parents=True, exist_ok=True)
    env = environment()
    fixed_sources = sources()
    actors = {}
    for variant, source in zip(VARIANTS, (baseline, candidate), strict=True):
        target = output / "actors" / f"{variant}.py"
        target.parent.mkdir(exist_ok=True)
        target.write_bytes(source.read_bytes())
        actors[variant] = {
            "path": str(target.relative_to(output)),
            "sha256": benchmark.digest(target),
        }
    patch = "".join(
        difflib.unified_diff(
            (output / actors["baseline"]["path"])
            .read_text(encoding="utf-8")
            .splitlines(keepends=True),
            (output / actors["candidate"]["path"])
            .read_text(encoding="utf-8")
            .splitlines(keepends=True),
            fromfile="baseline.py",
            tofile="candidate.py",
        )
    )
    (output / "actor_change.patch").write_text(patch, encoding="utf-8")
    entries = []
    for variant in VARIANTS:
        source = output / actors[variant]["path"]
        try:
            actor_class = load_actor(source)
        except Exception as error:
            # Invalid candidate modules are trial failures, not missing results.
            def failed_factory(*_args: Any, failure: Exception = error) -> Any:
                raise failure

            actor_class = failed_factory
        for case in data["cases"]:
            target = output / "runs" / variant / case["id"]
            record = await benchmark.execute_case(
                case,
                "reference",
                target,
                actor_factory=lambda _name, task, prior, cls=actor_class: cls(
                    "reconcile", task, prior
                ),
                actor_source=source,
                capture_errors=True,
            )
            record["environment_identity"] = env
            inputs, result = benchmark.core_assessment(case, record)
            entry = {
                "variant": variant,
                "record": record,
                "oracle": assess(case, record),
                "evaluation_input": inputs,
                "evaluation": result,
            }
            benchmark.write_json(target / "trial.json", entry)
            entries.append(entry)
    if sources() != fixed_sources:
        raise ValueError("judge or runtime sources changed during execution")
    result = summary(entries, data)
    benchmark.write_json(output / "summary.json", result)
    # importlib may write bytecode; caches are implementation details, not evidence.
    files = {
        str(p.relative_to(output)): benchmark.digest(p)
        for p in output.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    benchmark.write_json(
        output / "manifest.json",
        {
            "protocol": PROTOCOL,
            "inventory": data["inventory"],
            "groups": data["groups"],
            "actors": actors,
            "actor_diff_sha256": benchmark.digest(output / "actor_change.patch"),
            "workspace_head": subprocess.check_output(
                ["git", "-C", str(benchmark.ROOT), "rev-parse", "HEAD"], text=True
            ).strip(),
            "source_provenance": "Actual copied local files and archived diff. Workspace HEAD is context; new uncommitted actor sources are not authenticated by it.",
            "sources": fixed_sources,
            "environment": env,
            "files": files,
            "label_provenance": "Synthetic AI-authored reviewed regressions, not held-out or human-labelled production data",
            "authenticity": "Integrity relative to this manifest only, not source authentication",
        },
    )
    return result


def replay(output: Path) -> dict[str, Any]:
    """Recompute both judgments from raw events and DB, never execute actors."""
    data = dataset()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    if (
        manifest["protocol"] != PROTOCOL
        or manifest["inventory"] != data["inventory"]
        or manifest["groups"] != data["groups"]
    ):
        raise ValueError("archive experiment identity mismatch")
    if manifest["sources"] != sources() or manifest["environment"] != environment():
        raise ValueError("source inventory or execution environment changed")
    found = {
        str(p.relative_to(output))
        for p in output.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    if found != set(manifest["files"]) | {"manifest.json"}:
        raise ValueError("archive file inventory mismatch")
    for name, expected in manifest["files"].items():
        if benchmark.digest(output / name) != expected:
            raise ValueError(f"archive changed: {name}")
    if set(manifest["actors"]) != set(VARIANTS):
        raise ValueError("actor inventory mismatch")
    patch = "".join(
        difflib.unified_diff(
            (output / "actors/baseline.py").read_text(encoding="utf-8").splitlines(keepends=True),
            (output / "actors/candidate.py").read_text(encoding="utf-8").splitlines(keepends=True),
            fromfile="baseline.py",
            tofile="candidate.py",
        )
    )
    if (
        patch != (output / "actor_change.patch").read_text(encoding="utf-8")
        or benchmark.digest(output / "actor_change.patch") != manifest["actor_diff_sha256"]
    ):
        raise ValueError("actor diff mismatch")
    entries = []
    for variant in VARIANTS:
        actor = manifest["actors"][variant]
        if (
            actor["path"] != f"actors/{variant}.py"
            or benchmark.digest(output / actor["path"]) != actor["sha256"]
        ):
            raise ValueError("actor identity mismatch")
        for case in data["cases"]:
            path = output / "runs" / variant / case["id"]
            entry = json.loads((path / "trial.json").read_text(encoding="utf-8"))
            record = entry["record"]
            expected_judge = {
                "oracle_sha256": benchmark.digest(HERE / "goal_eval_recovery_oracle.py"),
                "evaluator_version": benchmark.EVALUATOR_VERSION,
                "evaluator_sha256": benchmark.digest(Path(benchmark.evaluation_core.__file__)),
                "runner_sha256": benchmark.digest(HERE / "goal_eval_recovery.py"),
            }
            if (
                entry["variant"] != variant
                or record["case_id"] != case["id"]
                or record["policy"] != "reference"
                or record["actor_source_sha256"] != actor["sha256"]
                or record["environment_identity"] != manifest["environment"]
                or record["judge_identity"] != expected_judge
                or record["condition_digest"]
                != hashlib.sha256(benchmark.canonical(case).encode()).hexdigest()
            ):
                raise ValueError("trial identity mismatch")
            with sqlite3.connect(f"file:{path / 'world.sqlite'}?mode=ro", uri=True) as conn:
                rows = [
                    dict(
                        zip(("key", "subject", "payload", "origin", "event_seq"), row, strict=True)
                    )
                    for row in conn.execute("SELECT * FROM effects ORDER BY rowid")
                ]
            if rows != record["effects"]:
                raise ValueError("archived world mismatch")
            inputs, evaluation = benchmark.core_assessment(case, record)
            if (
                entry["oracle"] != assess(case, record)
                or benchmark.canonical(entry["evaluation_input"]) != benchmark.canonical(inputs)
                or entry["evaluation"] != evaluation
            ):
                raise ValueError("trial judgments mismatch")
            entries.append(entry)
    if summary(entries, data) != json.loads((output / "summary.json").read_text(encoding="utf-8")):
        raise ValueError("summary replay mismatch")
    return {"mode": "evaluator_rescore", "consistent": True, "trials": len(entries)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("run", "replay"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline-source", type=Path)
    parser.add_argument(
        "--candidate-source", type=Path, default=HERE / "goal_eval_recovery_policy.py"
    )
    args = parser.parse_args()
    if args.mode == "run":
        if args.baseline_source is None:
            parser.error("run requires --baseline-source")
        result = asyncio.run(run(args.output, args.baseline_source, args.candidate_source))
    else:
        result = replay(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
