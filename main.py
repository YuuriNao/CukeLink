import argparse
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
import traceback
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


APP_NAME = "cukelink"
DEFAULT_BASE_DIR = Path(__file__).resolve().parent
BASE_DIR = Path(os.environ.get("P2P_LAN_BASE_DIR", DEFAULT_BASE_DIR))
ASSET_DIR = Path(os.environ.get("P2P_LAN_ASSET_DIR", BASE_DIR))
RUNTIME_DIR = BASE_DIR / "runtime"
STATE_FILE = RUNTIME_DIR / "state.json"
LOG_DIR = RUNTIME_DIR / "logs"
AGENT_DIR = RUNTIME_DIR / "agent"
AGENT_CONFIG = AGENT_DIR / "agent.json"
UI_DIR = ASSET_DIR / "ui"


NEBULA_CONFIG_TEMPLATE = """\
# Nebula config (sample). Fill certs and keys before running.
pki:
  ca: "ca.crt"
  cert: "host.crt"
  key: "host.key"

static_host_map: {}

lighthouse:
  am_lighthouse: false
  interval: 60
  hosts: []

listen:
  host: 0.0.0.0
  port: 4242

firewall:
  outbound:
    - port: any
      proto: any
      host: any
"""


RATHOLE_CLIENT_TEMPLATE = """\
[client]
remote_addr = "YOUR_SERVER_IP:2333"

[client.transport]
type = "tcp"

[client.services.nebula]
type = "tcp"
local_addr = "127.0.0.1:4242"
"""


RATHOLE_SERVER_TEMPLATE = """\
[server]
bind_addr = "0.0.0.0:2333"

[server.transport]
type = "tcp"

[server.services.nebula]
type = "tcp"
bind_addr = "0.0.0.0:4242"
"""

AGENT_CONFIG_TEMPLATE = {
    "nebula": {
        "bin": "nebula/nebula.exe",
        "config": "nebula/config.yml",
    },
    "rathole": {
        "bin": "tools/rathole/rathole.exe",
        "config": "tools/rathole/client.toml",
        "remote_addr": "YOUR_SERVER_IP:2333",
        "service_name": "mc_server",
        "local_port": 25565,
        "token": "naiyuemiling",
        "tcp_nodelay": True,
        "tcp_keepalive_secs": 10,
    },
    "peer_probe": {
        "ip": "192.168.100.3",
        "interval_sec": 5,
        "fail_threshold": 3,
    },
    "relay": {
        "enabled": True,
    },
}

@dataclass
class ProcInfo:
    name: str
    pid: int
    cmd: list
    started_at: str


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _ensure_dirs() -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_DIR.mkdir(parents=True, exist_ok=True)


