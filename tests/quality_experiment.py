"""量化实验：视频流压缩/缩放对 MaaEnd 模板匹配得分的影响。

真实云游戏画面 = 渲染 → H.264/AV1 编码 → 网络 → 浏览器解码 → 缩放显示。
这里用 JPEG 压缩 + 重采样模拟这一链路，统计同一批 Pipeline 节点的匹配得分，
用来决定 Web 控制器需要多强的锐化、以及 resource_web 需要下调多少阈值。
"""

from __future__ import annotations

import base64
import io
import os
import json
import logging
import random
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.basicConfig(level=logging.WARNING)

from PIL import Image, ImageFilter

from maaend_web.project import load_jsonc
from maaend_web.runner import Engine, EngineConfig, find_maaend_dir, resolve_assets_dir
from maaend_web.controller import WebConfig

def _assets() -> Path:
    env = os.environ.get("MAAEND_ASSETS")
    if env:
        return resolve_assets_dir(Path(env))
    found = find_maaend_dir()
    if not found:
        raise SystemExit("未找到 MaaEnd 安装目录，请设置 MAAEND_ASSETS")
    return resolve_assets_dir(found)


ASSETS = _assets()
RES = ASSETS / "resource"
GAME_URL = os.environ.get("MAAEND_WEB_URL", "http://127.0.0.1:8000/static.html")
PORT = int(os.environ.get("MAAEND_WEB_PORT", "9222"))

# ------------------------------------------------------------------ 收集节点


def collect_nodes(limit: int = 40, seed: int = 7) -> list[dict]:
    nodes = []
    for path in (RES / "pipeline").rglob("*.json"):
        try:
            data = load_jsonc(path)
        except Exception:
            continue
        for name, node in data.items():
            if not isinstance(node, dict):
                continue
            reco = node.get("recognition")
            if not isinstance(reco, dict) or reco.get("type") != "TemplateMatch":
                continue
            param = reco.get("param", {}) or {}
            roi = param.get("roi")
            tpl = param.get("template")
            if not roi or not tpl:
                continue
            if isinstance(roi, str):
                try:
                    roi = [int(v) for v in re.split(r"[,;\s]+", roi.strip()) if v]
                except ValueError:
                    continue
            if not (isinstance(roi, (list, tuple)) and len(roi) == 4):
                continue
            if not all(isinstance(v, (int, float)) for v in roi):
                continue
            if isinstance(tpl, list):
                tpl = tpl[0]
            tpl_path = RES / "image" / tpl
            if not tpl_path.exists():
                continue
            try:
                im = Image.open(tpl_path)
            except Exception:
                continue
            rw, rh = roi[2], roi[3]
            if im.width > rw - 6 or im.height > rh - 6:
                continue
            if im.width < 12 or im.height < 12:
                continue
            nodes.append(
                {
                    "name": name,
                    "roi": roi,
                    "tpl": tpl_path,
                    "size": im.size,
                    "threshold": (reco.get("param", {}) or {}).get("threshold", 0.7),
                }
            )
    random.Random(seed).shuffle(nodes)
    return nodes[:limit]


# ------------------------------------------------------------------ 画面合成


def compose(node: dict) -> Image.Image:
    canvas = Image.new("RGB", (1280, 720), (16, 20, 28))
    tpl = Image.open(node["tpl"]).convert("RGBA")
    x, y, w, h = node["roi"]
    # 放在 ROI 中间偏左下一点，避免总在正中心（更接近真实情况）
    px = x + max(0, (w - tpl.width) // 2) - 3
    py = y + max(0, (h - tpl.height) // 2) + 2
    canvas.paste(tpl, (px, py), tpl)
    return canvas


def degrade(img: Image.Image, quality: int, scale: float) -> Image.Image:
    """模拟云游戏视频链路：缩小 → JPEG 压缩 → 放大回原尺寸。"""
    w, h = img.size
    if scale != 1.0:
        small = img.resize((int(w * scale), int(h * scale)), Image.BILINEAR)
    else:
        small = img
    buf = io.BytesIO()
    small.save(buf, "JPEG", quality=quality, subsampling=2)
    buf.seek(0)
    out = Image.open(buf).convert("RGB")
    if scale != 1.0:
        out = out.resize((w, h), Image.BILINEAR)
    return out


# ------------------------------------------------------------------ 主流程


def main() -> None:
    nodes = collect_nodes(18)
    print(f"选取节点 {len(nodes)} 个")

    cfg = EngineConfig(
        assets_dir=ASSETS,
        resource_packs=["resource"],
        user_data_dir=Path(os.environ.get("MAAEND_WEB_DATA", Path.home() / ".maaend-web-bench")),
        game_url=GAME_URL,
        browser_port=PORT,
        agent_enabled=False,
        web=WebConfig(force_viewport=True, sharpen=0.0, keep_preview=False),
    )
    eng = Engine(cfg)
    eng.prepare()
    conn = eng.controller._conn

    profiles = [
        ("原图", 100, 1.0, 0.0),
        ("流-优(90)", 90, 1.0, 0.0),
        ("流-良(70)", 70, 1.0, 0.0),
        ("流-标(50)", 50, 1.0, 0.0),
        ("缩放+压缩", 65, 0.72, 0.0),
        ("压缩+锐化", 65, 0.72, 0.6),
    ]

    results = {}
    for node in nodes:
        base = compose(node)
        row = {}
        for label, q, scale, sharp in profiles:
            img = degrade(base, q, scale)
            if sharp:
                img = img.filter(ImageFilter.UnsharpMask(radius=1.1, percent=int(sharp * 100), threshold=2))
            buf = io.BytesIO()
            img.save(buf, "PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            conn.evaluate(f"setFrame('{b64}')", await_promise=True, timeout=20)
            time.sleep(0.05)
            probe_override = {
                node["name"]: {
                    "next": [],
                    "on_error": [],
                    "action": {"type": "DoNothing"},
                    "max_hit": 1,
                    "timeout": 3000,
                    "pre_delay": 0,
                    "post_delay": 0,
                }
            }
            detail = eng.tasker.post_task(node["name"], probe_override).wait().get()
            score = None
            if detail and detail.node_id_list:
                nd = eng.tasker.get_node_detail(detail.node_id_list[0])
                if nd and nd.recognition and nd.recognition.best_result:
                    score = float(nd.recognition.best_result.score)
            row[label] = score if score is not None else 0.0
        results[node["name"]] = row
        print(f"{node['name'][:34]:34s} " + " ".join(f"{k}={v:.3f}" for k, v in row.items()))

    # 汇总
    print("\n===== 汇总（均值 / 低于 0.70 的比例）=====")
    for label, *_ in profiles:
        vals = [r[label] for r in results.values()]
        avg = sum(vals) / len(vals)
        low = sum(1 for v in vals if v < 0.70) / len(vals)
        low80 = sum(1 for v in vals if v < 0.80) / len(vals)
        print(f"{label:12s} 均值={avg:.4f}  <0.70={low*100:.0f}%  <0.80={low80*100:.0f}%")

    out = Path(os.environ.get("MAAEND_BENCH_OUT", Path(__file__).resolve().parent / "quality_results.json"))
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("结果已保存:", out)
    eng.shutdown()


if __name__ == "__main__":
    main()
