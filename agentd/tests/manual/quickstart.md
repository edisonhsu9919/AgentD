# AgentD 人工测试 Quickstart

## 前置条件

```bash
cd agentd

# 1. 确保 PostgreSQL 在跑（端口 5433）
# 2. 确保 .env 配置正确（LLM 地址、数据库、SEED_ADMIN）
# 3. 确保 migration 已执行
.venv/bin/python -m alembic upgrade head
```

## 启动

打开两个终端窗口。

### 终端 1：启动后端

```bash
cd agentd
DEBUG=true .venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

看到 `[startup] DB schema version: 003 (up to date)` 表示正常启动。

### 终端 2：启动 CLI

```bash
cd agentd
.venv/bin/python scripts/agentd_cli.py
```

进入 REPL 后依次执行以下测试。

---

## Flow 1：基础链路（~1 分钟）

```
health
login
session new
prompt 你好，请用一句话介绍你自己
```

期望：
- `health` 显示 `status=ok  schema=003`
- `login` 显示 `Logged in as admin`
- `prompt` 后看到 `[sse] text_delta:` 和 `[sse] done  token_usage={...}`
- done 后自动显示 `[sse] title_update: "..."` （标题自动生成）

验证：
```
session-state
```
确认 `title` 已经不再是 "New Session"，`token_usage` 有非零值。

---

## Flow 2：文件读取 — 自动通过（~1 分钟）

`file_read` 权限默认为 `allow`，不会触发审批。

```
prompt 请读取 tests/manual/sample_code.py 的内容并告诉我这个文件做了什么
```

期望：
- `[tool] start file_read ...` — 工具调用开始
- `[tool] result file_read: ...` — 返回文件内容
- `[sse] text_delta:` — agent 分析文件
- `[sse] done`

---

## Flow 3：文件写入 — 触发审批（~2 分钟）

`file_write` 权限默认为 `ask`，会触发 HITL 审批。

```
prompt 在 tests/manual/ 目录下创建一个 hello.txt 文件，内容写 "Hello from AgentD"
```

期望：
- `[tool] start file_write ...` — 工具调用开始
- `[permission] PERMISSION REQUIRED: file_write`
- `[permission]   permission_id: xxxxxxxx-...`
- `[permission]   -> Use 'approve xxx...' or 'deny xxx...'`
- CLI 自动回到输入提示符

此时输入：
```
approve
```

期望：
- `[permission] Approved: xxx...`
- `[sse] Resuming SSE stream...`
- `[permission] resolved: xxx... decision=approved`
- `[tool] result ...` — 文件写入成功
- `[sse] done`

验证文件确实被创建：
```
prompt 请读取 tests/manual/hello.txt 的内容
```

### 测试 deny

```
prompt 在 tests/manual/ 目录下创建一个 secret.txt，内容写 "top secret"
```

等 `permission_ask` 出现后：
```
deny
```

期望：
- agent 收到拒绝消息，回复告知用户文件未创建
- `[sse] done`

---

## Flow 4：Bash 命令 — 触发审批（~1 分钟）

`bash` 权限默认为 `ask`。

```
prompt 请执行 ls -la tests/manual/ 看看这个目录下有哪些文件
```

等 `permission_ask` 出现后：
```
approve
```

期望：
- 看到 `tool_start bash`
- `permission_ask`
- approve 后看到 `tool_result` 包含目录列表
- agent 总结目录内容

---

## Flow 5：Skill 加载 + 持久化（~3 分钟）

### 5.1 创建 skill

```
skills create --name code_review --desc "Code review assistant" --file tests/manual/test_skill.md --tags review,code
```

期望：`Created skill: xxx...  name=code_review`

### 5.2 验证 skill 列表

```
skills list
```

期望：看到 `code_review` 在列表中。

### 5.3 第一轮：加载 skill

```
prompt 请加载 code_review skill
```

期望：
- `[tool] start skill {"action":"load","name":"code_review"}`
- `[tool] result skill: [Skill: code_review]...`
- agent 确认已加载

### 5.4 检查持久化

```
session-state
```

期望：`loaded_skills: ["code_review"]`

### 5.5 查看 prompt 分层

```
prompt-preview
```

期望：
- 看到 4 层：Runtime Header / Role Prompt / Rules / **Skills**
- Skills 层包含 `[Skill: code_review]` 和 skill 的完整内容

### 5.6 第二轮：无需再次调用 tool

```
prompt 请 review 一下 tests/manual/sample_code.py
```

期望：
- agent 先用 `file_read` 读取文件（自动通过）
- 然后按 skill 的格式回复（以 `[REVIEW]` 开头）
- **不再调用 `skill load`** — 因为 skill 已经在 system prompt 中

---

## Flow 6：消息历史（~30 秒）

```
messages
```

期望：
- 看到完整对话历史
- `user` / `assistant` / `tool` 角色分明
- `tool_call: file_read(...)` / `tool_call: skill(...)` 等显示工具名
- `tool_result [xxxxxxxx]: ...` 显示调用 ID

---

## Flow 7：多会话切换（~30 秒）

```
session new --title "Second Session"
session list
```

看到两个 session，当前的标 `*`。

```
session use <第一个 session 的 ID 前缀>
messages
```

可以切回第一个 session 并看到之前的消息。

---

## Flow 8：Prompt 分层可观测（~30 秒）

```
session new --title "Prompt Test"
prompt-preview
```

期望看到 3 层（无 skill 时没有第 4 层）：
```
[prompt] agent=build  model=...
[prompt] total_chars=~4700

========================================================================
## Environment
- Working directory: /tmp/agentd/workspaces/...
- Date: 2026-03-14
...
（Role Prompt 全文）
...
## Platform Rules
（Rules 全文）
========================================================================
```

---

## Flow 9：健康检查 + Schema 验证（~30 秒）

```
health
```

确认：
- `status=ok`
- `schema=003`
- `schema_ok=True`

---

## 故障排除

| 问题 | 解决 |
|------|------|
| `Cannot connect to http://127.0.0.1:8000` | 后端没启动，检查终端 1 |
| `schema_ok=False` | 执行 `.venv/bin/python -m alembic upgrade head` |
| prompt 后无响应 | 检查 .env 中 LLM 地址是否可达 |
| `Session is already running` | 上一次 prompt 还没结束，等一会或新建 session |
| `Session is waiting for permission` | 有未处理的审批，用 `approve` 或 `deny` |
