"""PC 환경 검사: 필요한 프로그램/라이브러리가 설치되어 있는지 확인한다.
사용법:  python check_env.py
"""
import importlib
import platform
import shutil
import subprocess
import sys

OK, NG = "[OK]", "[없음]"
problems = []

print(f"OS      : {platform.system()} {platform.release()} ({platform.machine()})")
print(f"Python  : {sys.version.split()[0]}", OK if sys.version_info >= (3, 9) else NG)
if sys.version_info < (3, 9):
    problems.append("Python 3.9 이상을 설치하세요: https://www.python.org/downloads/")

for tool in ("ffmpeg", "ffprobe"):
    path = shutil.which(tool)
    if path:
        ver = subprocess.run([tool, "-version"], capture_output=True, text=True).stdout.split("\n")[0]
        print(f"{tool:8}: {OK} {ver}")
    else:
        print(f"{tool:8}: {NG}")
        problems.append(
            "FFmpeg 설치 필요 → Windows: winget install Gyan.FFmpeg  /  macOS: brew install ffmpeg  "
            "/  Ubuntu: sudo apt install ffmpeg"
        )

if shutil.which("ffmpeg"):
    filters = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    for f in ("subtitles", "xfade", "loudnorm", "sidechaincompress", "zoompan"):
        has = f" {f} " in filters
        print(f"  filter {f:18}: {OK if has else NG}")
        if not has:
            problems.append(f"FFmpeg에 '{f}' 필터가 없습니다. full 빌드(Gyan.FFmpeg 등)를 설치하세요.")

for mod, pip_name, required in (("cv2", "opencv-python-headless", True),
                                ("numpy", "numpy", True),
                                ("faster_whisper", "faster-whisper", False)):
    try:
        m = importlib.import_module(mod)
        print(f"{mod:15}: {OK} {getattr(m, '__version__', '')}")
    except ImportError:
        print(f"{mod:15}: {NG}{'' if required else ' (선택 사항: 음성 자막 인식용)'}")
        if required:
            problems.append(f"pip install {pip_name}")

print()
if problems:
    print("해결해야 할 항목:")
    for p in dict.fromkeys(problems):
        print("  -", p)
    sys.exit(1)
print("모든 필수 구성요소가 준비되었습니다.")
