#!/usr/bin/env bash
# HDY Monitor — One-Click Installer
# Supports: Ubuntu 20.04+ / Debian 11+
# Run as root: sudo bash install.sh

set -euo pipefail

VERSION="1.1.0"

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
die()     { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && die "请使用 sudo 或 root 运行此脚本"

# ─────────────────────────────────────────────────────────────────────────────
# Detect script directory (where install.sh lives = source root)
# When piped through bash (curl ... | bash) or run as a temp file descriptor,
# BASH_SOURCE[0] may be empty, "bash", "/dev/fd/N", or "/proc/self/fd/N".
# In all such cases we download all project files from GitHub.
# ─────────────────────────────────────────────────────────────────────────────
GITHUB_RAW="https://raw.githubusercontent.com/ctsunny/hdy-api/main"

_src="${BASH_SOURCE[0]:-}"
_dir=""
if [[ -n "${_src}" ]] \
   && [[ "${_src}" != "bash" ]] \
   && [[ "${_src}" != */dev/fd/* ]] \
   && [[ "${_src}" != */proc/*/fd/* ]]; then
    _dir="$(cd "$(dirname "${_src}")" 2>/dev/null && pwd)" || true
fi

if [[ -z "${_dir}" ]] || [[ ! -f "${_dir}/requirements.txt" ]]; then
    info "检测到通过管道运行，从 GitHub 下载项目文件..."
    SCRIPT_DIR="$(mktemp -d)"
    trap 'rm -rf "${SCRIPT_DIR}"' EXIT

    # List of files to fetch from the repository root
    FILES=(
        requirements.txt
        main.py
        models.py
        database.py
        crawler.py
        notifier.py
        configure.sh
        upgrade.sh
        uninstall.sh
        hdy.sh
        "hdy-monitor.service"
    )
    for f in "${FILES[@]}"; do
        curl -fsSL "${GITHUB_RAW}/${f}" -o "${SCRIPT_DIR}/${f}"
    done

    # static directory
    mkdir -p "${SCRIPT_DIR}/static"
    curl -fsSL "${GITHUB_RAW}/static/index.html" -o "${SCRIPT_DIR}/static/index.html"

    success "项目文件下载完成"
else
    SCRIPT_DIR="${_dir}"
fi

INSTALL_DIR="/opt/hdy-monitor"
SERVICE_NAME="hdy-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ─────────────────────────────────────────────────────────────────────────────
# Detect existing installation and show install menu
# ─────────────────────────────────────────────────────────────────────────────
if [[ -d "${INSTALL_DIR}" ]]; then
    echo ""
    echo -e "${YELLOW}╔══════════════════════════════════════════════════════════╗${NC}"
    echo -e "${YELLOW}║  检测到已安装 HDY Monitor，请选择操作                    ║${NC}"
    echo -e "${YELLOW}╚══════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "  ${CYAN}1.${NC}  升级安装（保留配置和数据，推荐）"
    echo -e "  ${CYAN}2.${NC}  重新安装（清除旧数据，全新安装）"
    echo -e "  ${CYAN}0.${NC}  取消"
    echo ""
    read -rp "  请输入选项 [0-2]: " _install_choice </dev/tty
    case "${_install_choice}" in
        1)
            info "执行升级安装..."
            if [[ -f "${SCRIPT_DIR}/upgrade.sh" ]]; then
                bash "${SCRIPT_DIR}/upgrade.sh" --source-dir "${SCRIPT_DIR}"
            else
                UPGRADE_TMP=$(mktemp)
                curl -fsSL "${GITHUB_RAW}/upgrade.sh" -o "${UPGRADE_TMP}"
                bash "${UPGRADE_TMP}" --source-dir "${SCRIPT_DIR}"
                rm -f "${UPGRADE_TMP}"
            fi
            exit 0
            ;;
        2)
            warn "将清除旧安装数据，执行全新安装..."
            systemctl stop "${SERVICE_NAME}" 2>/dev/null || true
            rm -rf "${INSTALL_DIR}"
            success "旧安装已清除，继续全新安装"
            ;;
        0|*)
            info "已取消安装"
            exit 0
            ;;
    esac
fi

# ─────────────────────────────────────────────────────────────────────────────
# System dependencies
# ─────────────────────────────────────────────────────────────────────────────
info "安装系统依赖..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv curl openssl 2>/dev/null
success "系统依赖安装完成"

# ─────────────────────────────────────────────────────────────────────────────
# Create installation directory
# ─────────────────────────────────────────────────────────────────────────────
info "创建安装目录 ${INSTALL_DIR}..."
mkdir -p "${INSTALL_DIR}"
cp -r "${SCRIPT_DIR}/." "${INSTALL_DIR}/"
success "文件已复制到 ${INSTALL_DIR}"

# ─────────────────────────────────────────────────────────────────────────────
# Python virtual environment
# ─────────────────────────────────────────────────────────────────────────────
info "创建 Python 虚拟环境..."
python3 -m venv "${INSTALL_DIR}/venv"
VENV_PY="${INSTALL_DIR}/venv/bin/python"
info "安装 Python 依赖..."
"${INSTALL_DIR}/venv/bin/pip" install -q --upgrade pip
"${INSTALL_DIR}/venv/bin/pip" install -q -r "${INSTALL_DIR}/requirements.txt"
success "Python 依赖安装完成"

# ─────────────────────────────────────────────────────────────────────────────
# Generate random values
# ─────────────────────────────────────────────────────────────────────────────
info "生成随机端口、路径、账号密码..."

# Temporarily disable pipefail: tr gets SIGPIPE when head closes the pipe,
# which would make the whole pipeline return non-zero under pipefail.
set +o pipefail

# Random port 10000-65535
PORT=$(( RANDOM % 55535 + 10000 ))

# Random 8-char alphanumeric path prefix
BASE_PATH="/$(tr -dc 'a-z0-9' < /dev/urandom | head -c 8)"

# Random 8-char username
ADMIN_USER=$(tr -dc 'a-z' < /dev/urandom | head -c 8)

# Random 16-char password (letters + digits)
ADMIN_PASS=$(tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c 16)

set -o pipefail

# Random JWT secret key (32-char hex)
SECRET_KEY=$(openssl rand -hex 32)

# Hash password with bcrypt via Python
PASS_HASH=$("${VENV_PY}" -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('${ADMIN_PASS}'))")

success "随机值生成完成"

# ─────────────────────────────────────────────────────────────────────────────
# Write config.json
# ─────────────────────────────────────────────────────────────────────────────
CONFIG_FILE="${INSTALL_DIR}/config.json"
cat > "${CONFIG_FILE}" <<EOF
{
  "admin_username": "${ADMIN_USER}",
  "admin_password_hash": "${PASS_HASH}",
  "secret_key": "${SECRET_KEY}",
  "port": ${PORT},
  "base_path": "${BASE_PATH}"
}
EOF
chmod 600 "${CONFIG_FILE}"
success "config.json 已写入"

# ─────────────────────────────────────────────────────────────────────────────
# Determine run user (prefer non-root)
# ─────────────────────────────────────────────────────────────────────────────
if id "www-data" &>/dev/null; then
    RUN_USER="www-data"
elif id "nobody" &>/dev/null; then
    RUN_USER="nobody"
else
    RUN_USER="root"
fi
chown -R "${RUN_USER}:${RUN_USER}" "${INSTALL_DIR}"

# ─────────────────────────────────────────────────────────────────────────────
# Create systemd service
# ─────────────────────────────────────────────────────────────────────────────
info "创建 systemd 服务..."
sed \
    -e "s|__USER__|${RUN_USER}|g" \
    -e "s|__INSTALL_DIR__|${INSTALL_DIR}|g" \
    -e "s|__VENV_PYTHON__|${VENV_PY}|g" \
    -e "s|__PORT__|${PORT}|g" \
    "${INSTALL_DIR}/hdy-monitor.service" > "${SERVICE_FILE}"

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}" --quiet
systemctl restart "${SERVICE_NAME}"
success "systemd 服务已启动"

# ─────────────────────────────────────────────────────────────────────────────
# Install hdy management command
# ─────────────────────────────────────────────────────────────────────────────
info "安装 hdy 管理命令..."
cp "${INSTALL_DIR}/hdy.sh" /usr/local/bin/hdy
chmod +x /usr/local/bin/hdy
success "hdy 命令已安装 (输入 hdy 打开管理面板)"

# ─────────────────────────────────────────────────────────────────────────────
# Get server IP (try public IP first, fallback to internal IP)
# ─────────────────────────────────────────────────────────────────────────────
SERVER_IP=$(curl -s --max-time 5 https://api.ipify.org 2>/dev/null \
         || curl -s --max-time 5 https://ifconfig.me 2>/dev/null \
         || hostname -I | awk '{print $1}' 2>/dev/null \
         || echo "YOUR_SERVER_IP")

# ─────────────────────────────────────────────────────────────────────────────
# Save credentials to file
# ─────────────────────────────────────────────────────────────────────────────
CRED_FILE="${INSTALL_DIR}/credentials.txt"
cat > "${CRED_FILE}" <<EOF
HDY Monitor v${VERSION} 访问信息
====================
访问地址: http://${SERVER_IP}:${PORT}${BASE_PATH}/
管理员账号: ${ADMIN_USER}
管理员密码: ${ADMIN_PASS}
端口: ${PORT}
路径: ${BASE_PATH}

安装目录: ${INSTALL_DIR}
配置文件: ${CONFIG_FILE}
数据库: ${INSTALL_DIR}/hdy_monitor.db

服务管理:
  启动: systemctl start hdy-monitor
  停止: systemctl stop hdy-monitor
  重启: systemctl restart hdy-monitor
  日志: journalctl -u hdy-monitor -f
EOF
chmod 600 "${CRED_FILE}"

# ─────────────────────────────────────────────────────────────────────────────
# Done
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║       HDY Monitor v${VERSION} 安装成功！                  ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  🌐  访问地址:  ${CYAN}http://${SERVER_IP}:${PORT}${BASE_PATH}/${NC}"
echo -e "  👤  账号:      ${YELLOW}${ADMIN_USER}${NC}"
echo -e "  🔑  密码:      ${YELLOW}${ADMIN_PASS}${NC}"
echo ""
echo -e "  📄  完整信息已保存至: ${CRED_FILE}"
echo ""
echo -e "  📋  管理面板:  输入 ${CYAN}hdy${NC} 打开交互式管理菜单"
echo ""
echo -e "  服务管理:"
echo -e "    hdy                            # 打开管理菜单"
echo -e "    journalctl -u hdy-monitor -f   # 查看日志"
echo -e "    systemctl restart hdy-monitor  # 重启服务"
echo ""
