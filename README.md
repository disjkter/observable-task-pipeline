# Observable Task Pipeline

一个可运行、可测试、可恢复、可观测的长耗时任务执行管线 MVP。项目以动画素材处理为示例，使用六步 DAG；处理器和 GPU 调度为 MOCK，实现的数据库状态、并发领取、重试、租约恢复、产物校验和缓存复用均为真实机制。

## 架构

```text
Client -> FastAPI -> PostgreSQL <- Worker(s)
                         |           ^    |
                         +-- NOTIFY -+    +-> MOCK handlers
                         |                +-> local or S3/MinIO artifacts
                         +-> leases / attempts / events / cache / GPU leases
```

PostgreSQL 是状态事实来源。API 只创建和查询任务；独立 Worker 使用短事务与 `FOR UPDATE SKIP LOCKED` 原子领取步骤，在事务外执行耗时处理。Worker 通过心跳续租；恢复器处理过期租约，并以执行令牌阻止旧 Worker 覆盖新结果。PostgreSQL `LISTEN/NOTIFY` 只用于低延迟唤醒，通知丢失不会影响正确性，Worker 仍以有上限的空闲退避扫描兜底。

## 版本与迭代记录

### v0.2.0（2026-10-26）

- Worker 空闲等待从固定频率轮询改为指数退避，领取任务后重置退避。
- 使用 PostgreSQL `LISTEN/NOTIFY` 唤醒空闲 Worker，同时保留低频扫描作为可靠性兜底。
- 事件接口增加 `after_id` 游标和 `limit`，详情页只追加新事件，不再重复获取全部时间线。
- 产物存储抽象为本地与 S3-compatible 两种后端；MinIO 可通过自定义 endpoint 接入。
- 核心调度仍只依赖 PostgreSQL，没有引入任务队列或工作流引擎。

### v0.1.0（2026-10-24）

- 完成六步 DAG、任务/步骤状态、执行历史和事件 API。
- 完成并发领取、自动重试、租约心跳、崩溃恢复、旧 Worker 防覆盖和协作取消。
- 完成本地产物原子发布、SHA-256 校验、跨任务缓存和 MOCK GPU 资源租约。
- 增加中文任务监控台。

六步工作流：

```text
validate_asset -> transcode_video -> extract_keyframes -> model_inference
                                                            |
                                                            v
transcode_video --------------------------------------> generate_report
        |                                                   |
        +---------------------> package_result <------------+
```

`model_inference` 声明 MOCK GPU 资源。GPU 容量使用 PostgreSQL advisory transaction lock 与持久化资源租约原子预留。

## 技术栈

- Python 3.12
- FastAPI / Uvicorn
- PostgreSQL 16+、psycopg 3
- SQLAlchemy 2.x / Alembic
- Pydantic Settings
- Pytest / HTTPX
- boto3（仅启用 S3/MinIO 产物后端时安装）

核心运行不依赖 Docker、Celery、Redis、Kafka 或 Kubernetes。

## Windows 安装

```powershell
# 克隆或解压仓库后，在其父目录执行
Set-Location .\observable-task-pipeline
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

在 Windows 原生 PostgreSQL 中创建本地账号和数据库：

先确认 PostgreSQL 服务可用；如果命令未加入 PATH，请从 PostgreSQL 安装目录的 `bin` 目录运行：

```powershell
psql --version
pg_isready -h localhost -p 5432
```

```sql
CREATE ROLE pipeline_user LOGIN;
\password pipeline_user
CREATE DATABASE observable_pipeline OWNER pipeline_user;
```

编辑 `.env` 中的 `DATABASE_URL`。真实 `.env` 已被 Git 忽略；不要提交密码或 Token。

执行迁移：

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```

## 启动

终端 1：

```powershell
.\.venv\Scripts\Activate.ps1
uvicorn app.main:app --reload
```

终端 2：

```powershell
.\.venv\Scripts\Activate.ps1
python -m app.worker
```

Worker 空闲时从 `POLL_INTERVAL_SECONDS` 开始指数退避，最大等待时间由
`WORKER_IDLE_BACKOFF_MAX_SECONDS` 控制。创建任务、手动重试或步骤就绪时会在同一数据库事务中发送 `NOTIFY`；通知仅缩短等待，不取代数据库条件领取和兜底扫描。

### 可选：MinIO/S3 产物存储

默认 `ARTIFACT_BACKEND=local`，行为与 v0.1.0 一致。启用 S3-compatible 后端时安装可选依赖：

```powershell
pip install -e ".[dev,s3]"
```

配置示例：

```env
ARTIFACT_BACKEND=s3
ARTIFACT_S3_BUCKET=pipeline-artifacts
ARTIFACT_S3_PREFIX=observable-task-pipeline
ARTIFACT_S3_ENDPOINT_URL=http://127.0.0.1:9000
ARTIFACT_S3_REGION=us-east-1
ARTIFACT_S3_ACCESS_KEY_ID=replace-locally
ARTIFACT_S3_SECRET_ACCESS_KEY=replace-locally
```

Bucket 需预先创建。真实密钥只写入被 Git 忽略的 `.env`，不得写入 `.env.example` 或提交记录。AWS S3 可留空 endpoint，并使用 boto3 标准凭据链。两种后端都保持相同的 `publish/read/validate/published_checksum` 接口和 `.sha256` 发布标记语义。

Swagger UI：<http://127.0.0.1:8000/docs>

## 中文任务监控台

API 启动后直接访问：

```text
http://127.0.0.1:8000/
```

监控台使用 FastAPI 同源托管的原生 HTML、CSS 和 JavaScript，无需安装 Node.js。页面所有状态均来自 PostgreSQL-backed API：

