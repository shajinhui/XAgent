"""生成对话摘要（使用小模型）。"""
from __future__ import annotations

import os
from typing import Optional


async def generate_summary(content: str) -> str:
    """使用小模型生成摘要。

    Args:
        content: 待总结的对话内容

    Returns:
        生成的摘要文本
    """
    try:
        import litellm

        model = os.getenv("SUMMARIZE_MODEL", "claude-3-haiku-20240307")

        response = await litellm.acompletion(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": f"""总结以下对话历史，保留关键信息：

对话内容：
{content}

要求：
1. 提取用户的主要意图和目标
2. 列出已完成的关键操作和修改
3. 记录重要的决策和理由
4. 标注当前的阻塞点或待解决问题
5. 保留用户表达的偏好

用简洁的中文回答，控制在 500 tokens 内。""",
                }
            ],
            max_tokens=500,
            timeout=15,
        )
        return response.choices[0].message.content or "（摘要生成失败）"

    except Exception as e:
        # 失败时使用简单规则摘要
        return _fallback_summary(content)


def _fallback_summary(content: str) -> str:
    """规则摘要（当模型调用失败时使用）。"""
    lines = content.split("\n")

    # 提取前 10 条和最后 5 条关键信息
    summary_lines = []
    if len(lines) > 15:
        summary_lines.append("## 早期对话")
        summary_lines.extend(lines[:10])
        summary_lines.append("\n...[中间对话已压缩]...\n")
        summary_lines.append("## 最近对话")
        summary_lines.extend(lines[-5:])
    else:
        summary_lines = lines

    return "\n".join(summary_lines)[:2000]  # 限制长度
