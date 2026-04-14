#!/usr/bin/env bash
# HDY Monitor — 配置脚本
# 功能：交互式修改端口、管理员账号/密码、访问路径及通知渠道配置
# 支持：Ubuntu 20.04+ / Debian 11+
# 用法：sudo bash configure.sh

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# 颜色输出
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'
info()    { echo -e "${CYAN}[信息]${NC}  $*"; }
success() { echo -e "${GREEN}[成功]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[警告]${NC}  $*"; }
die()     { echo -e "${RED}[错误]${NC}  $*"; exit 1; }

[[ $EUID -ne 0 ]] && die "请使用 sudo 或 root 运行此脚本"

INSTALL_DIR="/opt/hdy-monitor"
CONFIG_FILE="${INSTALL_DIR}/config.json"
VENV_PY="${INSTALL_DIR}/venv/bin/python"
SERVICE_NAME="hdy-monitor"

[[ -d "${INSTALL_DIR}" ]] || die "未找到安装目录 ${INSTALL_DIR}，请先运行 install.sh"
[[ -f "${CONFIG_FILE}" ]] || die "未找到配置文件 ${CONFIG_FILE}"
[[ -f "${VENV_PY}" ]]     || die "未找到虚拟环境 ${VENV_PY}"

# ─────────────────────────────────────────────────────────────────────────────
# 辅助函数
# ─────────────────────────────────────────────────────────────────────────────

# 从 config.json 读取字段
_cfg_get() {
    python3 -c "import json,sys; d=json.load(open('${CONFIG_FILE}')); print(d.get('$1',''))"
}

# 更新 config.json 中的一个字符串字段
_cfg_set_str() {
    local key="$1" val="$2"
    python3 - <<PYEOF
import json
path = '${CONFIG_FILE}'
with open(path) as f:
    d = json.load(f)
d['${key}'] = '${val}'
with open(path, 'w') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
PYEOF
}

# 更新 config.json 中的一个整数字段
_cfg_set_int() {
    local key="$1" val="$2"
    python3 - <<PYEOF
import json
path = '${CONFIG_FILE}'
with open(path) as f:
    d = json.load(f)
d['${key}'] = int('${val}')
with open(path, 'w') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
PYEOF
}

