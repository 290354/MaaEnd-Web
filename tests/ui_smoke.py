"""UI 冒烟测试：不依赖真实鼠标，用 Tk 事件驱动界面。

验证三件事：任务树能勾选、双击能弹出选项对话框、对话框能正常关闭。
模态对话框会阻塞调用方，所以用 after 回调铺开步骤，而不是顺序等待。
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from maaend_web.config import AppConfig
from maaend_web.ui.app import App

cfg = AppConfig.load()
if os.environ.get("MAAEND_ASSETS"):
    cfg.maaend_dir = os.environ["MAAEND_ASSETS"]
cfg.game_url = os.environ.get("MAAEND_WEB_URL", "http://127.0.0.1:8000/static.html")
cfg.browser_port = int(os.environ.get("MAAEND_WEB_PORT", "9222"))
cfg.agent_enabled = False

app = App(cfg)
state = {}


def dialogs():
    return [w for w in app.winfo_children() if w.winfo_class() == "Toplevel"]


def step1():
    print("tasks:", len(app.task_checks))
    name = next(iter(app.task_checks))
    iid = f"task:{name}"
    bbox = app.tree.bbox(iid)
    print("bbox", iid, bbox)
    app.tree.event_generate("<Button-1>", x=bbox[0] + 8, y=bbox[1] + bbox[3] // 2)
    app.update()
    time.sleep(0.3)
    print("checked:", app.task_checks.get(name), "label:", app.tree.item(iid, "text"))
    assert app.task_checks.get(name) is True, "勾选失败"

    state["y"] = bbox[1] + bbox[3] // 2

    class Ev:
        y = bbox[1] + bbox[3] // 2

    # 模态窗口会卡住当前回调，所以打开动作交给 after 调度
    app.after(120, lambda: app._on_tree_double(Ev()))
    app.after(1400, step2)


def step2():
    tops = dialogs()
    print("toplevels:", [w.title() for w in tops])
    assert tops, "双击没有打开选项对话框"
    top = tops[0]
    print("dialog size:", top.winfo_width(), "x", top.winfo_height())
    assert top.winfo_width() > 300 and top.winfo_height() > 300, "对话框尺寸异常"
    for w in tops:
        w.destroy()
    app.after(400, step3)


def step3():
    assert not dialogs(), "对话框未正常关闭"
    print("SMOKE OK")
    app.destroy()


app.after(1500, step1)
app.mainloop()
