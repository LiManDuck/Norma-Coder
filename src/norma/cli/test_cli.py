"""NormaCLI 配置与命令行参数接线回归测试（headless）。

回归点：``--model`` 与 ``--config`` 此前被 argparse 解析后**未传入** NormaCLI，
等于静默失效（``norma --model glm-4`` / ``norma --config custom.json`` 都不生效，
而 ``--model`` 还写在帮助示例里）。本文件锁定两者的接线：

1. ``model_override`` 覆盖 ``config["model"]``，且优先级高于配置文件。
2. ``config_path`` 从指定文件加载自定义配置。
3. ``config_path`` 指向不存在文件时不崩溃、回退默认配置、且不在该路径落盘。
4. 默认配置路径受 ``NORMA_CONFIG_HOME`` 覆盖（与 session 存储一致，测试可隔离）。

注意：``NormaCLI()`` 构造会创建 session 并持有 jsonl 文件句柄（Windows 下不可
删除占用中的文件），故每个用例须在临时目录清理前 ``session_manager.close()``。

运行：``python -m norma.cli.test_cli``
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


def _set_home(tmp: str) -> None:
    os.environ["NORMA_CONFIG_HOME"] = tmp


async def test_model_override() -> None:
    from norma.cli.cli import NormaCLI

    with tempfile.TemporaryDirectory() as tmp:
        _set_home(tmp)
        cli = NormaCLI(model_override="glm-custom-xyz")
        try:
            assert cli.config["model"] == "glm-custom-xyz", cli.config["model"]
        finally:
            cli.session_manager.close()


async def test_config_path_loads_custom() -> None:
    from norma.cli.cli import NormaCLI

    with tempfile.TemporaryDirectory() as tmp:
        _set_home(tmp)  # 隔离 session 存储
        cfg = Path(tmp) / "myconf.json"
        cfg.write_text(
            json.dumps(
                {
                    "model": "from-file-model",
                    "api_key": "key-from-file",
                    "base_url": "http://example.test/v1",
                }
            ),
            encoding="utf-8",
        )
        cli = NormaCLI(config_path=str(cfg))
        try:
            assert cli.config["model"] == "from-file-model"
            assert cli.config["api_key"] == "key-from-file"
            assert cli.config["base_url"] == "http://example.test/v1"
        finally:
            cli.session_manager.close()


async def test_config_path_missing_does_not_crash() -> None:
    from norma.cli.cli import NormaCLI

    with tempfile.TemporaryDirectory() as tmp:
        _set_home(tmp)
        missing = Path(tmp) / "nope.json"
        cli = NormaCLI(config_path=str(missing))
        try:
            # 回退默认配置
            assert cli.config["model"] == "glm-4.5-air"
            # 不在缺失路径落盘
            assert not missing.exists()
        finally:
            cli.session_manager.close()


async def test_default_config_respects_config_home() -> None:
    from norma.cli.cli import NormaCLI
    from norma.session.session import get_config_home

    with tempfile.TemporaryDirectory() as tmp:
        _set_home(tmp)
        cli = NormaCLI()
        try:
            # 默认配置应写到 NORMA_CONFIG_HOME/config.json（而非硬编码 ~/.norma）
            assert get_config_home() == Path(tmp)
            assert (Path(tmp) / "config.json").exists()
            # 默认 stream_mode 与模型
            assert cli.config["stream_mode"] is True
        finally:
            cli.session_manager.close()


async def test_model_override_wins_over_config_file() -> None:
    from norma.cli.cli import NormaCLI

    with tempfile.TemporaryDirectory() as tmp:
        _set_home(tmp)
        cfg = Path(tmp) / "c.json"
        cfg.write_text(json.dumps({"model": "file-model"}), encoding="utf-8")
        cli = NormaCLI(config_path=str(cfg), model_override="cli-model")
        try:
            assert cli.config["model"] == "cli-model"
        finally:
            cli.session_manager.close()


async def _amain() -> int:
    tests = [
        ("model_override", test_model_override),
        ("config_path_loads_custom", test_config_path_loads_custom),
        ("config_path_missing_does_not_crash", test_config_path_missing_does_not_crash),
        ("default_config_respects_config_home", test_default_config_respects_config_home),
        ("model_override_wins_over_config_file", test_model_override_wins_over_config_file),
    ]
    failures = 0
    for name, fn in tests:
        try:
            await fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            import traceback

            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_cli_headless() -> None:
    """pytest 入口（若安装 pytest）。"""
    import asyncio

    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    import asyncio

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
