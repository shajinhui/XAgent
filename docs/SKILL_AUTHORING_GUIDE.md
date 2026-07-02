# Skill Authoring Guide

本文档说明 XCode 当前 Skills 系统里，一个可维护 skill 应该怎么写、怎么组织资源、怎么被导入和更新。

## 核心原则

Skills 是给模型按需读取的能力包，不是普通项目文档。系统提示里默认只展示 `name / description / path`，完整 `SKILL.md` 只有在用户显式选择、`$skill-name` 命中，或模型决定读取时才进入上下文。

写 skill 时遵守三点：

- `description` 负责触发：写清楚这个 skill 做什么、什么时候该用。
- `SKILL.md` 负责流程：只放必要步骤和资源导航，不塞长篇背景。
- 细节放资源：长规则、示例、模板、脚本放到标准目录，按任务需要读取。

## 推荐结构

```text
my-skill/
├── SKILL.md
├── references/
│   └── README.md
├── examples/
│   └── README.md
├── templates/
├── scripts/
└── assets/
```

当前支持的标准资源目录：

- `references/`：详细规则、领域知识、API/schema、长流程。
- `examples/`：典型用户请求、期望输出、示例输入。
- `templates/`：可复用文本、配置、代码或文档模板。
- `scripts/`：可复用脚本。模型读取或执行脚本仍必须走普通工具/权限链路。
- `assets/`：输出所需素材或样板文件。

## SKILL.md Frontmatter

必填：

```yaml
---
name: my-skill
description: Use when ...
---
```

可选：

```yaml
metadata:
  short-description: Short label for menus.
  icon: pdf
version: 1.0.0
dependencies:
  tools:
    - read_file
    - grep
policy:
  allow_implicit_invocation: true
```

字段说明：

- `name`：64 字符以内，建议小写、短横线命名。
- `description`：1024 字符以内，要包含触发场景。
- `metadata.short-description`：菜单或列表里的短描述。
- `metadata.icon`：可选图标提示，桌面端会识别 `pdf`、`github`、`browser`、`spreadsheet`、`presentation`、`code`、`tool` 等短名称；也可以填 1-4 个字符作为文本图标。
- `version` / `metadata.version`：用于 registry 和 update 提示。
- `dependencies.tools`：只做展示检查，缺失工具会显示提醒，不会自动安装。
- `policy.allow_implicit_invocation=false`：不进入隐式推荐 catalog，但仍可显式选择。

## 写作模式

一个好的 `SKILL.md` 通常包含：

```markdown
# My Skill

## Workflow

1. Inspect the task and decide whether references are needed.
2. Read `references/README.md` only when detailed rules matter.
3. Use `templates/example.md` when producing the final artifact.

## Resources

- `references/README.md`: detailed rules and edge cases.
- `examples/README.md`: representative prompts and outputs.
- `templates/example.md`: output template.
```

不要把所有规则复制进 `SKILL.md`。如果某段内容只有部分任务需要，就放进 `references/` 并在 `SKILL.md` 里写清楚何时读取。

## 创建和管理

桌面端支持：

- 新建 basic skill：只创建 `SKILL.md`。
- 新建 standard package：创建标准资源目录和 README 占位。
- 导入本地 skill package：导入前会校验 `SKILL.md`、路径、symlink 和包大小。
- 导入源可以是 workspace 外的显式本地路径；导入只复制 package，不扩大 workspace 文件权限。
- GitHub URL 安装：下载 archive，只抽取指定 package 路径。
- Curated registry 安装和更新：从 `openai/skills` 的 `skills/.curated` 列表安装，支持 `GITHUB_TOKEN` / `GH_TOKEN` header。

## 安全边界

- repo skills 只从当前 workspace 路径链上的 `.agents/skills/**/SKILL.md` 加载。
- user skills 从 `~/.agents/skills/**/SKILL.md` 加载，但不会加入普通 workspace 文件权限。
- symlink、`..` 越界路径、越界资源会被拒绝。
- model-facing `read_skill` / `read_skill_resource` 只读，不扩大普通 `read_file` 权限。
- `dependencies.tools` 不自动安装 MCP/plugin。

## 示例

见 `docs/examples/skills/standard-package/`。该示例展示了：

- `version`
- `dependencies.tools`
- 标准目录
- `references/` 按需读取
- `templates/` 输出模板
