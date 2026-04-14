#!/usr/bin/env bash
# HDY Monitor — 卸载脚本
# 功能：停止并移除 systemd 服务、删除安装目录（可选保留数据）
# 支持：Ubuntu 20.04+ / Debian 11+
# 用法：sudo bash uninstall.sh [--keep-data] [--yes]

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# 颜色输出
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[信息]${NC}  $*"; }
success() { echo -e "${GREEN}[成功]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[警告]${NC}  $*"; }
die()     { echo -e "${RED}[错误]${NC}  $*"; exit 1; }

[[ $EUID -ne 0 ]] && die "请使用 sudo 或 root 运行此脚本"

INSTALL_DIR="/opt/hdy-monitor"
SERVICE_NAME="hdy-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# 解析参数
KEEP_DATA=false
AUTO_YES=false
while [[ $# -gt 0 ]]; do
    case "$1" in
        --keep-data) KEEP_DATA=true; shift ;;
        --yes|-y)    AUTO_YES=true;  shift ;;
        *) warn "未知参数: $1"; shift ;;
    esac
done

echo ""
echo -e "${RED}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${RED}║            HDY Monitor — 卸载脚本                       ║${NC}"
echo -e "${RED}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 显示将要删除的内容
# ─────────────────────────────────────────────────────────────────────────────
echo -e "  将要执行的操作："
echo -e "  • 停止并禁用服务: ${YELLOW}${SERVICE_NAME}${NC}"
echo -e "  • 删除服务文件:   ${YELLOW}${SERVICE_FILE}${NC}"
if [[ "${KEEP_DATA}" == "true" ]]; then
    echo -e "  • ${GREEN}保留${NC} 数据目录: ${YELLOW}${INSTALL_DIR}${NC}（仅删除程序文件，保留 config.json 和数据库）"
else
    echo -e "  • 完全删除目录:   ${YELLOW}${INSTALL_DIR}${NC}（包含数据库和配置）"
fi
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 询问确认
# ─────────────────────────────────────────────────────────────────────────────
if [[ "${AUTO_YES}" == "false" ]]; then
    read -rp "  ⚠️  确认卸载 HDY Monitor？此操作不可逆！[y/N]: " confirm
    case "${confirm}" in
        [yY]|[yY][eE][sS]) : ;;
        *) echo ""; echo "  卸载已取消。"; echo ""; exit 0 ;;
    esac
fi

echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. 停止并禁用 systemd 服务
# ─────────────────────────────────────────────────────────────────────────────
if systemctl list-units --full -all 2>/dev/null | grep -q "${SERVICE_NAME}.service"; then
    info "停止服务 ${SERVICE_NAME}..."
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || warn "服务停止失败（可能已停止）"

    info "禁用服务自启动..."
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || warn "禁用失败（可能未启用）"
    success "服务已停止并禁用"
else
    warn "服务 ${SERVICE_NAME} 未安装，跳过"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 2. 删除 systemd 服务文件
# ─────────────────────────────────────────────────────────────────────────────
if [[ -f "${SERVICE_FILE}" ]]; then
    info "删除服务文件 ${SERVICE_FILE}..."
    rm -f "${SERVICE_FILE}"
    systemctl daemon-reload
    success "服务文件已删除"
else
    warn "服务文件不存在，跳过"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. 处理安装目录
# ─────────────────────────────────────────────────────────────────────────────
if [[ ! -d "${INSTALL_DIR}" ]]; then
    warn "安装目录 ${INSTALL_DIR} 不存在，跳过"
else
    if [[ "${KEEP_DATA}" == "true" ]]; then
        # 保留模式：只删除程序文件和虚拟环境，保留配置与数据
        info "保留模式：保留配置和数据库..."

        PRESERVE=(config.json hdy_monitor.db credentials.txt)
        TMPKEEP=$(mktemp -d)

        for f in "${PRESERVE[@]}"; do
            [[ -f "${INSTALL_DIR}/${f}" ]] && cp "${INSTALL_DIR}/${f}" "${TMPKEEP}/" && info "  已保存: ${f}"
        done

        info "删除程序文件和虚拟环境..."
        rm -rf "${INSTALL_DIR}"
        mkdir -p "${INSTALL_DIR}"

        for f in "${PRESERVE[@]}"; do
            [[ -f "${TMPKEEP}/${f}" ]] && mv "${TMPKEEP}/${f}" "${INSTALL_DIR}/" && info "  已还原: ${f}"
        done
        rm -rf "${TMPKEEP}"

        success "程序文件已删除，数据已保留于 ${INSTALL_DIR}"
    else
        # 完全删除
        info "完全删除安装目录 ${INSTALL_DIR}..."
        rm -rf "${INSTALL_DIR}"
        success "安装目录已完全删除"
    fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. 询问是否移除系统依赖（可选）
# ─────────────────────────────────────────────────────────────────────────────
if [[ "${AUTO_YES}" == "false" ]]; then
    echo ""
    read -rp "  是否移除由安装脚本安装的系统依赖（python3-venv、python3-pip 等）？[y/N]: " rm_deps
    case "${rm_deps}" in
        [yY]|[yY][eE][sS])
            info "移除系统依赖..."
            apt-get remove -y --auto-remove python3-pip python3-venv 2>/dev/null \
                && success "系统依赖已移除" \
                || warn "部分依赖移除失败（可能被其他程序使用）"
            ;;
        *)
            info "保留系统依赖" ;;
    esac
fi

# ─────────────────────────────────────────────────────────────────────────────
# 完成
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         HDY Monitor 已成功卸载！                         ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
if [[ "${KEEP_DATA}" == "true" ]]; then
    echo -e "  📦  数据已保留于: ${CYAN}${INSTALL_DIR}${NC}"
    echo -e "      若需彻底清除，请手动运行: ${YELLOW}rm -rf ${INSTALL_DIR}${NC}"
fi
echo ""
echo -e "  感谢使用 HDY Monitor！"
echo ""
