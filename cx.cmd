@echo off
rem 免安装入口：优先用项目自带的 .venv，其次用系统 python
rem 不带参数时（双击）跑完后暂停，方便看输出
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
set "PYTHONUTF8=1"
set "HERE=%~dp0"
if exist "%HERE%.venv\Scripts\python.exe" (
  "%HERE%.venv\Scripts\python.exe" -m chaoxing %*
) else (
  python -m chaoxing %*
)
if "%~1"=="" pause
