@echo off
REM 以调试模式启动「专用小鹅通 Chrome」(Profile 2)。
REM 前提：先把这个 Chrome 完全关闭（Chrome 单例，不关掉端口不会生效）。
REM 启动后不要关这个窗口，回到抓取脚本运行 grab.py 即可。

set CHROME="C:\Program Files\Google\Chrome\Application\chrome.exe"
set UDD=C:\Users\TimiHub\AppData\Local\Google\Chrome\User Data

echo [1/2] 检查是否有残留 Chrome 进程...
tasklist /FI "IMAGENAME eq chrome.exe" 2>nul | find /I "chrome.exe" >nul
if not errorlevel 1 (
    echo     发现 Chrome 仍在运行。请先完全关闭它，然后再运行本脚本。
    echo     ^(任务管理器里结束所有 chrome.exe，或右键任务栏图标选"退出"^)
    pause
    exit /b 1
)

echo [2/2] 启动带调试端口的 Chrome (Profile 2)...
start "" %CHROME% --remote-debugging-port=9222 --user-data-dir="%UDD%" --profile-directory="Profile 2" --no-first-run --no-default-browser-check

timeout /t 4 /nobreak >nul
echo.
echo 完成。现在可以运行:  python grab.py --url "课程URL"
pause