def _api_log(message: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "api.log"
    timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    log_path.open("a", encoding="utf-8").write(f"{timestamp} {message}\n")


def _write_file(path: Path, content: str, force: bool) -> None:
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def init_runtime(force: bool) -> None:
    _ensure_dirs()
    _write_file(RUNTIME_DIR / "nebula" / "config.yml", NEBULA_CONFIG_TEMPLATE, force)
    _write_file(RUNTIME_DIR / "rathole" / "client.toml", RATHOLE_CLIENT_TEMPLATE, force)
    _write_file(RUNTIME_DIR / "rathole" / "server.toml", RATHOLE_SERVER_TEMPLATE, force)
    if not STATE_FILE.exists() or force:
        STATE_FILE.write_text(json.dumps({"procs": []}, indent=2), encoding="utf-8")


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"procs": []}
    return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _spawn_process(
    name: str, cmd: list, log_path: Path, cwd: Optional[Path] = None
) -> tuple[ProcInfo, subprocess.Popen]:
    with log_path.open("ab") as log_fp:
        popen_kwargs = {
            "stdout": log_fp,
            "stderr": log_fp,
            "cwd": (cwd or BASE_DIR),
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
        proc = subprocess.Popen(cmd, **popen_kwargs)
    return ProcInfo(name=name, pid=proc.pid, cmd=cmd, started_at=_now_iso()), proc


def _kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            return


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _is_admin() -> bool:
    if os.name == "nt":
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _tail_log(path: Path, lines: int = 20) -> list[str]:
    if not path.exists():
        return []
    content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return content[-lines:]


def _wait_process_ready(proc: subprocess.Popen, wait_sec: float = 1.0) -> bool:
    deadline = time.time() + wait_sec
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        time.sleep(0.1)
    return proc.poll() is None


def _get_proc(state: dict, name: str) -> Optional[dict]:
    for proc in state.get("procs", []):
        if proc.get("name") == name:
            return proc
    return None


def _set_proc(state: dict, proc_info: ProcInfo) -> None:
    existing = _get_proc(state, proc_info.name)
    if existing:
        state["procs"] = [p for p in state.get("procs", []) if p.get("name") != proc_info.name]
    state["procs"].append(proc_info.__dict__)


def _remove_proc(state: dict, name: str) -> None:
    state["procs"] = [p for p in state.get("procs", []) if p.get("name") != name]


def _stop_proc_by_name(state: dict, name: str) -> None:
    proc = _get_proc(state, name)
    if not proc:
        return
    _kill_pid(proc["pid"])
    _remove_proc(state, name)


def _ping(ip: str, timeout_ms: int = 1000) -> bool:
    if os.name == "nt":
        cmd = ["ping", "-n", "1", "-w", str(timeout_ms), ip]
    else:
        timeout_s = max(1, int((timeout_ms + 999) / 1000))
        cmd = ["ping", "-c", "1", "-W", str(timeout_s), ip]
    result = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return result.returncode == 0


def _render_rathole_client_toml(
    remote_addr: str,
    service_name: str,
    local_port: int,
    token: str | None,
    tcp_nodelay: bool | None,
    tcp_keepalive_secs: int | None,
) -> str:
    token_line = f'token = "{token}"\n' if token else ""
    tcp_nodelay_line = (
        f"nodelay = {'true' if tcp_nodelay else 'false'}\n"
        if tcp_nodelay is not None
        else ""
    )
    tcp_keepalive_line = (
        f"keepalive_secs = {tcp_keepalive_secs}\n"
        if tcp_keepalive_secs is not None
        else ""
    )
    return (
        "[client]\n"
        f'remote_addr = "{remote_addr}"\n\n'
        "[client.transport]\n"
        "type = \"tcp\"\n\n"
        "[client.transport.tcp]\n"
        f"{tcp_nodelay_line}{tcp_keepalive_line}\n"
        f"[client.services.{service_name}]\n"
        "type = \"tcp\"\n"
        f"{token_line}"
        f'local_addr = "127.0.0.1:{local_port}"\n'
    )


def _resolve_path(path_str: str) -> Path:
    path = Path(path_str)
    if path.is_absolute():
        return path
    return (BASE_DIR / path).resolve()


def _load_agent_config(config_path: str) -> dict:
    path = Path(config_path)
    if not path.exists():
        agent_init(config_path, force=False)
    cfg = json.loads(Path(config_path).read_text(encoding="utf-8-sig"))
    merged = _merge_agent_defaults(cfg, AGENT_CONFIG_TEMPLATE)
    if merged != cfg:
        path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def _save_agent_config(config_path: str, cfg: dict) -> None:
    Path(config_path).write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _write_rathole_config_from_agent(cfg: dict) -> bool:
    rathole = cfg.get("rathole", {})
    rathole_config_path = rathole.get("config")
    rathole_remote = rathole.get("remote_addr")
    rathole_service = rathole.get("service_name", "app")
    rathole_local_port = rathole.get("local_port")
    rathole_token = rathole.get("token")
    rathole_tcp_nodelay = rathole.get("tcp_nodelay")
    rathole_tcp_keepalive = rathole.get("tcp_keepalive_secs")
    if not (rathole_config_path and rathole_remote and rathole_local_port):
        return False
    resolved_config = _resolve_path(rathole_config_path)
    resolved_config.parent.mkdir(parents=True, exist_ok=True)
    resolved_config.write_text(
        _render_rathole_client_toml(
            rathole_remote,
            rathole_service,
            int(rathole_local_port),
            rathole_token,
            rathole_tcp_nodelay,
            rathole_tcp_keepalive,
        ),
        encoding="utf-8",
    )
    return True


def _start_nebula_from_agent(cfg: dict, state: dict) -> bool:
    nebula = cfg.get("nebula", {})
    if not (nebula.get("bin") and nebula.get("config")):
        return False
    nebula_bin = _resolve_path(nebula["bin"])
    nebula_cfg = _resolve_path(nebula["config"])
    if not (nebula_bin.exists() and nebula_cfg.exists()):
        return False
    _stop_proc_by_name(state, "nebula")
    proc_info, proc = _spawn_process(
        "nebula",
        [str(nebula_bin), "-config", str(nebula_cfg)],
        LOG_DIR / "nebula.log",
        cwd=nebula_cfg.parent,
    )
    if not _wait_process_ready(proc, wait_sec=1.0):
        return False
    _set_proc(state, proc_info)
    _save_state(state)
    return True


def _start_rathole_from_agent(cfg: dict, state: dict) -> bool:
    rathole = cfg.get("rathole", {})
    if not rathole.get("bin"):
        return False
    if not _write_rathole_config_from_agent(cfg):
        return False
    rathole_bin = _resolve_path(rathole["bin"])
    rathole_cfg = _resolve_path(rathole["config"])
    if not (rathole_bin.exists() and rathole_cfg.exists()):
        return False
    _stop_proc_by_name(state, "rathole")
    proc_info, proc = _spawn_process(
        "rathole",
        [str(rathole_bin), str(rathole_cfg)],
        LOG_DIR / "rathole.log",
        cwd=rathole_cfg.parent,
    )
    if not _wait_process_ready(proc, wait_sec=1.0):
        return False
    _set_proc(state, proc_info)
    _save_state(state)
    return True


def start_services(args: argparse.Namespace) -> None:
    _ensure_dirs()
    state = _load_state()

    procs = []
    if args.nebula_bin and args.nebula_config:
        log_path = LOG_DIR / "nebula.log"
        proc_info, _ = _spawn_process(
            "nebula",
            [args.nebula_bin, "-config", args.nebula_config],
            log_path,
        )
        procs.append(proc_info)

    if args.rathole_bin and args.rathole_config:
        log_path = LOG_DIR / "rathole.log"
        proc_info, _ = _spawn_process(
            "rathole",
            [args.rathole_bin, args.rathole_mode, args.rathole_config],
            log_path,
        )
        procs.append(proc_info)

    state["procs"] = [p.__dict__ for p in procs]
    _save_state(state)


def stop_services() -> None:
    state = _load_state()
    for proc in state.get("procs", []):
        _kill_pid(proc["pid"])
    state["procs"] = []
    _save_state(state)


def status() -> None:
    state = _load_state()
    procs = state.get("procs", [])
    if not procs:
        print("No running processes recorded.")
        return
    for proc in procs:
        print(
            f'{proc["name"]} pid={proc["pid"]} started_at={proc["started_at"]} cmd={" ".join(proc["cmd"])}'
        )


def agent_init(config_path: str, force: bool) -> None:
    _ensure_dirs()
    path = Path(config_path)
    if path.exists() and not force:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(AGENT_CONFIG_TEMPLATE, indent=2), encoding="utf-8")
    print(f"[agent-init] cwd={Path.cwd()}")
    print(f"[agent-init] wrote={path.resolve()}")


