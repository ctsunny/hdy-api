# HDY 项目文档

本仓库包含两个子系统：

| 子系统 | 平台 | 说明 |
|--------|------|------|
| **hdy-monitor** | Linux 服务器 | 基于 FastAPI 的库存变动监控服务，自动爬取 szhdy.com 并多渠道推送通知 |
| **APIhdy V5** | Windows | 秒杀辅助工具，提供本地代理服务器与前端操作界面 |

---

## 目录

- [环境要求](#环境要求)
- [hdy-monitor（Linux）](#hdy-monitorlinux)
  - [构建](#构建)
  - [安装](#安装)
  - [配置](#配置)
  - [升级](#升级)
  - [卸载](#卸载)
  - [服务管理](#服务管理)
  - [配置文件说明](#配置文件说明)
  - [通知渠道](#通知渠道)
- [APIhdy V5（Windows）](#apihdy-v5windows)
  - [安装与启动](#安装与启动)
- [常见问题](#常见问题)

---

## 环境要求

### hdy-monitor（Linux）

- 操作系统：Ubuntu 20.04+ 或 Debian 11+
- Python：**3.9 或更高版本**
- 工具：`python3`、`python3-pip`、`python3-venv`、`openssl`、`curl`
- 运行权限：需要 **root / sudo**
- 可选工具：`rsync`（升级时使用）、`git`（从远程仓库升级时使用）

### APIhdy V5（Windows）

- Windows 10 / 11
- PowerShell 5.1+（系统自带）
- 端口 **8080** 未被占用

---

## hdy-monitor（Linux）

所有脚本均位于 `hdy-monitor/` 目录，需以 **root 或 sudo** 运行。

### 构建

> 适用场景：在本地开发环境验证依赖是否完整、源码语法是否正确，无需安装为系统服务。

```bash
cd hdy-monitor
bash build.sh
```

构建脚本执行以下步骤：

1. 检查 Python 版本（需 ≥ 3.9）
2. 检查 `pip3`、`openssl`、`curl` 等系统工具
3. 在项目目录创建 Python 虚拟环境 `./venv`
4. 升级 `pip` 并安装 `requirements.txt` 中的全部依赖
5. 验证核心模块（`fastapi`、`uvicorn`、`aiosqlite` 等）可正常导入
6. 对所有 `.py` 源文件进行语法检查

构建成功后，可继续执行安装：

```bash
sudo bash install.sh
```

---

### 安装

> 一键将 hdy-monitor 安装为 systemd 系统服务，自动生成随机端口、访问路径和管理员账号密码。

```bash
cd hdy-monitor
sudo bash install.sh
```

安装脚本执行以下步骤：

1. 安装系统依赖（`python3`、`python3-pip`、`python3-venv`、`curl`、`openssl`）
2. 将程序文件复制到 `/opt/hdy-monitor/`
3. 创建 Python 虚拟环境并安装依赖
4. 随机生成以下配置：
   - 监听端口（10000–65535 随机选取）
   - 访问路径前缀（8 位随机字母数字，如 `/a3f9k2qz`）
   - 管理员用户名（8 位随机小写字母）
   - 管理员密码（16 位随机字母数字，bcrypt 加密存储）
   - JWT 密钥（32 位随机十六进制）
5. 将配置写入 `/opt/hdy-monitor/config.json`（权限 600）
6. 创建并启动 systemd 服务 `hdy-monitor`

安装完成后，终端将显示访问信息：

```
🌐  访问地址:  http://<服务器IP>:<端口>/<路径>/
👤  账号:      <随机用户名>
🔑  密码:      <随机密码>

📄  完整信息已保存至: /opt/hdy-monitor/credentials.txt
```

> **重要**：请立即记录或保存 `/opt/hdy-monitor/credentials.txt` 中的登录信息。

---

### 配置

> 安装完成后，通过交互式菜单修改端口、账号密码、访问路径及通知渠道。

```bash
sudo bash /opt/hdy-monitor/configure.sh
# 或在源码目录执行：
sudo bash hdy-monitor/configure.sh
```

交互菜单选项：

```
1) 查看当前配置          —— 显示访问地址、账号、端口、服务状态
2) 修改管理员账号 / 密码  —— 更新用户名和密码（密码至少 8 位）
3) 修改端口与访问路径     —— 更改监听端口（1024–65535）和 URL 前缀
4) 配置通知渠道           —— 设置推送通知渠道（见下方列表）
5) 重启服务               —— 立即重启 hdy-monitor
0) 退出
```

修改端口后脚本会自动同步更新 systemd 服务文件并重新加载，无需手动操作。

**通知渠道支持：**

| 编号 | 渠道 | 所需参数 |
|------|------|----------|
| 1 | Telegram Bot | Bot Token、Chat ID |
| 2 | 企业微信机器人 | Webhook URL |
| 3 | 钉钉机器人 | Webhook URL、加签密钥（可选）|
| 4 | 飞书机器人 | Webhook URL |
| 5 | Server酱（微信） | SendKey |
| 6 | Bark（iOS） | Device Key |
| 7 | PushPlus | Token |
| 8 | Discord Webhook | Webhook URL |
| 9 | 自定义 Webhook | URL、请求方式（默认 POST）|

配置通知渠道前需确保服务已启动（数据库已初始化）。

---

### 升级

> 在保留全部配置和数据库的前提下，将程序更新到最新版本。

**方式一：从 Git 远程仓库升级**

```bash
sudo bash /opt/hdy-monitor/upgrade.sh --source https://github.com/ctsunny/hdy-api.git
```

**方式二：从本地目录升级**

```bash
sudo bash /opt/hdy-monitor/upgrade.sh --source-dir /path/to/new/hdy-monitor
```

**方式三：仅更新 Python 依赖**（不更换程序文件）

```bash
sudo bash /opt/hdy-monitor/upgrade.sh
```

升级脚本执行以下步骤：

1. **备份**当前的 `config.json`、`hdy_monitor.db`、`credentials.txt` 到 `/opt/hdy-monitor-backup-<时间戳>/`
2. 停止 `hdy-monitor` 服务
3. 从指定来源拉取新代码（保留配置和数据库不被覆盖）
4. 还原备份的配置文件
5. 更新 Python 虚拟环境依赖
6. 修正文件权限
7. 重新加载 systemd 并启动服务

升级完成后备份目录将保留，如需回滚可从备份目录中手动还原文件。

---

### 卸载

> 停止服务、移除系统服务注册及程序文件。

**完全卸载**（删除所有文件，包括数据库和配置）：

```bash
sudo bash /opt/hdy-monitor/uninstall.sh
```

**保留数据卸载**（仅删除程序文件，保留 `config.json`、数据库、凭据文件）：

```bash
sudo bash /opt/hdy-monitor/uninstall.sh --keep-data
```

**静默卸载**（跳过确认提示，适合脚本自动化）：

```bash
sudo bash /opt/hdy-monitor/uninstall.sh --yes
# 或组合使用：
sudo bash /opt/hdy-monitor/uninstall.sh --keep-data --yes
```

卸载脚本执行以下步骤：

1. 停止并禁用 `hdy-monitor` systemd 服务
2. 删除服务文件 `/etc/systemd/system/hdy-monitor.service` 并重新加载 systemd
3. 根据参数选择完全删除或保留数据目录
4. 交互式询问是否移除系统依赖（`python3-pip`、`python3-venv`）

若使用了 `--keep-data`，数据保留在 `/opt/hdy-monitor/`，需彻底清除时手动执行：

```bash
sudo rm -rf /opt/hdy-monitor
```

---

### 服务管理

安装完成后，可使用标准 systemd 命令管理服务：

```bash
# 查看服务状态
systemctl status hdy-monitor

# 启动服务
systemctl start hdy-monitor

# 停止服务
systemctl stop hdy-monitor

# 重启服务
systemctl restart hdy-monitor

# 实时查看日志
journalctl -u hdy-monitor -f

# 查看最近 100 行日志
journalctl -u hdy-monitor -n 100

# 查看今日日志
journalctl -u hdy-monitor --since today
```

---

### 配置文件说明

配置文件路径：`/opt/hdy-monitor/config.json`（权限 600，仅 root 可读）

```json
{
  "admin_username": "用户名",
  "admin_password_hash": "$2b$12$...（bcrypt 哈希）",
  "secret_key": "JWT 签名密钥（32 位十六进制）",
  "port": 12345,
  "base_path": "/abc12345"
}
```

| 字段 | 说明 |
|------|------|
| `admin_username` | 管理员登录用户名 |
| `admin_password_hash` | bcrypt 加密后的密码 |
| `secret_key` | JWT Token 签名密钥，修改后所有已登录会话立即失效 |
| `port` | 服务监听端口 |
| `base_path` | URL 访问路径前缀（如 `/abc12345`） |

数据库文件：`/opt/hdy-monitor/hdy_monitor.db`（SQLite）  
凭据记录：`/opt/hdy-monitor/credentials.txt`（权限 600）

---

### 通知渠道

通知渠道配置存储在数据库中，通过 `configure.sh` 的菜单选项 **4) 配置通知渠道** 进行设置，也可在 Web 界面的设置页面中管理。

配置完成后可通过 Web 界面的"测试通知"按钮验证推送是否正常。

---

## APIhdy V5（Windows）

位于 `APIhdy_V5/` 目录，为 Windows 平台的秒杀辅助工具，提供本地反向代理服务器和前端操作界面。

### 安装与启动

**无需额外安装**，双击运行即可：

```
APIhdy_V5\启动秒杀系统_V5.bat
```

或手动执行 PowerShell 脚本：

```powershell
cd APIhdy_V5
powershell -NoProfile -ExecutionPolicy Bypass -File "./server.ps1"
```

启动后将：

1. 在本机 `http://127.0.0.1:8080` 启动本地代理服务器
2. 自动打开浏览器访问操作界面

**注意事项：**

- 运行期间请保持 PowerShell / 命令提示符窗口**不要关闭**
- 若提示端口 8080 已被占用，请先关闭占用该端口的程序，再重新启动
- 若 Windows 安全策略阻止执行，请以管理员身份运行 `.bat` 文件

---

## 常见问题

**Q：安装后忘记账号密码怎么办？**  
A：密码保存在 `/opt/hdy-monitor/credentials.txt`，可使用 `sudo cat /opt/hdy-monitor/credentials.txt` 查看。也可通过 `sudo bash configure.sh` 菜单选项 2 重置密码。

**Q：如何查看当前访问地址？**  
A：执行 `sudo bash configure.sh` 并选择选项 1（查看当前配置）即可显示完整访问地址。

**Q：服务启动失败怎么排查？**  
A：运行 `journalctl -u hdy-monitor -n 50` 查看详细错误日志。

**Q：升级后配置和数据会丢失吗？**  
A：不会。升级脚本会在操作前自动备份 `config.json`、`hdy_monitor.db` 和 `credentials.txt`，升级后自动还原。

**Q：能否修改默认安装路径 `/opt/hdy-monitor`？**  
A：当前版本安装路径硬编码为 `/opt/hdy-monitor`，如需更改请在执行 `install.sh` 前手动修改脚本中的 `INSTALL_DIR` 变量。
