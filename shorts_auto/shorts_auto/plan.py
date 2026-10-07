"""분석 결과 → 편집 결정(EDL)."""
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Segment:
    path: str
    name: str
    src_start: float
    src_end: float
    speed: float = 1.0
    framing: str = "scale"          # scale | crop | blur
    crop_cx: float = 0.5
    zoom_at: float | None = None    # 세그먼트 내부 출력 시각
    reaction_at: float | None = None
    captions: list = field(default_factory=list)
    has_audio: bool = True
    motion_level: float = 0.0
    trans_in: float = 0.0           # 이전 클립과 겹치는 전환 길이
    trans_type: str = "fade"
    out_start: float = 0.0          # 최종 타임라인 위치(렌더 후 채움)
    reasons: list = field(default_factory=list)

    @property
    def out_len(self):
        return (self.src_end - self.src_start) / self.speed


def choose_framing(a, cfg):
    target = cfg["width"] / cfg["height"]
    src = a.width / a.height
    if abs(src - target) < 0.04:
        return "scale", 0.5, "원본이 9:16 → 그대로 확대"
    if src < target:
        return "crop", 0.5, "원본이 더 좁음 → 위아래 크롭"
    keep = target / src            # 크롭 시 남는 가로 비율
    cx = min(max(a.center_x, keep / 2), 1 - keep / 2)
    energy = np.asarray(a.col_energy)
    inside = 1.0
    if len(energy):
        lo, hi = int((cx - keep / 2) * len(energy)), int(np.ceil((cx + keep / 2) * len(energy)))
        inside = float(energy[lo:hi].sum())
    mode = cfg["framing"]
    if mode == "auto":
        mode = "crop" if inside >= 0.7 else "blur"
    why = f"가로 영상(움직임 {inside:.0%}가 크롭 영역 안) → {'피사체 중심 크롭' if mode == 'crop' else '블러 배경 + 전체 화면'}"
    return mode, cx, why


def build_plan(analyses, captions, cfg):
    segs = []
    for a in analyses:
        name = a.path.replace("\\", "/").split("/")[-1]
        s = Segment(path=a.path, name=name, src_start=a.trim_start, src_end=a.trim_end,
                    has_audio=a.has_audio, motion_level=a.motion_level, captions=captions.get(a.path, []))
        s.reasons += a.notes
        s.reasons.append(f"트림 {a.trim_start:.2f}~{a.trim_end:.2f}s (원본 {a.duration:.2f}s)")
        s.framing, s.crop_cx, why = choose_framing(a, cfg)
        s.reasons.append(why)
        # 속도: 길거나 움직임이 적으면 조금 빠르게
        length = s.src_end - s.src_start
        if length > cfg["max_clip_sec"]:
            s.speed = min(cfg["max_speedup"], length / cfg["max_clip_sec"])
        elif a.motion_level < 1.0 and length > 2.5:
            s.speed = 1.15
        if s.speed > 1.0:
            s.reasons.append(f"속도 {s.speed:.2f}x")
        if s.out_len > cfg["max_clip_sec"]:   # 그래도 길면 정점 이후를 자른다
            keep_until = max(a.peak_time or 0, s.src_start + cfg["max_clip_sec"] * s.speed)
            s.src_end = min(s.src_end, keep_until + 0.5)
        if a.reaction_time is not None:
            s.reaction_at = max(0.0, (a.reaction_time - s.src_start) / s.speed)
            if cfg["zoom_punch"] and a.peak_time and a.motion_level > 0.5:
                s.zoom_at = s.reaction_at
                s.reasons.append(f"반응 {a.reaction_time:.2f}s에 줌 펀치")
        segs.append(s)

    # 클립 사이 전환: 움직임이 큰 쪽이 끼면 빠른 컷, 잔잔하면 디졸브
    for prev, cur in zip(segs, segs[1:]):
        if prev.motion_level > 4 and cur.motion_level > 4:
            cur.trans_in, cur.trans_type = 0.1, "fade"
        elif prev.motion_level < 1.5 or cur.motion_level < 1.5:
            cur.trans_in, cur.trans_type = 0.35, "fade"
        else:
            cur.trans_in, cur.trans_type = 0.2, "smoothleft"
        cur.trans_in = min(cur.trans_in, prev.out_len / 3, cur.out_len / 3)

    # 전체 길이 제한
    total = sum(s.out_len for s in segs) - sum(s.trans_in for s in segs)
    if total > cfg["max_total_sec"]:
        ratio = (cfg["max_total_sec"] + sum(s.trans_in for s in segs)) / sum(s.out_len for s in segs)
        for s in segs:
            new_len = s.out_len * ratio
            s.src_end = s.src_start + new_len * s.speed
            s.reasons.append(f"전체 길이 제한으로 {new_len:.2f}s로 축소")
    return segs
