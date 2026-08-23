# Evidence Agent Core｜可验证的 Agent 学习治理

这是一个本地优先的 Agent 长期学习变更控制层。

它解决的不是“怎样让 AI 记住更多”，而是：一条会影响未来行为的规则，凭什么进入长期记忆？

```text
Session -> 捕获 -> 审核 -> 证据 -> 候选学习 -> 评估 -> 人工批准 -> 晋升
```

Agent 可以记录和提出候选规则，但不能悄悄批准自己的学习。

## 能做什么

- 保存 Session 原始记录及 SHA-256 来源指纹；
- 每次审核必须选择 `no-delta` 或关联一个候选学习；
- 用 JSON Evidence 和确定性 Eval 验证关键主张；
- 只有明确写出批准人，候选规则才能晋升；
- 用只追加账本保留长期学习历史；
- 为 `AGENTS.md` 和 `CLAUDE.md` 编译 Runtime Adapter；
- 安装 Adapter 时保留项目原有内容；
- 默认忽略原始对话、审核、证据和候选规则，防止误提交。

它不是向量数据库、RAG 框架、自动改写人格的系统，也不会上传本地对话或调用外部模型。

## 快速开始

需要 Python 3.11 或更高版本。

```bash
python -m pip install -e .
mkdir demo-workspace
evidence-agent-core --root demo-workspace init
evidence-agent-core --root demo-workspace status
```

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

`0.1.0` 是参考实现，接口可能在 `1.0` 前调整。不应把它作为法律、审计或关键业务记录的唯一存档。

## 许可证

MIT
