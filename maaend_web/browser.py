"""浏览器进程的发现、启动与页面接管。

MaaEnd-Web 不内嵌浏览器内核，而是驱动一个带调试端口的 Chrome / Edge。
这样做的好处是：云游戏对浏览器解码性能（H.264/AV1 硬解、WebRTC）的要求交给
真正的浏览器，我们只通过 CDP 截图与派发输入，不参与渲染，稳定性和画质都不打折。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional

from .cdp import CDPConnection, CDPError

log = logging.getLogger(__name__)

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"


# ------------------------------------------------------------------ 浏览器发现


def _windows_candidates() -> List[Path]:
    roots = [
        os.environ.get("PROGRAMFILES", r"C:\Program Files"),
        os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    rel = {
        "Google\\Chrome\\Application\\chrome.exe",
        "Microsoft\\Edge\\Application\\msedge.exe",
        "Chromium\\Application\\chrome.exe",
        "BraveSoftware\\Brave-Browser\\Application\\brave.exe",
    }
    out: List[Path] = []
    for root in roots:
        if not root:
            continue
        for suffix in rel:
            p = Path(root) / suffix
            if p.exists():
                out.append(p)
    # 注册表里登记的安装路径
    try:
        import winreg  # type: ignore

        for exe in ("chrome.exe", "msedge.exe"):
            for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(
                        hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
                    ) as key:
                        value, _ = winreg.QueryValueEx(key, "")
                        p = Path(value)
                        if p.exists():
                            out.append(p)
                except OSError:
                    continue
    except Exception:  # pragma: no cover - 非 Windows
        pass
    return out


def _mac_candidates() -> List[Path]:
    return [
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
        Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]


def _linux_candidates() -> List[Path]:
    names = [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "microsoft-edge-stable",
    ]
    out: List[Path] = []
    for name in names:
        found = shutil.which(name)
        if found:
            out.append(Path(found))
    return out


def find_browser(explicit: Optional[str] = None) -> Optional[Path]:
    """按优先级找一个 Chromium 内核浏览器。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p
        found = shutil.which(explicit)
        if found:
            return Path(found)
        return None
    if IS_WIN:
        cands = _windows_candidates()
    elif IS_MAC:
        cands = _mac_candidates()
    else:
        cands = _linux_candidates()
    for p in cands:
        if p.exists():
            return p
    return None


# ------------------------------------------------------------------ 会话


@dataclass
class PageInfo:
    id: str
    url: str
    title: str
    ws_url: str


def _http_json(port: int, path: str, method: str = "GET", timeout: float = 5.0) -> Any:
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", "replace")
    return json.loads(body) if body.strip() else None


