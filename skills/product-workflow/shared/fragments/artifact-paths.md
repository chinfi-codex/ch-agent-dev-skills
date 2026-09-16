## Artifact 路径约定

统一根目录：

```text
./docs/
  GLOSSARY.md
  EXPERIENCE.md
  project-memos/
    project-memo-YYYY-MM-DD.md
  features/
    <feature-slug>/
      <feature-summary>-feature-brief-YYYY-MM-DD.md
      <feature-summary>-prd-YYYY-MM-DD.md
      <feature-summary>-change-request-YYYY-MM-DD.md
      <feature-summary>-pd-review-report-YYYY-MM-DD.md
      <feature-summary>-retro-YYYY-MM-DD.md
```

路径使用规则：
- `./docs/` 是相对当前项目根目录的 artifact 归档路径
- `GLOSSARY.md`、`EXPERIENCE.md`、`project memo` 均为项目级唯一文件：懒创建、原地追加更新，不加日期后缀；`EXPERIENCE.md` 由 `/review` 维护
- 需求级文档统一按 `feature-slug` 归档，文件名 = `<feature-summary>-<类型>-YYYY-MM-DD.md`，类型见上方树形
- `feature-slug` 是需求级稳定标识，默认使用中文；一经建立不因标题调整而改变
- 文档更新使用“新文件 + 日期后缀”策略，不覆盖旧文件
- 读取上游时，先按文档类型过滤，再按日期选择最新版本
- 需求级文档读取不依赖固定旧文件名，应按以下模式匹配：
  - `*-feature-brief-*`
  - `*-prd-*`
  - `*-change-request-*`
  - `*-pd-review-report-*`
  - `*-retro-*`
- 文件命名保持稳定、可搜索、可比较，避免使用含糊名称如 `final-v2-latest`
