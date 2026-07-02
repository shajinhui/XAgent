# Skills Architecture

本文档说明 XCode 当前 Skills 模块的架构、加载流程和运行流程。它回答的是“系统怎么设计、代码怎么走”，而不是“用户怎么写一个 skill”。写作规范见 docs/SKILL_AUTHORING_GUIDE.md，最短使用路径见 docs/SKILLS_QUICKSTART.md。

## 设计目标

Skills 是一层独立的上下文能力，不是普通工具，也不是 workspace 文件权限的扩展。

核心原则：

- Progressive disclosure：系统提示只暴露 bounded catalog metadata，也就是 name / description / path。完整 SKILL.md 只有在显式选择、$skill-name 命中，或模型决定调用 read_skill 时才进入当前 turn。
- 权限隔离：user skill 文件不会加入普通 FileSystemPolicy，模型只能通过受限的 read_skill / read_skill_resource 读取 skill 内容。
- 本轮有效：显式 skill 注入只作用于当前 user_input turn，不写入长期 history.messages，恢复会话时也不会自动恢复旧 skill 内容。
- 管理和使用分离：skills/ 承载 catalog、selection、resource、management 核心逻辑；tools/skills/ 只是只读工具 wrapper；桌面端通过 WebSocket 控制事件管理 skills。

## 模块分层

~~~text
desktop Skill UI
  -> WebSocket control packets
    -> server/processors/skill_processor.py
      -> skills/manager.py
        -> skills/management.py / installer.py / registry.py / validator.py

model turn
  -> server/app.py user_input path
    -> skills/manager.py load catalog
    -> skills/selection.py explicit selection
    -> session/turn_context.py temporary injection
    -> server/runtime/turn_runner.py
      -> tools/skills/read_skill.py
      -> tools/skills/read_skill_resource.py
        -> skills/resources.py
~~~

### skills/

skills/ 是核心领域层。

- models.py：定义 SkillMetadata、SkillLoadOutcome、SkillLoadError、SkillInjection、TurnSkills、SkillScope、SkillSource、SkillPolicy。
- loader.py：扫描 repo/user skill roots，解析 SKILL.md YAML frontmatter，生成 catalog。
- manager.py：按 project_root/current_dir 缓存 catalog，刷新 SkillResourceResolver，统一包装 create/import/install/update/delete 后的 reload。
- render.py：把可隐式调用的 skills 渲染成 skills_instructions catalog block，默认预算 8000 字符。
- selection.py：解析 user_input.selected_skills 和文本里的 $skill-name，生成本轮显式注入。
- resources.py：只读 resolver，只允许读取当前 catalog 中 skill 目录内的 SKILL.md 和相对资源。
- management.py：处理显式用户触发的 create/import/update/delete，以及标准资源文件 list/read/save/delete。
- installer.py：记录 .skill-install.json，解析 GitHub source，下载 archive，只抽取目标 package。
- registry.py：读取 openai/skills curated registry，并标注本地 installed/update 状态。
- validator.py / spec.py：定义并校验本地 skill package 结构、大小、symlink、路径穿越和标准目录。
- dependencies.py：检查 dependencies.tools 的展示状态，不自动安装或启用缺失工具。

### tools/skills/

模型只看到两个 read-only 工具：

- read_skill(name?: str, path?: str)：读取当前 catalog 中一个 skill 的完整 SKILL.md。
- read_skill_resource(skill_path: str, resource: str)：读取指定 skill 目录内的相对资源。

这两个 wrapper 不承载 catalog 或安全逻辑，只从 ToolExecutionContext.skill_resource_resolver 拿 resolver。它们不需要审批，不扩大 read_file 的 workspace 权限。

### server/

