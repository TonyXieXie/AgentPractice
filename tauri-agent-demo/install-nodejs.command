#!/bin/bash

# ========================================
#   Node.js 安装脚本 (使用 nvm)
# ========================================

echo "================================"
echo "  Node.js 安装 (via nvm)"
echo "================================"
echo ""

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 检查是否已安装 Node.js
if command -v node &> /dev/null; then
    echo -e "${GREEN}✓ Node.js 已安装: $(node --version)${NC}"
    echo "如果想更新版本，可以继续安装 nvm"
    read -p "是否继续安装 nvm? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 0
    fi
fi

# 检查是否已安装 nvm
if [ -d "$HOME/.nvm" ] || command -v nvm &> /dev/null; then
    echo -e "${GREEN}✓ nvm 已安装${NC}"
    echo "正在加载 nvm..."
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
else
    echo -e "${YELLOW}正在安装 nvm...${NC}"
    
    # 安装 nvm
    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
    
    # 加载 nvm
    export NVM_DIR="$HOME/.nvm"
    [ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"
    
    echo -e "${GREEN}✓ nvm 安装完成${NC}"
fi

# 安装 Node.js LTS 版本
echo ""
echo -e "${YELLOW}正在安装 Node.js LTS 版本...${NC}"
nvm install --lts

# 设置默认版本
nvm use --lts
nvm alias default 'lts/*'

# 验证安装
echo ""
echo "================================"
echo -e "${GREEN}✓ Node.js 安装完成！${NC}"
echo "================================"
echo ""
echo "Node.js 版本: $(node --version)"
echo "npm 版本: $(npm --version)"
echo ""
echo "nvm 已安装到: $HOME/.nvm"
echo ""

# 提示如何在新终端中使用
echo "重要提示："
echo "如果在新终端中无法使用 node 命令，运行："
echo "  source ~/.zshrc"
echo "或重启终端"
echo ""

# 返回项目目录并安装依赖
echo "================================"
echo "正在安装项目前端依赖..."
echo "================================"
echo ""

cd "$(dirname "$0")"
npm install

echo ""
echo "================================"
echo -e "${GREEN}✓ 全部完成！${NC}"
echo "================================"
echo ""
echo "现在可以运行："
echo "  ./start-backend.sh  # 启动后端"
echo "  ./start-frontend.sh # 启动前端"
echo "  ./run.sh           # 同时启动"
echo ""
