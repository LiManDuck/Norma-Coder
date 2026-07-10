"""TaskStore 持久化回归测试（headless，隔离到临时目录）。

锁定 TaskStore 的关键不变量：

1. **CRUD 往返 + 持久化**：create/list/get/update/delete 经落盘 json 文件往返保真，
   跨 TaskStore 实例（新开同目录）可见；``addBlocks`` 镜像到对端的 ``blockedBy``、
   ``delete`` 清理对端反向引用，均持久化；
2. **原子写**：``_save`` 写入中途失败（``os.replace`` 抛错模拟磁盘满/权限）时，
   **原任务文件保持完整**（不被截断/损坏），无残留临时文件；``_load`` 对损坏文件
   静默返回 []，故非原子写会导致整表丢失--原子写是防此灾难的契约。

此前 tasks.py 无直接回归覆盖（仅经 test_tools 间接走 task_tools 工具层）。

运行：``python -m norma.tasks.test_tasks``
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from norma.tasks.tasks import TaskStore, TaskStatus  # noqa: E402


async def test_task_crud_and_persistence() -> bool:
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        store1 = TaskStore(base_dir=base)

        # create：数字 id 递增分配
        t1 = await store1.create("conv1", "task1", "desc1")
        t2 = await store1.create("conv1", "task2", "desc2")
        assert t1.id == "1", f"首任务 id 应为 '1'，实际 {t1.id}"
        assert t2.id == "2", f"次任务 id 应为 '2'，实际 {t2.id}"

        # update：状态变更 + addBlocks 镜像到对端 blockedBy
        updated = await store1.update(
            "conv1", t1.id, status=TaskStatus.IN_PROGRESS, addBlocks=[t2.id])
        assert updated is not None and updated.status == TaskStatus.IN_PROGRESS
        assert updated.blocks == [t2.id], f"t1.blocks 应含 t2，实际 {updated.blocks}"
        t2_after = await store1.get("conv1", t2.id)
        assert t2_after is not None
        assert t2_after.blockedBy == [t1.id], (
            f"addBlocks 应镜像到对端 blockedBy，实际 {t2_after.blockedBy}")

        # 跨实例持久化：新开同目录的 store 应读出全部状态
        store2 = TaskStore(base_dir=base)
        loaded = await store2.list("conv1")
        assert len(loaded) == 2, f"跨实例应读出 2 任务，实际 {len(loaded)}"
        r1 = next(x for x in loaded if x.id == t1.id)
        r2 = next(x for x in loaded if x.id == t2.id)
        assert r1.status == TaskStatus.IN_PROGRESS, "状态变更应持久化"
        assert r1.blocks == [t2.id], "blocks 应持久化"
        assert r2.blockedBy == [t1.id], "镜像 blockedBy 应持久化"

        # delete：清理对端反向引用，并持久化
        ok = await store1.delete("conv1", t1.id)
        assert ok, "delete 已存在的任务应返回 True"
        remaining = await store1.list("conv1")
        assert len(remaining) == 1 and remaining[0].id == t2.id, (
            "delete 后应只剩 t2")
        assert remaining[0].blockedBy == [], (
            f"delete t1 后 t2.blockedBy 应被清理，实际 {remaining[0].blockedBy}")

        # delete 持久化：再开实例确认
        store3 = TaskStore(base_dir=base)
        loaded3 = await store3.list("conv1")
        assert len(loaded3) == 1 and loaded3[0].id == t2.id, (
            "delete 应跨实例持久化")
        assert loaded3[0].blockedBy == [], "反向引用清理应持久化"

        # delete 不存在的任务返回 False
        assert await store3.delete("conv1", "no_such") is False, (
            "delete 不存在任务应返回 False")

        return True


async def test_save_atomic_failure_preserves_existing() -> bool:
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        store = TaskStore(base_dir=base)

        # 先写入 2 个任务（文件完整）
        await store.create("conv1", "task1", "desc1")
        await store.create("conv1", "task2", "desc2")
        before = await store.list("conv1")
        assert len(before) == 2

        # 模拟 os.replace 失败（磁盘满/权限），验证原文件不被破坏
        import norma.tasks.tasks as tasks_mod
        real_replace = os.replace

        def _boom(*args, **kwargs):
            raise OSError("simulated replace failure")

        os.replace = _boom
        raised = False
        try:
            await store.create("conv1", "task3", "desc3")
        except OSError:
            raised = True
        finally:
            os.replace = real_replace
        assert raised, (
            "os.replace 失败时 create 应抛 OSError（原子写失败应上抛，不得静默成功）")

        # 原任务文件必须完整：仍是 2 个任务，未被截断/清空
        after = await store.list("conv1")
        assert len(after) == 2, (
            f"原子写失败后原文件应完整（2 任务），实际 {len(after)} "
            f"-- 非原子写会截断文件、_load 静默返回 [] 致整表丢失")
        ids = {t.id for t in after}
        assert ids == {"1", "2"}, f"原任务 id 应保留，实际 {ids}"

        # 无残留临时文件
        leftovers = list(base.glob(".tmp_tasks_*"))
        assert leftovers == [], (
            f"失败时应清理临时文件，实际残留 {leftovers}")

        return True


async def _amain() -> int:
    tests = [
        ("task_crud_and_persistence", test_task_crud_and_persistence),
        ("save_atomic_failure_preserves_existing",
         test_save_atomic_failure_preserves_existing),
    ]
    failures = 0
    for name, fn in tests:
        try:
            ok = await fn()
            assert ok, f"{name} returned False/None"
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failures += 1
            import traceback
            print(f"FAIL  {name}: {e}")
            traceback.print_exc()
    print(f"=== {len(tests) - failures}/{len(tests)} passed ===")
    return 1 if failures else 0


def test_tasks_headless() -> None:
    """pytest 入口。"""
    assert asyncio.run(_amain()) == 0


if __name__ == "__main__":
    for _s in (sys.stdout, sys.stderr):
        try:
            _s.reconfigure(encoding="utf-8")
        except Exception:
            pass
    sys.exit(asyncio.run(_amain()))
