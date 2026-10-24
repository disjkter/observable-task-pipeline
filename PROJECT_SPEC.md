# Observable Task Pipeline MVP — Codex 项目开发规范

## 1. 项目目标

在约 3 天内完成一个可运行、可测试、可恢复、可观测的长时间任务执行管线 MVP，并发布公开 GitHub 仓库。

以动画素材处理为业务场景，一个任务由多个具有依赖关系的步骤组成。即使步骤失败、Worker 进程中断或任务执行时间较长，系统也能够恢复执行，并让上游查询任务和步骤状态。

核心功能：

- 异步任务执行，API 不等待任务完成。
- 任务状态、步骤状态和执行历史持久化。
- 步骤级重试和 Worker 崩溃恢复。
- 基于输入指纹的结果复用。
- DAG 步骤依赖管理。
- 任务事件、错误、耗时、重试次数和进度查询。
- GPU 资源需求声明及 MOCK 资源调度。
- 自动化测试和故障注入。
- Windows 本地运行，最终发布公开 GitHub 仓库。

本项目不需要真实动画处理、真实 AI 模型或真实 GPU。计算过程可以 MOCK，但数据库持久化、状态管理、租约恢复、结果复用和测试必须真实实现。

### 核心原则

1. PostgreSQL 是任务状态的事实来源。
2. API 负责创建和查询任务，独立 Worker 负责执行步骤。
3. 不依赖内存变量保存任务生命周期。
4. 步骤失败后不无条件重跑所有成功步骤。
5. 每次执行尝试均保留独立历史。
6. 租约过期不代表旧 Worker 已停止，必须防止旧执行者覆盖新结果。
7. 采用至少一次执行、幂等处理和可恢复状态管理，不宣称严格 exactly-once。
8. 不得伪造测试结果、Token 使用量、实际开发时间或失败案例。

## 2. 技术选型

| 技术 | 用途 |
|---|---|
| Python 3.12 | 开发语言 |
| FastAPI + Uvicorn | REST API |
| PostgreSQL 16 | 持久化任务、步骤、事件和结果索引 |
| SQLAlchemy 2.x | ORM 和数据库访问 |
| Alembic | 数据库迁移 |
| Pydantic | 请求与响应校验 |
| Pytest + HTTPX | 自动化测试 |
| logging | 结构化日志 |
| Git | 版本管理 |

### 环境约束

- Windows 本地开发。
- 不使用 Docker 或 Docker Compose。
- PostgreSQL 使用 Windows 原生安装版本。
- API 与 Worker 使用独立 Python 进程。
- 使用 `.venv` 管理虚拟环境。
- 使用 `.env` 保存本地配置。
- 提供 `.env.example`，不得提交真实密码或 Token。

目标命令：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

alembic upgrade head

# 终端 1
uvicorn app.main:app --reload

# 终端 2
python -m app.worker

