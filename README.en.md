# CukeLink

CukeLink is a graduation-project prototype for a virtual LAN tool with a local UI.

The current backend combines:
- Nebula for overlay networking and P2P attempts
- rathole for application-layer relay fallback
- a Python local agent for process control, config editing, and UI serving

## Current Scope

This repository mainly contains:
- Python backend logic
- launcher logic
- local web UI
- helper scripts

This repository does **not** include:
- real certificates or private keys
- production runtime logs
- packaged release artifacts
- real deployment binaries
- full private environment configuration

## Project Structure

- `main.py`: local agent API, process manager, Nebula/rathole control logic
- `start_ui.py`: Windows launcher, opens the local UI and handles elevation
- `ui/`: local browser UI
- `scripts/nebula_cert.ps1`: Nebula certificate generation helper
- `nebula/config.yml`: sample Nebula config
- `tools/rathole/start_raht.bat`: local helper script

## How It Works

1. The launcher starts the local agent.
2. The local UI is opened in the browser.
3. The agent can start or stop Nebula and rathole.
4. Nebula is used first for overlay networking and direct connectivity attempts.
5. If direct connectivity is not available, rathole can be used as a relay fallback for a selected local port.

## Notes

- This repository is intended as a source-code project, not a complete out-of-box release package.
- To actually run the full application, you still need your own Nebula binaries, rathole binaries, certificates, and environment-specific config files.
- Nebula client identity and virtual IP are determined by the certificate used on that node.

## Development

Typical local entry points:

```powershell
python main.py agent-api
python start_ui.py
```

## Security

Sensitive runtime assets are intentionally excluded from this repository, including:
- certificate files
- private keys
- release bundles
- logs
- real relay credentials

## License

This repository is currently for learning, experimentation, and graduation-project development.
