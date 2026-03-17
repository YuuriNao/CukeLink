# CukeLink

CukeLink 是一个用于本科毕业设计的虚拟局域网原型系统，带有本地图形化管理界面。

当前后端方案主要由以下部分组成：

- Nebula：用于覆盖网络与 P2P 连接尝试
- rathole：用于应用层中继兜底
- Python 本地 Agent：用于进程控制、配置管理与本地 API
- 本地 Web UI：用于参数修改、状态查看和启动控制

## 项目现状

本仓库当前主要包含：

- Python 后端逻辑
- 启动器逻辑
- 本地网页界面
- 辅助脚本

本仓库**不包含**以下真实运行资源：

- 真实证书与私钥
- 生产环境日志
- 打包后的发布产物
- 真实 Nebula / rathole 二进制文件
- 完整私有部署配置

## 目录说明

- `main.py`：本地 Agent API、进程管理、Nebula / rathole 控制逻辑
- `start_ui.py`：Windows 启动器，用于打开本地 UI 并处理管理员权限
- `ui/`：本地浏览器界面
- `scripts/nebula_cert.ps1`：Nebula 证书生成辅助脚本
- `nebula/config.yml`：Nebula 示例配置
- `tools/rathole/start_raht.bat`：本地辅助脚本

## 工作流程

1. 启动器启动本地 Agent
2. 自动在浏览器中打开本地 UI
3. 用户可通过 UI 启动或停止 Nebula 与 rathole
4. 系统优先尝试通过 Nebula 进行覆盖网络通信与直连
5. 当直连不可用时，可通过 rathole 对指定本地端口进行中继转发

## 当前设计说明

- 本项目当前更偏向“源码原型 + 本地运行控制台”，而不是完整可直接商用的成品
- 要实际运行完整系统，仍需要自行准备：
  - Nebula 二进制文件
  - rathole 二进制文件
  - 对应节点的证书与私钥
  - 环境相关配置文件
- Nebula 节点的虚拟 IP 与身份由该节点所使用的证书决定

## 本地开发

常用启动方式：

```powershell
python main.py agent-api
python start_ui.py