# 测试
pytest -q
```

Codex 必须根据真实项目配置验证命令。如果 PostgreSQL 尚未安装或不可用，先说明阻塞原因，不得静默改用 SQLite 并宣称验证了 PostgreSQL 的并发能力。

## 3. 系统架构

### API Layer

负责：

- 创建任务。
- 查询任务和步骤。
- 查询执行尝试历史及任务事件。
- 发起允许的重试。
- 请求取消任务。
- 健康检查。

API 不直接执行耗时步骤。

### Workflow Engine

负责：

- 定义工作流和 DAG。
- 判断步骤依赖是否满足。
- 管理状态迁移。
- 计算重试时间。
- 判断结果是否可以复用。
- 聚合任务状态和进度。

### Worker

Worker 必须是独立进程，负责：

1. 原子领取可执行步骤。
2. 创建执行尝试记录。
3. 获取需要的资源。
4. 在数据库事务之外执行处理器。
5. 定期更新心跳并续租。
6. 校验和保存产物。
7. 验证执行令牌有效后更新状态。
8. 记录耗时、成功或失败信息。
9. 释放资源并领取后续步骤。

### Recovery Manager

负责：

- 检测租约过期的运行中步骤。
- 通过事务和条件更新完成接管。
- 记录失联和恢复事件。
- 根据重试策略恢复执行。
- 保留成功步骤和有效产物。
- 防止旧 Worker 覆盖新 Worker 的状态。

### Artifact Store

第一版使用本地文件系统保存 MOCK 产物。

要求：

- 所有产物写入统一配置的根目录。
- 保存产物地址、输入指纹和校验值。
- 先写临时文件，验证后再发布。
- 数据库登记失败时，恢复流程可以识别已有的有效产物。
- 不能因单个任务失败而删除其他任务仍在使用的产物。

## 4. 动画处理工作流

实现以下六个步骤：

| Step Key | 步骤 | 依赖 | 资源 |
|---|---|---|---|
| `validate_asset` | 素材校验 | 无 | CPU |
| `transcode_video` | 视频预处理 | `validate_asset` | CPU |
| `extract_keyframes` | 提取关键帧 | `transcode_video` | CPU |
| `model_inference` | 模型推理 | `extract_keyframes` | GPU（MOCK） |
| `generate_report` | 生成报告 | `model_inference` | CPU |
| `package_result` | 打包结果 | `transcode_video`、`generate_report` | CPU |

要求：

- 所有前置步骤成功后，下游步骤才能执行。
- 前置步骤最终失败时，未执行的下游步骤保持 BLOCKED。
- 单步失败不应导致所有成功步骤重做。
- 输入、参数、模型版本或处理器版本变化时，需要重新判断缓存有效性。
- 第一版允许串行执行，核心功能完成后再考虑并行。
- 所有处理器使用统一接口。
- MOCK 处理器支持指定耗时、失败次数、错误类型、输出内容、超时、GPU OOM，以及产物已生成但数据库尚未更新等场景。
- 测试应使用短耗时和确定性故障注入，不能依赖长时间等待。

## 5. 数据库设计

至少建立以下五张表。

### 5.1 tasks

保存任务当前状态。

字段建议：

- `id`
- `task_no`
- `task_type`
- `status`
- `progress`
- `input_json`
- `result_json`
- `error_code`
- `error_message`
- `idempotency_key`
- `created_at`
- `started_at`
- `finished_at`
- `updated_at`
- `version`

要求：任务编号唯一；重复提交的幂等键有明确行为；进度由步骤状态计算。

### 5.2 task_steps

保存每个步骤的当前状态。

字段建议：

- `id`
- `task_id`
- `step_key`
- `step_name`
- `status`
- `depends_on`
- `resource_requirements`
- `input_json`
- `output_json`
- `input_hash`
- `attempt_count`
- `max_attempts`
- `next_retry_at`
- `worker_id`
- `lease_token`
- `lease_until`
- `heartbeat_at`
- `started_at`
- `finished_at`
- `created_at`
- `updated_at`

对 `(task_id, step_key)` 建立唯一约束。

### 5.3 step_attempts

每次执行尝试新增一条记录，不得覆盖历史。

字段建议：

- `id`
- `step_id`
- `attempt_no`
- `worker_id`
- `lease_token`
- `status`
- `started_at`
- `finished_at`
- `duration_ms`
- `error_code`
- `error_message`
- `retryable`
- `output_uri`
- `input_hash`

### 5.4 task_events

记录状态变化和重要事件。

字段建议：

- `id`
- `task_id`
- `step_id`
- `attempt_id`
- `event_type`
- `old_status`
- `new_status`
- `details_json`
- `created_at`

记录任务创建、步骤开始、步骤成功或失败、重试、租约过期、恢复、缓存命中、资源分配和释放、取消请求及任务完成等事件。

状态变更和对应事件应尽可能在同一事务中完成。

### 5.5 step_result_cache

保存可跨任务复用的结果。

字段建议：

- `id`
- `input_hash`
- `status`
- `output_uri`
- `checksum`
- `created_at`
- `expires_at`

对 `input_hash` 建立唯一约束或等效并发保护。只有状态成功且产物校验通过时才能复用。

## 6. 状态机设计

### 任务状态

- `QUEUED`
- `RUNNING`
- `RETRY_WAIT`
- `SUCCEEDED`
- `FAILED`
- `CANCEL_REQUESTED`
- `CANCELED`

### 步骤状态

- `BLOCKED`
- `PENDING`
- `RUNNING`
- `RETRY_WAIT`
- `SUCCEEDED`
- `FAILED`
- `SKIPPED`
- `CANCEL_REQUESTED`
- `CANCELED`

状态含义：

- BLOCKED：依赖未满足或前置步骤最终失败。
- PENDING：依赖满足，等待领取。
- RUNNING：已被 Worker 领取。
- RETRY_WAIT：失败后等待重试。
- SUCCEEDED：产物已验证并登记成功。
- FAILED：不可重试或已达到重试上限。

所有状态迁移通过统一服务管理，不允许各模块任意修改状态。

任务状态由步骤状态聚合：

- 所有必需步骤成功后任务才能成功。
- 有步骤运行时任务可以处于 RUNNING。
- 有步骤等待重试时任务可以处于 RETRY_WAIT。
- 不可恢复失败必须阻止相关下游执行。
- 取消请求必须与 Worker 实际执行协调。
- 任务未完成时不能错误显示 100% 进度。

## 7. 原子领取与租约恢复

### 原子领取

使用 PostgreSQL 事务和 `FOR UPDATE SKIP LOCKED` 或等效的原子领取方式：

1. 开启短事务。
2. 选择依赖满足、可以执行且没有有效执行者的步骤。
3. 锁定并领取步骤。
4. 更新状态、Worker 标识、执行令牌和租约截止时间。
5. 创建执行尝试记录。
6. 记录事件。
7. 提交事务。
8. 在事务之外执行耗时处理器。

必须检查条件更新的影响行数。不能在长时间计算期间持有数据库事务。

### 租约和心跳

每个运行中步骤需要：

- `worker_id`
- `lease_token`
- `lease_until`
- `heartbeat_at`

Worker 执行期间定期续租。

### 崩溃恢复

恢复器发现租约过期的步骤后：

1. 使用事务和条件更新确认接管权。
2. 生成新的执行令牌。
3. 使旧执行者的状态更新失效。
4. 记录租约过期和恢复事件。
5. 根据重试策略决定恢复或最终失败。
6. 保留成功步骤和有效产物。

注意：旧 Worker 可能仍在执行，因此不能仅凭租约过期判断旧进程已经停止。必须通过执行令牌或版本校验防止旧 Worker 覆盖新结果。

## 8. 失败重试策略

每个步骤单独配置最大尝试次数。默认最多总尝试 3 次，并明确 `max_attempts` 表示总尝试次数，而不是额外重试次数。

使用指数退避，可设置最大等待时间和随机抖动。

| 失败类型 | 处理方式 |
|---|---|
| 临时网络错误 | 可重试 |
| 外部服务暂时不可用 | 可重试 |
| 超时 | 根据步骤可取消性和幂等性判断 |
| 输入参数错误 | 不自动重试 |
| 输入文件不存在 | 不自动重试 |
| Worker 进程中断 | 租约过期后恢复 |
| GPU OOM | 记录错误、释放资源，按策略调整后重试 |
| 产物校验失败 | 缓存失效并重新生成 |
| 达到重试上限 | 标记失败并阻止下游执行 |

每次尝试都必须保存独立历史，不允许无限重试。

## 9. 结果复用与幂等

输入指纹基于影响结果的输入内容与配置，至少包含：

- 输入内容摘要。
- 步骤类型。
- 关键参数。
- 模型版本。
- 处理器或代码版本。

可以使用 SHA-256 对规范化后的输入描述生成指纹。

缓存命中前验证：

1. 缓存状态成功。
2. 产物文件存在。
3. 校验值一致。
4. 版本兼容。

命中后登记当前步骤的输出并记录缓存命中事件。

如果产物已经生成但数据库状态尚未更新，恢复逻辑应校验已有产物，满足条件时补登记。

重复创建任务必须有明确的幂等行为。系统采用至少一次执行与幂等处理，不承诺所有外部副作用严格只执行一次。

## 10. API 与可观测性

至少实现以下接口：

| 方法 | 路径 | 作用 |
|---|---|---|
| POST | `/api/tasks` | 创建任务 |
| GET | `/api/tasks/{task_id}` | 查询任务 |
| GET | `/api/tasks/{task_id}/steps` | 查询步骤 |
| GET | `/api/tasks/{task_id}/steps/{step_key}` | 查询步骤详情 |
| GET | `/api/tasks/{task_id}/steps/{step_key}/attempts` | 查询执行历史 |
| GET | `/api/tasks/{task_id}/events` | 查询任务事件 |
| POST | `/api/tasks/{task_id}/retry` | 发起允许的重试 |
| POST | `/api/tasks/{task_id}/cancel` | 请求取消 |
| GET | `/health` | 健康检查 |

统一使用 Pydantic 定义请求与响应。第一版使用 FastAPI Swagger UI 作为演示入口，不要求独立前端。

结构化日志尽可能包含：

- `task_id`
- `step_key`
- `attempt_id`
- `worker_id`
- `event_type`
- `duration_ms`
- `error_code`

不得记录密码、Token 或敏感输入。进度应根据步骤状态计算，不能把已启动步骤算作已完成。

## 11. GPU 资源调度模拟

不要求真实 GPU。实现轻量级 Resource Manager，支持：

- 步骤声明 CPU/GPU 资源需求。
- GPU 数量与模拟显存容量。
- 原子预留资源，避免超额分配。
- 资源不足时等待。
- 成功、失败、取消后释放资源。
- 资源租约过期回收。
- 模拟 GPU OOM 和 GPU Worker 失联。
- 记录资源分配、释放和失败事件。

资源预留和步骤领取必须协调，避免多个 Worker 同时超额分配资源。

只能声称实现了 MOCK 调度，不得声称验证了真实 GPU 利用率或显存分配。报告需说明未来接入真实 GPU Worker、NVIDIA 指标及 Prometheus/Grafana 的方案。

## 12. 秒级到小时级任务的扩展

在 `REPORT.md` 中分析：

- 心跳和租约续期。
- 长任务中的数据库事务边界。
- 中间检查点和分块执行。
- 大型产物迁移到 S3 兼容对象存储或 MinIO。
- 长任务取消和超时。
- 队列等待时间、执行时间分布和卡住任务检测。
- Worker 扩容和资源容量管理。
- 长任务与短任务的调度公平性。

不得将长任务绑定到一个 HTTP 请求、内存中的 Future 或长时间数据库事务。

当前 MVP 不必部署 Temporal、Kubernetes、消息队列或生产级监控平台，只需实现基础机制并清楚说明扩展方案。

## 13. 项目目录

```text
observable-task-pipeline/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── api/
│   ├── schemas/
│   ├── models/
│   ├── repositories/
│   ├── services/
│   ├── engine/
│   ├── workers/
│   ├── handlers/
│   ├── recovery/
│   ├── resources/
│   ├── storage/
│   └── observability/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── recovery/
├── scripts/
├── alembic/
├── .env.example
├── .gitignore
├── pyproject.toml
├── alembic.ini
├── PROJECT_SPEC.md
├── README.md
├── REPORT.md
└── LICENSE
```

可以按实际实现调整目录，但模块职责必须清楚，不能把全部逻辑堆在一个文件里。

## 14. 自动化测试与验收标准

### 基础功能

- 能创建任务并查询任务与步骤。
- 动画工作流可以正常完成。
- DAG 依赖判断正确。
- 任务完成后能查询结果。

### 重试与恢复

- 临时错误可以重试。
- 不可重试错误不会无限重试。
- 每次执行尝试均保留历史。
- Worker 被强制终止后，租约过期可以触发恢复。
- 成功步骤不会被无条件重做。
- 旧 Worker 不能覆盖新 Worker 的状态。
- 多个 Worker 竞争领取时，同一步骤只能有一个有效执行者。

### 结果复用

- 相同输入与配置命中有效缓存。
- 输入或版本变化时不会错误复用。
- 缓存文件缺失或校验失败时重新计算。
- 产物已生成但数据库未更新时可以恢复登记。
- 重复提交具有明确的幂等行为。

### 可观测性

- 可以查询任务状态和进度。
- 可以查询每个步骤状态。
- 可以查询每次尝试的耗时、错误和执行者。
- 可以查询任务事件。
- 可以查看缓存命中和资源分配记录。

### GPU MOCK

- 资源充足时可以分配。
- 资源不足时不会超额分配。
- 任务结束后资源能够释放。
- OOM 会被记录并按策略处理。
- 资源租约过期后能够回收。

### 必须尝试的故障测试

1. 正常执行。
2. 指定步骤首次失败，之后重试成功。
3. Worker 在步骤执行期间被强制终止。
4. 重启 Worker 后恢复未完成步骤。
5. 产物已生成但数据库状态尚未更新。
6. 两个 Worker 同时领取同一步骤。
7. 相同输入重复提交。
8. 缓存产物校验失败。
9. GPU 资源不足与模拟 OOM。

测试应尽量短小、确定、可重复。所有测试必须真实运行，未通过的测试不得写成通过。

## 15. 三天开发计划

### 第一天：主链路

- 检查 Windows Python 和 PostgreSQL 环境。
- 创建项目骨架、依赖配置和数据库迁移。
- 实现五张核心表。
- 实现任务创建与查询 API。
- 定义六个步骤及 DAG。
- 实现基础 Worker 和 MOCK 处理器。
- 验证正常执行流程。

**验收：** 能创建任务，并查询完整的执行状态。

### 第二天：可靠性

- 实现原子领取和执行尝试记录。
- 实现错误分类、重试和退避。
- 实现租约续期与过期恢复。
- 实现输入指纹、产物校验和结果缓存。
- 实现 GPU 资源模拟。
- 编写 Worker 崩溃和并发领取测试。

**验收：** Worker 崩溃后能够恢复，成功步骤不被无条件重做，缓存复用有效。

### 第三天：可观测与交付

- 完善事件、执行历史和错误查询。
- 运行故障注入测试并修复关键问题。
- 编写 README 和 REPORT。
- 整理真实测试结果和演示步骤。
- 检查 Git 忽略规则和敏感信息。
- 发布公开 GitHub 仓库。

**验收：** 其他人可以根据 README 在 Windows 上独立运行和验证项目。

## 16. README 与 REPORT

### README.md

必须包含：

- 项目简介与架构图。
- 技术选型理由。
- Windows 环境要求。
- PostgreSQL 安装、建库和迁移步骤。
- API 与 Worker 启动命令。
- API 使用示例。
- 测试命令。
- 故障恢复复现步骤。
- 已知限制和后续改进方向。

### REPORT.md

必须回答：

1. 实现方式和时间规划。
2. 长时间任务执行模型。
3. 任务和步骤状态设计。
4. 步骤依赖关系。
5. 哪些状态需要持久化及其原因。
6. 失败恢复与结果复用机制。
7. 使用的 AI Coding 工具及 Token 使用情况。
8. 个人投入时间最多的部分。
9. 失败案例、原因、修复和遗留限制。
10. 秒级到小时级任务的扩展设计。
11. GPU 资源调度和可观测性设计。

实际开发时间、Token 数量和失败案例必须基于真实记录。无法获取准确 Token 数量时，说明统计口径或数据不可得，不得伪造数字。

## 17. MVP 范围之外

核心验收通过之前，不做：

- 真实动画模型推理。
- 真实 GPU 集群。
- Kubernetes。
- Kafka。
- Celery。
- Redis。
- 独立复杂前端。
- 多租户权限系统。
- 复杂分布式调度和自动扩缩容。
- 生产级 Prometheus/Grafana 部署。

## 18. Codex 开发规范

1. 开始前先检查项目目录、现有代码和运行环境。
2. 已有文件必须先检查再修改，不能无条件覆盖。
3. 不一次性生成整个项目，按阶段开发。
4. 每完成一个模块就运行相应测试。
5. 先实现数据模型和状态机，再实现 Worker。
6. 先实现单 Worker 正常执行，再测试多 Worker 并发。
7. 先实现失败重试，再实现租约恢复。
8. 先保证产物持久化，再实现结果复用。
9. 出错时定位根因、修复并运行回归测试。
10. 不得跳过测试、忽略异常或伪造状态。
11. 不得将耗时计算放进长期数据库事务。
12. 不得把租约过期等同于旧 Worker 已停止。
13. 不得声称实现了严格 exactly-once。
14. 未经验证不得宣称功能完成。
15. 不得编造测试结果、Token 消耗和开发时间。
16. 如果某项设计无法在三天内可靠完成，先说明原因并提出简化方案，不得默默删掉核心验收标准。

每个阶段结束后，汇报：

- 已实现的功能。
- 新增或修改的关键文件。
- 实际运行的命令和真实测试结果。
- 尚未解决的问题。
- 下一阶段计划。

## 19. 第一阶段 Codex 指令

阅读本文件后，先不要一次性生成整个项目。

第一阶段只完成环境检查、项目骨架、依赖配置、数据库模型和 Alembic 初始迁移。

具体要求：

1. 检查当前 Windows 的 Python 版本和项目目录。
2. 不使用 Docker。
3. 不覆盖现有文件，先检查再修改。
4. 为 `tasks`、`task_steps`、`step_attempts`、`task_events` 和 `step_result_cache` 建立基础模型。
5. 明确表关系、唯一约束和必要索引。
6. 创建 `.env.example`，不把真实密码写入代码。
7. 创建或更新 README，说明安装、数据库配置和验证命令。
8. 实际运行可执行的测试和数据库迁移。
9. 如果 PostgreSQL 未安装或不可用，明确报告阻塞原因，不得伪造成功结果。

完成后，汇报修改文件、实际运行的命令、测试结果和未解决的问题，并等待用户确认后，再进入第二阶段的状态机和 Worker 实现。

## 20. 最终完成定义

项目只有满足以下条件才可认为完成：

- Windows 本地可运行，无 Docker 依赖。
- API 与 Worker 可以独立启动。
- PostgreSQL 持久化正常。
- 动画 DAG 可以正常执行。
- 任务、步骤、执行历史和事件可查询。
- 步骤失败可按策略重试。
- Worker 中断后可恢复。
- 结果缓存可以校验和复用。
- 并发领取测试通过。
- GPU MOCK 通过容量和释放测试。
- 关键自动化测试真实运行。
- README 和 REPORT 完成。
- 公开 GitHub 仓库内容与实际实现一致。

**最终目标：交付一个其他人能够运行、注入故障、查询状态并验证恢复行为的工程作品，而不是只堆叠技术栈的代码演示。**