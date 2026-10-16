# 需求说明：主模型需求符合性核对（合并前最后一道门）

> 状态：已实现（2026-10-16，本仓库内；sync.sh 分发待人确认）· 提出：2026-10-16 · 仓库：ch-agent-dev-skills（skill 规则仓）

## 1. 背景

实际使用中发现：用次级模型（`dispatch.model.default` 档）开发代码，自评审（`/ai-review`，同档模型）全部通过、合入之后，仍出现**与原始需求有未实现项、或行为语义有偏差**的情况。现有闭环对代码质量类问题覆盖得不错，但拦不住这类问题，原因是三个结构性盲区：

1. **所有门都是 diff 驱动的，而「未实现」不在 diff 里**。ai-review 审的是 `merge-base...HEAD` 写了什么；整条 DoD 没做、行为链缺一环，在 diff 中完全不可见。只有「diff → spec」方向（看写的对不对），缺「spec → diff」方向（看要的有没有写）。
2. **所有门都是命令退出码驱动的，而「偏差」不在退出码里**。DoD 命令是拆票时写的，exit 0 只证明命令跑过，不证明行为语义是需求要的语义。次级模型把 DoD 写窄、实现走捷径（stub 掉主路径、只覆盖 happy path），主会话验收复跑重跑的是同一批窄命令，照样全绿。
3. **评审与实现同档，存在同源盲区**。评审对话取与实现同档的模型清单——「严格性靠规则组不靠模型档位」对 correctness / 红线类问题大体成立，但「实现的行为是不是需求要的行为」是语义判断，同档模型会犯与实现相同的需求误读，看不出偏差。

## 2. 目标

1. 合并前拦截「需求未实现 / 语义偏差」两类缺口，不再依赖人事后发现
2. 不显著抬高 token 成本：只把需求语义轴升到主模型，代码质量轴（ai-review）仍用同档模型
3. 保持既有不变式：成功只认落盘证据；写查分离（核对只判不改）；打回走既有 BLOCKER 回修通道，不新造流程

非目标：替代 ai-review（代码质量评审原样保留）；改变测试策略本身；把 `non-gating`（性能 / 展示）纳入 AI 门（仍归人工确认）。

## 3. 需求

### R1 需求覆盖矩阵（实现方填，impl-log 新节）

- impl-log 模板新增「需求覆盖矩阵」节：票 DoD 每条 / 「要做什么」每个行为点 → 实现位置（文件:符号）+ 覆盖测试 + evidence 行；关键路径（需求行为的主干实现）标 ★
- 由实现对话在 Step 5 填写。把主模型的工作从「自己找映射」变成「核映射」——更便宜，且每行是可证伪的指针，难造假；填「无对应实现」即自认未实现
- `process_check.py` 机器核对：任一 phase 下该节缺失或为空 → FAIL（与「漂移与升档记录」同一判据级别）

### R2 主会话合并前需求符合性核对（主模型校验）

主会话在监督循环收口步、验收复跑之后、合并之前执行，与 ai-review 正交（评审判「写得好不好」，本步判「要的有没有、偏没偏」）：

- **反向枚举先行**：先不看 impl-log 覆盖矩阵，从票「要做什么 / 不做什么」+ tech-spec 锚点节独立枚举需求点清单——防止被实现方叙事带偏；这一步专门抓「缺席」
- **逐条核映射**：对照覆盖矩阵，抽查映射指向的代码位置（读代码语义，不只看 evidence 退出码；至少覆盖 ★ 关键路径行），逐条判 `已实现 / 语义偏差 / 未实现`
- **集成票加做 feature 级核对**：对 tech-spec 全节（不只票锚点）枚举 feature 级需求点，对照本 feature 各票覆盖矩阵的并集，抓票间接缝缺口（A 票以为 B 票做）——票级核对结构性抓不到的一类
- 结论按模板落盘 `impl/<issue-id>/conformance-<issue-id>.md`（yaml 头：`verdict: conform / deviate / missing`、`commit`、`checker`；正文：需求点清单与判定、偏差详情、抽查记录、集成票的 feature 级节）
- **打回路径**：`deviate / missing` → 不合并，票 `verify-fails` +1（与验收复跑打回共用计数），缺口清单按派发协议派回实现对话修复（等同 BLOCKER 回修路径）；修复后重跑受影响 DoD 命令、主会话聚焦缺口条目与回修 diff 重核；`verify-fails` 达 2 → `needs-human`
- **打回原因分类**（未实现 / 语义偏差 / DoD 本身窄）记入 conformance 文件，供复盘归因（见 R5）
- **执行身份**：默认主会话亲自核（输入有界：票全文 + spec 锚点节 + diff stat + 覆盖矩阵 + 抽查代码，不读全量代码）；主会话上下文紧张时按派发协议开独立对话执行（模型取 `dispatch.model` escalated 首选），主会话只读结论。核对产物全部落盘，上下文只留结论行
- **分档触发**（`conformance.mode`）：`always`（默认）每票必核；`sampled` 时 light 档票抽查（未抽中写 `verdict: skipped` 落盘留痕），scoped / full / 集成票仍一律必核——按实测档位（tier-target）判定，升档票不免核

