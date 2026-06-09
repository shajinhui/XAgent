# Workspace 与权限策略架构

本文档定义 Codex-mini 下一阶段的 workspace 与 permission 架构。目标不是把 OpenAI Codex 或 Claude Code analysis 的实现照搬过来，而是吸收它们的稳定边界，落到当前 Python runtime + Electron/Vue desktop client 的产品形态里。

## 参考基准

### OpenAI Codex

OpenAI Codex 的核心启发是：workspace 不是一个孤立目录，而是 `cwd`、project trust、permission profile、filesystem policy、exec policy 和 sandbox enforcement 的组合。

可对齐的源码点：

- `codex-rs/core/src/config/permissions.rs`：内置 `:read-only`、`:workspace`、`:danger-no-sandbox` permission profile。
- `codex-rs/protocol/src/permissions.rs`：filesystem policy 使用 `read`、`write`、`none` entry，并默认保护 `.git`、`.agents`、`.codex` 等 workspace metadata。
- `codex-rs/sandboxing/src/seatbelt.rs`：macOS Seatbelt profile 由 filesystem/network policy 生成，而不是固定模板。
- `codex-rs/core/src/exec_policy.rs`：命令策略支持 prefix rule、prompt、forbidden、safe/dangerous heuristic 和可追加规则。
- `codex-rs/core/src/agents_md.rs`：`AGENTS.md` 从 project root 到 cwd 分层读取，不越过项目根。
- `codex-rs/config/src/loader/mod.rs`：project-local config 必须受 trust gate 约束，敏感配置不能由项目本地配置随意指定。

### Claude Code Analysis

Claude Code analysis 的核心启发是：项目身份和当前执行目录必须分开。

可对齐的源码点：

- `src/bootstrap/state.ts`：区分 `projectRoot`、`originalCwd`、当前 `cwd` 和 session project dir。
- `src/utils/config.ts`：项目配置 key 优先使用 canonical git root，不在 git repo 里才使用原始目录。
- `src/utils/permissions/permissionSetup.ts`：additional directories 需要显式加入 permission context，不能静默扩大权限。
- `src/utils/sessionRestore.ts`：恢复 session 时恢复 worktree/cwd，并清掉依赖 cwd 的缓存。
- `src/tools/EnterWorktreeTool/EnterWorktreeTool.ts` 与 `src/tools/ExitWorktreeTool/ExitWorktreeTool.ts`：进入临时 worktree 会改变当前执行目录，但不一定改变稳定项目身份；删除 worktree 前必须 fail-closed。

## 设计目标

Codex-mini 的 workspace 与权限系统要满足这些目标：

- 用户选择的目录是安全边界，不是普通 UI 状态。
- Python runtime 拥有 workspace、tool execution、permission、sandbox 和 transcript 的最终解释权。
- Desktop client 只负责展示、审批、选择目录和发送用户决策。
- 任何额外目录、网络、危险命令、外部写入都必须通过显式规则或用户确认进入权限上下文。
- 用户批准只代表允许尝试执行，不代表绕过 sandbox。
- session resume 后必须恢复或重新验证 workspace 与权限快照，不能静默绑定到当前启动目录。
- 当前执行目录可以变化，但稳定项目身份不能漂移。

## 当前差距

当前实现已经有第一版能力：

- `workspace/` 可以验证并打开用户选择的目录。
- `WorkspaceContext` 已拆出 `selected_root`、`project_root`、`current_dir`、session-only trust、workspace snapshot 和 additional root model。
- `open_workspace` 会创建新 session；当前 `SessionStore` 绑定到 `workspace.project_root`，工具执行边界使用 `workspace.selected_root`。
- `change_directory` 可以在已授权目录内切换 `current_dir`，且不会创建新 session。
- `add_dir` 可以显式加入 read/write additional root，并立即刷新工具执行的 filesystem policy。
- `run_command.cwd` 可以在 active filesystem policy 的可写目录中执行。
- `FileSystemPolicy`、`PermissionProfile`、`ApprovalPolicy` 和 `NetworkPolicy` 已有第一版。
- read/write/edit/grep/run_command cwd 已统一走 `current_dir + filesystem policy`。
- `.env` 默认禁止 read/write，`.codex-mini` 默认禁止 write。
- `write_file`、`edit_file`、`run_command` 会先走 WebSocket permission request。
- `run_command` 批准后进入 macOS Seatbelt。
- `sandbox/macos_executor.py` 已按 `FileSystemPolicy` 和 `NetworkPolicy` 生成第一版 Seatbelt profile。

