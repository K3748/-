"""완성 영상 재분석: 검은 화면, 멈춤, 음량, 규격, 길이를 검사하고 수정 방법을 제안한다."""
import re

from .ffmpeg_utils import probe, run


def inspect(path, cfg):
    info = probe(path)
    log = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
               "-vf", "blackdetect=d=0.15:pix_th=0.08,freezedetect=n=0.002:d=0.6",
               "-af", "ebur128=peak=true", "-f", "null", "-"], capture=True)
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", log)]
    fs = [float(x) for x in re.findall(r"freeze_start: ([\d.]+)", log)]
    fe = [float(x) for x in re.findall(r"freeze_end: ([\d.]+)", log)]
    freezes = [(s, fe[i] if i < len(fe) else info["duration"]) for i, s in enumerate(fs)]
    lufs = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    peak = re.findall(r"Peak:\s+(-?[\d.]+|-inf) dBFS", log)
    issues = []
    if (info["width"], info["height"]) != (cfg["width"], cfg["height"]):
        issues.append({"type": "resolution", "detail": f"{info['width']}x{info['height']}"})
    if info["duration"] > cfg["max_total_sec"] + 2:
        issues.append({"type": "too_long", "detail": f"{info['duration']:.1f}s"})
    for s, e in blacks:
        issues.append({"type": "black", "start": s, "end": e})
    for s, e in freezes:
        issues.append({"type": "freeze", "start": s, "end": e})
    integrated = float(lufs[-1]) if lufs else None
    if integrated is not None and abs(integrated - cfg["target_lufs"]) > 1.5:
        issues.append({"type": "loudness", "detail": f"{integrated:.1f} LUFS"})
    if peak and peak[-1] != "-inf" and float(peak[-1]) > -0.5:
        issues.append({"type": "clipping", "detail": f"peak {peak[-1]} dBFS"})
    return {"duration": info["duration"], "lufs": integrated, "issues": issues}


def fix_plan(issues, segs, cfg, actions=None):
    """시각 기반 문제를 해당 세그먼트의 트림/속도 수정으로 바꾼다. 수정했으면 True."""
    changed = False
    for it in issues:
        if it["type"] not in ("black", "freeze"):
            continue
        mid = (it["start"] + it["end"]) / 2
        idx = max(i for i, s in enumerate(segs) if s.out_start <= mid + 1e-6)
        s = segs[idx]
        a, b = it["start"] - s.out_start, it["end"] - s.out_start
        length = (b - a) * s.speed
        if a <= 0.3 and s.out_len - length > 1.0:                     # 앞부분 문제
            s.src_start += length + 0.05
            if s.zoom_at is not None:
                s.zoom_at = max(0.0, s.zoom_at - (length + 0.05) / s.speed)
            s.reasons.append(f"QC: 앞부분 {it['type']} {length:.2f}s 제거")
            zone = "head"
        elif b >= s.out_len - 0.3 and s.out_len - length > 1.0:       # 뒷부분 문제
            s.src_end -= length + 0.05
            s.reasons.append(f"QC: 뒷부분 {it['type']} {length:.2f}s 제거")
            zone = "tail"
        elif s.speed < cfg["max_speedup"] + 0.25:                     # 중간 → 빨리 넘기기
            s.speed = min(s.speed * 1.3, cfg["max_speedup"] + 0.25)
            if s.zoom_at is not None:
                s.zoom_at /= 1.3
            s.reasons.append(f"QC: 중간 {it['type']} → 속도 {s.speed:.2f}x")
            zone = "mid"
        else:
            continue
        if actions is not None:
            actions.append({"seg": idx, "name": s.name, "type": it["type"], "zone": zone,
                            "start": it["start"], "end": it["end"]})
        changed = True
    return changed


def fix_audio(path, out, cfg, measured_lufs):
    """측정값과 목표의 차이만큼 게인을 주고 리미터로 피크를 막는다."""
    gain = cfg["target_lufs"] - measured_lufs
    run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-c:v", "copy",
         "-af", f"volume={gain:.2f}dB,alimiter=limit=0.84:level=false,aresample=48000",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(out)])
