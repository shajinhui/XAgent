# 文件操作工具

使用文件操作工具时，遵循以下准则：

- 使用 `edit_file` 工具修改现有文件，使用 `write_file` 创建新文件
- `edit_file` 支持 `dry_run=true` 参数来预览统一差异而不实际写入
- 不要在调用 `edit_file` 或 `write_file` 后重新读取文件来验证，工具会在失败时报错
- 受保护的路径（如 `.env`、`.git`、`.codex-mini`、`.venv`、`__pycache__`）不应被写入
- `.env` 文件不应被文件工具读取
- 编辑文件时默认使用 ASCII，除非有明确理由且文件已经使用非 ASCII 字符
- 保持与现有代码库风格一致