仍需要收口的差距：

- `change_directory` / `add_dir` 已有第一版协议和 desktop 入口；session resume 会按持久化 workspace snapshot 重新验证并恢复这些 policy 变化。
- `ExecPolicy` 已有第一版独立模型，支持 prefix allow/ask/deny、session allowlist 和 dangerous prefix suggestion denylist。
- 命令策略已有 trust-gated project-local rule 文件加载第一片。
- CLI 路径没有完整 approve/deny/retry 闭环。
- session resume 已恢复 workspace policy、additional dirs 和 current dir；permission mode 仍未产品化持久化。
- `AGENTS.md` 已按 project root 到 cwd 分层加载；project-local policy config 已接 trust gate 第一片，后续补 UI 和持久化编辑体验。

## 当前落地状态

截至当前架构，Codex-mini 已经完成运行时模块化，并落地了 `WorkspaceContext v2` 的第一片；workspace/permission v2 还没有完整实现。

已和新架构对齐的部分：

- WebSocket transport 已降为薄入口，`server/runtime/` 负责 turn runner、model streaming、session state 和 WebSocket runtime context。
- `server/processors/` 已承载 `open_workspace`、session 操作和标题生成等控制请求。
- `tools/core/` 已承载工具协议、registry、router、runner、catalog 和工具 metadata。
- `workspace/`、`security/`、`sandbox/` 已分成独立目录，可以承接后续 workspace/permission v2。
- `WorkspaceContext` 已拆出 `selected_root`、`project_root`、`current_dir`、`WorkspaceTrust`、`WorkspaceSnapshot` 和 `AdditionalRoot`。
- workspace payload 只发 `selected_root`、`project_root`、`current_dir`、`trust`、`additional_roots` 等 v2 字段；旧 `root` 与 `allowed_roots` 不再进入协议和前端类型。
- `security/permissions.py` 已提供 `FileSystemPolicy`、`PermissionProfile`、`ApprovalPolicy` 和 `NetworkPolicy`。
- 工具读写和命令 cwd 已通过 `SecurityPolicy` 统一使用 filesystem policy。
- macOS Seatbelt profile 已从 filesystem/network policy 生成，不再使用固定全局读模板。
- `security/exec_policy.py` 已提供 prefix allow/ask/deny、session allow、危险命令拒绝和 `suggested_prefix_rule`。
- `server/processors/request_dispatcher.py` 已支持 `change_directory`、`add_dir` 和 `workspace_policy_changed`。
- `workspace/instructions.py` 已支持从 `project_root` 到 `current_dir` 分层加载 `AGENTS.md`，并避免 external additional root 越界加载。
- desktop TitleBar 已有打开 workspace、切换 current dir 和加入 additional root 的原生目录选择入口。

尚未落地到代码的部分：

- trust/untrust WebSocket 控制事件和桌面端最小入口已完成第一版；还没有完整策略编辑 UI。
- 运行时 session allowlist 还没有恢复到 trusted project/user config。
- workspace resume 已按当前 v2 schema 严格重新验证 `selected_root`、`project_root`、`current_dir` 和 `additional_roots`；permission profile 仍未产品化持久化。

下一阶段落地时，应把本文档当作目标架构，把 `docs/PROJECT_ARCHITECTURE_STATUS.md` 当作当前代码事实。两者不一致时，优先以代码事实为准，再同步更新文档。

## 总体架构