def _merge_agent_defaults(cfg: dict, defaults: dict) -> dict:
    merged = dict(cfg)
    for key, default_value in defaults.items():
        current_value = merged.get(key)
        if isinstance(default_value, dict):
            if not isinstance(current_value, dict):
                current_value = {}
            merged[key] = _merge_agent_defaults(current_value, default_value)
        else:
            merged.setdefault(key, default_value)
    return merged


def agent_run(config_path: str, once: bool) -> None:
    _ensure_dirs()
    cfg = _load_agent_config(config_path)
    state = _load_state()

    nebula = cfg.get("nebula", {})
    rathole = cfg.get("rathole", {})
    probe = cfg.get("peer_probe", {})
    relay = cfg.get("relay", {})

    if nebula.get("bin") and nebula.get("config"):
        _stop_proc_by_name(state, "nebula")
        proc = _spawn_process(
            "nebula",
            [nebula["bin"], "-config", nebula["config"]],
            LOG_DIR / "nebula.log",
        )
        _set_proc(state, proc)
        _save_state(state)

    fail_threshold = int(probe.get("fail_threshold", 3))
    interval = int(probe.get("interval_sec", 5))
    target_ip = probe.get("ip")
    relay_enabled = bool(relay.get("enabled", True))

    fail_count = 0
    while True:
        ok = True
        if target_ip:
            ok = _ping(target_ip)
        if ok:
            fail_count = 0
            if relay_enabled:
                state = _load_state()
                _stop_proc_by_name(state, "rathole")
                _save_state(state)
                print(f"[agent] p2p ok, relay off for {target_ip}")
        else:
            fail_count += 1
            print(f"[agent] p2p fail {fail_count}/{fail_threshold} for {target_ip}")
            if relay_enabled and fail_count >= fail_threshold:
                state = _load_state()
                if _start_rathole_from_agent(cfg, state):
                    print("[agent] relay on")
        if once:
            return
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            return