- server/runtime/websocket_context.py：持有连接级 SkillManager，在 current_system_prompt() 中把 skills catalog 拼进 system prompt，并在 refresh_tool_runner() 中把 resolver 注入工具上下文。
- server/app.py：处理 user_input，加载 catalog，解析显式选择，创建 TurnSkills 和 TurnContext，发送 skill_used / skill_warning。
- server/runtime/turn_runner.py：每次模型请求使用 TurnContext.model_messages()，保证当前 turn 的临时 skill 注入持续存在；工具读取到的 skill 内容在 turn 结束后从长期 history 中替换为占位。
- server/processors/request_dispatcher.py：只做 packet 分发。
- server/processors/skill_processor.py：处理所有不进入模型 turn 的 skill 管理 packet。

### desktop/

桌面端只负责展示和发送控制事件：

- 主侧边栏“插件”页面承载 Skills catalog、选择和生命周期管理。
- SkillMenu.vue 负责列表、搜索、选择、新建、导入、安装、编辑、删除入口。
- SkillEditor.vue 负责新建、导入、编辑表单和标准资源文件管理。
- ChatComposer.vue 的 + 菜单提供本轮快速选择，输入框内显示已选 skill chip。
- runtime.ts 维护 skill state 和 WebSocket actions，不另拆 store。

## 存储位置

当前只实现本地文件型 skills。

~~~text
repo skill:
  {project_root}/.agents/skills/**/SKILL.md

user skill:
  ~/.agents/skills/**/SKILL.md
~~~

标准 package 推荐目录：

~~~text
my-skill/
├── SKILL.md
├── references/
├── examples/
├── templates/
├── scripts/
└── assets/
~~~

GitHub 或本地导入产生的安装来源记录：

~~~text
my-skill/.skill-install.json
~~~

## SKILL.md 元数据

loader.py 解析 YAML frontmatter。

必填字段：

~~~yaml
---
name: rotate-pdf
description: Use when the user needs to rotate PDF pages.
---
~~~

可选字段：

~~~yaml
metadata:
  short-description: Rotate PDF pages
  icon: pdf
version: 1.0.0
policy:
  allow_implicit_invocation: true
dependencies:
  tools:
    - read_file
~~~

当前校验规则：

- name 必填，最长 64 字符。
- description 必填，最长 1024 字符。
- metadata.short-description 可选，最长 1024 字符。
- metadata.icon 或顶层 icon 可选，最长 64 字符。
- version 或 metadata.version 可选，最长 64 字符。
- dependencies.tools 只做展示检查，不自动安装 MCP/plugin。
- policy.allow_implicit_invocation=false 表示不进入隐式 catalog，但仍允许用户显式选择或 $skill-name 使用。

## Catalog 加载流程

Catalog 加载由 SkillManager.load_for_workspace() 驱动。

~~~text
WebSocketRuntimeContext.current_system_prompt()
  -> SkillManager.load_for_workspace(workspace)
    -> load_skills_for_workspace(workspace)
      -> skill_roots_for_workspace(project_root, current_dir)
      -> load_skills_from_roots(roots)
        -> _iter_skill_files(root)
        -> parse_skill_file(path, root)
    -> SkillResourceResolver.update(outcome)
  -> render_available_skills(outcome)
  -> append skills_instructions to system prompt
~~~

### 1. 计算可见 roots

skills/loader.py 从两个来源构造扫描 root：

- repo：从 project_root 到 current_dir 的路径链，每一层都检查 .agents/skills。
- user：~/.agents/skills。

如果 current_dir 不在 project_root 内，只扫描 project_root 的 repo skills，再追加 user skills。

### 2. 扫描 SKILL.md

扫描使用 os.walk(..., followlinks=False)，并有边界：

- 不跟随 symlink。
- 忽略点目录。
- 最大扫描深度 MAX_SCAN_DEPTH = 6。
- 每个 root 最多扫描 MAX_SKILLS_DIRS_PER_ROOT = 2000 个目录。
- SKILL.md 本身不能是 symlink。
- canonical path 必须仍在当前 skill root 内。

单个 skill 解析失败不会阻断整个 catalog。失败会进入 SkillLoadOutcome.errors，前端通过 skills_listed.errors[] 或 skill_warning 展示。

### 3. 解析 metadata

parse_skill_file() 读取完整文件，但只把 frontmatter 里的 metadata 放进 SkillMetadata。正文不会进入 system prompt。

