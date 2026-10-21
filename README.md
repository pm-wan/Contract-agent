# 合同审查平台

本仓库包含 9 月 24 日产品设计和正在开发中的 Python 实现。当前交付计划见[实施计划与里程碑](docs/development/00-project/实施计划与里程碑.md)，实现记录见 [M0 进展记录](docs/development/00-project/M0进展记录.md) 和 [M1 进展记录](docs/development/00-project/M1进展记录.md)。

## 本机启动

需要 Python 3.13、uv、Docker 和 Docker Compose。先复制配置模板，并填写 DeepSeek API Key 和本地随机认证令牌：

```bash
cp .env.example .env
```

Edit `.env` and set `CONTRACT_REVIEW_MODEL_API_KEY` to your DeepSeek API key. The login page only accepts an existing username and password. Provision the first administrator once from a trusted terminal with `uv run python -m contract_app.bootstrap_admin`. `.env` and `.env.pageindex` are ignored by Git; do not commit API keys.

### 方式一：使用项目自带的本地 PostgreSQL（最简单）

此方式用项目 Compose 启动 PostgreSQL，映射到本机 `127.0.0.1:5438`；运行迁移后可启动 API 和 Web：

```bash
uv sync --extra database --extra ai --no-install-package PyPDF2
docker compose up -d postgres
DATABASE_URL='postgresql://contract_admin:local-dev-only@127.0.0.1:5438/contract_audit' uv run alembic upgrade head
docker compose --profile dev-app up --build
```

The API is at `http://127.0.0.1:8001` and the Web app at `http://127.0.0.1:5174` on this computer. Docker publishes both app ports on the host network interfaces, so another trusted device on the same LAN can open the Web app at `http://<本机局域网IP>:5174` and the API at `http://<本机局域网IP>:8001`; on Windows, find the IPv4 address with `ipconfig`. PostgreSQL remains bound to `127.0.0.1:5438`. On a new tenant, create the first administrator from the API container with `docker compose exec api uv run python -m contract_app.bootstrap_admin`; the login page does not create administrator accounts.

### 方式二：使用本机共享 infra PostgreSQL

`.env.example` 中 `DATABASE_URL` 和 `DATABASE_ADMIN_URL` 指向共享 infra 的 `contract_agent` 数据库（`localhost:5432`）。先按照 `/mnt/project/infra/README.md` 启动共享 infra，并创建 `contract_agent` 数据库；确认数据库可连接后，在终端加载 `.env` 并运行迁移：

```bash
set -a; . ./.env; set +a
uv sync --extra database --extra ai --no-install-package PyPDF2
uv run alembic upgrade head
export APP_ENV=development
export AUTH_TENANT_ID='00000000-0000-4000-8000-000000000001'
uv run uvicorn contract_app.api:app --host 0.0.0.0 --port 8000
```

该方式在本机直接启动 API，不要同时启动项目 Compose 的 `postgres` 并误以为 API 会自动连接共享数据库。项目 Compose 中的 API/worker 仍使用 Compose 网络内的 `contract_audit`；如需容器连接共享 infra，需另行配置容器到宿主机的网络和数据库地址。

### 前端

Local development uses username/password sessions and role permissions. Set `AUTH_TENANT_ID` to the fixed tenant UUID used by this workspace.

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web dev
```

前端将显示 Vite 输出的本机和局域网访问地址；局域网内可用 `http://<本机局域网IP>:5173` 访问。登录页只接受系统中已有的账号密码；新租户首次使用时，由管理员在服务端终端运行 `uv run python -m contract_app.bootstrap_admin` 创建首个系统管理员。

## 本地开发说明

本地 DOCX 流程使用宿主机 LibreOffice Writer 和 CJK 字体；容器化解析 Worker 为可选项。宿主机转换显式禁用 OpenCL，以避免无头启动卡死；渲染器身份会记录此设置。复杂客户文档分页、真实 OCR 质量、真实模型表现和生产部署尚未验证。当前回归记录见 [M1 进展记录](docs/development/00-project/M1进展记录.md)。

本项目的 Compose 数据库账号只用于本机开发，切勿用于其他环境。使用项目自带 PostgreSQL 时，API 容器会自动执行数据库迁移。若使用共享 infra 数据库，则通过本机加载 `.env` 执行迁移；测试环境与日常开发库目前共用 `.env.example` 中的 `contract_agent`，测试可能写入或清理库内数据。

若要单独运行后端测试，请先确认测试数据可以丢弃：

```bash
set -a; . ./.env; set +a
uv run pytest -q
uv run ruff check src tests migrations
uv run mypy src/contract_app
```

## 本地认证