```text
Desktop Client / Future IDE
  |
  | open_workspace / change_directory / add_dir
  | permission_decision / resume_session
  v
Runtime Bridge: server/app.py
  |
  v
WorkspaceManager
  |
  +-- WorkspaceContext
  +-- WorkspaceTrust
  +-- WorkspaceSnapshot
  |
  v
Security Layer
  |
  +-- FileSystemPolicy
  +-- ApprovalPolicy
  +-- ExecPolicy
  +-- NetworkPolicy
  |
  v
ToolRegistry
  |
  +-- read_file / grep
  +-- write_file / edit_file
  +-- run_command
  +-- web_fetch
  |
  v
Sandbox Layer
  |
  +-- macOS Seatbelt profile generated from policy
  +-- future Linux/Windows sandbox adapters
  |
  v
Session Layer
  |
  +-- SQLite index
  +-- append-only JSONL transcript
  +-- resume/recovery
```

## Workspace 模型

### WorkspaceContext

`WorkspaceContext` 应升级为稳定项目身份和当前执行位置分离的模型。

```python
@dataclass
class WorkspaceContext:
    selected_root: Path
    project_root: Path
    current_dir: Path
    display_name: str
    git_root: Path | None
    trust: WorkspaceTrust
    filesystem_policy: FileSystemPolicy
    permission_profile: PermissionProfile
    approval_policy: ApprovalPolicy
    additional_roots: list[AdditionalRoot]
    session_store: SessionStore | None = None
```

字段语义：

- `selected_root`：用户通过 desktop directory picker 或 CLI 启动参数选择的目录。
- `project_root`：稳定项目身份。优先 canonical git root，否则 selected root。
- `current_dir`：当前执行目录。默认 selected root，可在授权范围内变化。
- `git_root`：真实 git root，可为空。
- `trust`：项目本地配置、hooks、exec policy 是否可加载的信任状态。
- `filesystem_policy`：当前文件读写策略。
- `permission_profile`：当前能力档位。
- `approval_policy`：什么时候需要问用户。
- `additional_roots`：显式加入的额外目录。

### WorkspaceTrust

```python
class TrustLevel(StrEnum):
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"
    SESSION_ONLY = "session_only"

@dataclass
class WorkspaceTrust:
    level: TrustLevel
    trust_key: str
    source: Literal["default", "user_config", "session"]
    project_config_enabled: bool
```

第一版可以默认 `SESSION_ONLY`：

- 允许用户使用该目录作为 workspace。
- 不加载项目本地 `.codex-mini/config.toml` 的高风险能力。
- 不持久保存信任选择。

当前已引入 trusted project 第一片：

- 以 canonical git root 或 selected root 作为 trust key。
- 只有 trusted project 才加载 project-local config、hooks、exec policy。
- project-local config 有 denylist，不能设置模型 provider、API endpoint、credential path、外部 notifier 之类高风险项。
- 目前已有用户侧 trust store、project-local policy config 白名单读取，以及 trust/untrust WebSocket + desktop 最小入口；后续补 hooks/MCP gate 和策略编辑器。

### AdditionalRoot

```python
@dataclass
class AdditionalRoot:
    path: Path
    access: Literal["read", "write"]
    source: Literal["session", "user_config", "project_config", "cli"]
    approved_at: float
```

规则：

- additional root 必须显式加入，不能由工具执行自动创建。
- 写权限 additional root 必须来自用户显式选择；当前 desktop 入口会让用户在加入目录时选择 read/write。
- additional root 也要保护 metadata，例如其中的 `.git`、`.codex-mini`、`.env`。
- 不存在的 additional root 在 resume 时直接让恢复失败，返回 `workspace_error`；当前协议不做旧数据兼容或静默修复。

## 权限模型

### PermissionProfile

```python
class PermissionProfile(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"
    DANGER_FULL_ACCESS = "danger_full_access"
    EXTERNAL_SANDBOX = "external_sandbox"
```

建议默认：

