#!/usr/bin/env bash
# HDY Monitor — 管理菜单
# 安装后通过 hdy 命令启动交互式管理面板
# 用法：hdy  或  sudo hdy

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

INSTALL_DIR="/opt/hdy-monitor"
SERVICE_NAME="hdy-monitor"
GITHUB_RAW="https://raw.githubusercontent.com/ctsunny/hdy-api/main"

[[ $EUID -ne 0 ]] && exec sudo "$0" "$@"

# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────
_svc_status() {
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        echo -e "${GREEN}● 运行中${NC}"
    else
        echo -e "${RED}○ 已停止${NC}"
    fi
}

_show_credentials() {
    local cred="${INSTALL_DIR}/credentials.txt"
    local cfg="${INSTALL_DIR}/config.json"
    if [[ -f "${cred}" ]]; then
        echo ""
        echo -e "${CYAN}──────────── 访问信息 ────────────${NC}"
        # 读取 config.json 中的端口和路径
        if command -v python3 &>/dev/null && [[ -f "${cfg}" ]]; then
            local info_line port base_path username server_ip
            # Parse all fields in a single Python call
            info_line=$(python3 - <<'PYEOF'
import json, sys
try:
    d = json.load(open("/opt/hdy-monitor/config.json"))
    print(d.get("port","?"), d.get("base_path",""), d.get("admin_username","?"), sep="\t")
except Exception as e:
    print("?\t\t?", sep="\t")
PYEOF
)
            port=$(echo "${info_line}" | cut -f1)
            base_path=$(echo "${info_line}" | cut -f2)
            username=$(echo "${info_line}" | cut -f3)
            server_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "YOUR_IP")
            echo -e "  🌐  访问地址:  ${CYAN}http://${server_ip}:${port}${base_path}/${NC}"
            echo -e "  👤  账号:      ${YELLOW}${username}${NC}"
            echo -e "  🔑  密码:      ${YELLOW}(见 ${cred})${NC}"
        else
            grep -E '(访问地址|账号|密码|端口|路径)' "${cred}" | sed 's/^/  /'
        fi
        echo -e "${CYAN}──────────────────────────────────${NC}"
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# 菜单动作
# ─────────────────────────────────────────────────────────────────────────────
action_status() {
    echo ""
    echo -e "  服务状态:  $(_svc_status)"
    _show_credentials
    echo ""
    echo -e "  安装目录:  ${INSTALL_DIR}"
    echo ""
    read -rp "  按 Enter 返回菜单..." _dummy || true
}

action_start() {
    systemctl start "${SERVICE_NAME}" && echo -e "\n  ${GREEN}服务已启动${NC}\n" || echo -e "\n  ${RED}启动失败，运行 journalctl -u ${SERVICE_NAME} -n 30 查看原因${NC}\n"
    sleep 1
}

action_stop() {
    systemctl stop "${SERVICE_NAME}" && echo -e "\n  ${YELLOW}服务已停止${NC}\n" || echo -e "\n  ${RED}停止失败${NC}\n"
    sleep 1
}

action_restart() {
    systemctl restart "${SERVICE_NAME}" && echo -e "\n  ${GREEN}服务已重启${NC}\n" || echo -e "\n  ${RED}重启失败，运行 journalctl -u ${SERVICE_NAME} -n 30 查看原因${NC}\n"
    sleep 1
}

action_logs() {
    echo ""
    echo -e "  ${CYAN}按 Ctrl+C 退出日志查看${NC}"
    echo ""
    journalctl -u "${SERVICE_NAME}" -f --no-pager || true
}

action_configure() {
    if [[ -f "${INSTALL_DIR}/configure.sh" ]]; then
        bash "${INSTALL_DIR}/configure.sh"
    else
        echo -e "\n  ${RED}configure.sh 未找到，请重新安装${NC}\n"
        sleep 2
    fi
}

action_upgrade() {
    echo ""
    echo -e "  ${CYAN}开始升级 HDY Monitor...${NC}"
    echo ""
    if [[ -f "${INSTALL_DIR}/upgrade.sh" ]]; then
        bash "${INSTALL_DIR}/upgrade.sh" --source "https://github.com/ctsunny/hdy-api.git"
    else
        # 从 GitHub 下载最新升级脚本再运行
        local tmp_script
        tmp_script=$(mktemp)
        curl -fsSL "${GITHUB_RAW}/upgrade.sh" -o "${tmp_script}"
        bash "${tmp_script}" --source "https://github.com/ctsunny/hdy-api.git"
        rm -f "${tmp_script}"
    fi
    echo ""
    read -rp "  按 Enter 返回菜单..." _dummy || true
}

action_uninstall() {
    echo ""
    if [[ -f "${INSTALL_DIR}/uninstall.sh" ]]; then
        bash "${INSTALL_DIR}/uninstall.sh"
    else
        local tmp_script
        tmp_script=$(mktemp)
        curl -fsSL "${GITHUB_RAW}/uninstall.sh" -o "${tmp_script}"
        bash "${tmp_script}"
        rm -f "${tmp_script}"
    fi
    # 若卸载成功，移除自身
    if [[ ! -d "${INSTALL_DIR}" ]]; then
        rm -f /usr/local/bin/hdy
        echo -e "\n  ${GREEN}hdy 命令已移除${NC}\n"
        exit 0
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# 主菜单循环
# ─────────────────────────────────────────────────────────────────────────────
main_menu() {
    while true; do
        clear
        local status_str
        status_str="$(_svc_status)"

        echo ""
        echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
        echo -e "${GREEN}║            HDY Monitor 管理面板                          ║${NC}"
        echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
        echo ""
        echo -e "  服务状态: ${status_str}"
        echo ""
        echo -e "  ${BOLD}────── 服务控制 ──────${NC}"
        echo -e "  ${CYAN}1.${NC}  查看运行状态与访问信息"
        echo -e "  ${CYAN}2.${NC}  启动 HDY Monitor"
        echo -e "  ${CYAN}3.${NC}  停止 HDY Monitor"
        echo -e "  ${CYAN}4.${NC}  重启 HDY Monitor"
        echo -e "  ${CYAN}5.${NC}  查看实时日志"
        echo ""
        echo -e "  ${BOLD}────── 管理 ──────${NC}"
        echo -e "  ${CYAN}6.${NC}  修改配置（端口 / 账号 / 通知渠道）"
        echo -e "  ${CYAN}7.${NC}  升级到最新版本"
        echo -e "  ${CYAN}8.${NC}  卸载 HDY Monitor"
        echo ""
        echo -e "  ${CYAN}0.${NC}  退出"
        echo ""
        echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
        echo ""
        read -rp "  请输入选项 [0-8]: " choice

        case "${choice}" in
            1) action_status ;;
            2) action_start ;;
            3) action_stop ;;
            4) action_restart ;;
            5) action_logs ;;
            6) action_configure ;;
            7) action_upgrade ;;
            8) action_uninstall ;;
            0) echo ""; exit 0 ;;
            *) echo -e "\n  ${RED}无效选项，请重新输入${NC}\n"; sleep 1 ;;
        esac
    done
}

main_menu
