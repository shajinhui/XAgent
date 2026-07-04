"""Shell 命令形态检查，避免普通命令工具自行脱离 Runtime。"""

from __future__ import annotations

import shlex


def requests_shell_backgrounding(command: str) -> bool:
    """识别裸 `&` 或 `nohup`，引导调用受管理进程工具。"""

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return False
    return bool(tokens) and (tokens[0] == "nohup" or "&" in tokens)