SkillMetadata.as_dict() 会输出：

- name
- description
- path
- scope
- source
- enabled
- policy
- 可选 short_description
- 可选 icon
- 可选 version
- 可选 dependencies
- 可选 install

### 4. 缓存和 resolver 刷新

SkillManager 使用下面的 key 缓存 catalog：

~~~text
(workspace.project_root.resolve(), workspace.current_dir.resolve())
~~~

每次 load_for_workspace() 返回 outcome 后，都会调用：

~~~text
self.resolver.update(outcome)
~~~

这样 read_skill 和 read_skill_resource 总是基于当前 workspace/current_dir 的 catalog 做权限判断。

### 5. 渲染 system prompt

render_available_skills() 只渲染 outcome.visible_skills()，也就是 allow_implicit_invocation=true 的 skills。

输出形态：

~~~text
<skills_instructions>
## Skills
...
### Available skills
- rotate-pdf: Use when ... (file: /abs/path/SKILL.md)
### How to use skills
...
</skills_instructions>
~~~

预算超出时，超出的 skill 会被省略，并返回 warning。完整 SKILL.md 不会因为 catalog 渲染进入上下文。

## 显式运行流程

显式运行指用户通过 UI 勾选 skill，或在消息中写 $skill-name。

~~~text
client user_input
  content: "帮我旋转这个 PDF"
  selected_skills: [{ name, path }]

server/app.py
  -> refresh_history_system_prompt()
  -> history.append_user_message(...)
  -> skill_manager.load_for_workspace(...)
  -> collect_selected_skills(...)
  -> build_skill_injections(...)
  -> TurnContext.from_runtime(... turn_skills ...)
  -> run_turn(...)

TurnContext.model_messages()
  -> history before user message
  -> temporary <skill> messages
  -> current user message
  -> remaining history
~~~

关键点：

- selected_skills 里如果有 path，优先按 path 精确匹配当前 catalog。
- 只有 name 时，必须在当前 catalog 中唯一，否则不选择。
- 文本 $skill-name 也只在名称唯一时选择。
- 选择失败或名称歧义会进入 TurnSkills.warnings，并发送 skill_warning。
- build_skill_injections() 会通过 resolver 读取完整 SKILL.md，包成 <skill>...</skill> user message。
- 注入位置是当前用户消息之前，由 TurnContext.current_user_message_index 控制。
- 注入不写回 history.messages，只在 model_messages() 动态返回时出现。

显式 skill 被加载后会记录并发送：

~~~text
skill_used:
  turn_id
  name
  path
  scope
  invocation_type: explicit
~~~

## 隐式运行流程

隐式运行指用户没有显式选择，但任务明显匹配 catalog 中某个 skill 的 description。模型先看到的是 skills_instructions 里的 metadata。

~~~text
model sees catalog metadata
  -> decides a skill matches
  -> calls read_skill(name/path)
    -> tools/skills/read_skill.py
      -> SkillResourceResolver.read_skill(...)
  -> model receives full <skill>...</skill>
  -> if SKILL.md references resources
    -> calls read_skill_resource(skill_path, resource)
~~~

read_skill 成功后工具结果 metadata 会包含：

~~~text
skill_used: true
skill_name
skill_path
skill_scope
invocation_type: implicit
~~~

turn_runner.py 据此记录并发送 skill_used 事件。工具结果仍会在当前 turn 内作为 role=tool 回灌给模型，保证模型能继续执行 skill 指令。

## 资源读取流程

Skill resource 由 SkillResourceResolver.read_resource() 读取。

~~~text
read_skill_resource(skill_path, "references/foo.md")
  -> resolve skill_path in current catalog
  -> require resource is relative
  -> reject absolute path
  -> reject ".."
  -> reject symlink component
  -> require resolved path stays inside skill.directory
  -> reject directory
  -> read text with max char cap
~~~

当前资源读取上限是 MAX_SKILL_RESOURCE_CHARS = 40000。超出后返回截断内容和提示。