- 未 trusted 或普通项目：`WORKSPACE_WRITE`
- 只读 review / plan：`READ_ONLY`
- 极少数用户显式选择：`DANGER_FULL_ACCESS`
- 外部容器或 IDE 自己提供沙箱时：`EXTERNAL_SANDBOX`

语义：

- `READ_ONLY`：允许读授权 roots，不允许写，命令默认只读 sandbox。
- `WORKSPACE_WRITE`：允许写 workspace 和显式 write roots，保护 metadata，网络默认 restricted。
- `DANGER_FULL_ACCESS`：Codex-mini 不加 filesystem sandbox，但仍保留危险命令拦截和审计。默认 UI 不暴露。
- `EXTERNAL_SANDBOX`：假定外层已经提供隔离，Codex-mini 仍做 policy 决策和审计。

### ApprovalPolicy

```python
class ApprovalPolicy(StrEnum):
    ON_REQUEST = "on_request"
    ON_FAILURE = "on_failure"
    NEVER = "never"
    UNLESS_TRUSTED = "unless_trusted"
    GRANULAR = "granular"
```

第一版优先实现：

- `ON_REQUEST`：写文件、编辑文件、执行命令、网络访问都问。
- `NEVER`：需要批准时直接拒绝，不弹窗。

后续扩展：

- `ON_FAILURE`：先在沙箱内执行，失败后询问是否扩大权限或追加规则。
- `UNLESS_TRUSTED`：trusted project 中安全命令可自动执行。
- `GRANULAR`：分别控制 sandbox escalation、rules approval、network approval、write approval。

### FileSystemPolicy

```python
class FileAccess(StrEnum):
    READ = "read"
    WRITE = "write"
    NONE = "none"

@dataclass
class FileSystemEntry:
    path: Path | SpecialPath | GlobPattern
    access: FileAccess

@dataclass
class FileSystemPolicy:
    entries: list[FileSystemEntry]
    protected_metadata_names: set[str]
    glob_scan_max_depth: int | None = None
```

内置 special paths：

- `:project_root`
- `:selected_root`
- `:current_dir`
- `:tmpdir`

默认 `workspace_write` 策略：

```text
:selected_root = write
:tmpdir = write
selected_root/.git = read
selected_root/.codex-mini = read
selected_root/.env = none
selected_root/.venv = read
selected_root/__pycache__ = none
```

注意：

- `.git` 和 `.codex-mini` 默认可读但不可写，除非用户显式授权。
- `.env` 默认 deny read/write，因为它经常包含 secret。
- `.venv` 默认可读不可写，避免命令污染环境。
- `__pycache__` 可以直接 deny 或忽略写入，第一版建议 deny write。

### NetworkPolicy

```python
class NetworkPolicy(StrEnum):
    RESTRICTED = "restricted"
    ENABLED = "enabled"
```

第一版：

- `run_command` 默认 `RESTRICTED`。
- `web_fetch` 默认关闭。
- `web_fetch` 启用后仍要求 approval。

后续：

- domain allowlist / denylist。
- localhost allowlist。
- network approval 写入 transcript。
- managed proxy 或本地网络审计。

### ExecPolicy

```python
class ExecDecision(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"

@dataclass
class ExecRule:
    decision: ExecDecision
    prefix: list[str]
    justification: str | None
    source: Literal["builtin", "user", "project", "session"]
```

命令判断顺序：

1. 解析 argv 或 shell command segment。
2. 匹配 explicit deny rule。
3. 匹配 dangerous command heuristic。
4. 匹配 explicit ask rule。
5. 匹配 explicit allow rule。
6. 匹配 known safe command heuristic。
7. 根据 approval policy 与 sandbox mode 决定 allow/ask/deny。

危险命令第一版保留并扩展：

- `rm -rf /`
- fork bomb
- `mkfs`
- `dd if=`
- `shutdown`
- `reboot`
- `curl | sh`
- `wget | sh`
- `sudo`
- `chmod -R 777`
- shell wrapper 下的危险片段

