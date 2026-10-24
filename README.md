# Observable Task Pipeline

一个可运行、可测试、可恢复、可观测的长耗时任务执行管线 MVP。项目以动画素材处理为示例，使用六步 DAG；处理器和 GPU 调度为 MOCK，实现的数据库状态、并发领取、重试、租约恢复、产物校验和缓存复用均为真实机制。

## 架构

```text
Client -> FastAPI -> PostgreSQL <- Worker(s)
                         ^           |
                         |           +-> MOCK handlers
                         |           +-> local artifact store
                         +-> leases / attempts / events / cache / GPU leases
```

PostgreSQL 是状态事实来源。API 只创建和查询任务；独立 Worker 使用短事务与 `FOR UPDATE SKIP LOCKED` 原子领取步骤，在事务外执行耗时处理。Worker 通过心跳续租；恢复器处理过期租约，并以执行令牌阻止旧 Worker 覆盖新结果。

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

不依赖 Docker、Celery、Redis、Kafka 或 Kubernetes。

## Windows 安装

```powershell
cd 'E:\ProgramFiles\PythonProject\observable-task-pipeline'
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
Copy-Item .env.example .env
```

在 Windows 原生 PostgreSQL 中创建本地账号和数据库：

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

Swagger UI：<http://127.0.0.1:8000/docs>

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

## 已知限制

- 处理器、GPU 和动画产物均为 MOCK；没有验证真实 GPU 利用率或显存分配。
- 本地文件产物存储只适合单机；多机应迁移至 S3/MinIO。
- 当前轮询 PostgreSQL，未部署消息队列；高负载时应加入通知或队列层。
- 取消为协作式：不能抢占不可取消的外部调用，只会在处理器返回后拒绝发布成功状态。
- 保证是至少一次执行加幂等结果处理，不宣称严格 exactly-once。
- 当前开发机验证版本为 PostgreSQL 17.11，规格目标 PostgreSQL 16；所用特性在两者均受支持。
