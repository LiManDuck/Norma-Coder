"""统一回归测试运行器。

发现并运行所有 ``src/norma/**/test_*.py`` 模块，汇总 pass/fail 计数后退出。
每个模块通过 ``python -m <dotted_name>`` 在独立子进程中运行，避免 asyncio 事件循环
冲突或跨模块导入副作用。与现有 ``python -m norma.xxx.test_xxx`` 独立运行完全兼容。

用法::

    PYTHONPATH=src python -m norma.run_tests

退出码：0 表示全绿，1 表示至少一个模块失败或异常。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]  # .../src  (run_tests.py is at src/norma/)
_REPO = _SRC.parent


def _find_test_modules() -> list[str]:
    """扫描 src/norma 下所有 test_*.py，返回规范模块名列表。"""
    modules: list[str] = []
    for pyfile in sorted(_SRC.rglob("norma/**/test_*.py")):
        rel = pyfile.relative_to(_SRC).with_suffix("")
        dotted = ".".join(rel.parts)
        modules.append(dotted)
    return modules


def run_one(module: str) -> tuple[bool, str]:
    """在一个子进程中运行一个测试模块。

    Returns:
        (passed, last_output_line)
    """
    env = dict(os.environ, PYTHONPATH=str(_SRC))
    try:
        proc = subprocess.run(
            [sys.executable, "-m", module],
            cwd=_REPO,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (120s)"
    except Exception as exc:
        return False, f"subprocess error: {exc}"

    last = ""
    out_text = proc.stdout or ""
    if proc.stderr:
        out_text += "\n" + proc.stderr
    lines = [l for l in out_text.strip().splitlines() if l.strip()]
    last = lines[-1] if lines else f"rc={proc.returncode}"

    passed = proc.returncode == 0
    return passed, last


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    modules = _find_test_modules()
    if not modules:
        print("未找到任何 test_*.py 模块")
        return 1

    n = len(modules)
    failures: list[tuple[str, str]] = []
    for i, mod in enumerate(modules, 1):
        passed, last = run_one(mod)
        status = "PASS" if passed else "FAIL"
        mark = "✓" if passed else "✗"
        print(f"[{i:02d}/{n:02d}] {mark} {mod}")
        if not passed:
            # 打印失败模块的尾行以帮助快速诊断
            indent = "      "
            for detail_line in last.splitlines():
                print(f"{indent}{detail_line}")
            failures.append((mod, last))

    print(f"\n{'=' * 50}")
    passed_n = n - len(failures)
    if failures:
        print(f"  {passed_n}/{n} 模块通过  ({len(failures)} 失败)")
        print()
        print("  失败模块:")
        for mod, last in failures:
            short = last.splitlines()[-1] if last else ""
            print(f"    ✗ {mod}  — {short}")
        return 1
    else:
        print(f"  ✓ 全绿！{n}/{n} 模块通过")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
