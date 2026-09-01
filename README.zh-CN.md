# Evidence Agent Core｜可验证的 Agent 学习治理

这是一个本地优先的 Agent 全局协调与长期学习变更控制层。

`0.4` 在 `0.3` 的单 Agent / 多 Agent 全局协议上，增加了确定性的 shadow
评估门。所有请求先进入同一种 `Work` 工作信封，系统再选择最小且有效的执行
路径，不再让单 Agent 流程和多 Agent 流程各自保留一套“新旧样式”；是否晋升
为强制模式仍是独立的人类决定。

它解决的不是“怎样让 AI 记住更多”，而是：一条会影响未来行为的规则，凭什么进入长期记忆？

```text
Session -> 捕获 -> 审核 -> 证据 -> 候选学习 -> 评估 -> 人工批准 -> 晋升
```

Agent 可以记录和提出候选规则，但不能悄悄批准自己的学习。

运行中的协作使用另一条独立链路：

```text
Work -> 路由 -> Agents -> 租约任务 -> 不可变成果 -> 决策 -> 现实结果
```

两条链不会混在一起：Agent 可以自主发布日常工作成果，但任何会影响未来
行为的长期规则，仍然必须经过证据、评估和明确的人类批准。

## 能做什么

- 保存 Session 原始记录及 SHA-256 来源指纹；
- 提供全局 `shadow`、`enforced`、`rollback` 三种协调模式；
- 注册可发现的 Agent 能力，并用依赖任务和租约防止重复工作；
- 发布不可变的证据、假设、实现、复核与结果成果；
- 按 Work 返回局部上下文，不把全部长期记忆塞进运行时；
- 用正向样本、负对照、延迟、重复劳动、Work 碎片、显式冲突和成果交接检查
  组成确定性的 shadow 评估；
- 每次审核必须选择 `no-delta` 或关联一个候选学习；
- 用 JSON Evidence 和确定性 Eval 验证关键主张；
- 只有明确写出批准人，候选规则才能晋升；
- 用只追加账本保留长期学习历史；
- 为 `AGENTS.md` 和 `CLAUDE.md` 编译 Runtime Adapter；
- 安装 Adapter 时保留项目原有内容；
- 默认忽略原始对话、审核、证据和候选规则，防止误提交。

它不是 Agent harness、向量数据库、RAG 框架或自动改写人格的系统，也不会
上传本地对话或调用外部模型。Codex 等 harness 继续负责线程、工具、沙箱和
审批；本项目负责可移植的工作状态与证据门控的长期继承。

## 快速开始

需要 Python 3.11 或更高版本。

```bash
python -m pip install -e .
mkdir demo-workspace
evidence-agent-core --root demo-workspace init
evidence-agent-core --root demo-workspace status
```

协调层默认处于 `shadow` 模式，只记录全局工作信封和路由建议，不改变真实
执行路径：

```bash
evidence-agent-core --root demo-workspace coord status
evidence-agent-core --root demo-workspace coord mode enforced \
  --changed-by human-owner \
  --note "开始受控的全局协调试点。"
```

完整操作链见 [`docs/COORDINATION.md`](docs/COORDINATION.md)。

### 全局接入本地 Codex

Codex Hooks 是用户级生命周期入口。安装器会把隔离运行时放到
`~/.codex/evidence-agent-core/`，把六类托管事件合并进
`~/.codex/hooks.json`，并以全局 `shadow` 模式启动：

```bash
PYTHONPATH=src /opt/homebrew/bin/python3.11 \
  -m evidence_agent_core.cli codex-install \
  --source . \
  --python /opt/homebrew/bin/python3.11

~/.codex/evidence-agent-core/bin/evidence-agent-core codex-doctor
```

安装后需要由人类在 Codex 中打开一次 `/hooks`，检查并信任所有实际发现的
用户级 Hook。安装器不会替自己越过这个安全检查。

适配器要求 `SessionStart`、`UserPromptSubmit`、`SubagentStart`、
`SubagentStop` 和 `Stop` 五类事件，并为支持该事件的 Codex 版本额外配置
`SessionEnd`。Codex 0.137 会发现前五类事件并忽略可选的第六类。提示词正文
不会持久化，Work 只保存 SHA-256 指纹和字符数；Agent 输出则以有限长度的
私有摘要保存，使后续 Agent 能够复用或质疑前序成果。即使 Codex 给每个子
Agent 分配不同的 `turn_id`，适配器也会按 Session 把这些子回合绑定到当前
根 Work，确保一次请求始终只有一个协调信封。

安装、审计与回滚方法见 [`docs/CODEX_HOOKS.md`](docs/CODEX_HOOKS.md)。

对一组明确列出的不可变 Work 运行 shadow 评估，不改变全局模式：

```bash
~/.codex/evidence-agent-core/bin/evidence-agent-core \
  codex-shadow-eval --spec ./evaluations/shadow-suite.json
```

评估只会返回 `remain_shadow` 或 `eligible_for_human_review`，通过也不会自动切换
到 `enforced`。完整格式和默认门槛见
[`docs/SHADOW_EVALUATION.md`](docs/SHADOW_EVALUATION.md)。

捕获一次 Session：

```bash
evidence-agent-core --root demo-workspace capture \
  --session-id session-001 \
  --transcript ./path/to/session.jsonl \
  --runtime local-agent \
  --event session-end \
  --auto-review
```

如果本次没有值得跨 Session 继承的变化：

```bash
evidence-agent-core --root demo-workspace review session-001 \
  --decision no-delta \
  --note "没有经得起审核的长期变化。"
```

完整晋升流程见 [`examples/synthetic-demo`](examples/synthetic-demo) 和
[`docs/WORKFLOW.md`](docs/WORKFLOW.md)。

## 隐私边界

初始化时会生成默认拒绝式 `.gitignore`。只有配置文件与人工维护的 Core
文件允许进入版本控制；Session、Evidence、Review、Proposal、Eval、Ledger
和编译产物默认都不提交。

这是一道防误操作保护，不等于加密。公开仓库推送前仍应检查暂存文件。
详细说明见 [`docs/PRIVACY.md`](docs/PRIVACY.md)。

## 当前状态

`0.4.0` 是参考实现，接口可能在 `1.0` 前调整。不应把它作为法律、审计或关键业务记录的唯一存档。

## 许可证

MIT
