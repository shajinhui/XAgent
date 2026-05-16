"""内置工具目录，集中装配默认工具集合。"""

from __future__ import annotations

from tools.core.protocol import FunctionTool, Tool
from tools.core.registry import ToolRegistry
from tools.filesystem import edit_file, read_file, write_file
from tools.interaction import ask_user
from tools.network import web_fetch
from tools.search import grep
from tools.shell import run_command


def builtin_tools() -> list[Tool]:
    return [
        FunctionTool(read_file.META, read_file.schema, read_file.run),
        FunctionTool(ask_user.META, ask_user.schema, ask_user.run),
        FunctionTool(write_file.META, write_file.schema, write_file.run),
        FunctionTool(edit_file.META, edit_file.schema, edit_file.run),
        FunctionTool(grep.META, grep.schema, grep.run),
        FunctionTool(run_command.META, run_command.schema, run_command.run),
        FunctionTool(web_fetch.META, web_fetch.schema, web_fetch.run),
    ]


def build_default_registry() -> ToolRegistry:
    return ToolRegistry(builtin_tools())
