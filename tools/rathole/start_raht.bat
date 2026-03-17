@echo off
chcp 65001 >nul
echo Starting Rathole Client from current directory...
set RUST_LOG=info
rathole.exe -c client.toml
pause