# EvidenceFlow 0.2 运行时替换与验证

2026-11-24。当前请求链已切换到 `app/runtime/`，替换前代码、测试和历史实验保持封存。本文只报告新运行时的测量，不沿用旧图工作流的在线成绩。

## 替换范围

| 当前实现 | 取代的结构 |
|---|---|
| `RequestCoordinator` 与类型化阶段事件 | 图构建、共享 AgentState、节点调度 |
| 显式路由与 `ExecutionPlan` | 模型意图/复杂度链、旧 PlanStep、技能 JSON 加载 |
| `ReadDispatcher`、新工具基类与目录 | 旧执行节点与工具基础协议 |
| `EvidenceEngine` | 旧生成、忠实度模型复核与回答回退链 |
| 快照读取协调与去重 RRF | 旧混合检索协调与融合实现 |

旧图节点、记忆压缩、查询改写与 Self-RAG 已从当前源码移出。默认依赖不含 LangGraph / LangChain；历史重放需要安装 `legacy-eval`。持久化工单、对话槽位、知识生命周期和业务网关继续使用项目已有实现，第三方适配器继续保留来源，详见 [版权清单](../../THIRD_PARTY_NOTICES.md)。

替换前完整快照为 `eval/baselines/pre_independent_runtime.zip`，182 个文件，摘要为 `caf79c94a49ef9fbc189a272d03e253a7106d2fcdd4a690a1c54445bce4ae360`。旧接口的单元测试随接口退役并保存在该快照；当前测试以 API 行为、业务状态和新协议边界为准，测试数量不能跨版本直接比较质量。

[源码审计](runtime-v2/source-audit.json) 记录 7 个新运行时文件、5 个指定替换文件的前后摘要，以及退役和未改动应用文件。可运行 `python -m eval.audit_runtime --output eval/runs/runtime-audit.json` 复核。该审计说明文件与依赖边界，不证明整个仓库没有第三方代码。

## 当前检查

| 检查 | 结果 |
|---|---|
| 后端完整测试 | 209 passed，`app` + `eval` 覆盖率 76.95% |
| Ruff 检查与格式 | 通过，157 个 Python 文件格式检查 |
| mypy | 115 个源码文件通过 |
| 依赖锁同步 | `uv lock --check` 通过 |
| 前端测试与构建 | 5 个测试通过，TypeScript / Vite 构建通过 |

完整后端输出见 [测试记录](runtime-v2/backend-tests.txt)。新协议测试覆盖提议篡改、读取夹带写操作、越界订单、缺少事实查询、重复读取复用、引用伪造、结构化事实输出、HTTP 预算、整次请求用量与流式取消回滚。独立子进程禁止导入 LangGraph / LangChain 及旧运行时包后，仍能装配容器并形成取消订单提议。

## 当前回归与在线验收

| 测量 | 通过 / 尝试 | 未运行 | 意外业务写入 | 模型 HTTP 尝试 |
|---|---:|---:|---:|---:|
| 100 条业务任务，离线单轮 | 100 / 100 | 0 | 0 | 0 |
| 32 条多轮对话，离线单轮 | 32 / 32 | 0 | 0 | 0 |
| 5 条开发任务，在线单轮 | 5 / 5 | 0 | 0 | 4 |

离线使用冻结任务、合成政策、BM25 + Hash Embedding 和词项精排；多轮对话每轮重建容器并保留同一任务数据库。已有数据曾用于诊断，当前结果属于回归，不能作为未见任务的泛化准确率，也不与历史结果构成新的受控消融。

在线使用 `deepseek-flash`，关闭 thinking，包含取消、退款、退货、缺订单号澄清和退货政策五个任务族。三条操作与一条政策回答各调用一次模型，澄清不调用模型。最终轮上限 16 次 HTTP 尝试，实际 4 次；无模型失败、无结构化输出错误，四次均报告 usage：输入 9,771 token，输出 250 token。未提供单价，不估算费用。

执行轨迹确认前置订单读取先发生，随后才保存提议和人工确认执行。申请提交不表示退款到账。政策样例明确指出质量问题条件缺证，返回 `needs_review=true`；这不是完整语义质量评测，裁判主要检查引用身份和数据库状态。

## 轨迹审阅发现与补修

首次在线小批量也是 5/5，但轨迹审阅发现：模型在提议中附带前置读取时，提议分支跳过了这些读取。原有工单服务仍独立核对订单，所以任务裁判没有暴露该遗漏。随后调整为先执行计划中的读取，任一前置读取失败都不生成提议，并增加成功/失败两个集成场景。

首次结果保存在 [补修前在线材料](runtime-v2/pre-read-fix-online/summary.json)，最终结果在 [新版在线材料](runtime-v2/online-acceptance/summary.json)，源码摘要分别记录，未覆盖。两次共 8 次模型 HTTP 请求，低于本次累计 20 次上限。补修前在线成绩不用于代表最终实现。

## 复现与源码

```powershell
python -m pytest -q --cov=app --cov=eval --cov-fail-under=70
python -m eval.service_benchmark --mode offline --split all --output eval/runs/v2-service-new
python -m eval.p0_runner --dataset eval/dataset/p0_sealed.jsonl --output eval/runs/v2-dialogue-new --variant full --mode offline --max-calls 40 --repeat 0
python -m eval.service_benchmark --mode online --split dev --limit 5 --max-calls 20 --output eval/runs/v2-online-new
```

输出目录必须不存在。在线命令会产生新的 API 消费，不属于上述已结束实验的累计预算。

- 当前 `app` + `eval` 源码摘要：`5c197139fb6fa4dabc082e17107a5ef1c119837ab0de8d07c497386103be789a`。
- 多轮运行器的应用源码摘要：`20d2247e30adb5c7d60f0a75769a910c57b4ae748c0081f2d1cc9abe9cf1b4a1`。
- [业务回归](runtime-v2/service-regression/summary.json)、[多轮回归](runtime-v2/dialogue-regression/summary.json)、[最终在线验收](runtime-v2/online-acceptance/summary.json) 同目录保存逐任务轨迹；公开副本只移除合成文档来源中的本机路径，未改变响应、业务状态或判分。

冻结的任务、裁判、原始 P0 快照与历史结果没有变动。当前仍为演示身份、本地订单库和单进程知识发布，不支持真实支付执行，也不声称生产可用性或未见表达的覆盖率。