本地登录使用系统账号和密码。登录页不提供管理员初始化；新租户首次使用时，在 API 环境中执行一次性命令创建首个管理员。已有账号后，后续账号由系统管理员在“组织与权限”中创建并分配组织部门和角色。

```bash
set -a; . ./.env; set +a
uv run python -m contract_app.bootstrap_admin
```

使用 Docker Compose 时，在项目目录执行 `docker compose exec api uv run python -m contract_app.bootstrap_admin`。命令会交互式收集账号、姓名、组织名称和初始密码；密码不会回显，且每个租户只能初始化一次。新成员首次登录后必须修改密码。

组织部门用于管理成员归属；角色授予功能权限；案件成员关系和逐份材料授权继续决定用户可查看的具体案件与文件。主体台账由 `entity_registry.use` / `entity_registry.manage` 控制，主体纠错由 `entity_corrections.use` 控制。

API 也支持配置 OIDC Bearer Token。需要提供 issuer、API audience、JWKS URL，以及可映射到租户的签名 claim；issuer 和 JWKS 地址必须使用 HTTPS。相同的 issuer + `sub` 映射到稳定 actor UUID，系统仍通过本地角色、案件成员和材料授权决定数据范围。

```bash
export CONTRACT_AUTH_MODE=oidc
export OIDC_ISSUER='https://idp.example.com/'
export OIDC_AUDIENCE='contract-audit-api'
export OIDC_JWKS_URL='https://idp.example.com/.well-known/jwks.json'
export OIDC_TENANT_CLAIM='tenant_id'
```

OIDC token 无效时返回 401；JWKS 缺失或无效时失败关闭并返回 503。账号和角色仍由本系统的组织与权限模块管理。
## 可选能力

**遥测：** 默认关闭。仅在本机控制台输出 span/metric 元数据，可设置 `CONTRACT_OTEL_EXPORTER=console`；旧变量 `CONTRACT_OTEL_CONSOLE=1` 仍兼容。启用 OTLP traces 和 metrics 时，设置 `CONTRACT_OTEL_EXPORTER=otlp` 并配置标准 `OTEL_EXPORTER_OTLP_*` endpoint/header。默认传输协议为 HTTP/protobuf，也支持 `grpc`；按信号设置的协议变量优先。宿主机 Worker 需安装 `telemetry` extra（`uv sync --extra database --extra telemetry`）；API、parse-worker 和 PageIndex 镜像已包含。此设置不会启用 OTLP logs，也不会配置 collector、采样、保留策略、仪表板或告警。未批准 collector 的访问控制与数据处理前，不要启用导出。

**S3 对象存储：** 默认使用本地对象存储。要连接 S3 兼容服务，安装可选 SDK：

```bash
uv sync --extra database --extra ai --extra storage --no-install-package PyPDF2
```

设置 `OBJECT_STORE_BACKEND=s3` 和 `S3_BUCKET`；也可配置 `S3_ENDPOINT_URL`、`AWS_REGION` 和 `S3_PREFIX`。Boto3 使用标准凭据提供链，仓库中不得存放凭据。每租户对象回收器还需要在配置的 prefix 范围内有 `ListBucket`、`GetObject`（用于元数据检查）和 `DeleteObject` 权限。

**依赖许可证清单：** 如需准备依赖许可证审查，安装计划交付的 extras 并导出锁定依赖的许可证元数据：

```bash
uv sync --frozen --extra ai --extra pageindex --extra database --extra storage --no-install-package PyPDF2
uv run --frozen python scripts/export_license_inventory.py --output /tmp/contract-audit-license-inventory.json
```

JSON 与 `uv.lock` 的 SHA-256 绑定，只包含当前 Python/平台实际安装的发行包；未安装的锁文件条目会单独列出。PageIndex 上游锁依赖中仍包含 `PyPDF2`，但安装时显式排除；PageIndex 适配器将其旧导入名绑定到单独锁定且已修复的 `pypdf`，测试也覆盖真实 PageIndex reader 路径。此做法并未移除锁文件安全提示；PageIndex 更新时需要重新验证。所有条目均标记为 `unreviewed`：包元数据清单仅供审查参考，不代表法律结论。输出路径必须不存在。

仓库中提供了可审查的基线快照：[依赖许可证元数据清单.json](docs/development/00-project/依赖许可证元数据清单.json)。该快照只适用于当前 Linux/Python 环境和锁文件哈希；未安装的平台条件包及有意排除的 `PyPDF2` 会明确显示，不会被标成已审查。

CI 使用 `--check` 对该快照运行同一导出器；Python 主/次版本、平台、锁文件哈希、安装包集合和许可证元数据必须匹配（忽略 Python 补丁版本差异）。有意更新时请导出到新文件并审阅差异，不要在比较前覆盖仓库快照。

