#!/usr/bin/env bash
# HDY Monitor — 构建脚本
# 功能：检查环境依赖、创建虚拟环境、安装 Python 依赖、验证项目可正常启动
# 支持：Ubuntu 20.04+ / Debian 11+
# 用法：bash build.sh

set -euo pipefail

# ─────────────────────────────────────────────────────────────────────────────
# 颜色输出
# ─────────────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[信息]${NC}  $*"; }
success() { echo -e "${GREEN}[成功]${NC}  $*"; }
warn()    { echo -e "${YELLOW}[警告]${NC}  $*"; }
die()     { echo -e "${RED}[错误]${NC}  $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║            HDY Monitor — 构建脚本                       ║${NC}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# 1. 检查 Python 版本（要求 3.9+）
# ─────────────────────────────────────────────────────────────────────────────
info "检查 Python 版本..."
if ! command -v python3 &>/dev/null; then
    die "未找到 python3，请先安装 Python 3.9 或更高版本"
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(python3 -c "import sys; print(sys.version_info.major)")
PY_MINOR=$(python3 -c "import sys; print(sys.version_info.minor)")

if [[ $PY_MAJOR -lt 3 ]] || { [[ $PY_MAJOR -eq 3 ]] && [[ $PY_MINOR -lt 9 ]]; }; then
    die "需要 Python 3.9+，当前版本为 ${PY_VERSION}"
fi
success "Python ${PY_VERSION} ✓"

# ─────────────────────────────────────────────────────────────────────────────
# 2. 检查系统工具
# ─────────────────────────────────────────────────────────────────────────────
info "检查系统工具..."
for cmd in pip3 openssl curl; do
    if command -v "$cmd" &>/dev/null; then
        success "${cmd} ✓"
    else
        warn "${cmd} 未安装，部分功能可能不可用"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
# 3. 创建 / 更新 Python 虚拟环境
# ─────────────────────────────────────────────────────────────────────────────
VENV_DIR="${SCRIPT_DIR}/venv"
info "创建 Python 虚拟环境（${VENV_DIR}）..."

if ! python3 -m venv --help &>/dev/null 2>&1; then
    die "python3-venv 模块不可用，请运行: apt-get install python3-venv"
fi

python3 -m venv "${VENV_DIR}"
success "虚拟环境已就绪"

VENV_PY="${VENV_DIR}/bin/python"
VENV_PIP="${VENV_DIR}/bin/pip"

# ─────────────────────────────────────────────────────────────────────────────
# 4. 升级 pip 并安装依赖
# ─────────────────────────────────────────────────────────────────────────────
info "升级 pip..."
"${VENV_PIP}" install -q --upgrade pip

info "安装 Python 依赖（来自 requirements.txt）..."
if [[ ! -f "${SCRIPT_DIR}/requirements.txt" ]]; then
    die "未找到 requirements.txt（路径: ${SCRIPT_DIR}/requirements.txt）"
fi
"${VENV_PIP}" install -q -r "${SCRIPT_DIR}/requirements.txt"
success "所有依赖安装完成"

# ─────────────────────────────────────────────────────────────────────────────
# 5. 验证核心模块可正常导入
# ─────────────────────────────────────────────────────────────────────────────
info "验证核心模块..."
MODULES=(fastapi uvicorn aiosqlite httpx bs4 lxml jose passlib pydantic aiosmtplib)
for mod in "${MODULES[@]}"; do
    if "${VENV_PY}" -c "import ${mod}" 2>/dev/null; then
        success "  ${mod} ✓"
    else
        die "模块 ${mod} 导入失败，请检查 requirements.txt"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
# 6. 验证源文件语法（干运行）
# ─────────────────────────────────────────────────────────────────────────────
info "检查源文件 Python 语法..."
for pyfile in main.py crawler.py database.py notifier.py models.py; do
    if [[ -f "${SCRIPT_DIR}/${pyfile}" ]]; then
        "${VENV_PY}" -m py_compile "${SCRIPT_DIR}/${pyfile}" \
            && success "  ${pyfile} 语法正确 ✓" \
            || die "  ${pyfile} 语法错误，请检查代码"
    else
        warn "  ${pyfile} 不存在，跳过"
    fi
done

# ─────────────────────────────────────────────────────────────────────────────
# 完成
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║         HDY Monitor 构建完成！                           ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  虚拟环境路径: ${CYAN}${VENV_DIR}${NC}"
echo -e "  Python  路径: ${CYAN}${VENV_PY}${NC}"
echo ""
echo -e "  下一步可运行:"
echo -e "    ${YELLOW}sudo bash install.sh${NC}   # 安装为系统服务"
echo -e "    ${YELLOW}bash configure.sh${NC}      # 交互式配置"
echo ""
