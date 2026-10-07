"""FFmpeg 렌더링: 클립별 정규화 → 전환 연결 → 자막/BGM/효과음/음량."""
from pathlib import Path

from .captions import write_ass
from .ffmpeg_utils import run, probe
from .sfx import find_bgm, find_sfx

ENC = ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p"]


def _framing_filter(s, cfg):
    W, H = cfg["width"], cfg["height"]
    if s.framing == "blur":
        return (f"split=2[bg][fg];[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur=25:2,eq=brightness=-0.08[bgb];[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
                f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    if s.framing == "crop":
        return (f"scale={W}:{H}:force_original_aspect_ratio=increase,"
                f"crop={W}:{H}:x='min(max({s.crop_cx:.4f}*iw-{W / 2},0),iw-{W})':y='(ih-{H})/2'")
    return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"


def render_segment(s, cfg, out: Path):
    fps, W, H = cfg["fps"], cfg["width"], cfg["height"]
    dur = s.out_len
    v = f"[0:v]setpts=(PTS-STARTPTS)/{s.speed:.4f},fps={fps},{_framing_filter(s, cfg)}"
    if s.zoom_at is not None:
        z, t0 = cfg["zoom_amount"], s.zoom_at
        v += (f",zoompan=z='if(gte(it,{t0:.3f}),min({z},1+(it-{t0:.3f})*{(z - 1) / 0.15:.4f}),1)'"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps}")
    v += ",setsar=1,format=yuv420p[v]"
    inputs = ["-ss", f"{s.src_start:.3f}", "-t", f"{(s.src_end - s.src_start):.3f}", "-i", s.path]
    if s.has_audio:
        tempo = f"atempo={s.speed:.4f}," if abs(s.speed - 1) > 1e-3 else ""
        a = f"[0:a]asetpts=PTS-STARTPTS,{tempo}aresample=48000,aformat=channel_layouts=stereo,apad[a]"
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        a = "[1:a]anull[a]"
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", f"{v};{a}",
         "-map", "[v]", "-map", "[a]", "-t", f"{dur:.3f}", *ENC, "-c:a", "aac", "-b:a", "192k", str(out)])
    return probe(out)["duration"]


def join_segments(segs, files, out: Path):
    """xfade/acrossfade로 연결하고 각 세그먼트의 최종 타임라인 위치를 기록한다."""
    if len(files) == 1:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(files[0]), "-c", "copy", str(out)])
        segs[0].out_start = 0.0
        return
    inputs, parts = [], []
    for f in files:
        inputs += ["-i", str(f)]
    lens = [probe(f)["duration"] for f in files]
    cum, vlab, alab = lens[0], "[0:v]", "[0:a]"
    segs[0].out_start = 0.0
    for i in range(1, len(files)):
        td = max(0.04, segs[i].trans_in)
        off = cum - td
        segs[i].out_start = off
        parts.append(f"{vlab}[{i}:v]xfade=transition={segs[i].trans_type}:duration={td:.3f}:offset={off:.3f}[v{i}]")
        parts.append(f"{alab}[{i}:a]acrossfade=d={td:.3f}[a{i}]")
        vlab, alab = f"[v{i}]", f"[a{i}]"
        cum = off + lens[i]
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(parts),
         "-map", vlab, "-map", alab, *ENC, "-c:a", "aac", "-b:a", "192k", str(out)])


def caption_events(segs, total):
    events = []
    for i, s in enumerate(segs):
        if not s.captions:
            continue
        nxt = segs[i + 1].out_start if i + 1 < len(segs) else total
        start, end = s.out_start + (s.trans_in if i else 0) + 0.05, nxt - 0.05
        n = len(s.captions)
        bounds = [start + (end - start) * k / n for k in range(n + 1)]
        if n >= 2 and s.reaction_at is not None:   # 두 번째 자막을 반응 시점에 맞춤
            r = s.out_start + s.reaction_at
            if bounds[0] + 0.8 < r < bounds[2] - 0.8:
                bounds[1] = r
        for k, text in enumerate(s.captions):
            if bounds[k + 1] - bounds[k] >= 0.6:
                events.append((bounds[k], bounds[k + 1], text))
    return events


