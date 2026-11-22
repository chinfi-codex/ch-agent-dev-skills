# 原文与报告工具

`python3 skills/shared/scripts/artifact_text.py <bundle|revise|render> --manifest <manifest.json>`

所有路径相对 manifest 所在目录。sha256 是输入文件原始字节的 SHA256。工具失败返回非零，既有输出不会覆盖；父目录须预先存在。工具不改变冻结上下文、回执、验收计划或授权，也不证明语义覆盖。临时 manifest 放当前任务的临时目录，无须作为新流程报告长期保存。

## 去重读取

```json
{"sources":[{"path":"spec.md","sha256":"<64位哈希>","start":10,"end":30},{"path":"spec.md","sha256":"<同一哈希>","start":20,"end":40}]}
```

输出 JSON 包含合并后的 10–40 行原文、路径、哈希和区间。不传 start/end 表示全文。按技能必读范围列清单；不做语义筛选，不按预算截断。评审完整背景不可用实现者局部清单替代。

## 修订为完整新版本

```json
{"source":"prd-v1.md","sha256":"<64位哈希>","output":"prd-v2.md","replacements":[{"old":"唯一匹配的旧段落","new":"修改后的完整段落"}]}
```

全部补丁在原始文本上定位，重复或重叠匹配拒绝，未修改字节保留。之后仍检查稳定 ID、引用、需求完整性并按原流程评审。版本漂移仍使冻结证据失效。

## 报告渲染

先依据对应正式模板建立保留全部必需章节的骨架，将动态位置写为 `{{candidate}}`、`{{judgment}}` 等占位符。

```json
{"template":"report-skeleton.md","sha256":"<64位哈希>","output":"report.md","fields":{"candidate":"从当前候选提取的事实","judgment":"本角色本轮独立完成的判断与依据"}}
```

fields 必须恰好覆盖骨架占位符且非空，原文插入，不执行模板代码。它只负责拼装，不自动生成 pass，不核查骨架是否符合正式模板；报告仍需原候选门检查。每个角色独立形成判断，不能把同一判断同时填到评审与符合性报告。候选复审必须实际重新核对，不能复制旧结论冒充新结论。
