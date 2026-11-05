## 术语与数据口径（GLOSSARY，dev 阶段）

项目级唯一术语文件：`./docs/GLOSSARY.md`（存在才读，不存在不阻塞）。

- 输出（票状态、impl-log、evidence、评审报告、对话）必须使用表内**标准术语**；引用任何指标必须带口径（定义 / 统计窗口 / 数据来源），且与 GLOSSARY 一致
- 实现与评审中**不新建、不回写** GLOSSARY（worktree 分支回写会与主干漂移）；发现术语冲突、缺口或别名误用，记入 impl-log「遗留」或评审报告「评审员意见」，由文档阶段与复盘统一处理
- 架构事实唯一写入源是 `docs/architecture/*.json`。票 DoD 声明契约更新时，承接票在 worktree 分支内按稳定 ID 修改接口分片，随代码同一 MR 合入；flow-back 同样补记对应分片并在 impl-log「红线接触记录」登记「接口 → 代码位置」映射。`docs/ARCHITECTURE.md` 是确定性生成的只读短首页，所有开发阶段均不得手改。
