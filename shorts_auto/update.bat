@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py
%PY% -c "from pathlib import Path; from shorts_auto import updater; updater.update(Path('.').resolve())"
%PY% -m pip install -q -r requirements.txt
echo.
echo 업데이트 완료. 아무 키나 누르면 닫힙니다.
pause >nul
