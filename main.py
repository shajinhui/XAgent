from pathlib import Path

from sandbox.executor import DockerSandbox


if __name__ == "__main__":
    root = Path(__file__).parent.resolve()
    sandbox = DockerSandbox(project_root=str(root))

    result = sandbox.run("python --version")
    print("执行成功:", result.ok)
    print("退出码:", result.exit_code)
    if result.stdout:
        print("标准输出:\n", result.stdout)
    if result.stderr:
        print("错误输出:\n", result.stderr)
