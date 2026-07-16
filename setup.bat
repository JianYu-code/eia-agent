@echo off
echo ========================================
echo   恒新环保智能系统 - 环境初始化
echo ========================================
echo.

cd /d "%~dp0"

echo [1/3] 创建 Python 虚拟环境...
python -m venv venv
if %errorlevel% neq 0 (
    echo 错误: 无法创建虚拟环境。请确认 Python 3.12+ 已安装。
    pause
    exit /b 1
)
echo   完成

echo.
echo [2/3] 安装依赖...
venv\Scripts\python -m pip install --upgrade pip -q
venv\Scripts\pip install -r backend\requirements.txt
if %errorlevel% neq 0 (
    echo 错误: 依赖安装失败。请检查网络连接。
    pause
    exit /b 1
)
echo   完成

echo.
echo [3/3] 检查 Ollama...
ollama list >nul 2>&1
if %errorlevel% neq 0 (
    echo 提示: 未检测到 Ollama，请从 https://ollama.com 安装
    echo 安装后运行: ollama pull bge-m3
) else (
    ollama list | find "bge-m3" >nul 2>&1
    if %errorlevel% neq 0 (
        echo 正在拉取 bge-m3 模型...
        ollama pull bge-m3
    ) else (
        echo Ollama + bge-m3 就绪
    )
)

echo.
echo ========================================
echo   初始化完成！
echo ========================================
echo.
echo 下一步:
echo   1. 准备数据目录（见 README.md）
echo   2. venv\Scripts\python scripts\ingest_mineru.py full
echo   3. venv\Scripts\python scripts\scan_xiaozhi.py full
echo   4. cd backend ^&^& ..\venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000
echo.
pause
