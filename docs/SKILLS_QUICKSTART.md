# Skills Quickstart

这份文档只讲怎么把 Skills 跑起来，不讲扩展生态。

## 1. 创建一个 Skill

在桌面端聊天输入框左下角打开工具菜单，进入 `Skills`：

1. 点击 `+`
2. 范围选择 `repo` 或 `user`
3. 模板选择 `standard package`
4. 填写名称和描述
5. 保存

`repo` skill 会创建在当前项目的 `.agents/skills/` 下。`user` skill 会创建在用户级 skill 目录下，不会变成普通 workspace 文件权限。

## 2. 写最小 SKILL.md

一个最小 skill 长这样：

```markdown
---
name: demo-skill
description: Use when the user asks for demo-specific guidance.
---

# Demo Skill

## Instructions

- Follow the demo workflow.
- Read `references/README.md` only when detailed rules are needed.
```

`description` 很重要，它决定模型什么时候会考虑使用这个 skill。

## 3. 添加资源

编辑 skill 时可以在资源面板新增文件，例如：

```text
references/guide.md
templates/output.md
examples/README.md
```

资源只在当前 skill 目录内可读，不能用 `..` 越界。

## 4. 使用 Skill

有两种方式：

- 在 Skills 菜单里勾选 skill，然后发送消息。
- 在消息里写 `$demo-skill`。

当前轮会把完整 `SKILL.md` 临时注入到用户消息前。注入内容不会写入长期 history，也不会在 resume 后自动恢复。

## 5. 读取资源

模型需要资源时会使用：

- `read_skill`
- `read_skill_resource`

普通 repo 文件仍然走普通文件工具；user skill 资源不会扩大 workspace 文件权限。

## 6. 安装和更新

Skills 菜单支持：

- 从 GitHub URL 安装
- 从 curated registry 安装
- 重新安装 GitHub-installed skill
- registry 条目标记为 `可更新` 时直接更新

如果设置了 `GITHUB_TOKEN` 或 `GH_TOKEN`，GitHub 请求会带 token header，但 token 不会写入安装记录。

本地导入的来源路径可以在 workspace 外。导入只会校验并复制 skill package，不会把来源目录加入普通 workspace 文件权限。

## 7. 什么时候算能用

能完成下面这条链路，就说明 Skills 基本可运行：

```text
创建 skill -> 写 SKILL.md -> 添加资源 -> 选择 skill -> 发送消息 -> 模型读取 skill/resource
```
