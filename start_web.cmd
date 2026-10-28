@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY_BOOT=python"
where python >nul 2>nul
if errorlevel 1 set "PY_BOOT=py -3"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] 正在创建 Python 环境...
  %PY_BOOT% -m venv .venv
  if errorlevel 1 goto :error
)

echo [2/3] 正在检查下载与转写依赖...
".venv\Scripts\python.exe" -c "import yt_dlp, faster_whisper" >nul 2>nul
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 goto :error
)

echo [3/3] 正在启动取流台...
if not defined HF_ENDPOINT set "HF_ENDPOINT=https://hf-mirror.com"
if not defined HF_HUB_DISABLE_XET set "HF_HUB_DISABLE_XET=1"
if not defined HF_HUB_DISABLE_SYMLINKS_WARNING set "HF_HUB_DISABLE_SYMLINKS_WARNING=1"
set "COOKIE_ARGS="
if defined YTDLP_COOKIES_FROM_BROWSER set "COOKIE_ARGS=--cookies-from-browser %YTDLP_COOKIES_FROM_BROWSER%"
if defined YTDLP_COOKIES_FROM_BROWSER (
  echo [Cookie] 将读取 %YTDLP_COOKIES_FROM_BROWSER% 的本地浏览器 Cookie。
  echo [Cookie] Cookie 只用于本机 yt-dlp，请先关闭该浏览器，避免 Cookie 数据库被锁定。
) else (
  echo [Cookie] 未启用浏览器 Cookie；抖音等要求新鲜 Cookie 的页面请先设置 YTDLP_COOKIES_FROM_BROWSER=chrome 或 edge。
)
set "APP_PORT=8877"
".venv\Scripts\python.exe" -c "import socket; s=socket.socket(); busy=s.connect_ex(('127.0.0.1', 8877)) == 0; s.close(); raise SystemExit(1 if busy else 0)" >nul 2>nul
if errorlevel 1 (
  echo 端口 8877 已被占用，将自动使用一个空闲端口。
  set "APP_PORT=0"
)
".venv\Scripts\python.exe" web_app.py --port %APP_PORT% --open-browser %COOKIE_ARGS%
goto :eof

:error
echo.
echo 启动失败，请检查 Python 和网络连接。
pause
exit /b 1