class BrowserSession:
    """一个带远程调试端口的浏览器实例。"""

    def __init__(
        self,
        executable: Optional[str] = None,
        user_data_dir: Optional[str] = None,
        port: int = 0,
        window_size: tuple[int, int] = (1280, 820),
        headless: bool = False,
        extra_args: Optional[List[str]] = None,
        app_mode: bool = True,
    ) -> None:
        self.executable = find_browser(executable)
        self.user_data_dir = user_data_dir or str(
            Path(tempfile.gettempdir()) / "MaaEndWebProfile"
        )
        self.port = port
        self.window_size = window_size
        self.headless = headless
        self.extra_args = extra_args or []
        self.app_mode = app_mode
        self.proc: Optional[subprocess.Popen] = None
        self.browser_ws: Optional[str] = None
        self.game_url: str = "about:blank"
        self._page: Optional[CDPConnection] = None
        self._page_id: Optional[str] = None
        self._owns_process = False

    # -------------------------------------------------------------- 生命周期

    def start(self, url: str = "about:blank", timeout: float = 40.0) -> None:
        """启动浏览器并打开 url。"""
        if not self.executable:
            raise RuntimeError(
                "未找到 Chrome / Edge。请安装 Chromium 内核浏览器，"
                "或在设置中手动指定浏览器路径。"
            )
        profile = Path(self.user_data_dir)
        profile.mkdir(parents=True, exist_ok=True)
        # 上次异常退出可能留下锁文件
        for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            try:
                (profile / name).unlink(missing_ok=True)
            except OSError:
                pass

        args: List[str] = [
            str(self.executable),
            "--remote-debugging-port=0",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-features=Translate,TranslateUI,MediaRouter,CalculateNativeWinOcclusion",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-ipc-flooding-protection",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-popup-blocking",
            f"--window-size={self.window_size[0]},{self.window_size[1]}",
        ]
        if self.headless:
            args.append("--headless=new")
        if self.app_mode and not self.headless:
            args.append(f"--app={url}")
        else:
            args.append(url)
        if IS_WIN:
            args.append("--disable-gpu-vsync")  # 避免部分显卡驱动下画面卡顿
        args.extend(self.extra_args)

        log.info("启动浏览器: %s", " ".join(args[:3]))
        creationflags = 0
        if IS_WIN:
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        self.proc = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        self._owns_process = True

        deadline = time.time() + timeout
        port_file = profile / "DevToolsActivePort"
        while time.time() < deadline:
            if port_file.exists():
                try:
                    lines = port_file.read_text(encoding="utf-8").splitlines()
                    if len(lines) >= 1 and lines[0].strip().isdigit():
                        self.port = int(lines[0].strip())
                        break
                except OSError:
                    pass
            if self.proc.poll() is not None:
                raise RuntimeError("浏览器进程已退出，请检查是否被安全软件拦截。")
            time.sleep(0.15)
        else:
            raise RuntimeError("等待浏览器调试端口超时。")

        deadline = time.time() + 15
        while time.time() < deadline:
            try:
                version = _http_json(self.port, "/json/version")
                self.browser_ws = version.get("webSocketDebuggerUrl", "")
                break
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(0.2)
        else:
            raise RuntimeError("浏览器调试接口不可用。")

        # 关掉启动时可能自动打开的额外空白页
        time.sleep(0.4)

    def attach(self, port: int) -> None:
        """接管一个已经带调试端口运行的浏览器。"""
        self.port = port
        version = _http_json(port, "/json/version")
        self.browser_ws = version.get("webSocketDebuggerUrl", "")
        self._owns_process = False
        log.info("已接管浏览器: %s", version.get("Browser"))

    def close(self, kill: bool = False) -> None:
        if self._page:
            try:
                self._page.close()
            except Exception:
                pass
            self._page = None
        if kill and self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass

    # -------------------------------------------------------------- 页面

    def list_pages(self) -> List[PageInfo]:
        if not self.port:
            return []
        try:
            raw = _http_json(self.port, "/json/list", timeout=3)
        except Exception:
            return []
        pages = []
        for item in raw or []:
            if item.get("type") != "page":
                continue
            if not item.get("webSocketDebuggerUrl"):
                continue
            pages.append(
                PageInfo(
                    id=item.get("id", ""),
                    url=item.get("url", ""),
                    title=item.get("title", ""),
                    ws_url=item["webSocketDebuggerUrl"],
                )
            )
        return pages

    def open_page(self, url: str) -> PageInfo:
        if not self.port:
            raise RuntimeError("浏览器尚未启动。")
        try:
            item = _http_json(self.port, f"/json/new?{url}", method="PUT", timeout=10)
        except Exception:
            item = _http_json(self.port, "/json/new", method="PUT", timeout=10)
            if item and item.get("webSocketDebuggerUrl"):
                conn = CDPConnection(item["webSocketDebuggerUrl"])
                try:
                    conn.call("Page.navigate", url=url)
                finally:
                    time.sleep(0.2)
        if not item or not item.get("webSocketDebuggerUrl"):
            raise RuntimeError("创建标签页失败。")
        return PageInfo(
            id=item.get("id", ""),
            url=item.get("url", url),
            title=item.get("title", ""),
            ws_url=item["webSocketDebuggerUrl"],
        )

    def find_page(self, url_keyword: str) -> Optional[PageInfo]:
        for page in self.list_pages():
            if url_keyword and url_keyword in page.url:
                return page
        return None

    def ensure_page(self, url: str, url_keyword: Optional[str] = None) -> CDPConnection:
        """复用匹配的标签页，没有则新建；返回已启用必要域的连接。"""
        keyword = url_keyword or _page_keyword(url)
        if self._page and not self._page.closed and self._page_id:
            return self._page

        page = self.find_page(keyword) if keyword else None
        if page is None:
            page = self.open_page(url)
            # 新建标签页需要一点时间导航
            time.sleep(0.8)

        conn = CDPConnection(page.ws_url, timeout=30)
        for domain in ("Page", "Runtime", "DOM", "Network"):
            conn.try_call(f"{domain}.enable")
        try:
            conn.call("Page.bringToFront", timeout=3)
        except CDPError:
            pass
        self._page = conn
        self._page_id = page.id
        return conn

    @property
    def page(self) -> Optional[CDPConnection]:
        return self._page

    def focus(self) -> None:
        """把浏览器窗口拉到前台（仅自己启动的实例）。"""
        if not self._owns_process:
            return
        try:
            if IS_WIN:
                import ctypes

                hwnd = ctypes.windll.user32.FindWindowW(None, "云·终末地")
                if hwnd:
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
        except Exception:
            pass


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse

        return urlparse(url).hostname or ""
    except Exception:
        return ""


def _page_keyword(url: str) -> str:
    """页面匹配关键字：优先用路径，根路径时退回主机名。

    云·终末地网页版只有单个页面，路径足够区分；
    对 about:blank 之类的内部页面则用主机名兜底。
    """
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        path = (parsed.path or "").strip("/")
        if path and parsed.scheme in ("http", "https"):
            return path
        return parsed.hostname or ""
    except Exception:
        return ""
