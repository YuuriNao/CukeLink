import argparse
import ctypes
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def _prepare_env() -> None:
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        os.environ.setdefault("P2P_LAN_BASE_DIR", str(exe_dir))
        os.environ.setdefault("P2P_LAN_ASSET_DIR", str(Path(sys._MEIPASS)))


def _open_browser(url: str, delay: float) -> None:
    time.sleep(delay)
    webbrowser.open(url)


def _log_launcher(message: str) -> None:
    base = Path(os.environ.get("P2P_LAN_BASE_DIR", Path.cwd()))
    log_dir = base / "runtime" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "launcher.log"
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    log_path.open("a", encoding="utf-8").write(f"{ts} {message}\n")


def _is_listening(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.3):
            return True
    except OSError:
        return False


def _is_agent_healthy(host: str, port: int) -> bool:
    url = f"http://{host}:{port}/health"
    try:
        with urllib.request.urlopen(url, timeout=0.8) as resp:
            if resp.status != 200:
                return False
            body = resp.read().decode("utf-8", errors="ignore")
            return '"ok": true' in body or '"ok":true' in body
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _is_admin() -> bool:
    if os.name != "nt":
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _elevate_self() -> bool:
    if os.name != "nt":
        return False
    if getattr(sys, "frozen", False):
        exe = sys.executable
        params = subprocess.list2cmdline(sys.argv[1:])
    else:
        exe = sys.executable.replace("python.exe", "pythonw.exe")
        if not Path(exe).exists():
            exe = sys.executable
        script_path = str(Path(__file__).resolve())
        params = subprocess.list2cmdline([script_path, *sys.argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 0)
    return rc > 32


def _notify(message: str, title: str = "CukeLink") -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x40)
    else:
        print(f"{title}: {message}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--no-elevate", action="store_true")
    args = parser.parse_args()

    try:
        if not args.no_elevate and not _is_admin():
            if _elevate_self():
                return 0
            _log_launcher("elevation canceled or failed")
            return 1

        _prepare_env()
        url = f"http://{args.host}:{args.port}/"

        if _is_listening(args.host, args.port) and _is_agent_healthy(args.host, args.port):
            _notify("检测到 CukeLink 已在运行。")
            _open_browser(url, 0.1)
            return 0

        if _is_listening(args.host, args.port) and not _is_agent_healthy(args.host, args.port):
            _notify("端口已被占用，但 CukeLink 未正常响应。请先关闭占用 8787 端口的进程后重试。")
            return 1

        import main as agent_main

        t = threading.Thread(target=_open_browser, args=(url, 1.2), daemon=True)
        t.start()
        agent_main.agent_api(agent_main.AGENT_CONFIG, args.host, args.port)
        return 0
    except Exception:
        _log_launcher("launcher error\n" + traceback.format_exc())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
