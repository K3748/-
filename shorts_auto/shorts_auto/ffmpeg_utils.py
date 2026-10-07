import json
import subprocess


class FFmpegError(RuntimeError):
    pass


def run(cmd, cwd=None, capture=False):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise FFmpegError(f"명령 실패: {' '.join(map(str, cmd))[:400]}\n{proc.stderr[-2000:]}")
    return proc.stderr if capture else None


def probe(path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True, encoding="utf-8",
    )
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s["codec_type"] == "video"), None)
    a = next((s for s in data.get("streams", []) if s["codec_type"] == "audio"), None)
    if v is None:
        raise FFmpegError(f"비디오 스트림이 없습니다: {path}")
    num, den = (v.get("avg_frame_rate") or "30/1").split("/")
    fps = float(num) / float(den) if float(den) else 30.0
    return {
        "width": int(v["width"]),
        "height": int(v["height"]),
        "fps": fps or 30.0,
        "duration": float(data["format"].get("duration") or v.get("duration") or 0),
        "has_audio": a is not None,
    }
