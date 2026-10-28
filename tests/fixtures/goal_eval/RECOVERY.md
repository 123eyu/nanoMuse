# Developer recovery benchmark

This extends the twelve-case evidence-rule benchmark merged in #585 with
execution and source-comparison regressions. It runs under the existing default
pytest suite. Production runtime, GoalStore status, UI and workflows are unchanged.

## What this validates

The developer workflow is: reproduce a failure, compare actual component source
snapshots under fixed conditions, inspect changed outcomes and evidence, then
retain reviewed regressions. The current subject is a trusted local scripted
MockLLM recovery component. This is not autonomous model evaluation, a production
nanoMuse recovery fix, a developer productivity study or a multi-file agent adapter.

Two datasets serve distinct purposes:

- `goal-eval.recovery.v1.1`: 25 frozen cases and seven injected policy controls.
  These check whether the independent oracle detects known mistakes.
- `goal-eval.recovery-extension.v1.1`: 18 reviewed regression cases, added after
  identifying defects in the local component. They are not held-out validation.
  Source comparison keeps the original 25 and extension 18 separately visible.

Each trial executes fresh MuseAgent/MockLLM/Sentinel tool calls against an isolated
SQLite action service. The actor receives only the obligation, public service
contract, prior attempt count and tool observations. The host does not pass fault
schedules, gold, oracle functions or database handles to it. Actor modules are
trusted Python code in the same process; this API boundary is not a security sandbox.

## Run locally

From a checkout with the normal development dependencies installed:

```sh
.venv/bin/python tests/goal_eval_recovery.py run --output /tmp/recovery-controls
.venv/bin/python tests/goal_eval_recovery.py replay --output /tmp/recovery-controls
.venv/bin/python -m pytest tests/test_goal_eval_recovery.py -q
```

The default candidate is `tests/goal_eval_recovery_policy.py`.
`--candidate-source` accepts another trusted single-file component with the same
ScriptedPolicy interface. Use a new
output directory. No account, API key, network or personal configuration is needed.
The tests replace socket connection functions in the test process while trials
execute. This is an accidental-egress check, not a subprocess or security sandbox.
The tests live under `tests/` and are collected by the existing pytest and Python
CI matrix; no workflow or dependency changes are needed.

## Contracts and gold

The key denotes an obligation. Only when advertised as native idempotency does
the service guarantee atomic deduplication by that key. An existing key returns its
stored content, including conflicting content, without adding an effect. The actor
must check receipt binding, freshness, state and exact content before claiming success.
Read lookup is permitted after write revocation. Lookup consumes the call budget,
not the write-attempt budget. Both budgets and idempotency are public actor inputs.
Only unavailable/pending reads permit the idempotent fallback; misbound or stale
observations are treated as anomalies. A bound receipt with conflicting content is
reported as conflict. These are explicit synthetic service rules, not assumptions
about arbitrary real services.

Fault and lookup schedules advance globally within a case and repeat their last
item. The clock is fixed at 1000. The world commits SQLite effects independently
of receipts, so a timeout can conceal either success or no effect. Separate observer
availability controls evidence supplied to the evaluation core.

Gold is AI-authored and cross-reviewed, not human-labelled production data. The
standalone stdlib oracle imports no actor, runner, service or evaluator. It checks
exact final effects, authorization independently derived from event order, sequential
trace integrity, budgets, fixed handling expectations and cited observations.
Some extension cases also pin write counts for this specific component regression.
These procedural expectations do not grade every possible correct agent strategy.

## Source change and evidence

The baseline fixture preserves the pre-edit component, with only a trailing blank
line normalized. The candidate fixes two defects: unnecessary abstention when
readback fails despite guaranteed native idempotency, and acceptance of a bound
receipt without checking its content. The service's key-atomic behavior is fixed
for both variants before comparison. It is part of the environment, not a change
made only for the candidate.

Each comparison copies and loads both source files, stores their hashes and an
actual `actor_change.patch`, and pins the shared runtime, runner, oracle, evaluator,
fixtures, Python and dependency identity. Workspace HEAD is context, not proof that
new uncommitted component files exist in that commit. Runtime or judge changes are
incomparable in this adapter; the controlled variable is the single actor module.
A broader across-runtime comparison requires a different adapter.

Start with `summary.json`, then `runs/VARIANT/CASE/trial.json`. Reports distinguish:

- Handling correctness under the full independent oracle.
- Hidden world completion and the evaluation core's separate outcome judgment.
- Unsupported completion claims, tool calls and MockLLM call counts.
- Improved, regressed, unchanged, missing and incomparable case pairs.

The core evaluates goal outcome, not whether an actor handled uncertainty properly.
A correct unresolved result can pass the oracle while the goal remains incomplete;
a separate observer can verify a real effect the actor could not establish. A
conflicting receipt can be rejected even when an actual matching effect exists.
The two judgments need not have the same label and are not interchangeable.

Missing pairs stay in the planned denominator. Mixed actor sources within one
variant are rejected. The regression gate requires every pair comparable, every
candidate case passing and no false completion. Passing pairs with changed costs
remain `unchanged`; inspect call deltas separately. Paid-model token/cost values
are unavailable, not measured zero. Elapsed times in trials are local measurements,
not production latency. Crashes and malformed terminal output remain failed trials
with actual partial effects and errors instead of disappearing from the denominator.

## Reproduction limits

Replay rederives oracle results, core inputs/results, database effects and summary
from archived records under the same current judge/runtime identity. Archives
from the former experiment paths have different source identities and must be
regenerated after migration. Replay does not
load or execute actors. Fresh repeat execution and judgment replay are separate
checks. Hashes detect changes relative to a manifest; they do not authenticate the
source or protect against a consistent forger rewriting the whole archive.

The experiment does not validate simultaneous triggers, process termination,
providers, multi-file source changes, mobile/desktop runtime, long-term operation
or developer productivity. Hidden chains of thought are not graded. The next
breadth validation needs separately reviewed business cases and a frozen holdout;
more variants of one recovery defect do not establish coverage of every official
use case. App display and runtime intervention remain later, separately reviewed work.

## 中文阅读说明

先看哪些场景改善、哪些退化，再打开对应的动作、实际成果和证据引用。
43 个源码对照场景分为原 25 个和新增 18 个回归，不能与 #585 的 12 场景合并算成功率。
恢复测试已放到 tests/ 下，由现有 pytest 与 Python CI 自动收集，不改工作流或依赖。
本版能验证本地恢复组件的一次代码修复，并检出反向退化；真实模型表现和开发
效率仍未测量。源码、标准答案、原始记录和中英文发布材料须在发布前审核。