repo skill 的资源也可以在普通 workspace 权限允许时通过普通文件工具读取；user skill 资源不会加入 workspace 文件权限，只能通过 read_skill_resource 读取。

## Turn 结束后的清理和持久化

Skills 内容不应该污染长期上下文。

当前策略：

- 显式注入：只在 TurnContext.model_messages() 动态拼接，不写入 history.messages。
- 隐式 read_skill / read_skill_resource：工具结果在当前 turn 内回灌给模型；turn 结束后 _strip_skill_tool_results() 把长期 history 中对应 tool message 内容替换为占位。
- transcript：emit_tool_result() 会把 read_skill / read_skill_resource 的结果内容替换成占位再写入 durable transcript。
- recovery：session/recovery.py 只恢复 user_message、assistant_message 和 tool_call_result，不会把 skill_used / skill_warning 恢复成模型消息。

占位示例：

~~~text
[skill content omitted from transcript]
[skill resource content omitted from transcript]
[skill content omitted from history after this turn]
[skill resource content omitted from history after this turn]
~~~

## 管理流程

Skill 管理是用户显式控制事件，不进入模型工具层。

SkillRequestProcessor 当前处理：

- list_skills
- list_installable_skills
- get_skill
- create_skill
- import_skill
- install_skill
- install_registry_skill
- reinstall_skill
- update_skill
- delete_skill
- list_skill_resources
- get_skill_resource
- save_skill_resource
- delete_skill_resource

### 创建

~~~text
create_skill packet
  -> SkillRequestProcessor._handle_create_skill()
  -> SkillManager.create_skill()
  -> skills.management.create_skill()
  -> write {repo|user}/.agents/skills/{slug}/SKILL.md
  -> parse_skill_file()
  -> reload catalog
  -> skill_saved + skills_listed payload
~~~

创建目标只允许：

- repo：workspace.project_root/.agents/skills
- user：~/.agents/skills

可选 package_template：

- basic：只创建 SKILL.md。
- standard：创建标准资源目录和 README 占位。

### 本地导入

~~~text
import_skill packet
  -> resolve explicit source_path
  -> validate package
  -> copy package into repo/user .agents/skills
  -> write .skill-install.json with local source
  -> reload catalog
~~~

本地导入源可以是用户显式给出的本机绝对路径，也可以是相对 workspace.current_dir 的路径。导入只读取、校验并复制 package，不会把来源目录加入普通 workspace 文件权限。

导入会拒绝：

- 源路径不是目录或 SKILL.md。
- 源目录缺少可读 SKILL.md。
- 源路径或文件包含 symlink。
- package 校验失败。
- 目标 skill 已存在。

### GitHub 安装和重新安装

~~~text
install_skill packet
  -> parse GitHub repo/tree URL
  -> download archive
  -> extract only requested package path
  -> validate package
  -> copy into repo/user .agents/skills
  -> write .skill-install.json with github source
~~~

安全约束：

- 只接受 github.com URL。
- archive 最大 50 MB。
- zip entry 必须是安全相对路径。
- 拒绝 symlink、路径穿越、非法 package。
- 不执行远程脚本。
- 如果存在 GITHUB_TOKEN 或 GH_TOKEN，请求会带 Authorization header，但 token 不会写入安装记录。

reinstall_skill 只允许 GitHub-installed skill。它会从 .skill-install.json 读取来源，下载新包，校验后用临时目录替换原目录，失败时保留备份回滚。

### Curated registry

list_installable_skills 读取 openai/skills 的 skills/.curated，返回可安装条目，并根据本地 catalog 标注：

- installed
- installed_path
- installed_version
- update_available
- dependency_status

install_registry_skill 复用 registry entry 的 GitHub source，走同一条 GitHub 安装链路。

### 编辑和删除

get_skill / update_skill 只管理 SKILL.md 正文和 frontmatter 字段。SKILL.md 文件本身通过 skill editor 管理，不通过 resource editor 管理。

标准资源文件由下面事件管理：

- list_skill_resources
- get_skill_resource
- save_skill_resource
- delete_skill_resource

资源管理只能访问标准目录：

