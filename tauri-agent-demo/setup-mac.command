#!/bin/bash

# ========================================
#   环境安装脚本 (Mac)
# ========================================

echo "================================"
echo "  Tauri Agent Demo - 环境安装"
echo "================================"
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查 Homebrew
echo "检查 Homebrew..."
if ! command -v brew &> /dev/null; then
    echo -e "${YELLOW}Homebrew 未安装，正在安装...${NC}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    
    # 添加 Homebrew 到 PATH (Apple Silicon)
    if [[ $(uname -m) == 'arm64' ]]; then
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
else
    echo -e "${GREEN}✓ Homebrew 已安装${NC}"
fi

# 检查 Node.js
echo ""
echo "检查 Node.js..."
if ! command -v node &> /dev/null; then
    echo -e "${YELLOW}Node.js 未安装，正在通过 Homebrew 安装...${NC}"
    brew install node
else
    echo -e "${GREEN}✓ Node.js 已安装: $(node --version)${NC}"
fi

# 检查 Rust
echo ""
echo "检查 Rust..."
if ! command -v rustc &> /dev/null; then
    echo -e "${YELLOW}Rust 未安装，正在安装...${NC}"
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
    source "$HOME/.cargo/env"
else
    echo -e "${GREEN}✓ Rust 已安装: $(rustc --version)${NC}"
fi

# 安装 Tauri CLI (如果需要)
echo ""
echo "检查 Tauri CLI..."
if ! command -v cargo-tauri &> /dev/null; then
    echo -e "${YELLOW}安装 Tauri CLI...${NC}"
    cargo install tauri-cli
fi

# 安装前端依赖
echo ""
echo "安装前端依赖 (npm)..."
npm install

# 设置 Python 虚拟环境
echo ""
echo "设置 Python 虚拟环境..."
cd python-backend

if [ ! -d "venv" ]; then
    echo -e "${YELLOW}创建 Python 虚拟环境...${NC}"
    python3 -m venv venv
else
    echo -e "${GREEN}✓ Python 虚拟环境已存在${NC}"
fi

# 激活虚拟环境并安装依赖
echo "安装 Python 依赖..."
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
deactivate

cd ..

echo ""
echo "================================"
echo -e "${GREEN}✓ 环境安装完成！${NC}"
echo "================================"
echo ""
echo "下一步："
echo "  1. 运行 ./start-backend.sh 启动后端服务"
echo "  2. 运行 ./start-frontend.sh 启动前端应用"
echo "  或者运行 ./run.sh 同时启动两者"
echo ""
echo "注意: 如果提示权限错误，运行 chmod +x *.sh"
echo ""
