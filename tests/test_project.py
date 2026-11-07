"""ProjectInterface 解析回归测试。

覆盖两类曾经踩过的坑：
1. checkbox 选项（PI v2.3.0）必须按 cases 顺序合并被选中的分支；
2. resolve_task 不能写穿 case 上的 pipeline_override 源数据——
   否则跑过一次的任务会把选项状态永久污染给之后的所有任务。

用法：
    python tests/test_project.py [MaaEnd 安装目录]
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from maaend_web.project import Project, _deep_merge  # noqa: E402
from maaend_web.runner import find_maaend_dir, resolve_assets_dir  # noqa: E402


def _locate() -> Path:
    if len(sys.argv) > 1:
        return resolve_assets_dir(sys.argv[1])
    env = os.environ.get("MAAEND_ASSETS")
    if env:
        return resolve_assets_dir(Path(env))
    found = find_maaend_dir()
    if not found:
        raise SystemExit("未找到 MaaEnd 安装目录，请作为第一个参数或 MAAEND_ASSETS 传入")
    return resolve_assets_dir(found)


def test_deep_merge_isolation() -> None:
    src = {"a": {"b": {"c": 1}}}
    dst: dict = {}
    _deep_merge(dst, src)
    dst["a"]["b"]["c"] = 99
    assert src["a"]["b"]["c"] == 1, "深合并写穿了源数据"


def test_checkbox(project: Project) -> None:
    name = "SellProductSchedule"
    opt = project.options[name]
    assert opt.type == "checkbox", f"{name} 应为 checkbox，实际 {opt.type}"
    assert len(opt.checked()) == len(opt.cases), "默认应全选"

    # 取消一部分后，不应再出现被取消的分支
    keep = [opt.cases[0].name]
    only = project.resolve_task("SellProduct", {name: keep})
    attach = only["OutpostTradingScheduleEnabled"]["attach"]
    assert list(attach) == ["monday"], f"只选周一却得到 {list(attach)}"

    full = project.resolve_task("SellProduct", {name: opt.checked()})
    assert len(full["OutpostTradingScheduleEnabled"]["attach"]) == len(opt.cases)

    # 顺序：先全选、再单选，两者结果必须不同（污染时这里会相等）
    assert full != only, "先后解析互相污染"


def test_task_override_isolated(project: Project) -> None:
    """连续解析同一任务，源数据保持不变。"""
    task = next((t for t in project.tasks.values() if t.option), None)
    if task is None:
        return
    snapshot = copy.deepcopy(task.pipeline_override)
    cassnap = {
        n: copy.deepcopy(o.pipeline_override) for n, o in project.options.items()
    }
    project.resolve_task(task.name)
    project.resolve_task(task.name)
    assert task.pipeline_override == snapshot, "任务 override 被改写"
    for n, o in project.options.items():
        assert o.pipeline_override == cassnap[n], f"选项 {n} 的 override 被改写"


def main() -> int:
    assets = _locate()
    print(f"资源目录：{assets}")
    project = Project(assets, lang="zh_cn")
    print(f"任务 {len(project.tasks)} 个 / 配置项 {len(project.options)} 个")

    test_deep_merge_isolation()
    print("  深合并隔离          OK")
    test_checkbox(project)
    print("  checkbox 合并        OK")
    test_task_override_isolated(project)
    print("  任务 override 隔离   OK")
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
