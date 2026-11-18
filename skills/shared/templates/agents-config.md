# agents-config

> 由 `/setup-dev` 产出；人可直接修订本文件。dev 阶段各 skill 的唯一配置真源。
> v2 硬切：配置由 `agents_config.py` 按 `shared/references/agents-config.schema.json` 严格校验。未知字段、旧 scoped/full 档位、旧 redlines、main-drift-ignore 和手写证据配置一律拒绝。
> 默认 `server-enforced` 只有在线证明分支保护、必跑检查与 expected-SHA 合并能力后才能放行；需要协作式 CI 或本地人工模式时必须显式改配置，不自动降级。

```yaml
schema-version: 2

# issue 发布面：gitlab = glab CLI；local = dev/features/**/issues/ 本地票文件
tracker: gitlab
main-branch: main
repo-level: B                 # A = 飞行软件 / 涉密，只允许只读辅助；B = 完整流程；C = 内部效率工具

# 票级唯一质量门。实现、回修和主会话复跑均由 verify_issue.py 实际执行，不接受手写 all-passed。
quality-gate:
  fast: "pytest -q <相关测试文件>"

# 本机需求级关联选择集预算；票级 fast 不经槽位包装。真源仍是机器级 slots budget，此处为镜像。
host-budget:
  total: 8
  max-runs: 2
  queue-timeout: 1200

verify-policy:
  # 只约束拆票粒度，不用于选择测试范围或模型。
  ticket-max-files: 10
  ticket-max-loc: 400

  # 绝对禁止路径：新增、修改、删除、重命名任一命中即 FAIL，登记不能解除。
  deny:
    - "protocols/**"
    - "**/secrets*"
    - "**/*.key"
    - ".env*"

  # TDD 新增测试允许，但必须有 runner 见红记录；既有测试更新须映射 DoD / spec 并由独立评审闭合。
  test-change:
    - "tests/**"
    - "**/tests/**"
    - "test/**"
    - "**/test/**"
    - "**/test_*"
    - "**/*_test.*"
    - "**/*.test.*"
    - "**/*.spec.*"

  # 每条派生物只有唯一生成器；process_check 在独立临时工作区重跑并比较 tree，不接受文字声明。
  # 示例：{id: architect-guide, glob: "docs/ARCHITECT.md", generator: "python3 ../shared/scripts/arch_check.py render write --repo ."}
  generated-refresh: []

  # 所有 changed paths 都核 declared-scope；越界必须重新确认票上下文，登记漂移不再放行。
  scope: strict
  base-freeze: true

  # 先在失败处理：清单内 quarantine 显式 skip；清单外只对失败单项做 base 对照；未执行不算通过。
  attribution: quarantine
  quarantine-max-age-days: 14
  gate-scope:
    categories-file: null
    non-gating: [perf, display]
    env-skip: true

  # 可选：只能由同一 CI workflow 的 schedule / dispatch 触发；本机不得补跑 full。
  daily-regression: null

max-fix-rounds: 99
review-round-alert: 3

conformance:
  mode: always                # 仅旧 --phase merge/close 诊断路径使用；票级交付走 --phase ticket，不含 conformance
  sample-rate: 1/3            # 同上；feature 级 conformance 始终全量，不抽样
  checker: main-session       # feature conformance 的 checker 身份

feature-closeout:
  # server-enforced：服务端保护 + CI API 证明；cooperative-ci：CI 可用但无完整服务端强制；local-human：人工确认合 main。
  mode: server-enforced
  integration-branch: "feat/<feature-slug>"
  since: null
  verify-budget-seconds: 1200
  selection-map: null
  ci:                         # server-enforced / cooperative-ci 必填；local-human 必须为 null
    provider: gitlab          # gitea | gitlab
    base-url: "https://gitlab.example.com"
    repository: "group/project"
    workflow: "full"
    artifact: "verdict.json"
    token-env: "GITLAB_TOKEN" # 只存环境变量名，不在配置中保存 token / verdict / proof
  # main 或候选前进后必须重新同步、freeze 和验证，没有 main-drift-ignore 豁免。
  verify-instance: null       # 仅 local-human：{name, deploy, lease}
  prod: null                  # 仅 local-human：{instance, sync, dry-run, status, rollback}；始终需单独授权

# 主会话监督、独立实现与独立评审的原生角色。开发按票风险选档，绝不按测试范围选档。
dispatch:
  schema-version: 1
  executor: subagent
  parallel-max: 2
  hosts:
    codex:
      supervisor-review:
        model: gpt-6-astra
        effort: low
      development:
        default:
          model: gpt-6.1-sol
          effort: medium
        escalated:
          model: gpt-6.1-sol
          effort: medium
    claude-code:
      supervisor-review:
        model: claude-opus-5-5
        effort: medium
      development:
        default:
          model: claude-sonnet-5-5
          effort: medium
        escalated:
          model: claude-sonnet-5-5
          effort: medium
    zcode:
      supervisor-review:
        model: bigmodel-api/GLM-5.3
        effort: high
      development:
        default:
          model: deepseek/deepseek-flash
          effort: high
        escalated:
          model: deepseek/deepseek-flash
          effort: high
    dsh:
      supervisor-review:
        model: deepseek-official/deepseek-v4-pro
        effort: high
      native_session_command: null # DSH 原生 session 查询命令：输出真实 session JSON；null = host-verified 不可用
      development:
        default:
          model: deepseek-official/deepseek-flash
          effort: high
        escalated:
          model: deepseek-official/deepseek-flash
          effort: high

# 证据与独立性的信任边界。非 server-enforced 时必须去掉 protected-branch / expected-SHA 声明，不得自动降级。
enforcement:
  review-trust: host-verified  # host-verified = 宿主真实会话回执；operator-attested = 人工另开对话的协作证明
  require-host-capabilities: true
  require-protected-branch: true
  require-expected-sha: true
  require-clean-tree: true

ocr:
  mode: delegate              # delegate = OCR 筛文件 / 规则，独立宿主对话评审；local / ci 需 LLM 端点
  provider: null
  model: null
  rule-file: .opencodereview/rule.json

gitlab-ci: manual             # ocr.mode=ci 时必须 enabled

artifact-budget:
  impl-log-max-lines: 120
  evidence-max-lines: 120
  impl-dir-max-mb: 20
  integration-dir-max-mb: 20
  artifact-manifest-max-mb: 5

# local-human 的开发服务与实例租约。命令必须显式配置；运行记录绑定 candidate，停止/释放幂等。
integration-runtime:
  ready-timeout-seconds: 60
  stop-timeout-seconds: 30
  lease-timeout-seconds: 3600
  runtime-dir: ".agent-dev/runtime"
  dev-service: null           # local-human 可显式配置 {start, ready, stop, status}；不从项目猜启动命令

# 只清已失效代际；当前、最后成功及被引用证据始终保留。salvage GC 仍需独立预检确认。
retention:
  keep-generations: 2
  salvage-days: 30
  journal-days: 30
  integration-days: 30
```

## 运行纪律

1. 票级只运行本票 DoD + `quality-gate.fast`；完整套件只有登记的 CI full workflow。
2. `verify_issue.py` 产生测试证据，`review_receipt.py` 绑定独立评审，`process_check.py` 只读聚合，`delivery.py` 唯一执行合并，`close-and-clean` 唯一清理。
3. `server-enforced` 任一能力未知即停止；`cooperative-ci` 必须明确报告“无服务端强制”；`local-human` 必须有 `merge-main` 人工确认，push / Prod 另行授权。
4. 修改本文件后必须重新生成 ticket context、宿主原生角色并刷新验证；在途票不能沿用旧配置下的通过结论。