prefix rule 建议：

- 可以保存窄规则，例如 `["git", "status"]`、`["npm", "run", "test"]`。
- 禁止保存过宽规则，例如 `["python"]`、`["python", "-c"]`、`["bash"]`、`["sh", "-c"]`、`["node", "-e"]`、`["git"]`。
- destructive 命令不得建议持久 prefix rule。

## 工具决策流程

### read_file / grep

```text
tool call
  -> resolve path against current_dir
  -> normalize path without following unsafe escape
  -> check read permission
  -> check deny_read and protected secret
  -> execute read
  -> emit tool_call_result
```

拒绝条件：

- path 越过所有 readable roots。
- 命中 deny read。
- 读取 `.env` 等 secret path。

### write_file / edit_file

```text
tool call
  -> resolve path against current_dir
  -> check write permission
  -> protected metadata check
  -> if approval required: permission_request
  -> approved retry
  -> execute write in process
  -> append transcript
```

拒绝条件：

- path 越过 writable roots。
- 写入 `.git`、`.codex-mini`、`.env`、`.venv`。
- approval policy 为 `NEVER` 且需要批准。

### run_command

```text
tool call
  -> resolve cwd against current_dir
  -> cwd must be directory under authorized root
  -> exec policy decision
  -> if ask: permission_request
  -> approved retry
  -> build sandbox policy from filesystem/network policy
  -> run via sandbox adapter
  -> emit result
```

关键规则：

- 用户批准后仍进入 sandbox。
- `cwd` 不扩大 write roots。
- `cwd` 进入 additional root 必须该 root 已显式授权。
- 危险命令即使用户批准也可以继续 deny，除非未来有专门的 destructive confirmation 模式。

### web_fetch

```text
tool call
  -> network policy check
  -> URL validation
  -> optional domain policy
  -> permission_request
  -> fetch with timeout and byte limit
```

第一版保持默认关闭，后续补：

- SSRF 防护。
- localhost / private IP 默认拒绝。
- domain allowlist。
- response byte cap。

## Sandbox 生成

当前 `sandbox/macos_executor.py` 已改为 policy-driven。第一版实现会读取必要系统目录、临时目录和 `FileSystemPolicy.readable_roots`，并只写入 `FileSystemPolicy.writable_roots`、临时目录和 `/dev/null`。

目标接口：

```python
class SecureMacOSSandboxExecutor:
    def run(
        self,
        command: str,
        *,
        timeout_seconds: int,
        cwd: Path,
        filesystem_policy: FileSystemPolicy,
        network_policy: NetworkPolicy,
    ) -> CommandExecResult:
        ...
```

Seatbelt profile 生成规则：

- `file-read*` 只允许必要系统目录、临时目录和 readable roots。
- `file-write*` 只允许 writable roots。
- protected metadata 用 deny 或 require-not carveout。
- deny read glob 转成 read deny。
- network restricted 时不生成 network allow。
- network enabled 时才放开 outbound/inbound。
- `/dev/null` 和必要临时目录按 profile 配置放行。

第一阶段不需要完整实现 Codex 的 glob 与 special path 复杂度，但要先把最大偏差修掉：不要全盘读。

## Event Protocol

新增或明确这些事件：

```text
workspace_opened
workspace_changed
cwd_changed
workspace_policy_changed
permission_request
permission_decision
permission_decision_ack
session_suspended
session_resumed
```

`ready` 应包含：

```json
{
  "workspace": {
    "selected_root": "...",
    "project_root": "...",
    "current_dir": "...",
    "git_root": "...",
    "trust": {"level": "session_only"},
    "permission_profile": "workspace_write",
    "approval_policy": "on_request",
    "additional_roots": []
  },
  "tools": {}
}
```

`permission_request` 应包含：

```json
{
  "tool": "run_command",
  "arguments": "{}",
  "decision": "ask",
  "category": "command_approval",
  "reason": "...",
  "command": "npm run test",
  "cwd": ".",
  "policy": {
    "permission_profile": "workspace_write",
    "approval_policy": "on_request",
    "network": "restricted"
  },
  "suggested_prefix_rule": ["npm", "run", "test"]
}
```