# 重启服务
_restart_service() {
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        info "重启服务 ${SERVICE_NAME}..."
        systemctl restart "${SERVICE_NAME}"
        success "服务已重启"
    else
        warn "服务当前未运行，跳过重启"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# 子菜单：修改管理员密码
# ─────────────────────────────────────────────────────────────────────────────
_change_password() {
    echo ""
    read -rp "  请输入新的管理员用户名（直接回车保持不变 [$(  _cfg_get admin_username)]）: " new_user
    read -rsp "  请输入新密码（至少 8 位）: " new_pass; echo ""
    read -rsp "  请再次输入新密码确认: " new_pass2; echo ""

    [[ "${new_pass}" == "${new_pass2}" ]] || { warn "两次密码不一致，取消操作"; return; }
    [[ ${#new_pass} -ge 8 ]] || { warn "密码长度不足 8 位，取消操作"; return; }

    if [[ -n "${new_user}" ]]; then
        _cfg_set_str "admin_username" "${new_user}"
        success "管理员用户名已更新为: ${new_user}"
    fi

    PASS_HASH=$("${VENV_PY}" -c \
        "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('${new_pass}'))")
    python3 - <<PYEOF
import json
path = '${CONFIG_FILE}'
with open(path) as f:
    d = json.load(f)
d['admin_password_hash'] = '${PASS_HASH}'
with open(path, 'w') as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
PYEOF
    chmod 600 "${CONFIG_FILE}"
    success "管理员密码已更新"
    _restart_service
}

# ─────────────────────────────────────────────────────────────────────────────
# 子菜单：修改端口与访问路径
# ─────────────────────────────────────────────────────────────────────────────
_change_network() {
    CURRENT_PORT=$(_cfg_get port)
    CURRENT_PATH=$(_cfg_get base_path)
    echo ""
    read -rp "  当前端口 [${CURRENT_PORT}]，输入新端口（直接回车保持不变）: " new_port
    read -rp "  当前路径 [${CURRENT_PATH}]，输入新路径前缀（如 /myapp，直接回车保持不变）: " new_path

    if [[ -n "${new_port}" ]]; then
        [[ "${new_port}" =~ ^[0-9]+$ ]] && [[ $new_port -ge 1024 ]] && [[ $new_port -le 65535 ]] \
            || { warn "端口无效（需要 1024-65535），取消操作"; return; }
        _cfg_set_int "port" "${new_port}"

        # 同步更新 systemd 服务文件
        SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
        if [[ -f "${SERVICE_FILE}" ]]; then
            sed -i "s/--port [0-9]*/--port ${new_port}/" "${SERVICE_FILE}"
            systemctl daemon-reload
        fi
        success "端口已更新为: ${new_port}"
    fi

    if [[ -n "${new_path}" ]]; then
        # 确保路径以 / 开头
        [[ "${new_path}" == /* ]] || new_path="/${new_path}"
        # 去除末尾斜杠
        new_path="${new_path%/}"
        _cfg_set_str "base_path" "${new_path}"
        success "访问路径已更新为: ${new_path}"
    fi

    _restart_service
}

# ─────────────────────────────────────────────────────────────────────────────
# 子菜单：查看当前配置
# ─────────────────────────────────────────────────────────────────────────────
_show_config() {
    echo ""
    echo -e "  ${BOLD}当前配置信息${NC}"
    echo -e "  ─────────────────────────────"
    local port user path
    port=$(_cfg_get port)
    user=$(_cfg_get admin_username)
    path=$(_cfg_get base_path)
    SERVER_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "YOUR_SERVER_IP")

    echo -e "  🌐  访问地址: ${CYAN}http://${SERVER_IP}:${port}${path}/${NC}"
    echo -e "  👤  管理员账号: ${YELLOW}${user}${NC}"
    echo -e "  🔒  管理员密码: ${YELLOW}（已加密存储，请查阅 ${INSTALL_DIR}/credentials.txt）${NC}"
    echo -e "  🔌  端口: ${YELLOW}${port}${NC}"
    echo -e "  📂  路径前缀: ${YELLOW}${path:-/（根路径）}${NC}"
    echo ""
    echo -e "  服务状态:"
    systemctl status "${SERVICE_NAME}" --no-pager -l 2>/dev/null || echo "  （服务未安装或未运行）"
    echo ""
}

# ─────────────────────────────────────────────────────────────────────────────
# 子菜单：配置通知渠道
# ─────────────────────────────────────────────────────────────────────────────
_configure_notify() {
    DB_PATH="${INSTALL_DIR}/hdy_monitor.db"
    [[ -f "${DB_PATH}" ]] || { warn "数据库不存在，服务未初始化，请先启动服务后再配置通知"; return; }

    echo ""
    echo -e "  ${BOLD}支持的通知渠道${NC}"
    echo "  1) Telegram Bot"
    echo "  2) 企业微信机器人（WeCom）"
    echo "  3) 钉钉机器人（DingTalk）"
    echo "  4) 飞书机器人（Feishu）"
    echo "  5) Server酱（微信推送）"
    echo "  6) Bark（iOS）"
    echo "  7) PushPlus"
    echo "  8) Discord Webhook"
    echo "  9) 自定义 Webhook"
    echo "  0) 返回主菜单"
    echo ""
    read -rp "  请选择渠道编号: " ch

    case "$ch" in
    1)
        read -rp "  Bot Token: " tg_token
        read -rp "  Chat ID: " tg_chat
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['telegram'] = {'enabled': True, 'bot_token': '${tg_token}', 'chat_id': '${tg_chat}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "Telegram 通知渠道已保存"
        ;;
    2)
        read -rp "  Webhook URL: " wc_url
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['wecom'] = {'enabled': True, 'webhook_url': '${wc_url}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "企业微信通知渠道已保存"
        ;;
    3)
        read -rp "  Webhook URL: " dt_url
        read -rp "  加签密钥（可为空）: " dt_secret
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['dingtalk'] = {'enabled': True, 'webhook_url': '${dt_url}', 'secret': '${dt_secret}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "钉钉通知渠道已保存"
        ;;
    4)
        read -rp "  Webhook URL: " fs_url
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['feishu'] = {'enabled': True, 'webhook_url': '${fs_url}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "飞书通知渠道已保存"
        ;;
    5)
        read -rp "  Server酱 SendKey: " sc_key
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['serverchan'] = {'enabled': True, 'sendkey': '${sc_key}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "Server酱通知渠道已保存"
        ;;
    6)
        read -rp "  Bark Device Key: " bark_key
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['bark'] = {'enabled': True, 'device_key': '${bark_key}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "Bark 通知渠道已保存"
        ;;
    7)
        read -rp "  PushPlus Token: " pp_token
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['pushplus'] = {'enabled': True, 'token': '${pp_token}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "PushPlus 通知渠道已保存"
        ;;
    8)
        read -rp "  Discord Webhook URL: " dc_url
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['discord'] = {'enabled': True, 'webhook_url': '${dc_url}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "Discord 通知渠道已保存"
        ;;
    9)
        read -rp "  Webhook URL: " wh_url
        read -rp "  请求方式 [POST]: " wh_method
        wh_method="${wh_method:-POST}"
        "${VENV_PY}" - "${DB_PATH}" <<PYEOF
import asyncio, aiosqlite, json, sys
DB = sys.argv[1]
async def main():
    async with aiosqlite.connect(DB) as db:
        row = await db.execute_fetchall("SELECT notify_channels FROM config WHERE id=1")
        nc = json.loads(row[0][0] or '{}')
        nc['webhook'] = {'enabled': True, 'url': '${wh_url}', 'method': '${wh_method}'}
        await db.execute("UPDATE config SET notify_channels=? WHERE id=1", (json.dumps(nc),))
        await db.commit()
asyncio.run(main())
PYEOF
        success "自定义 Webhook 通知渠道已保存"
        ;;
    0) return ;;
    *) warn "无效选项" ;;
    esac
}

# ─────────────────────────────────────────────────────────────────────────────
# 主菜单
# ─────────────────────────────────────────────────────────────────────────────
while true; do
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║            HDY Monitor — 配置管理                       ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo "  1) 查看当前配置"
    echo "  2) 修改管理员账号 / 密码"
    echo "  3) 修改端口与访问路径"
    echo "  4) 配置通知渠道"
    echo "  5) 重启服务"
    echo "  0) 退出"
    echo ""
    read -rp "  请选择操作 [0-5]: " choice
    case "$choice" in
        1) _show_config ;;
        2) _change_password ;;
        3) _change_network ;;
        4) _configure_notify ;;
        5) _restart_service ;;
        0) echo ""; echo "  再见！"; echo ""; exit 0 ;;
        *) warn "无效选项，请重新输入" ;;
    esac
done
