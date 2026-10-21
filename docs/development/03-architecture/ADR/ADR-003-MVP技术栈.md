# ADR-003：内部 MVP 技术栈（已被取代）

> 状态：已取代
> 原决定日期：2026-09-17；取代日期：2026-09-25
> 当前技术基线见 [ADR-004：Python 全栈模块化单体](ADR-004-Python全栈模块化单体.md) 和 [技术栈基线](../技术栈基线.md)。本文保留历史决策，不作为新工程依赖清单。

## 背景

既有文档只确定 Go、Eino、PostgreSQL 和对象存储方向，尚不能直接创建工程，需要冻结 Web、数据库访问、前端、文档解析、测试和部署技术栈。

## 决定

采用：

- Go 1.26.8、chi v5、pgx v5、sqlc、goose；
- Eino v0.9.13；
- PostgreSQL 18.6；
- React 19.3、TypeScript 5.9、Vite 7、Ant Design 6.6；
- Python 3.13 + FastAPI 0.141 + PyMuPDF 1.28 的无状态文档解析 sidecar；
- OpenAPI 3.1、JSON Schema 2020-12；
- Docker Compose 作为内部 MVP 编排方式。

详细约束见 [技术栈基线](../技术栈基线.md)。

## 备选方案

- 全 Go 文档解析：生态与 OCR 质量不足，暂不采用；
- Java/Tika 解析：运行时更重，MVP 暂不采用；
- Gin/GORM：领域收益不足且会弱化 SQL 控制；
- Kubernetes/微服务：当前规模不需要；
- Eino alpha 版本：主链路不接受预发布依赖。

## 后果

仓库将同时包含 Go、TypeScript 和一个受限 Python Adapter。需要分别维护锁文件和安全扫描，但换取更可靠的文档解析和清晰的主业务边界。

## 复审条件

文档解析 sidecar 成为性能瓶颈、OCR 需要 GPU 服务、外部部署禁止 Python、或团队无法承担多语言维护时重新评审。
