# ARCH-CHECK 规则与检查（v1）

> 配套脚本：`shared/scripts/arch_check.py`；严格 schema：`shared/references/arch-check.schema.json`。
> 原则：**只报告、不改业务代码**；唯一写入是 `init` 创建规则 / 指南、`render write` 生成 `docs/ARCHITECT.md` 和 `install-hook` 写入 `.git/hooks/pre-push`。

## 模型

- `docs/ARCH-CHECK.json` 是唯一机器规范：分层、组、依赖方向、禁用调用、参数 / 类行数、注释坏味道、重复度。
- `docs/ARCHITECT.md` 是规则的确定性渲染：给人和 agent 读的分层 / 放置指南，禁止手改；`render check` 做字节比对。
- `docs/GLOSSARY.md` 继续登记 feature-module 归属；不再有 `docs/architecture/modules.json`。
- 旧 `docs/architecture/*.json` 与 `docs/ARCHITECTURE.md` 不再读取；`arch_check.py` 只输出 INFO 提示删除。

## 规则文件

```json
{
  "schema": "agent-dev.arch-check/v1",
  "version": 1,
  "language": "python",
  "source_roots": ["."],
  "architecture": {
    "title": "项目架构",
    "summary": "上层可调用下层，下层不得反向依赖上层。",
    "layers": [
      {"name": "presentation", "description": "HTTP/UI", "may_depend_on": ["domain"]},
      {"name": "domain", "description": "业务", "may_depend_on": ["data"]},
      {"name": "data", "description": "存储", "may_depend_on": []}
    ],
    "placement": ["新增 controller 放 app/controllers/。"]
  },
  "groups": [
    {"id": "controller", "paths": ["app/controllers/**"], "layer": "presentation", "description": "HTTP handlers"},
    {"id": "service", "paths": ["app/services/**"], "layer": "domain", "description": "业务逻辑"},
    {"id": "model", "paths": ["app/model/**"], "layer": "data", "description": "存储模型"}
  ],
  "rules": {
    "forbidden": [
      {"id": "model-no-upward", "severity": "error",
       "from": ["model"], "to": ["controller", "service"],
       "comment": "Step 1: find the upward imports in app/model.\nStep 2: move shared types down or invert the dependency."}
    ],
    "cycles": [
      {"id": "no-cycles", "severity": "error", "groups": ["controller", "service", "model"],
       "comment": "Step 1: trace the A->B and B->A edges.\nStep 2: sink shared types into a lower layer."}
    ],
    "banned_calls": [
      {"id": "no-shell", "severity": "warn", "from": ["service"], "calls": ["subprocess.*", "os.system"],
       "comment": "Step 1: find the raw call.\nStep 2: use the shared runner."}
    ],
    "methods": [
      {"id": "max-params", "severity": "error", "max_params": 5, "exclude": ["tests/**"],
       "count_varargs": false,
       "comment": "Step 1: find the method.\nStep 2: collapse params into a request object."}
    ],
    "types": [
      {"id": "max-class-lines", "severity": "warn", "max_lines": 900, "exclude": ["tests/**"],
       "comment": "Step 1: find the class.\nStep 2: split by responsibility."}
    ],
    "comments": [
      {"id": "no-junk-comments", "severity": "warn",
       "kinds": ["commented_code", "placeholder", "long_line", "long_docstring"],
       "max_lines": 2, "max_docstring_lines": 6, "exclude": [], "allow": [],
       "comment": "Step 1: delete or restore the comment.\nStep 2: keep only WHY."}
    ],
    "duplication": [
      {"id": "max-duplication", "severity": "warn", "max_rate": 10, "min_lines": 10, "exclude": ["tests/**"],
       "comment": "Step 1: locate the duplicate block.\nStep 2: extract a shared function."}
    ]
  }
}
```

规则要求：

- 每条规则必须有唯一 `id`、`severity`（`error` / `warn`）和含 `Step 1` / `Step 2` 的 `comment`。
- `groups` 的 `layer` 必须在 `architecture.layers` 中存在；`from` / `to` / `cycles.groups` 只能引用已登记组。
- `paths` / `exclude` 是仓库相对 glob；禁止绝对路径、`..`、反斜杠。
- `source_roots` 是 Python import 的根；`["."]` 表示仓库根，`["src"]` 表示从 `src/` 开始解析。
- 规则变更必须进 tech-spec 3.2 和承接票 `declared-scope`；放宽或删除规则要说明理由。

## 检查项

| 类别 | 判定 | 说明 |
| --- | --- | --- |
| `forbidden` | 组 A 的文件 import 组 B 的文件 | 解析 `ast.Import` / `ast.ImportFrom`，只判仓库内部模块；未解析 import 记 INFO |
| `cycles` | 组级依赖图有环 | 输出环路径与示例 `file:line`；每个环只报一次 |
| `banned_calls` | `ast.Call` 的限定名匹配 `calls` 通配 | 支持 `import x as y`、`from x import y as z` 别名 |
| `methods` | 函数参数个数 > `max_params` | 排除 `self` / `cls`；`count_varargs` 控制是否计 `*args/**kwargs` |
| `types` | 类行数 > `max_lines` | 按 AST 的 `end_lineno - lineno + 1` |
| `comments` | 注释掉的代码、TODO / 空占位、连续长注释、超长 docstring、复述代码 | `commented_code` / `placeholder` / `long_line` / `long_docstring` 较确定；`restating` 是启发式，默认 warn |
| `duplication` | 归一化行滑窗重复率 > `max_rate` | 默认窗口 `min_lines` 行；启发式，建议先 warn |

## 门禁与豁免

- `arch_check.py check`：0 无 error，1 有 error（`--strict` 时 warn 也算），2 规则 / 输入错误。
- feature `freeze / check` 从 candidate commit 读取规则并运行；`render check` 比对 `docs/ARCHITECT.md` 字节；规则或指南哈希写入 `candidate.json`，冻结后变更即 FAIL。
- 本地 pre-push hook 由 `install-hook` 安装，检查 `HEAD`；无规则文件时跳过。可用 `--no-verify` 绕过，但 candidate 门仍会拦截。
- 误报处理：优先加 `exclude` / `allow` 或把规则降为 `warn`；不要删除规则来绕过。规则变更走 tech-spec / 票 scope / ai-review。
- 非 Python 文件不检查；项目没有 Python 时 `check` 输出 INFO。当前版本不提供其他语言适配器。
