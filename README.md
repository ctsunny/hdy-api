# HDY Monitor

基于 FastAPI 的库存变动监控服务，自动爬取 szhdy.com 并多渠道推送通知。

---

## 环境要求

- 操作系统：Ubuntu 20.04+ 或 Debian 11+
- Python：**3.9 或更高版本**
- 运行权限：需要 **root / sudo**
- 工具：`python3`、`python3-pip`、`python3-venv`、`openssl`、`curl`

---

## 一键安装

```bash
sudo bash <(curl -fsSL https://raw.githubusercontent.com/ctsunny/hdy-api/main/install.sh)
```

安装完成后，终端将显示访问信息：

```
🌐  访问地址:  http://<服务器IP>:<端口>/<路径>/
👤  账号:      <随机用户名>
🔑  密码:      <随机密码>

📄  完整信息已保存至: /opt/hdy-monitor/credentials.txt
```

> **重要**：请立即记录或保存 `/opt/hdy-monitor/credentials.txt` 中的登录信息。

---

## 目录

- [配置](#配置)
- [升级](#升级)
- [卸载](#卸载)
- [服务管理](#服务管理)
- [配置文件说明](#配置文件说明)
- [通知渠道](#通知渠道)
- [常见问题](#常见问题)

---

## 配置

安装完成后，通过交互式菜单修改端口、账号密码、访问路径及通知渠道：

```bash
sudo bash /opt/hdy-monitor/configure.sh
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

---

## 升级

在保留全部配置和数据库的前提下，将程序更新到最新版本：

```bash
sudo bash /opt/hdy-monitor/upgrade.sh --source https://github.com/ctsunny/hdy-api.git
```

升级脚本执行以下步骤：

1. **备份**当前的 `config.json`、`hdy_monitor.db`、`credentials.txt` 到 `/opt/hdy-monitor-backup-<时间戳>/`
2. 停止服务
3. 从远程仓库拉取新代码（保留配置和数据库）
4. 还原备份的配置文件
5. 更新 Python 虚拟环境依赖
6. 重新加载 systemd 并启动服务

---

## 卸载

**完全卸载**（删除所有文件，包括数据库和配置）：

```bash
sudo bash /opt/hdy-monitor/uninstall.sh
```

**保留数据卸载**：

```bash
sudo bash /opt/hdy-monitor/uninstall.sh --keep-data
```

**静默卸载**（跳过确认提示）：

```bash
sudo bash /opt/hdy-monitor/uninstall.sh --yes
```

---

## 服务管理

```bash
# 查看服务状态
systemctl status hdy-monitor

# 启动 / 停止 / 重启
systemctl start hdy-monitor
systemctl stop hdy-monitor
systemctl restart hdy-monitor

# 实时查看日志
journalctl -u hdy-monitor -f

# 查看最近 100 行日志
journalctl -u hdy-monitor -n 100
```

---

## 配置文件说明

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
| `base_path` | URL 访问路径前缀 |

数据库：`/opt/hdy-monitor/hdy_monitor.db`（SQLite）  
凭据记录：`/opt/hdy-monitor/credentials.txt`（权限 600）

---

## 常见问题

**Q：安装后忘记账号密码怎么办？**  
A：执行 `sudo cat /opt/hdy-monitor/credentials.txt` 查看。也可通过 `sudo bash /opt/hdy-monitor/configure.sh` 菜单选项 2 重置密码。

**Q：如何查看当前访问地址？**  
A：执行 `sudo bash /opt/hdy-monitor/configure.sh` 并选择选项 1。

**Q：服务启动失败怎么排查？**  
A：运行 `journalctl -u hdy-monitor -n 50` 查看详细错误日志。

**Q：升级后配置和数据会丢失吗？**  
A：不会。升级脚本会在操作前自动备份，升级后自动还原。
