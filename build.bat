@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "C:\Users\admin\.workbuddy\binaries\python\envs\network-monitor\Scripts\python.exe" set "PY=C:\Users\admin\.workbuddy\binaries\python\envs\network-monitor\Scripts\python.exe"
if not defined PY set "PY=python"

echo [1/5] 安装依赖（requirements.txt）...
if exist "requirements-lock.txt" (
  "%PY%" -m pip install -r requirements-lock.txt
) else (
  "%PY%" -m pip install -r requirements.txt
)
if errorlevel 1 goto :fail

echo [2/5] 保留 dist 中的数据库、目标和设置 ...
if not exist "dist" mkdir "dist"

echo [3/5] 生成图标（如缺失）...
if not exist "NetworkMonitor.ico" "%PY%" make_icon.py
if errorlevel 1 goto :fail

echo [4/5] 开始打包 ...
"%PY%" -m PyInstaller --noconfirm NetworkMonitor.spec
if errorlevel 1 goto :fail

echo [5/5] 完成：dist\NetworkMonitor.exe
pause
exit /b 0

:fail
echo 构建失败，请检查上方的错误信息。
pause
exit /b 1
