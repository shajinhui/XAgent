from pathlib import Path

from sandbox.executor import DockerSandbox


if __name__ == "__main__":
    root = Path(__file__).parent.resolve()
    sandbox = DockerSandbox(project_root=str(root))

    result = sandbox.run("python --version")
    print("ok:", result.ok)
    print("exit_code:", result.exit_code)
    if result.stdout:
        print("stdout:\n", result.stdout)
    if result.stderr:
        print("stderr:\n", result.stderr)
