"""Memory 自动学习：从对话中提取用户信息。"""

from __future__ import annotations

from typing import Dict, List

from session.models import TranscriptEvent


def extract_user_preferences(events: List[TranscriptEvent]) -> List[str]:
    """从对话中提取用户偏好和个人信息。"""
    preferences = []

    for event in events:
        if event.type == "user_message":
            content = event.payload.get("content", "")
            if not content:
                continue

            # 检测偏好表达
            lower_content = content.lower()

            # 1. "我喜欢/不喜欢"
            if "喜欢" in content or "prefer" in lower_content:
                preferences.append(content[:100])

            # 2. "我是/我在/我的"（个人信息）
            if any(phrase in content for phrase in ["我是", "我在", "我的", "i am", "i'm", "my "]):
                preferences.append(content[:100])

            # 3. 工具/技术偏好
            if any(word in lower_content for word in ["用", "使用", "use ", "pnpm", "npm", "yarn"]):
                preferences.append(content[:100])

    return preferences[:5]  # 最多5条