## 当前功能边界

已实现 `GET /health/live`、租户范围的案件创建/列表/详情、分阶段文档上传与目录、明确的审阅人选择/字段授权与撤销、冻结输入后的运行调度、仅审阅人可读的文档产物、运行状态/结果 API，以及实验性的受限合同审查候选定位。

案件创建时法律实体默认为未确认。审阅人确认实体并记录核验依据前，只允许上传主合同；附件上传、材料选择/授权及输入预览/冻结暂不可用。当前 Web 提供“上传合同审核 → 开始审核 → 查看结果”开发主线。创建运行须满足 `APP_ENV=development` 或 `APP_ENV=test`，并显式设置 `CONTRACT_REVIEW_EXECUTION_ENABLED=true`；生产环境始终拒绝此开发运行入口。候选定位器只输出经过校验、判断为 `undetermined` 的付款条款候选；它不是完整的法律/企业审查，也不会生成报告。

新发起的主合同审核使用合同驱动的双维流程：大模型阅读冻结授权材料，自主生成问题，检索本地法规全文、联网权威法律资料及公司制度文件，再形成带逐字引用的比对结果。使用官方 DeepSeek 服务时自动复用现有凭据调用其搜索/抓取接口。资料在“公司知识库”上传与启用；案件从首页或案件列表创建。详见[双维审查使用说明](docs/development/06-rules/销售合同双维审查试验台说明.md)。

上传会保存原件并排入持久化的租户级解析任务。每个可信租户运行一个 parse worker；解析完成后，如 PageIndex 模型配置可用，再运行 PDF 索引 Worker：

```bash
set -a; . ./.env; set +a
uv run python -m contract_app.documents.parse_worker
```

解析 Worker 写入不可变的标准化产物后才会排入索引任务。解析完成前，读取解析内容会返回冲突状态，原文预览/冻结不可用。解析错误会显示在文档目录中；原件仍可供审阅人检查。

索引 Worker 接收 PDF，以及由解析 Worker 转成持久化、版本化标准 PDF 的 DOCX。解析、PageIndex 提交、引用页、预览和冻结输入身份均绑定同一份 PDF。真实 PageIndex 服务质量和部署检查尚未完成。PageIndex 和 AgentScope 是可选依赖。完成 API/对象存储配置并运行解析 Worker 后，可启动索引 Worker：

```bash
set -a; . ./.env; . ./.env.pageindex; set +a
uv run python -m contract_app.documents.index_worker
```

每租户对象回收器只会清除超过宽限期且没有已提交文档版本引用的对象。它与上传/回滚补偿共享每对象 PostgreSQL advisory lock，并在删除前重新检查数据库引用。每个可信租户运行一个回收器；默认最短对象年龄为 24 小时，每小时扫描一次：

```bash
set -a; . ./.env; set +a
export DOCUMENT_GC_MINIMUM_AGE_SECONDS=86400
export DOCUMENT_GC_INTERVAL_SECONDS=3600
uv run python -m contract_app.documents.object_gc
```

回收器不会删除数据库记录或仍被任何文档版本引用的对象。它也会在相同对象键锁下清理本地过期 `.upload-<sha256>-*` 临时文件；S3 `PutObject` 写入是原子的，不会产生这类本地临时文件。

本地实验性 `contract_review` processor 使用开发专用付款规则，以及由该规则、工具契约和输出 schema 生成的 Markdown Skill 候选作为模型上下文。它会在标准化文本中本地查找候选词，再通过授权的 PageIndex gateway 处理候选页；这不是 PageIndex 语义搜索或模型质量验收。不要把合成结果作为签约建议，也不要将此实验 Worker 部署为生产审查服务。Worker 从环境变量读取模型凭据，不需要把凭据放到浏览器：

```bash
set -a; . ./.env; set +a
uv run --no-sync --extra ai python -m contract_app.workflow.contract_review
```

OpenAI 兼容模型可配置 `CONTRACT_REVIEW_MODEL_BASE_URL`。API 和 Worker 必须使用相同的对象存储后端。

## DOCX 本机渲染模式

本地 DOCX 开发时，建议 API/Web 使用 Compose，持久化解析 Worker 在宿主机运行。该模式使用宿主机 LibreOffice，避免在 Worker 镜像中安装第二套办公套件。先创建共享对象目录；UID/GID 覆盖可使 API 容器与宿主机 Worker 使用相同文件所有者，`container-parse` profile 保持不启用：

```bash
mkdir -p .local/contract-audit/objects .local/contract-audit/pageindex
LOCAL_DEV_UID="$(id -u)" LOCAL_DEV_GID="$(id -g)" \
  docker compose -f compose.yaml -f compose.host-renderer.yaml \
  --profile dev-app up --build
```