## Transcript 与 Resume

每个 session 需要记录 workspace snapshot：

```json
{
  "type": "workspace_snapshot",
  "payload": {
    "selected_root": "...",
    "project_root": "...",
    "current_dir": "...",
    "git_root": "...",
    "trust_level": "session_only",
    "permission_profile": "workspace_write",
    "approval_policy": "on_request",
    "additional_roots": [],
    "filesystem_policy_hash": "..."
  }
}
```

resume 时：

- `selected_root` 不存在：返回 `workspace_error`，要求用户重新打开 workspace。
- `project_root` 不存在或与当前 selected root 推导结果不一致：返回 `workspace_error`。
- `current_dir` 不存在、缺失或不在已授权目录内：返回 `workspace_error`。
- `additional_roots` 缺失、格式错误或任一目录不可用：返回 `workspace_error`。
- 旧 `root` / `allowed_roots` 快照不再兼容；需要清理旧数据时显式运行 `make clean-sessions`。
- permission profile 当前不参与恢复兼容；后续产品化前不得在 resume 时静默升级。
- project trust 不应在 resume 时静默升级。

## AGENTS.md / Project Docs

目标行为：

- 从 `project_root` 到 `current_dir` 搜索 `AGENTS.md`。
- 顺序为 root 到 leaf。
- 不越过 `project_root`。
- 总大小默认 32 KiB。
- 后续可支持 `AGENTS.override.md`。

第一版接口：

```python
class ProjectInstructionsLoader:
    def load(self, workspace: WorkspaceContext) -> ProjectInstructions:
        ...
```

输出进入 system/developer context，但不允许项目文档改变 runtime 权限。权限只能通过 trust-gated config 或用户审批改变。

## 配置分层

建议配置层级从低到高：

```text
system defaults
user config
trusted project config
session overrides
runtime approval decisions
```

已落地的第一版：

- system defaults
- trusted project config 的白名单读取
- session overrides
- runtime approval decisions

第二版继续补 user config、trust UI 和策略编辑持久化。

project-local config denylist：

- model provider
- API base URL
- credential path
- notifier command
- arbitrary hook command
- disabling sandbox
- broad exec allow rules

## 实施计划

### PR1: WorkspaceContext v2

修改范围：

- `workspace/models.py`
- `workspace/manager.py`
- `workspace/validator.py`
- `server/runtime/websocket_context.py`
- `server/processors/request_dispatcher.py`
- `server/protocol/events.py`
- `tests/test_workspace.py`
- `tests/test_server_websocket.py`

交付：

- 新增 `selected_root`、`project_root`、`current_dir`、`trust`。
- `open_workspace` 返回完整 workspace dict。
- session metadata 写入 workspace snapshot。
- current_dir 默认 selected root。
- git root 推导独立于 selected root。
- 前端类型一次性迁移到 v2 payload，不保留旧 `root` / `allowed_roots` 兼容路径。

验收：

- 打开 git repo 子目录时，project_root 可指向 git root，selected_root 保持用户选择目录。
- 打开非 git 目录时，project_root 等于 selected_root。
- resume metadata 能看到 workspace snapshot。

状态：

- 已完成第一版实现和单元测试覆盖。
- PR6 已补齐第一版 resume 重新验证。

### PR2: Unified Security Context

修改范围：

- `security/policy.py`
- `security/permissions.py`
- `tools/core/context.py`
- `tools/core/types.py`
- `tools/filesystem/read_file.py`
- `tools/filesystem/write_file.py`
- `tools/filesystem/edit_file.py`
- `tools/search/grep.py`
- `tools/shell/run_command.py`
- `tools/core/registry.py`
- `tools/core/runner.py`

交付：

