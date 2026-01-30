@echo off
echo ============================================
echo DeJob 招聘聚合器 - 服务器重启脚本
echo ============================================
echo.

:: 停止当前运行的服务器
echo [1] 停止当前服务器...
netstat -ano | findstr :5000 > nul
if %errorlevel% equ 0 (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr :5000 ^| findstr LISTENING') do (
        echo 找到进程 PID: %%a
        taskkill /PID %%a /F > nul 2>&1
        if %errorlevel% equ 0 (
            echo 成功停止进程
        ) else (
            echo 停止进程失败，请手动关闭
        )
    )
) else (
    echo 未找到运行中的服务器
)

:: 等待2秒
timeout /t 2 /nobreak > nul

:: 检查环境变量
echo.
echo [2] 检查 Gmail OAuth2 配置...
if "%GMAIL_CLIENT_ID%"=="" (
    echo ⚠️  GMAIL_CLIENT_ID 未设置
    echo    请设置环境变量: set GMAIL_CLIENT_ID=您的客户端ID
) else (
    echo ✅ GMAIL_CLIENT_ID 已设置
)

if "%GMAIL_CLIENT_SECRET%"=="" (
    echo ⚠️  GMAIL_CLIENT_SECRET 未设置
    echo    请设置环境变量: set GMAIL_CLIENT_SECRET=您的客户端密钥
) else (
    echo ✅ GMAIL_CLIENT_SECRET 已设置
)

echo.
echo [3] 启动服务器...
echo 访问地址: http://localhost:5000
echo 按 Ctrl+C 停止服务器
echo ============================================
echo.

:: 启动服务器
python bacnked.py