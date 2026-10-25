# Offline GoalEval benchmark

This suite compares the read-only evaluation core with an actual temporary SQLite
`GoalStore` baseline on twelve fixed scenarios. Expected outcomes are specified
before execution. They are not generated from the evaluator or GoalStore status.

## Run

From the repository root, with the normal development dependencies installed:

```sh
uv run python -m pytest -q tests/test_goal_eval_benchmark.py
uv run python tests/goal_eval_benchmark.py --output ../goal-eval-report
uv run python tests/goal_eval_benchmark.py --replay ../goal-eval-report
```

Choose a new output directory for each run. The report contains the baseline,
expected outcome, observed verdict and decision for each case. Saved inputs and
results support complete replay. The existing CI Python matrix collects the
scenario, replay and fault-injection tests automatically. Generate a persistent
archive with the second command; this PR does not add CI artifact uploads.

## What the fixtures mean

The task is to produce a report with a nonempty title and a nonempty list of
nonempty text items, have the service accept it, and submit successfully at most
once. The submission window must still be open at evaluation time. These are
explicit rules for this fixture, not defaults for arbitrary goals. Structure
checks do not establish factual accuracy or usefulness of the report.

The twelve scenarios cover valid completion, skipped steps, an unsupported
completion claim, stalled preparation, a closed window, denied submission,
revoked acceptance, duplicate submission after interruption, reconciliation,
invalid report structure, stale evidence and a deduplicated trigger. Recovery
and deduplication are represented by fixed histories. No process is interrupted
and no production scheduler is exercised by this offline suite.

Business outcome and available evidence are separate. In the stale-evidence
case the underlying outcome remains true, but the evaluator must return
`unknown`. The unavailable-source case exposes no acceptance evidence to the
evaluator even though the fixture records the underlying service state.
`partial` can include zero outcome progress. Decisions are recommendations;
no goal status or UI is changed and no sensitive action is authorized.

The tests also check that deliberately wrong evaluation behavior is detected.
Always returning `verified`, always returning `unknown`, and copying the
GoalStore status must fail. Replay establishes reproducibility, not correctness
of the expected answer. Local hashes detect changes, not source authenticity.

These scenarios are a regression set selected for known failure mechanisms.
They do not estimate model error rates, production reliability or long-horizon
goal achievement. The suite uses no LLM, external network or personal settings.

<details>
<summary>简体中文</summary>

## 离线 GoalEval 评测

这组测试用十二个固定场景，对照只读评价核心和使用临时 SQLite 的实际 `GoalStore`
的结果。标准答案在运行前设定，不从评价器输出或目标状态生成。

在仓库根目录安装正常开发依赖后，运行上面的三条命令：第一条运行测试，
第二条生成报告，第三条重放全部保存输入。每次运行选择新的输出目录。
报告逐例列出原状态、预期结果、实际判断和策略建议。现有 CI 的 Python
矩阵会自动收集场景、回放和故障自检测试。需要持久归档时运行第二条命令；
本次 PR 不新增 CI 归档上传。

样板目标是生成标题非空、条目为非空文本列表的周报，由服务接受，且最多
成功提交一次；核验时提交窗口仍须开放。这些是本场景明确写入的条件，
不作为任意目标的默认规则。格式合格不代表内容真实或有业务价值。

十二个场景覆盖正常完成、跳过步骤、无证据宣告、持续准备、窗口关闭、
审批拒绝、成果撤销、中断后重复提交、核对后恢复、格式错误、证据过期和
重复事件去重。恢复和去重表现为固定历史；本轮不强制结束进程，也不验证
生产调度器。

成果是否成立和证据是否充分分别判断。证据过期场景的成果仍为真，但评价器
必须返回 `unknown`；来源不可用场景向评价器隐藏接受状态，场景本身仍记录
服务事实。`partial` 允许没有成果进展。策略输出只作为建议，不改变目标状态
或界面，也不授权敏感操作。

测试还检查三种故意错误的行为：全部判成功、全部判未知、照抄旧目标状态，
都必须被测出。回放只证明可重复，不证明标准答案正确；本地摘要只能检查
记录变化，不能认证来源。

这些场景按失败机制选取，用于回归检查，不用于推算模型出错率、生产可靠性
或长期目标成功率。整套离线评测不调用模型、不访问外网、不读取个人设置。

</details>
