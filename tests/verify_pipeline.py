"""端到端验证：MaaEnd 的真实 Pipeline 节点能否在网页控制器上命中。

用法：
    # 1) 先准备一个能被 CDP 接管的浏览器
    chrome --remote-debugging-port=9222 --user-data-dir=/tmp/prof about:blank
    # 2) 运行
    MAAEND_ASSETS=/path/to/MaaEnd/assets python3 tests/verify_pipeline.py

脚本会把一张 MaaEnd 模板图贴进对应 ROI，注入页面 canvas，
再让 MaaFramework 独立识别一次，校验命中框与预期位置是否一致。
"""

from __future__ import annotations

import base64
import io
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from maaend_web.controller import WebConfig
from maaend_web.runner import Engine, EngineConfig, find_maaend_dir, resolve_assets_dir

def _assets() -> Path:
    env = os.environ.get("MAAEND_ASSETS")
    if env:
        return resolve_assets_dir(Path(env))
    found = find_maaend_dir()
    if not found:
        raise SystemExit("未找到 MaaEnd 安装目录，请设置 MAAEND_ASSETS")
    return resolve_assets_dir(found)


ASSETS = _assets()
GAME_URL = os.environ.get("MAAEND_WEB_URL", "http://127.0.0.1:8000/static.html")
PORT = int(os.environ.get("MAAEND_WEB_PORT", "9222"))

CASES = [
    ("InStashProgressionTab", "SceneManager/ProgressionTabChoosed.png", (357, 0, 510, 74)),
    ("__DijiangDepotItemCategoryDoodadSelect", None, None),
]


def main() -> int:
    engine = Engine(
        EngineConfig(
            assets_dir=ASSETS,
            user_data_dir=Path(os.environ.get("MAAEND_WEB_DATA", "/tmp/maaend-web-verify")),
            game_url=GAME_URL,
            browser_port=PORT,
            agent_enabled=False,
            web=WebConfig(force_viewport=True, sharpen=0.0, keep_preview=False),
        )
    )
    engine.prepare()
    conn = engine.controller._conn
    ok_all = True

    node, tpl_rel, roi = CASES[0]
    tpl_path = ASSETS / "resource" / "image" / tpl_rel
    tpl = Image.open(tpl_path).convert("RGBA")
    canvas = Image.new("RGB", (1280, 720), (18, 22, 30))
    px = roi[0] + (roi[2] - tpl.width) // 2
    py = roi[1] + (roi[3] - tpl.height) // 2
    canvas.paste(tpl, (px, py), tpl)
    buf = io.BytesIO()
    canvas.save(buf, "PNG")
    conn.evaluate(f"setFrame('{base64.b64encode(buf.getvalue()).decode()}')", await_promise=True)
    time.sleep(0.3)

    detail = engine.tasker.post_task(
        node,
        {node: {"next": [], "on_error": [], "action": {"type": "DoNothing"}, "max_hit": 1, "timeout": 3000}},
    ).wait().get()
    hit = False
    box = None
    if detail and detail.node_id_list:
        nd = engine.tasker.get_node_detail(detail.node_id_list[0])
        if nd and nd.recognition:
            hit = bool(nd.recognition.hit)
            box = nd.recognition.box
    expect = (px, py, tpl.width, tpl.height)
    matched = bool(box) and abs(box.x - expect[0]) <= 2 and abs(box.y - expect[1]) <= 2
    print(f"节点 {node}: hit={hit} box={box} 预期={expect} 一致={matched}")
    ok_all = ok_all and hit and matched

    engine.shutdown()
    print("\n结果：" + ("通过" if ok_all else "失败"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
