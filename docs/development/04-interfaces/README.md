# 04 接口契约

- [OpenAPI 0.3.0 基线](OpenAPI.yaml)
- [合同系统接入规范](合同系统接入规范.md)
- [财务系统接入规范](财务系统接入规范.md)
- [事件与回调规范](事件与回调规范.md)
- [MVP 标准化数据导入规范](MVP标准化数据导入规范.md)

`OpenAPI.yaml` 是 FastAPI 实际路由/请求/响应模型生成的唯一 HTTP 契约，包含当前已实现的案件、文档、授权审计、来源预览/冻结、运行、结果及人工任务 API；不是外部真实来源的接口定义。修改接口实现后需执行 `uv run python scripts/export_openapi.py > docs/development/04-interfaces/OpenAPI.yaml`，并在 Web 工程执行 `pnpm api:types` 更新前端 `paths` 与 schema。CI 同时检查服务端生成物和 TypeScript 类型未漂移。

前端 `src/api.ts` 使用 `openapi-fetch` 按生成的路径、请求参数和响应 schema 调用 JSON API。文件上传进度和 PDF/DOCX 二进制读取保留自定义传输层，以满足 XHR 进度、MIME 限制、缓存策略和错误脱敏；对应 JSON 类型仍引用生成 schema。

接口实现前必须提供成功、空、部分、错误和超时样本，并确定版本、授权、分页、幂等和完整性语义。

本轮 MVP 的真实财务与合同系统只读接入安排在实施后半段。前半段使用标准化 JSON 和模拟 Adapter 验证流程、权限、来源状态和快照；最终验收需真实接口联调证据。真实审批回写仍不在本轮范围。
