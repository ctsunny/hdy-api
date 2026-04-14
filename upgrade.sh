#!/usr/bin/env bash
# HDY Monitor — 升级脚本
# 功能：从 Git 拉取最新代码，更新 Python 依赖，平滑重启服务，保留所有配置和数据
# 支持：Ubuntu 20.04+ / Debian 11+
# 用法：sudo bash upgrade.sh [--source <git-repo-url>]

set -euo pipefail

VERSION="1.1.0"

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
VENV_PIP="${INSTALL_DIR}/venv/bin/pip"
BACKUP_DIR="/opt/hdy-monitor-backup-$(date +%Y%m%d_%H%M%S)"

# 解析参数
SOURCE_REPO=""
SOURCE_DIR=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --source) SOURCE_REPO="$2"; shift 2 ;;
        --source-dir) SOURCE_DIR="$2"; shift 2 ;;
        *) warn "未知参数: $1"; shift ;;
    esac
done

[[ -d "${INSTALL_DIR}" ]] || die "未找到安装目录 ${INSTALL_DIR}，请先运行 install.sh"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║       HDY Monitor v${VERSION} — 升级脚本                 ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. 备份当前版本（保留配置和数据库）
# ─────────────────────────────────────────────────────────────────────────────
info "备份当前安装到 ${BACKUP_DIR}..."
mkdir -p "${BACKUP_DIR}"

# 备份配置和数据库（最重要的文件）
[[ -f "${INSTALL_DIR}/config.json" ]]        && cp "${INSTALL_DIR}/config.json"        "${BACKUP_DIR}/"
[[ -f "${INSTALL_DIR}/hdy_monitor.db" ]]     && cp "${INSTALL_DIR}/hdy_monitor.db"     "${BACKUP_DIR}/"
[[ -f "${INSTALL_DIR}/credentials.txt" ]]    && cp "${INSTALL_DIR}/credentials.txt"    "${BACKUP_DIR}/"

success "备份完成 → ${BACKUP_DIR}"

# ─────────────────────────────────────────────────────────────────────────────
# 2. 停止服务
# ─────────────────────────────────────────────────────────────────────────────
if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
    info "停止服务 ${SERVICE_NAME}..."
    systemctl stop "${SERVICE_NAME}"
    success "服务已停止"
else
    warn "服务当前未运行，继续升级"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 3. 获取新代码
# ─────────────────────────────────────────────────────────────────────────────
if [[ -n "${SOURCE_REPO}" ]]; then
    # 从 Git 仓库拉取
    info "从 Git 仓库获取最新代码: ${SOURCE_REPO}..."
    command -v git &>/dev/null || die "未安装 git，请运行: apt-get install git"

    TMPDIR_GIT=$(mktemp -d)
    git clone --depth=1 "${SOURCE_REPO}" "${TMPDIR_GIT}/repo"
    NEW_SOURCE="${TMPDIR_GIT}/repo"

elif [[ -n "${SOURCE_DIR}" ]]; then
    # 从本地目录复制
    [[ -d "${SOURCE_DIR}" ]] || die "源目录不存在: ${SOURCE_DIR}"
    info "从本地目录获取新代码: ${SOURCE_DIR}..."
    NEW_SOURCE="${SOURCE_DIR}"

elif [[ -d "${INSTALL_DIR}/.git" ]]; then
    # 安装目录本身是 Git 仓库，直接 pull
    info "从 Git 仓库拉取更新..."
    cd "${INSTALL_DIR}"
    git pull --ff-only
    NEW_SOURCE="${INSTALL_DIR}"

else
    warn "未指定代码来源，将只更新 Python 依赖"
    NEW_SOURCE=""
fi