class _AgentHandler(BaseHTTPRequestHandler):
    server_version = "CukeLink/0.1"

    def log_message(self, format: str, *args) -> None:
        # In noconsole/pythonw mode, stderr may be None on Windows.
        try:
            _api_log("http " + (format % args))
        except Exception:
            return

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        return json.loads(data.decode("utf-8"))

    def _send_json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        try:
            if self.path in ("/", "/index.html"):
                ui_path = UI_DIR / "index.html"
                if not ui_path.exists():
                    self._send_json(404, {"error": "ui_not_found"})
                    return
                body = ui_path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if self.path == "/health":
                self._send_json(200, {"ok": True})
                return
            if self.path != "/status":
                self._send_json(404, {"error": "not_found"})
                return
            cfg = _load_agent_config(self.server.agent_config_path)
            state = _load_state()
            self._send_json(
                200,
                {
                    "config": cfg,
                    "procs": state.get("procs", []),
                },
            )
        except Exception:
            _api_log("GET error\n" + traceback.format_exc())
            self._send_json(500, {"error": "internal_error"})

    def do_POST(self) -> None:
        try:
            if self.path == "/rathole/port":
                body = self._read_json()
                port = body.get("local_port")
                try:
                    port = int(port)
                except (TypeError, ValueError):
                    self._send_json(400, {"error": "invalid_port"})
                    return
                cfg = _load_agent_config(self.server.agent_config_path)
                cfg.setdefault("rathole", {})
                cfg["rathole"]["local_port"] = port
                _save_agent_config(self.server.agent_config_path, cfg)
                self._send_json(200, {"ok": True, "local_port": port})
                return
            if self.path == "/rathole/service":
                body = self._read_json()
                name = body.get("service_name")
                if not name or not isinstance(name, str):
                    self._send_json(400, {"error": "invalid_service_name"})
                    return
                cfg = _load_agent_config(self.server.agent_config_path)
                cfg.setdefault("rathole", {})
                cfg["rathole"]["service_name"] = name
                _save_agent_config(self.server.agent_config_path, cfg)
                self._send_json(200, {"ok": True, "service_name": name})
                return
            if self.path == "/rathole/token":
                body = self._read_json()
                token = body.get("token")
                if not token or not isinstance(token, str):
                    self._send_json(400, {"error": "invalid_token"})
                    return
                cfg = _load_agent_config(self.server.agent_config_path)
                cfg.setdefault("rathole", {})
                cfg["rathole"]["token"] = token
                _save_agent_config(self.server.agent_config_path, cfg)
                self._send_json(200, {"ok": True})
                return
            if self.path == "/rathole/tcp":
                body = self._read_json()
                nodelay = body.get("nodelay")
                keepalive_secs = body.get("keepalive_secs")
                if nodelay is not None and not isinstance(nodelay, bool):
                    self._send_json(400, {"error": "invalid_nodelay"})
                    return
                if keepalive_secs is not None:
                    try:
                        keepalive_secs = int(keepalive_secs)
                    except (TypeError, ValueError):
                        self._send_json(400, {"error": "invalid_keepalive"})
                        return
                cfg = _load_agent_config(self.server.agent_config_path)
                cfg.setdefault("rathole", {})
                if nodelay is not None:
                    cfg["rathole"]["tcp_nodelay"] = nodelay
                if keepalive_secs is not None:
                    cfg["rathole"]["tcp_keepalive_secs"] = keepalive_secs
                _save_agent_config(self.server.agent_config_path, cfg)
                self._send_json(200, {"ok": True})
                return
            if self.path == "/peer/ip":
                body = self._read_json()
                ip = body.get("ip")
                if not ip:
                    self._send_json(400, {"error": "invalid_ip"})
                    return
                cfg = _load_agent_config(self.server.agent_config_path)
                cfg.setdefault("peer_probe", {})
                cfg["peer_probe"]["ip"] = ip
                _save_agent_config(self.server.agent_config_path, cfg)
                self._send_json(200, {"ok": True, "ip": ip})
                return
            if self.path == "/rathole/remote":
                body = self._read_json()
                remote = body.get("remote_addr")
                if not remote or ":" not in remote:
                    self._send_json(400, {"error": "invalid_remote_addr"})
                    return
                cfg = _load_agent_config(self.server.agent_config_path)
                cfg.setdefault("rathole", {})
                cfg["rathole"]["remote_addr"] = remote
                _save_agent_config(self.server.agent_config_path, cfg)
                self._send_json(200, {"ok": True, "remote_addr": remote})
                return
            if self.path == "/agent/restart-relay":
                cfg = _load_agent_config(self.server.agent_config_path)
                state = _load_state()
                if not _start_rathole_from_agent(cfg, state):
                    self._send_json(
                        400,
                        {
                            "error": "rathole_start_failed",
                            "log_tail": _tail_log(LOG_DIR / "rathole.log", lines=12),
                        },
                    )
                    return
                self._send_json(200, {"ok": True})
                return
            if self.path == "/rathole/stop":
                state = _load_state()
                _stop_proc_by_name(state, "rathole")
                _save_state(state)
                self._send_json(200, {"ok": True})
                return
            if self.path == "/nebula/start":
                if not _is_admin():
                    self._send_json(400, {"error": "requires_admin"})
                    return
                cfg = _load_agent_config(self.server.agent_config_path)
                state = _load_state()
                if not _start_nebula_from_agent(cfg, state):
                    self._send_json(
                        400,
                        {
                            "error": "nebula_start_failed",
                            "log_tail": _tail_log(LOG_DIR / "nebula.log", lines=12),
                        },
                    )
                    return
                self._send_json(200, {"ok": True})
                return
            if self.path == "/nebula/stop":
                state = _load_state()
                _stop_proc_by_name(state, "nebula")
                _save_state(state)
                self._send_json(200, {"ok": True})
                return
            if self.path == "/agent/exit":
                state = _load_state()
                for proc in list(state.get("procs", [])):
                    _kill_pid(proc["pid"])
                state["procs"] = []
                _save_state(state)
                self._send_json(200, {"ok": True})
                def _shutdown() -> None:
                    time.sleep(0.2)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()
                return
            self._send_json(404, {"error": "not_found"})
        except Exception:
            _api_log("POST error\n" + traceback.format_exc())
            self._send_json(500, {"error": "internal_error"})


