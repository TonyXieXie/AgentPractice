#!/bin/bash

# 一键提交脚本

echo "================================"
echo "  Git 提交助手"
echo "================================"
echo ""

# 显示当前状态
echo "📊 当前状态："
git status --short

echo ""
echo "即将提交："
echo "  ✅ Mac 启动脚本 (*.command)"
echo "  ✅ 安装文档 (*.md)"
echo "  🗑️  移除环境文件 (venv, __pycache__, *.db)"
echo ""

read -p "是否继续提交? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "已取消"
    exit 0
fi

# 添加新文件
echo ""
echo "📝 添加文件..."
git add *.command *.md .gitignore

# 提交
echo ""
echo "💾 提交更改..."
git commit -m "feat: 添加 Mac 启动脚本和文档

- 添加双击启动的 .command 脚本（run, start-backend, start-frontend）
- 添加环境安装脚本（setup-mac, install-nodejs）
- 添加 Mac 安装文档（SETUP-MAC.md, INSTALL-STATUS.md）
- 完善 .gitignore 忽略 Python venv、__pycache__、数据库文件
- 移除之前误提交的环境文件"

echo ""
echo "✅ 提交完成！"
echo ""
echo "下一步可以推送到远程仓库："
echo "  git push origin main"