### R3 机器门禁（process_check.py `--phase close`）

- 新增第三阶段 `close`：= 现有 `--phase merge` 全部核对 + conformance 核对。`/implement` 转 ready 前仍跑 `--phase merge`（此时 conformance 文件尚不存在），主会话收口复核改用 `--phase close`
- conformance 核对项（均可机器判定）：
  - `impl/<issue-id>/conformance-<issue-id>.md` 存在且有 yaml 头
  - `verdict` 为 `conform`；`skipped` 仅在 `conformance.mode = sampled` 且实测档位为 light 时接受
  - 新鲜度：`conform` 的 `commit` 与 HEAD 的本票文件一致（复用 evidence 的 `_same_ticket_tree` 判据；rebase 无冲突且 blob 不变天然通过，可复用不重核）
- `agents-config.md` 新增 `conformance` 节（`mode` / `light-sample` / `checker`），模板与 dev-config fragment 同步；存量配置无此节时按默认 `always` + 主会话亲核执行，即默认启用

### R4 拆票 DoD 覆盖性自查与门②覆盖表（上游预防）

「未实现」的一大来源是拆票时 DoD 写窄写偏——票级核对再严也只是照窄 DoD 核。`/issue-split`：

- Step 1 增加覆盖性自查：每条 DoD 必须映射到 tech-spec 节锚点；标注节的每个行为点至少被一条 DoD（或人工确认项）覆盖，覆盖不上的要么补 DoD、要么显式写入「不做什么」
- Step 2 门②：除票清单外，向人呈现「需求点 → 票 / DoD」覆盖表——人确认的不只是票清单，还有需求覆盖关系；覆盖表只在对话呈现，不新增落盘产物

### R5 数据回流

打回原因三类（未实现 / 语义偏差 / DoD 本身窄）随 conformance 文件落盘，复盘时统计：「DoD 窄」占比高 → 强化门②核对；「语义偏差」高 → 票面行为描述不够具体，或 default 档票也需升级模型。

## 4. 涉及文件

- 新增 `skills/shared/templates/conformance.md`
- `skills/shared/templates/impl-log.md`：「需求覆盖矩阵」节
- `skills/implement/SKILL.md.tmpl`：Step 5 产出覆盖矩阵；版本 0.6.0 → 0.7.0
- `skills/shared/fragments/supervision-loop.md`：收口步插符合性核对、打回步覆盖两类打回、`--phase close`
- `skills/shared/templates/agents-config.md` + `skills/shared/fragments/dev-config.md`：`conformance` 节
- `skills/shared/templates/issue.md`：`verify-fails` 注释扩展为收口打回计数
- `skills/shared/fragments/dev-artifact-paths.md`：impl 目录清单加 conformance 文件
- `skills/setup-dev/SKILL.md.tmpl`：逐项确认清单加 conformance 默认项；版本 0.5.0 → 0.6.0
- `skills/issue-split/SKILL.md.tmpl`：DoD 覆盖性自查 + 门②覆盖表；版本 0.5.0 → 0.6.0
- `skills/shared/scripts/process_check.py`：`--phase close` + 覆盖矩阵核对，测试加用例
- 改完运行 `node --experimental-strip-types skills/scripts/gen-skill-docs.ts`；`bash sync.sh` 分发前先问人

## 5. 验收

- 单元测试：close 相无 conformance 文件 FAIL / conform 且 commit 一致 PASS / `deviate` FAIL / `skipped` 仅 sampled+light 通过 / commit 过期 FAIL；覆盖矩阵缺失 FAIL（review 相同样拦截）
- 全量：`python3 -m unittest discover -s skills/shared/scripts/tests` 全绿；生成后的 SKILL.md 与 tmpl 一致
- 实战回测（建议，非阻塞）：在 pandu 仓挑一张已合并的次级模型票，按新流程重放收口步，确认核对能发现当时的缺口、且 conform 票的主会话核对输入有界

## 6. 待人裁决

- `conformance.mode` 默认 `always` 的 token 成本观察期：建议全量跑 2–4 周收集打回率，再决定是否对 light 票切 `sampled`
- 「DoD 窄」类打回占比的复盘触发线（占比超多少强化门②）由首次复盘数据定