- 新增 `FileSystemPolicy`、`PermissionProfile`、`ApprovalPolicy`。
- 新增 `NetworkPolicy`，先供 Seatbelt profile 生成使用。
- 所有 path resolver 统一使用 current_dir + workspace policy。
- `.codex-mini` 加入 protected metadata。
- `.env` 默认 deny read/write。
- additional roots 先建模型，并在 PR5 接入第一版 WebSocket/desktop 入口。

验收：

- read/write 越界被拒。
- 读 `.env` 被拒。
- 写 `.git`、`.codex-mini`、`.venv` 被拒。
- `run_command.cwd` 不扩大权限。

状态：

- 已完成第一版实现和单元测试覆盖。
- additional roots 已有第一版 desktop/UI 协议入口，并会在 resume 时重新验证；后续补 project-local 持久配置边界。
- Seatbelt profile 已从 filesystem/network policy 生成，后续继续补更细的 glob/special path 规则。

### PR3: Policy-driven Seatbelt

修改范围：

- `sandbox/macos_executor.py`
- `tools/shell/run_command.py`
- `security/permissions.py`
- `tests/test_macos_executor.py`

交付：

- `SecureMacOSSandboxExecutor.run()` 接收 filesystem/network policy。
- Seatbelt profile 按 readable/writable roots 生成。
- 去掉固定 `allow file-read*`。
- network restricted 默认无 network allow。

验收：

- sandbox profile 中 read roots 和 write roots 可断言。
- 命令可读 workspace，但不能读未授权目录。
- 命令可写 workspace，但不能写 protected metadata。

状态：

- 已完成第一版实现和单元测试覆盖。
- 当前仍是 roots + protected name 级别；复杂 glob、special path 展开和真实端到端 sandbox 隔离测试留到后续收口。

### PR4: Exec Policy Rules

修改范围：

- `security/exec_policy.py`
- `security/policy.py`
- `server/runtime/turn_runner.py`
- `desktop/src/renderer/src/components/PermissionDialog.vue`
- `desktop/src/renderer/src/stores/runtime.ts`
- `tests/test_exec_policy.py`

交付：

- prefix allow/ask/deny rule。
- dangerous prefix suggestions denylist。
- permission request 可携带 `suggested_prefix_rule`。
- 用户批准可选择仅本次或本 session。

验收：

- `npm run test` 可以被 session allow。
- `python -c` 不建议持久 prefix rule。
- `rm -rf /` 直接 deny。
- approval policy `NEVER` 下 ask 变 deny。

状态：

- 已完成第一版实现和单元测试覆盖。
- WebSocket `permission_decision` 支持 `scope=session` 与 `prefix_rule`，前端在存在 `suggested_prefix_rule` 时展示本会话同类命令批准入口。
- 已有 trust-gated project-local exec policy 配置文件第一片，但还没有跨 session 持久运行时批准规则。

### PR5: Workspace Protocol and Desktop UI

修改范围：

- `server/app.py`
- `desktop/src/renderer/src/stores/runtime.ts`
- `desktop/src/renderer/src/types/runtimeEvents.ts`
- `desktop/src/renderer/src/components/TitleBar.vue`

交付：

- `change_directory`。（已完成第一版）
- `add_dir`。（已完成第一版）
- `workspace_policy_changed`。（已完成第一版）
- TitleBar 显示 selected root/current dir，并提供切换目录和加入额外目录入口。（已完成第一版）
- 权限弹窗展示 profile、cwd、command、prefix suggestion。（prefix suggestion 已完成；profile/cwd 还需继续补齐）

验收：

- UI 能显示 selected root 和 current dir。（已完成）
- 切 cwd 不创建新 session。（已完成）
- open workspace 创建新 session。（已完成）
- add dir 必须来自用户显式选择，并进入 runtime policy。（已完成第一版）

### PR6: Resume and Project Docs

修改范围：

- `session/recovery.py`
- `session/store.py`
- `workspace/manager.py`
- `server/app.py`
- 新增 `workspace/instructions.py`

交付：

