"""Explain missing runtime dependencies before importing the desktop UI."""
import importlib
import sys
from pathlib import Path


def check_dependencies() -> bool:
    failures = []
    for package, module in (("PySide6", "PySide6.QtWidgets"), ("Pillow", "PIL.Image")):
        try:
            importlib.import_module(module)
        except Exception as error:
            failures.append(f"{package}：{error}")
    if not failures:
        return True
    root = Path(__file__).resolve().parents[1]
    print("App 尚無法啟動：目前使用的 Python 環境缺少或無法載入必要套件。", file=sys.stderr)
    print(f"Python：{sys.executable}", file=sys.stderr)
    for failure in failures:
        print(f"- {failure}", file=sys.stderr)
    print("\n請在終端機執行以下指令，再重新啟動：", file=sys.stderr)
    if sys.platform == "win32":
        quoted = str(root).replace("'", "''")
        commands = [f"Set-Location '{quoted}'", ".venv\\Scripts\\python.exe -m ensurepip --upgrade",
                    ".venv\\Scripts\\python.exe -m pip install -r requirements.txt"]
    else:
        import shlex
        commands = [f"cd {shlex.quote(str(root))}", ".venv/bin/python -m ensurepip --upgrade",
                    ".venv/bin/python -m pip install -r requirements.txt"]
    for command in commands:
        print(command, file=sys.stderr)
    return False
