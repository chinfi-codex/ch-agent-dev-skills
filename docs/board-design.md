# 跨项目 / 跨 agent 需求看板 — 设计方案

> 基于 [Backlog.md](https://github.com/MrLesk/Backlog.md) 方法论，把本仓 13 个 skill 流水线的产物状态投影成一张统一看板。
> 原型已落地：`skills/board/`（`/board` skill + 零依赖 CLI `board.py`），演示产物在 `docs/board-demo/`。

## 1. Backlog.md 方法论要点（借鉴对象)

| 方法论 | Backlog.md 做法 | 本看板吸收方式 |
|---|---|---|
| Markdown 真源 | 每任务一个 `.md` 文件 + YAML frontmatter，git 随代码走 | 不新建任务格式：直接以现有 docs/dev 两树产物为真源 |
| 可配置状态机 | `config.yml` 里 `statuses` 列表即看板列 | 统一阶段模型 + 单一映射表（§3），列调整只改一处 |
| 多视图同源 | `board`（终端）/ `board export`（md）/ `browser`（Web）/ `--json` | `board.py` 四视图同源输出（§6） |
| 三检查点 agent 流 | spec → plan → code，一任务一上下文一 PR | 复用本仓门② / 派发 / 评审流水线，看板只做投影不加新门 |
| 依赖 DAG | `dependencies` 字段 + frontier 计算 | 复用票 `blocked-by`，看板标 frontier / 集成票 |
| 机器可读 | 稳定 `--json` 供脚本与其他 agent 消费 | `board.py json` |

**关键差异**：Backlog.md 的 CLI/Web 是读写一体（拖拽改状态）；本看板 v1 **只读**——写路径唯一（各 skill 自己改产物），看板不做第二写口，避免双写漂移。

## 2. 现状与缺口

本仓流水线已有完整状态机，天然是 Backlog.md 式真源：

- **需求阶段**（`./docs/features/<module>/<slug>/`）：feature-brief（状态结论）→ PRD（`status: 草稿|定稿`）→ pd-review（`verdict`）
- **票阶段**（`./dev/features/<module>/<slug>/`）：tech-spec（`草稿|已确认（待拆解）`）→ issue 票（`proposed/confirmed/in-progress/in-review/mr-submitted/needs-human/done` + `blocked-by` / `verify-tier` / `verify-fails`）

三个缺口：

1. **无统一列模型**：需求文档状态与票状态是两套词汇，无法同板呈现「一个需求走到哪了」
2. **无跨 agent 字段**：票被派发给哪个宿主 / 模型（zcode·glm / codex·gpt / claude·opus）只在派发对话里，票文件上没有，跨 agent 汇总无从读起
3. **无跨项目视图**：每个仓库各自闭环，多项目并行时要逐仓翻目录

## 3. 统一阶段模型与双看板

8 个阶段覆盖从需求到合并全链路，**分两块看板展示**：

- **需求看板**（docs 树文档卡）：`brief 需求澄清 → prd PRD/评审 → spec 技术方案 → ready 待拆票`
- **开发看板**（dev 树票卡）：`ready 待执行 → dev 实现中 → review 评审/合并 → blocked 阻塞 → done 已完成`

同一张 `ready` 列在两板语义不同：需求看板里它是「spec 已确认待拆票」的文档卡，开发看板里它是「待派发」的票卡——由卡的 `board` 字段区分，JSON 消费者可直接过滤。已完成票默认不展示（终端只显示计数、`--show-done` 展开；Web 默认隐藏、勾选显示），对齐 Backlog.md「complete 后离板」的语义。开发看板层级 = **看板（固定阶段列）→ 泳道（执行 agent，`⚙host·model`，跨项目聚合，未派发垫底）→ feature（泳道格内分组，带票进度 `x/y`）**；未写 `dispatch` 块的票归入「未派发」泳道。

**票级映射**（`board.py` 中 `ISSUE_STAGE`，单一映射点）：

| 票 status | 列 | 附加徽章 |
|---|---|---|
| proposed | ready | 门②待确认 |
| confirmed | ready | frontier（blocked-by 全 done）→「可派发」；否则显示 blocked-by |
| in-progress | dev | 宿主·模型、DoD 进度 |
| in-review | review | 评审轮次 |
| mr-submitted | review | 待合并 |
| needs-human | blocked | 需人工裁决 |
| done | done | — |

**需求级映射**（尚未拆票的 feature 出文档卡；已拆票的 feature 只显示聚合进度 `x/y done`，不重复计卡）：

| 产物状态 | 列 |
|---|---|
| 仅有 feature-brief | brief（带状态结论徽章） |
| PRD 草稿；或 pd-review `verdict: 阻塞` | prd |
| PRD 定稿 / 小变更单，尚无 tech-spec | spec（徽章「待出 tech-spec」） |
| tech-spec 草稿 | spec |
| tech-spec 已确认（待拆解） | ready（徽章「待拆票」） |

**存量数据兼容**（pandu 318 票实测适配）：

- 键名变体：`issue-id` 缺失时回退 `id`，再回退文件名；`title` 缺失取文件名短名
- 扩展状态词：`implementing`→dev、`review-pending`→review、`analysis`→ready、`待技术共识确认`→blocked；`superseded`/`cancelled`/`deferred` 视为关闭（入 done 列、徽章显示原状态，对齐 Backlog.md archive 语义）；未识别状态落入 ready 并出「未识别状态」告警（词汇卫生信号）
- 告警只报活跃票：done 票的复跑失败 / 评审轮次 / 停滞是历史，不进告警；评审轮次告警限 dev/review 列（proposed 票命中同 ID 旧评审报告不算）

## 4. 跨 agent 契约扩展（唯一写侧改动）

票 frontmatter 增加可选 `dispatch` 块，**派发时由主会话写入**：

```yaml
dispatch:
  host: zcode              # zcode | claude | codex | workbuddy | human
  model: glm-4.7           # 实际执行模型（取 dispatch.model.<host> 清单实中项）
  dispatched-at: '2026-10-14 09:30'
  attempts: 1              # 打回重派 +1
```

- **写入点**：派发协议 fragment「派发」一步写初值；`/implement` 打回重派时更新；合并后置 `done` 不动此块（留痕谁做的）
- **兼容**：缺省 = 未派发 / 历史票，看板显示「未派发」，不强制回填
- 配套：feature-brief 模板补 frontmatter `status`（与 2026-10 spec frontmatter 机器门禁同一思路），brief 阶段才有机器可读状态

## 5. 多项目聚合

**注册表**（查找顺序：`--registry` → `./board.projects.yml` → `~/.config/ch-agent-board/projects.yml`）：

```yaml
projects:
  - name: pandu
    path: /abs/path/to/pandu
  - name: nova
    path: /abs/path/to/nova
```

**聚合语义**：

- **项目 × 阶段矩阵**：一眼看各项目卡片分布
- **宿主在制 WIP**：跨项目按 `宿主·模型` 汇总在制 / 已完成，识别单点过载
- **feature 聚合行**：已拆票需求显示 `票进度 x/y done`
- **告警**（全项目汇总置顶）：
  | 告警 | 判据 | 数据来 |
  |---|---|---|
  | 需人工裁决 | status=needs-human | 票 frontmatter |
  | 验收复跑失败×n | verify-fails ≥ 1（≥2 应已转 needs-human） | 票 frontmatter |
  | 评审第N轮 | reviews/ 同票报告数 ≥ `review-round-alert` | dev/agents-config + reviews/ |
  | 停滞N天 | dev/review 列且 dispatched-at 距今 ≥ 阈值（默认 3 天） | dispatch 块 |
  | 集成票档位非 full | DAG 无后继但 verify-tier ≠ full（模块隔离认定除外） | 票 frontmatter 推导 |

## 6. 视图层

| 命令 | 对标 Backlog.md | 说明 |
|---|---|---|
| `board.py board [--show-done]` | `backlog board` | 终端双看板：需求看板卡表 + 开发看板（矩阵 + 宿主 WIP + 告警 + 卡表；已完成默认只显示计数） |
| `board.py export -o x.md` | `backlog board export` | Markdown 报告，分需求/开发两节，可分享 / 归档 |
| `board.py html -o x.html` | `backlog browser` | 单文件 Web 双看板：需求/开发分区、项目/宿主/文本筛选、「显示已完成」开关（v1 只读无拖拽） |
| `board.py json` | `--json` | 稳定 JSON，卡带 `board: requirement|dev` 字段 |
| `board.py demo DIR` | — | 生成演示工作区 |

过滤：`--project` / `--host` / `--stale-days`。

## 7. 原型清单

- `skills/board/board.py` — 零依赖 CLI（Python 3.9+ stdlib），扫描 + 四视图
- `skills/board/SKILL.md.tmpl` — `/board` skill 包装（只读契约、注册表初始化、告警解读指引），已接入 `sync.sh` 分发四宿主
- `docs/board-demo/workspace/` — 演示数据：pandu（执行中，3 宿主并行）+ nova（澄清/阻塞/待合并）
- `docs/board-demo/terminal.txt` / `board-report.md` / `board.html` — 三种视图实拍

试跑：

```bash
python3 skills/board/board.py board --registry docs/board-demo/workspace/board.projects.yml
```

## 8. 落地路线

- **v1（本次）**：只读投影 + 四视图 + 演示；`dispatch` 块为可选字段，历史票不受影响
- **v2（契约补丁）**：`shared/templates/issue.md` 加 `dispatch` 块注释；`dispatch-protocol.md` 派发一步写初值；`implement` 打回重派更新 `attempts`；`feature-brief.md` 补 frontmatter `status`；改后跑 `sync.sh`
- **v3（交互增强）**：HTML 拖拽回写 frontmatter（仍需过各 skill 门禁）、`--watch` 刷新、MCP 输出（对齐 Backlog.md MCP）、与 GitLab tracker 状态对账
