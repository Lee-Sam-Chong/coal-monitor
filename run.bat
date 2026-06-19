@echo off
chcp 65001 >nul
title 中国煤炭月度产量数据监测系统

echo ════════════════════════════════════════════════════════
echo   ⛏️  中国煤炭月度产量数据监测系统
echo   China Coal Production Monitor
echo ════════════════════════════════════════════════════════
echo.

REM 检查 Python 是否可用
python --version >nul 2>&1
if errorlevel 1 (
    echo ❌ 错误: 未找到 Python，请先安装 Python 3.8+
    echo    下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo ✅ Python 已检测

REM 安装依赖
echo.
echo 📦 安装依赖...
pip install -r requirements.txt -q
if errorlevel 1 (
    echo ⚠️  部分依赖安装可能有问题，继续尝试...
)

REM 启动服务
echo.
echo 🚀 启动 Web 服务...
echo    访问地址: http://localhost:5000
echo.

python start.py serve

if errorlevel 1 (
    echo.
    echo ❌ 服务启动失败
    echo    请尝试手动运行: python start.py seed  # 生成示例数据
    pause
)