def agent_api(config_path: str, host: str, port: int) -> None:
    _ensure_dirs()
    server = HTTPServer((host, port), _AgentHandler)
    server.agent_config_path = config_path
    print(f"[agent-api] listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=APP_NAME)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="Create runtime dirs and sample configs.")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing files.")

    p_start = sub.add_parser("start", help="Start nebula/rathole processes.")
    p_start.add_argument("--nebula-bin", help="Path to nebula binary.")
    p_start.add_argument("--nebula-config", help="Path to nebula config.yml.")
    p_start.add_argument("--rathole-bin", help="Path to rathole binary.")
    p_start.add_argument(
        "--rathole-mode",
        default="client",
        choices=["client", "server"],
        help="Rathole run mode.",
    )
    p_start.add_argument("--rathole-config", help="Path to rathole config.toml.")

    sub.add_parser("stop", help="Stop recorded processes.")
    sub.add_parser("status", help="Show recorded processes.")
    p_agent_init = sub.add_parser("agent-init", help="Create agent config template.")
    p_agent_init.add_argument("--config", default=str(AGENT_CONFIG))
    p_agent_init.add_argument("--force", action="store_true")
    p_agent_run = sub.add_parser("agent-run", help="Run agent with p2p/relay switching.")
    p_agent_run.add_argument("--config", default=str(AGENT_CONFIG))
    p_agent_run.add_argument("--once", action="store_true", help="Run one probe cycle.")
    p_agent_api = sub.add_parser("agent-api", help="Run local HTTP API for UI control.")
    p_agent_api.add_argument("--config", default=str(AGENT_CONFIG))
    p_agent_api.add_argument("--host", default="127.0.0.1")
    p_agent_api.add_argument("--port", default=8787, type=int)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.cmd == "init":
        init_runtime(args.force)
        print("Initialized runtime directory with sample configs.")
        return 0
    if args.cmd == "start":
        start_services(args)
        print("Started requested services. Check runtime/logs for output.")
        return 0
    if args.cmd == "stop":
        stop_services()
        print("Stopped services.")
        return 0
    if args.cmd == "status":
        status()
        return 0
    if args.cmd == "agent-init":
        agent_init(args.config, args.force)
        print(f"Agent config written to {args.config}")
        return 0
    if args.cmd == "agent-run":
        agent_run(args.config, args.once)
        return 0
    if args.cmd == "agent-api":
        agent_api(args.config, args.host, args.port)
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
