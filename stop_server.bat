@echo off
echo ============================================
echo 停止 DeJob 服务器
echo ============================================
echo.

echo 正在查找运行在端口 5000 的进程...
netstat -ano | findstr :5000 | findstr LISTENING > nul

if %errorlevel% equ 0 (
    echo 找到服务器进程，正在停止...

    for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do (
        echo 进程 PID: %%a
        taskkill /PID %%a /F
    )

    echo 服务器已停止
) else (
    echo 未找到运行中的服务器
)

echo.
echo 按任意键退出...
pause > nul