def finalize(joined: Path, segs, cfg, assets: Path, work: Path, out: Path, seed=None, log=print):
    total = probe(joined)["duration"]
    write_ass(caption_events(segs, total), cfg, work / "subs.ass")

    inputs = ["-i", str(joined.resolve())]
    graph = ["[0:a]asplit=2[orig][key]"]
    mix = ["[orig]"]
    bgm = find_bgm(assets, seed)
    generated, sources = None, {}
    lib = None
    from . import freesound
    if cfg.get("use_freesound", True) and freesound.get_key():
        lib = freesound.Library(assets, log)
    if not bgm and cfg.get("auto_bgm", True):
        from .audio_gen import build_bgm
        files = {}
        if lib:
            for m in dict.fromkeys(s.mood for s in segs):
                path, info = lib.bgm(m)
                if path:
                    files[m], sources[f"bgm:{m}"] = path, info
        bgm = work / "bgm_generated.wav"
        generated = build_bgm([(s.out_start, s.mood) for s in segs], total, bgm, files=files)
    if bgm:
        inputs += ["-stream_loop", "-1", "-i", str(bgm.resolve())]
        graph.append(f"[1:a]atrim=0:{total:.3f},asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo,"
                     f"volume={cfg['bgm_volume']},afade=t=in:d=0.5,afade=t=out:st={max(0, total - 1.2):.3f}:d=1.2[bgm]")
        # 원본 소리가 클 때 BGM을 자동으로 낮춤(더킹)
        graph.append("[bgm][key]sidechaincompress=threshold=0.05:ratio=6:attack=20:release=350[bgmd]")
        mix.append("[bgmd]")
    else:
        graph.append("[key]anullsink")

    cues = []   # (시각, 종류)
    for i, s in enumerate(segs):
        if i:
            cues.append((max(0, s.out_start - 0.12), "whoosh"))
        if cfg.get("auto_sfx", True):
            from .audio_gen import REACTION_SFX
            if s.ding:
                cues.append((s.out_start + (s.trans_in if i else 0) + 0.1, "ding"))
            if s.reaction_at is not None:
                cues.append((s.out_start + s.reaction_at, REACTION_SFX[s.mood]))
        elif s.zoom_at is not None:
            cues.append((s.out_start + s.zoom_at, "pop"))
    base = inputs.count("-i")
    for k, (t, kind) in enumerate(cues):
        path = find_sfx(assets, kind, lib)
        if lib and f"sfx:{kind}" in lib.credits and path.name == lib.credits[f"sfx:{kind}"]["file"]:
            sources[f"sfx:{kind}"] = lib.credits[f"sfx:{kind}"]
        inputs += ["-i", str(path.resolve())]
        ms = int(t * 1000)
        graph.append(f"[{base + k}:a]aformat=channel_layouts=stereo,adelay={ms}|{ms},volume={cfg['sfx_volume']}[s{k}]")
        mix.append(f"[s{k}]")
    graph.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:duration=first,"
                 f"loudnorm=I={cfg['target_lufs'] + cfg['lufs_pre_offset']:.1f}:TP=-1.5:LRA=11,aresample=48000[aout]")
    graph.append("[0:v]subtitles=subs.ass[vout]")
    run(["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", ";".join(graph),
         "-map", "[vout]", "-map", "[aout]", *ENC, "-c:a", "aac", "-b:a", "192k",
         "-movflags", "+faststart", "-t", f"{total:.3f}", str(out.resolve())], cwd=work)
    return {"bgm": bgm.name if bgm else None, "bgm_generated": generated, "sfx_cues": cues,
            "sources": sources}
