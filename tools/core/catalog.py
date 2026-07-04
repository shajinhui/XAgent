"""内置工具目录，集中装配默认工具集合。"""

from __future__ import annotations

from tools.core.protocol import FunctionTool, Tool
from tools.core.registry import ToolRegistry
from tools.filesystem import edit_file, read_file, write_file, list_files
from tools.git import review as git_review
from tools.interaction import ask_user
from tools.network import web_fetch
from tools.patching import patch_review
from tools.search import grep, file_search
from tools.shell import process_control, run_command
from tools.skills import read_skill, read_skill_resource
from tools.testing import run_tests
from tools.planning import task_list


def builtin_tools() -> list[Tool]:
    return [
        FunctionTool(read_file.META, read_file.schema, read_file.run),
        FunctionTool(ask_user.META, ask_user.schema, ask_user.run),
        FunctionTool(write_file.META, write_file.schema, write_file.run),
        FunctionTool(edit_file.META, edit_file.schema, edit_file.run),
        FunctionTool(list_files.META, list_files.schema, list_files.run),
        FunctionTool(file_search.META, file_search.schema, file_search.run),
        FunctionTool(run_tests.META, run_tests.schema, run_tests.run),
        FunctionTool(task_list.META, task_list.schema, task_list.run),
        FunctionTool(task_list.CREATE_META, task_list.create_schema, task_list.create_run),
        FunctionTool(patch_review.APPLY_META, patch_review.apply_schema, patch_review.apply_run),
        FunctionTool(patch_review.REJECT_META, patch_review.reject_schema, patch_review.reject_run),
        FunctionTool(
            patch_review.ROLLBACK_META,
            patch_review.rollback_schema,
            patch_review.rollback_run,
        ),
        FunctionTool(git_review.STATUS_META, git_review.status_schema, git_review.status_run),
        FunctionTool(git_review.DIFF_META, git_review.diff_schema, git_review.diff_run),
        FunctionTool(git_review.DIFF_FILE_META, git_review.diff_file_schema, git_review.diff_file_run),
        FunctionTool(
            git_review.CHANGED_FILES_META,
            git_review.changed_files_schema,
            git_review.changed_files_run,
        ),
        FunctionTool(read_skill.META, read_skill.schema, read_skill.run),
        FunctionTool(read_skill_resource.META, read_skill_resource.schema, read_skill_resource.run),
        FunctionTool(grep.META, grep.schema, grep.run),
        FunctionTool(run_command.META, run_command.schema, run_command.run),
        FunctionTool(
            process_control.START_META,
            process_control.start_schema,
            process_control.start_run,
        ),
        FunctionTool(
            process_control.STATUS_META,
            process_control.status_schema,
            process_control.status_run,
        ),
        FunctionTool(
            process_control.STOP_META,
            process_control.stop_schema,
            process_control.stop_run,
        ),
        FunctionTool(web_fetch.META, web_fetch.schema, web_fetch.run),
    ]


def build_default_registry() -> ToolRegistry:
    return ToolRegistry(builtin_tools())
