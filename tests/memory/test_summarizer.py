"""summarizer 单元测试。"""

from session.models import TranscriptEvent
from memory.summarizer import summarize_session, extract_task_state


def test_summarize_session():
    """测试会话摘要生成。"""
    events = [
        TranscriptEvent(
            event_id="1",
            session_id="s1",
            type="user_message",
            timestamp=1.0,
            payload={"content": "优化性能"},
        ),
        TranscriptEvent(
            event_id="2",
            session_id="s1",
            type="tool_call_started",
            timestamp=2.0,
            payload={
                "tool_name": "write_file",
                "args": {"file_path": "/test/file.py"},
            },
        ),
        TranscriptEvent(
            event_id="3",
            session_id="s1",
            type="tool_call_started",
            timestamp=3.0,
            payload={
                "tool_name": "execute_command",
                "args": {"command": "pytest"},
            },
        ),
    ]

    summary = summarize_session(events)
    assert "优化性能" in summary
    assert "write_file" in summary
    assert "/test/file.py" in summary
    assert "pytest" in summary


def test_extract_task_state():
    """测试任务状态提取。"""
    events = [
        TranscriptEvent(
            event_id="1",
            session_id="s1",
            type="user_message",
            timestamp=1.0,
            payload={"content": "实现用户登录功能"},
        ),
        TranscriptEvent(
            event_id="2",
            session_id="s1",
            type="tool_call_started",
            timestamp=2.0,
            payload={
                "tool_name": "write_file",
                "args": {"file_path": "/auth/login.py"},
            },
        ),
    ]

    state = extract_task_state(events)
    assert "登录功能" in state["goal"]
    assert "/auth/login.py" in state["modified_files"]


def test_empty_session():
    """测试空会话摘要。"""
    summary = summarize_session([])
    assert summary == "空会话"