- 首页显示真实任务列表、全库状态统计、服务端状态筛选和分页。
- 首页可以创建 MOCK 动画处理任务；当前没有真实素材上传接口。
- 点击任务编号或“查看详情”进入 `/tasks/{task_id}`。
- 详情页显示任务、六步骤 DAG、每次执行尝试、事件时间线、产物地址、缓存命中和 MOCK GPU 资源事件。
- 页面只在后端允许的状态显示任务级重试或取消，并在确认后调用真实 API。
- 活动任务详情每 2.5 秒刷新；首页每 5 秒刷新。页面位于后台时暂停轮询，重新可见时立即刷新。

建议演示顺序：

1. 同时启动 API 与 Worker，打开监控台。
2. 点击“创建模拟任务”，保留默认输入并提交。
3. 在详情页观察步骤由等待执行到成功，以及 MOCK GPU 分配/释放事件。
4. 使用 `fail_times` 故障配置通过 API 创建任务，可在页面观察失败、等待重试和多次 attempt。
5. Worker 执行长步骤时停止进程，等待租约到期后重启 Worker，可在事件时间线看到 `LEASE_EXPIRED` 和恢复事件。
6. 再次创建输入和配置完全相同的任务，可在详情页看到 `CACHE_HIT` 事件和缓存命中次数。

监控台依赖的真实接口：

```text
GET  /health
GET  /api/tasks
POST /api/tasks
GET  /api/tasks/{task_id}
GET  /api/tasks/{task_id}/steps
GET  /api/tasks/{task_id}/steps/{step_key}/attempts
GET  /api/tasks/{task_id}/events?after_id={last_event_id}&limit=200
POST /api/tasks/{task_id}/retry
POST /api/tasks/{task_id}/cancel
```

事件接口按 ID 升序返回 `id > after_id` 的新事件，`limit` 范围为 1～500；省略 `after_id` 时从最早事件开始。当前页面不支持真实文件上传、步骤级重试、产物下载、实时 WebSocket、真实 GPU 指标或用户权限；产物 URI 只作为后端返回的文本展示。

## API 示例

```powershell
$body = @{
  task_type = 'animation'
  idempotency_key = 'demo-001'
  input = @{ source = 'mock://demo-asset'; model_version = 'mock-v1' }
} | ConvertTo-Json -Depth 5

$task = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/tasks' `
  -ContentType 'application/json' -Body $body

Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.id)"
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.id)/steps"
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.id)/events"
```

接口包括创建/查询任务、步骤详情、尝试历史、事件、手动重试、取消和健康检查。相同非空幂等键返回原任务；相同输入可跨任务命中已校验的产物缓存。

## MOCK 故障注入

请求 `input.mock` 支持：

```json
{
  "source": "mock://fault-demo",
  "mock": {
    "sleep_seconds": {"validate_asset": 2},
    "fail_times": {"transcode_video": 1},
    "error_types": {"transcode_video": "TEMPORARY"},
    "gpu_oom_step": "model_inference",
    "crash_after_artifact_step": "extract_keyframes",
    "output_content": {"generate_report": "custom mock report"}
  }
}
```

- `TEMPORARY`、超时类错误可按指数退避重试。
- `INVALID_INPUT`、`MISSING_INPUT` 不自动重试。
- `gpu_oom_step` 模拟 GPU OOM，并保证资源释放。
- `crash_after_artifact_step` 用于验证产物已发布但数据库尚未登记的恢复。

## Worker 崩溃恢复复现

1. 创建一个为某步骤配置数秒 `sleep_seconds` 的任务。
2. 启动 Worker，等待步骤变为 `RUNNING`。
3. 在 Worker 终端按 `Ctrl+C`，不要修改数据库状态。
4. 等待 `.env` 中 `LEASE_SECONDS` 超时。
5. 重新运行 `python -m app.worker`。
6. 查询 attempts/events，可看到 `LEASE_EXPIRED`、`STEP_RECOVERED` 和新的尝试；已成功步骤不会重做。

## 测试与检查

测试会连接 `.env` 指向的 PostgreSQL，并清理测试创建的任务数据。请使用开发数据库，不要指向生产数据库。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m alembic check
```

测试覆盖正常执行、重试、不可重试错误、幂等提交、缓存命中/损坏、并发领取、真实 Worker 子进程终止、租约恢复、旧 Worker 防覆盖、产物补登记、取消及 MOCK GPU 容量/OOM/释放。

## 数据表

- `tasks`：任务聚合状态与进度。
- `task_steps`：步骤状态、依赖、重试和执行租约。
- `step_attempts`：每次执行的不可覆盖历史。
- `task_events`：可查询的状态与资源事件。
- `step_result_cache`：按输入指纹唯一保护的有效产物。
- `resource_leases`：持久化 MOCK GPU 容量租约。

产物旁会原子发布 `.sha256` 校验 sidecar。数据库尚未登记时，恢复器只接受与该发布校验值一致的孤立产物。

## 已知限制

- 处理器、GPU 和动画产物均为 MOCK；没有验证真实 GPU 利用率或显存分配。
- 本地后端只适合单机；v0.2.0 已提供 S3/MinIO 抽象，但尚未做真实对象存储的压力、权限和故障注入测试。
- 当前使用 PostgreSQL 通知加退避扫描，未部署消息队列；`NOTIFY` 是广播式、非持久通知，不能代替数据库事实状态。
- 取消为协作式：不能抢占不可取消的外部调用，只会在处理器返回后拒绝发布成功状态。
- 保证是至少一次执行加幂等结果处理，不宣称严格 exactly-once。
- 当前开发机验证版本为 PostgreSQL 17.11，规格目标 PostgreSQL 16；所用特性在两者均受支持。