API 和 Web 端口发布到宿主机的网络接口，可用 `http://<本机局域网IP>:5174` 访问 Web；PostgreSQL 仍仅绑定 `127.0.0.1:5438`。在另一个宿主机终端，为可信本机租户运行一个解析 Worker。渲染器版本和环境标识会明确写入，以记录生成页坐标的宿主机环境；LibreOffice 或字体集变更时应同步更新：

```bash
export DATABASE_URL='postgresql://contract_runtime:local-dev-only@127.0.0.1:5438/contract_audit'
export DOCUMENT_PARSE_TENANT_ID='00000000-0000-4000-8000-000000000001'
export OBJECT_STORE_BACKEND=local
export OBJECT_STORE_DIR="$PWD/.local/contract-audit/objects"
export DOCX_RENDERER_EXPECTED_VERSION=26.8.0.3
export DOCX_RENDERER_ENVIRONMENT_ID=host-cachyos-lo26.8-noto20240730
uv run --extra database --extra telemetry python -m contract_app.documents.parse_worker
```

宿主机渲染仅适用于可信本机工作站。它不是可复现的部署配置。容器化解析 Worker 使用独立的可选 profile；去掉覆盖文件并同时启动两个 profile 即可使用：`docker compose --profile dev-app --profile container-parse up --build`。停止 profile 时不要添加 `-v`，以保留 PostgreSQL、本地对象和 Web 依赖数据。宿主机渲染的对象目录被 Git 忽略，且与默认 Compose 命名卷相互独立。

## PageIndex Compose Worker

PageIndex 索引提供独立的可选 Compose profile。首次使用时，将模板复制为本地配置文件（若文件已存在，请保留并直接编辑）：

```powershell
if (!(Test-Path .env.pageindex)) { Copy-Item infra/pageindex.env.example .env.pageindex }
notepad .env.pageindex
```

在 `.env.pageindex` 中填写 `OPENAI_API_KEY`。默认 `PAGEINDEX_MODEL=deepseek-flash` 和 `OPENAI_BASE_URL=https://api.deepseek.com` 已适配 DeepSeek 的 OpenAI 兼容接口；Compose 会在 Worker 启动时检查模型名和 API Key。`.env.pageindex` 被 Git 忽略。

启动 API/Web 与 PageIndex Worker：

```bash
docker compose -f compose.yaml -f compose.host-renderer.yaml \
  --profile dev-app --profile pageindex up --build
```

PageIndex Worker 使用包含 PageIndex extra 的独立镜像（API 镜像不包含推理依赖），使用本地对象存储配置并将索引写入持久化命名卷。它限定在本机开发租户；不要在本机开发以外使用固定租户或 Compose 凭据。目前只索引 PDF；排队的 PDF 任务会触发模型 API 调用。停止 profile 且保留数据时，不要添加 `-v`：

```bash
docker compose -f compose.yaml -f compose.host-renderer.yaml \
  --profile dev-app --profile pageindex down
```

若要使用全容器化解析/索引开发栈，去掉宿主机渲染覆盖文件并添加 `--profile container-parse`。

## Web 工作台

旧版审查/结果原型只使用合成 fixture；在原型中选择来源不代表获得访问授权，冻结操作也刻意保持禁用。

```bash
pnpm --dir web install --frozen-lockfile
pnpm --dir web dev
pnpm --dir web lint
pnpm --dir web typecheck
pnpm --dir web test
pnpm --dir web build
```

案件列表、案件详情和文档上传通过开发 API 代理访问后端。旧版 `/cases/demo` 审查/结果路由仍是 M0 合成原型，不可用于真实审查。`/runs/{runId}` 展示 API 返回的运行状态、候选结果和已实现的人工复核操作；它会定期重新检查结果授权，并且仅在校验来源、哈希和偏移后展示解析文本引用。案件详情按“主合同与主体 → 补充材料 → 授权并开始审核 → 审核结果”组织。主体从台账选择并记录本案核验依据；主合同解析和索引就绪后，审核人确认逐份材料授权，系统再预览、冻结并创建合同审查运行；已产出的审核批次在结果区查看原文证据。运行创建后跳转结果页；解析、索引和审核分别由现有 Worker 处理，需要按上文启动对应 Worker。重试保留本次输入和运行请求键，避免重复提交。支持技术栈和可观察 UI 行为见 [ADR-005](docs/development/03-architecture/ADR/ADR-005-React与AntDesign前端配套栈.md)、[DESIGN.md](DESIGN.md) 和 [UX-CONTRACT.md](web/UX-CONTRACT.md)。

