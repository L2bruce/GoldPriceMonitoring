@echo off
chcp 65001 >nul
REM ============================================
REM  一键打包 Windows 版金价监控 (GoldPrice)
REM  需先安装 Python 3.9+（勾选 Add to PATH）
REM ============================================
cd /d "%~dp0"

echo [1/3] 准备虚拟环境...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv || goto :fail
)

echo [2/3] 安装依赖...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements-win.txt pyinstaller || goto :fail

echo [3/3] 打包中...
".venv\Scripts\pyinstaller.exe" --noconfirm --clean --noconsole --name GoldPrice ^
    --icon build\icon.ico --collect-data plotly --onefile main.py || goto :fail

echo.
echo ============================================
echo  打包完成: dist\GoldPrice.exe
echo  双击即可运行（绿色版，无需安装）
echo ============================================
pause
exit /b 0

:fail
echo.
echo [失败] 打包出错，请检查上方错误信息
pause
exit /b 1
