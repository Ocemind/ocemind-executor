@echo off
rem 控制台调试入口：窗口模式下看不到报错，排查问题时用这个。
cd /d "%~dp0"
python run.py %*
if errorlevel 1 pause
