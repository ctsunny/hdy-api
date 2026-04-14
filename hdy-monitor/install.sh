#!/usr/bin/env bash
# HDY Monitor — One-Click Installer
# Supports: Ubuntu 20.04+ / Debian 11+
# Run as root: sudo bash install.sh

set -euo pipefail

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
# ─────────────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/hdy-monitor"
SERVICE_NAME="hdy-monitor"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

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

# Random port 10000-65535
PORT=$(( RANDOM % 55535 + 10000 ))

# Random 8-char alphanumeric path prefix
BASE_PATH="/$(cat /dev/urandom | tr -dc 'a-z0-9' | head -c 8)"

# Random 8-char username
ADMIN_USER=$(cat /dev/urandom | tr -dc 'a-z' | head -c 8)

# Random 16-char password (letters + digits)
ADMIN_PASS=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c 16)

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
# Get server IP
# ─────────────────────────────────────────────────────────────────────────────
SERVER_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "YOUR_SERVER_IP")

# ─────────────────────────────────────────────────────────────────────────────
# Save credentials to file
# ─────────────────────────────────────────────────────────────────────────────
CRED_FILE="${INSTALL_DIR}/credentials.txt"
cat > "${CRED_FILE}" <<EOF
HDY Monitor 访问信息
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
echo -e "${GREEN}║          HDY Monitor 安装成功！                          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  🌐  访问地址:  ${CYAN}http://${SERVER_IP}:${PORT}${BASE_PATH}/${NC}"
echo -e "  👤  账号:      ${YELLOW}${ADMIN_USER}${NC}"
echo -e "  🔑  密码:      ${YELLOW}${ADMIN_PASS}${NC}"
echo ""
echo -e "  📄  完整信息已保存至: ${CRED_FILE}"
echo ""
echo -e "  服务管理:"
echo -e "    journalctl -u hdy-monitor -f   # 查看日志"
echo -e "    systemctl restart hdy-monitor  # 重启服务"
echo ""
