# Runtime Interaction Logging

XCode 会把 WebSocket runtime 的原始交互边界写入当前项目：

```text
.codex-mini/logs/runtime-YYYY-MM-DD.jsonl
```

日志是诊断副本，不参与 session 恢复；可恢复会话的唯一真相仍是
`.codex-mini/sessions/transcripts/*.jsonl`。

## 记录内容

每行是一个独立 JSON 对象，包含：

- `timestamp`、`session_id`、`turn_id`、`request_id`。
- `schema_version`：当前新记录为 `2`；已有旧日志不会被重写。
- `level`：`info`、`warning` 或 `error`，工具失败会稳定标记为 `error`。
- `summary`：不展开完整 payload 的单行摘要，适合 `tail` / `rg` 快速查看。
- `payload_bytes`：当前 payload 的 UTF-8 字节数，用于定位异常膨胀的事件。
- `actor`：`user`、`agent`、`model`、`tool` 或 `runtime`。
- `event`：如 `user_message`、`model_request`、`model_response`、
  `tool_call_started`、`tool_call_result`、`permission_request`、
  `permission_decision`、`final_answer`。
- `payload`：该交互的结构化内容。

`model_request.payload.message_snapshot` 使用 turn 内增量快照：第一次模型请求写
`mode=full` 的完整消息，后续请求只写 `mode=delta`、`base_message_count` 和新增/变化
的尾部消息。这样仍可按顺序重建实际请求，又不会在长工具链中反复复制此前的全部源码、
工具参数和输出。

`model_response` 保存模型可见文本与 tool calls。相同的 assistant 内容仍写入 transcript，
但不再以 `assistant_message` 重复镜像到 runtime log。逐 token 的 `assistant_token`
也不单独写入，避免同一回复被重复数百次。

## 安全边界

- 日志文件使用 `0600`，日志目录使用 `0700`（支持 POSIX 权限的平台）。
- `API Key`、authorization/token/password 等结构化敏感字段会替换为
  `[REDACTED]`。
- `reasoning_content` 不写入，避免持久化隐藏推理内容。
- 完整 `SKILL.md`、Skill resource 和本轮 Skill 注入正文不写入；对应日志只保留
  Skill 生命周期元数据和占位文本。
- 普通用户消息、文件工具结果和命令输出按原文记录，仍可能含有用户主动输入的
  密码或源码敏感内容。因此 `.codex-mini/logs/` 必须按本机敏感数据处理，不能提交。

## 查看日志

```bash
tail -f .codex-mini/logs/runtime-$(date +%F).jsonl
```

如果安装了 `jq`，可以先看紧凑时间线：

```bash
jq -r '[.timestamp, .level, .event, .summary] | @tsv' \
  .codex-mini/logs/runtime-$(date +%F).jsonl
```

只看失败事件：

```bash
jq 'select(.level == "error")' \
  .codex-mini/logs/runtime-$(date +%F).jsonl
```

定位体积最大的事件：

```bash
jq -s 'sort_by(.payload_bytes) | reverse | .[:10] | .[] |
  {timestamp, event, payload_bytes, summary}' \
  .codex-mini/logs/runtime-$(date +%F).jsonl
```

## 关闭日志

运行服务前设置：

```bash
export CODEX_MINI_RUNTIME_LOG_ENABLED=false
```

关闭只影响新的 runtime log，不影响 transcript、session resume 或已有日志文件。
