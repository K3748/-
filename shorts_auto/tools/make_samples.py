"""AI 생성 영상의 흔한 문제(멈춘 시작, 검은 끝, 늦은 반응)를 흉내 낸 테스트 클립을 만든다.
사용법: python tools/make_samples.py [출력폴더=input]
"""
import subprocess
import sys
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else "input")
out.mkdir(parents=True, exist_ok=True)

# (파일명, 해상도, 배경색, 반응 시작 시각, 앞쪽 정지 길이, 뒤쪽 검은 화면 길이)
CLIPS = [
    ("01_월급 들어왔다.mp4", "1280x720", "0x3a5f8f", 2.5, 1.0, 0.6),
    ("02_신나는 월급 댄스.mp4", "720x1280", "0x8f5f3a", 1.0, 0.5, 0.0),
    ("03_결제 알림이 계속 온다.mp4", "1280x720", "0x2f2f2f", 3.0, 0.8, 0.8),
    ("04_kling_20261006_8f3a2c.mp4", "1080x1080", "0x4f8f4f", 1.5, 0.0, 0.5),
]

for name, size, color, react, head, tail in CLIPS:
    w, h = map(int, size.split("x"))
    body = 5.0
    # 캐릭터 역할의 박스: 반응 시각 전에는 거의 정지, 이후 크게 움직임
    x = f"'(W-w)/2+if(gt(t,{react}),sin((t-{react})*9)*W*0.25,sin(t*1.5)*4)'"
    y = f"'(H-h)/2+if(gt(t,{react}),cos((t-{react})*7)*H*0.12,0)'"
    vf = (
        f"[0:v][1:v]overlay=x={x}:y={y}:shortest=1,"
        f"tpad=start_mode=clone:start_duration={head}:stop_mode=add:stop_duration={tail}:color=black[v]"
    )
    audio_freq = 300 + 100 * len(name) % 400
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d={body}",
        "-f", "lavfi", "-i", f"testsrc2=s={w // 4}x{h // 3}:r=30:d={body}",
        "-f", "lavfi", "-i", f"sine=f={audio_freq}:d={body + head + tail}",
        "-filter_complex", vf + f";[2:a]volume=0.05[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(out / name),
    ]
    subprocess.run(cmd, check=True)
    print("생성:", out / name)