# ─────────────────────────────────────────────────────────────────────────────
# 4. 复制新文件（跳过配置、数据库、虚拟环境）
# ─────────────────────────────────────────────────────────────────────────────
if [[ -n "${NEW_SOURCE}" ]] && [[ "${NEW_SOURCE}" != "${INSTALL_DIR}" ]]; then
    info "更新程序文件..."
    SKIP_PATTERNS=(config.json hdy_monitor.db credentials.txt venv)
    RSYNC_EXCLUDES=()
    for p in "${SKIP_PATTERNS[@]}"; do
        RSYNC_EXCLUDES+=(--exclude="${p}")
    done

    if command -v rsync &>/dev/null; then
        rsync -a "${RSYNC_EXCLUDES[@]}" "${NEW_SOURCE}/" "${INSTALL_DIR}/"
    else
        # fallback: cp 并手动跳过保留文件
        find "${NEW_SOURCE}" -maxdepth 1 -mindepth 1 | while read -r item; do
            base="$(basename "${item}")"
            skip=false
            for p in "${SKIP_PATTERNS[@]}"; do
                [[ "${base}" == "${p}" ]] && skip=true && break
            done
            if [[ "${skip}" == "false" ]]; then
                cp -r "${item}" "${INSTALL_DIR}/"
            fi
        done
    fi
    success "程序文件更新完成"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 5. 还原保留文件（以防复制时被覆盖）
# ─────────────────────────────────────────────────────────────────────────────
for f in config.json hdy_monitor.db credentials.txt; do
    if [[ -f "${BACKUP_DIR}/${f}" ]]; then
        cp -f "${BACKUP_DIR}/${f}" "${INSTALL_DIR}/${f}"
    fi
done
success "配置和数据库已还原"

# ─────────────────────────────────────────────────────────────────────────────
# 6. 更新 Python 依赖
# ─────────────────────────────────────────────────────────────────────────────
info "更新 Python 依赖..."
"${VENV_PIP}" install -q --upgrade pip
"${VENV_PIP}" install -q --upgrade -r "${INSTALL_DIR}/requirements.txt"
success "Python 依赖更新完成"

# ─────────────────────────────────────────────────────────────────────────────
# 7. 修正文件权限
# ─────────────────────────────────────────────────────────────────────────────
# 确定运行用户
RUN_USER="root"
for u in www-data nobody; do
    id "${u}" &>/dev/null && RUN_USER="${u}" && break
done

chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"
chmod 600 "${INSTALL_DIR}/config.json" 2>/dev/null || true
chmod 600 "${INSTALL_DIR}/credentials.txt" 2>/dev/null || true
success "文件权限已修正（运行用户: ${RUN_USER}）"

# ─────────────────────────────────────────────────────────────────────────────
# 8. 重新加载 systemd 并启动服务
# ─────────────────────────────────────────────────────────────────────────────
info "重新加载 systemd 并启动服务..."
systemctl daemon-reload
systemctl start "${SERVICE_NAME}"
sleep 2

if systemctl is-active --quiet "${SERVICE_NAME}"; then
    success "服务已成功启动"
else
    die "服务启动失败，请检查日志: journalctl -u ${SERVICE_NAME} -n 50"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 9. 清理临时文件
# ─────────────────────────────────────────────────────────────────────────────
if [[ -n "${TMPDIR_GIT:-}" ]] && [[ -d "${TMPDIR_GIT:-}" ]]; then
    rm -rf "${TMPDIR_GIT}"
fi

# ─────────────────────────────────────────────────────────────────────────────
# 完成
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       HDY Monitor v${VERSION} 升级完成！                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  备份位置: ${CYAN}${BACKUP_DIR}${NC}"
echo ""
echo -e "  服务管理:"
echo -e "    journalctl -u ${SERVICE_NAME} -f    # 查看实时日志"
echo -e "    systemctl status ${SERVICE_NAME}    # 查看服务状态"
echo ""
echo -e "  如需回滚，请运行:"
echo -e "    ${YELLOW}sudo bash install.sh${NC}  （使用备份目录中的文件重新安装）"
echo ""
