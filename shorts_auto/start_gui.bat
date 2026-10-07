@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py
%PY% -c "import cv2, numpy, tkinter" 2>nul || (
    echo 처음 실행: 필요한 라이브러리를 설치합니다...
    %PY% -m pip install -r requirements.txt
)
%PY% gui.py
if errorlevel 1 (
    echo.
    echo 오류가 발생했습니다. 위 메시지를 복사해서 알려 주세요.
    pause
)
