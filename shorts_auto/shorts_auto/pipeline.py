import json
import shutil
from dataclasses import asdict
from pathlib import Path

from . import qc
from .analyze import decide, measure
from .captions import text_for_clip
from .plan import build_plan
from .render import finalize, join_segments, render_segment

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def run_pipeline(input_dir: Path, output: Path, assets: Path, cfg, seed=None, log=print):
    clips = sorted(p for p in input_dir.iterdir() if p.suffix.lower() in VIDEO_EXT)
    if not clips:
        raise SystemExit(f"'{input_dir}' 폴더에 영상이 없습니다.")
    work = output.parent / f"_work_{output.stem}"
    work.mkdir(parents=True, exist_ok=True)

    log(f"[1/5] 영상 {len(clips)}개 분석")
    analyses, captions = [], {}
    for p in clips:
        a = decide(measure(p, cfg["analysis_fps"]), cfg)
        analyses.append(a)
        captions[a.path] = text_for_clip(p, cfg, log)
        log(f"  - {p.name}: {a.duration:.2f}s → 사용 {a.trim_start:.2f}~{a.trim_end:.2f}s, "
            f"반응 {a.reaction_time if a.reaction_time is None else round(a.reaction_time, 2)}s, "
            f"자막 {captions[a.path] or '없음'}")

    log("[2/5] 편집 계획 수립")
    segs = build_plan(analyses, captions, cfg)
    report = {"rounds": []}
    for rnd in range(cfg["qc_max_rounds"] + 1):
        log(f"[3/5] 렌더링 (회차 {rnd + 1})")
        files = []
        for i, s in enumerate(segs):
            f = work / f"seg_{i:02d}.mp4"
            render_segment(s, cfg, f)
            files.append(f)
        joined = work / "joined.mp4"
        join_segments(segs, files, joined)
        log("[4/5] 자막 · BGM · 효과음 · 음량 처리")
        mix = finalize(joined, segs, cfg, assets, work, work / "final.mp4", seed)

        log("[5/5] 완성본 검사")
        result = qc.inspect(work / "final.mp4", cfg)
        report["rounds"].append(result)
        for it in result["issues"]:
            log(f"  ! {it}")
        if not result["issues"]:
            log("  문제 없음")
            break
        if rnd < cfg["qc_max_rounds"] and qc.fix_plan(result["issues"], segs, cfg):
            log("  → 편집 계획을 수정해 다시 렌더링합니다")
            continue
        if any(i["type"] in ("loudness", "clipping") for i in result["issues"]):
            for _ in range(2):
                log(f"  → 음량 재보정 ({result['lufs']} LUFS)")
                qc.fix_audio(work / "final.mp4", work / "final_fixed.mp4", cfg, result["lufs"])
                (work / "final_fixed.mp4").replace(work / "final.mp4")
                result = qc.inspect(work / "final.mp4", cfg)
                report["rounds"].append(result)
                if not any(i["type"] in ("loudness", "clipping") for i in result["issues"]):
                    break
        break

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(work / "final.mp4", output)
    report.update(mix=mix, segments=[asdict(s) for s in segs],
                  analyses=[a.summary() for a in analyses])
    rp = output.with_suffix(".report.json")
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"\n완료: {output}  (길이 {report['rounds'][-1]['duration']:.2f}s, "
        f"음량 {report['rounds'][-1]['lufs']} LUFS)\n편집 결정 기록: {rp}")
    return report