- references/
- examples/
- templates/
- scripts/
- assets/

资源路径必须是相对路径，不能包含 ..，不能是 symlink，不能越过当前 skill 目录。

## 事件和协议边界

### 请求 packet

管理类：

~~~text
list_skills
list_installable_skills
get_skill
create_skill
import_skill
install_skill
install_registry_skill
reinstall_skill
update_skill
delete_skill
list_skill_resources
get_skill_resource
save_skill_resource
delete_skill_resource
~~~

运行类：

~~~text
user_input.selected_skills
plan_confirm.selected_skills
~~~

模型工具：

~~~text
read_skill
read_skill_resource
~~~

### 响应事件

~~~text
skills_listed
installable_skills_listed
skill_loaded
skill_saved
skill_deleted
skill_error
skill_resources_listed
skill_resource_loaded
skill_resource_saved
skill_resource_deleted
skill_used
skill_warning
~~~

## 安全边界总结

- Skills 不扩大 workspace 普通文件权限。
- user skills 不加入 FileSystemPolicy。
- read_skill / read_skill_resource 是只读工具，不需要审批。
- model-facing skill tools 只能读取当前 catalog 中的 skill。
- name 选择存在歧义时拒绝，path 精确匹配优先。
- 所有文件型 skill 路径都要 canonical 后仍位于 skill root 内。
- loader、validator、resource resolver 和 management 都拒绝 symlink。
- GitHub 安装只下载和解包，不执行远程代码。
- dependencies.tools 只做状态展示。
- transcript 和 recovered context 不持久化完整 skill 内容。

## 典型调用链

### 打开 workspace 后 system prompt 更新

~~~text
open_workspace / change_directory / resume_session
  -> WebSocketRuntimeContext.refresh_history_system_prompt()
  -> current_system_prompt()
  -> render_system_prompt_with_project_instructions()
  -> skill_manager.load_for_workspace()
  -> render_available_skills()
  -> history.replace_system_prompt()
~~~

### 用户勾选 skill 并发送消息

~~~text
ChatComposer selected skill chip
  -> runtime.selectedSkillPaths
  -> user_input.selected_skills
  -> collect_selected_skills()
  -> build_skill_injections()
  -> TurnContext.model_messages()
  -> model sees full SKILL.md before current user message
~~~

### 模型隐式使用 skill

~~~text
model sees catalog line
  -> tool_call read_skill({ path })
  -> resolver reads SKILL.md
  -> skill_used implicit event
  -> model follows SKILL.md
  -> optional read_skill_resource(...)
  -> final answer
  -> skill tool content stripped from long-term history
~~~

## 扩展点

当前枚举已经预留但未产品化：

- SkillScope.ADMIN
- SkillScope.SYSTEM
- SkillScope.PLUGIN
- SkillScope.ORCHESTRATOR
- SkillSource.ENVIRONMENT_RESOURCE
- SkillSource.ORCHESTRATOR_RESOURCE
- SkillSource.CUSTOM_RESOURCE

后续如果接入插件、orchestrator 或远端 skill source，应保持现有分层：

- discovery/provider 逻辑进入 skills/ 域。
- model-facing 能力仍只通过受限 read-only wrapper 暴露。
- system prompt 仍只展示 bounded metadata。
- 完整 skill 内容仍只在当前 turn 按需读取，并避免持久化到 durable transcript。

## 验证入口

常用验证命令：

~~~bash
.venv/bin/python -m unittest tests.test_skills tests.test_server_websocket
.venv/bin/python -m compileall agent_loop.py tools security sandbox server session workspace skills tests
pnpm --dir desktop run build
git diff --check
~~~

重点覆盖：

- loader 解析和错误收集。
- repo path chain 与 user root 扫描。
- symlink、..、越界资源拒绝。
- $skill-name 唯一性选择。
- system prompt bounded catalog。
- 显式 skill 注入不污染 history.messages。
- tool-call 后本轮 skill 内容可见，turn 结束后被清理。
- resume 后不恢复旧 skill 注入。
- 管理事件 create/import/install/update/delete 后 reload catalog。
