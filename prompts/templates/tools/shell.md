# Shell 命令工具

使用 shell 时，必须遵守以下准则：

- 搜索文本或文件时，优先使用 `rg` 或 `rg --files`，因为 `rg` 比 `grep` 等替代方案快得多（如果找不到 `rg` 命令，则使用替代方案）
- 不要使用 Python 脚本来尝试输出文件的大块内容
- 命令在沙箱环境中执行，默认情况下网络访问被拒绝
- 避免使用破坏性命令，如 `rm -rf`、`git reset --hard` 等，除非用户明确要求
- 不要在 `run_command` 中使用 `nohup` 或 `&` 启动后台服务；使用 `start_process`，并在已知端口时传入 `expected_port`
- 使用 `process_status` 核对 PID、进程组、日志和端口 listener 后，才能宣称服务启动成功
- 停止服务必须使用 `stop_process` 返回的 `process_id`；不得用 `kill`、`pkill`、`killall` 或 `lsof | xargs kill` 结束未知进程
- 对于长时间运行的命令，考虑告知用户预期的执行时间