- workspace snapshot 写入 session metadata，policy 变化写入 `workspace_policy_changed` transcript。（已完成第一版）
- resume 重新验证 workspace。（已完成第一版）
- `AGENTS.md` 从 project_root 到 current_dir 分层加载。（已完成第一版）
- 依赖 current_dir 的 system prompt 刷新机制。（已完成第一版）

验收：

- 删除 current_dir 后 resume 返回 `workspace_error`。（已完成）
- 删除 additional root 后 resume 返回 `workspace_error`。（已完成）
- project docs 不越过 project_root。（已完成）

### PR7: Trust-Gated Project Config

修改范围：

- `workspace/trust.py`
- `workspace/project_config.py`
- `workspace/manager.py`
- `server/runtime/session_state.py`
- `server/runtime/websocket_context.py`
- `context/permissions_context.py`
- `tests/test_workspace.py`

交付：

- 用户侧 trust store 第一版。（已完成）
- WebSocket `trust_workspace` / `untrust_workspace` 控制事件。（已完成第一版）
- 桌面端 trust level 展示与最小 trust/untrust 入口。（已完成第一版）
- 默认 `session_only` workspace 不读取 `.codex-mini/config.toml`。（已完成）
- trusted project 可读取白名单 permission / exec policy config。（已完成第一版）
- project-local config denylist：model/API/credential/hooks/sandbox 等敏感字段直接拒绝。（已完成第一版）
- 过宽 exec allow rule 直接拒绝。（已完成第一版）
- resume 不会因为当前 trust store 变成 trusted 而静默升级旧 session 权限。（已完成）

验收：

- 未 trusted 项目里的 `.codex-mini/config.toml` 被忽略。（已完成）
- trusted 项目里的 read-only / approval / exec deny rule 会进入 ToolRunner。（已完成）
- trusted 项目的敏感配置和过宽 allow rule 返回 `workspace_error`。（已完成）
- trusted snapshot 在当前 trust store 不可信时恢复失败。（已完成）
- trust workspace 后会重新加载 project-local policy，并通过 `workspace_policy_changed` 推送给前端。（已完成）
- invalid project config 会阻止 trust 写入用户侧 trust store。（已完成）

## 测试矩阵

### Unit Tests

- workspace path validation。
- project root 推导。
- current_dir 解析。
- additional root 校验。
- filesystem policy read/write/none。
- protected metadata。
- exec policy prefix match。
- approval policy 决策。
- Seatbelt profile 生成。

### Runtime Tests

- WebSocket `open_workspace`。
- WebSocket `change_directory`。
- WebSocket `add_dir` / `workspace_policy_changed`。
- permission request approve retry。
- permission request deny result。
- session suspend/resume。
- transcript workspace snapshot。

### macOS Sandbox Tests

- approved command 可以读 workspace 文件。
- approved command 不能读 deny read 文件。
- approved command 可以写 workspace 普通文件。
- approved command 不能写 `.git`、`.codex-mini`、`.env`。
- network restricted 下外网命令失败。

### Desktop Smoke Tests

- 打开 workspace。
- 切换 cwd。
- 执行需要权限的 command。
- 拒绝权限后模型收到 deny。
- resume 后 workspace 状态展示正确。

## 不做什么

第一轮不做：

- 完整 worktree 产品 UI。
- 完整 project trust 策略编辑器。
- 复杂 glob policy 编辑器。
- MCP tool 权限治理。
- network proxy。
- cloud policy。

但数据模型要预留这些扩展，避免后面重拆。

## 推荐底线

当前 runtime 内核边界已经按以下五点打准，后续再补 trust gate 和产品化 UI：

1. `project_root` 和 `current_dir` 分离。
2. 所有工具统一走 `FileSystemPolicy`。
3. Seatbelt 从 policy 生成。
4. approval 决策写入 transcript。
5. resume 重新验证 workspace snapshot。

这五件事完成后，Codex-mini 的 workspace 和权限策略已经和 Codex / Claude Code 的工程方向完成第一阶段对齐。
