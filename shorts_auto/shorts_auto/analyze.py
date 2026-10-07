"""OpenCV로 클립을 분석해 편집 결정을 내린다."""
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

from .ffmpeg_utils import probe


@dataclass
class ClipAnalysis:
    path: str
    width: int
    height: int
    fps: float
    duration: float
    has_audio: bool
    times: list = field(default_factory=list)
    motion: list = field(default_factory=list)
    brightness: list = field(default_factory=list)
    sharpness: list = field(default_factory=list)
    scene_cuts: list = field(default_factory=list)
    center_x: float = 0.5          # 움직임 중심 (0~1)
    center_y: float = 0.5
    col_energy: list = field(default_factory=list)  # 가로 방향 움직임 분포
    trim_start: float = 0.0
    trim_end: float = 0.0
    reaction_time: float | None = None
    peak_time: float | None = None
    motion_level: float = 0.0
    notes: list = field(default_factory=list)

    def summary(self):
        d = asdict(self)
        for k in ("times", "motion", "brightness", "sharpness", "col_energy"):
            d.pop(k)
        return d


def _smooth(x, k=5):
    if len(x) < k:
        return np.asarray(x, dtype=float)
    return np.convolve(x, np.ones(k) / k, mode="same")


def measure(path, analysis_fps=15) -> ClipAnalysis:
    info = probe(path)
    a = ClipAnalysis(path=str(path), **info)
    cap = cv2.VideoCapture(str(path))
    step = max(1, round(a.fps / analysis_fps))
    prev, prev_hist, idx = None, None, 0
    cx_w, cy_w, wsum = 0.0, 0.0, 0.0
    cols = np.zeros(192)
    while True:
        ok = cap.grab()
        if not ok:
            break
        if idx % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = idx / a.fps
            small = cv2.resize(frame, (192, int(192 * a.height / a.width)))
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            a.times.append(t)
            a.brightness.append(float(gray.mean()))
            a.sharpness.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
            if prev is None:
                a.motion.append(0.0)
            else:
                diff = cv2.absdiff(gray, prev).astype(np.float32)
                m = float(diff.mean())
                a.motion.append(m)
                cols += diff.sum(axis=0)
                total = diff.sum()
                if total > 0:
                    ys, xs = np.indices(diff.shape)
                    cx_w += (xs * diff).sum() / total / diff.shape[1] * m
                    cy_w += (ys * diff).sum() / total / diff.shape[0] * m
                    wsum += m
                if cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < 0.5 and gray.mean() > 15:
                    a.scene_cuts.append(t)
            prev, prev_hist = gray, hist
        idx += 1
    cap.release()
    if wsum > 0:
        a.col_energy = (cols / cols.sum()).tolist() if cols.sum() else []
        a.center_x, a.center_y = float(cx_w / wsum), float(cy_w / wsum)
    if not a.duration and a.times:
        a.duration = a.times[-1] + 1 / analysis_fps
    return a


def decide(a: ClipAnalysis, cfg) -> ClipAnalysis:
    """트림 구간과 반응 시점을 결정한다."""
    t = np.array(a.times)
    if len(t) < 4:
        a.trim_start, a.trim_end = 0.0, a.duration
        return a
    motion = _smooth(a.motion, 3)
    bright = np.array(a.brightness)
    sharp = np.array(a.sharpness)
    med_motion = float(np.median(motion[motion > 0])) if (motion > 0).any() else 0.0
    med_sharp = float(np.median(sharp)) or 1.0
    freeze_th = max(0.15, 0.2 * med_motion)

    bad = (bright < 14) | (sharp < 0.3 * med_sharp)          # 검은 화면 / 흐릿함
    frozen = motion < freeze_th                               # 멈춘 화면

    # 1) 앞부분: 검은/흐린/멈춘 프레임 건너뛰기
    start = 0.0
    for i in range(1, len(t)):
        if t[i] > cfg["max_head_trim"]:
            break
        if not bad[i] and not frozen[i]:
            start = max(0.0, t[i - 1])
            break
    else:
        start = min(cfg["max_head_trim"], t[-1] * 0.25)
    # 첫 0.5초 안의 장면 전환(AI 첫 프레임 튐) 이후로 시작
    for c in a.scene_cuts:
        if start <= c < start + 0.5:
            start = c
            a.notes.append(f"초반 장면 튐 제거 ({c:.2f}s)")

    # 2) 뒷부분: 검은/흐린/멈춘 프레임과 마지막 급변(모핑 깨짐) 제거
    end = a.duration
    for i in range(len(t) - 1, 0, -1):
        if a.duration - t[i] > cfg["max_tail_trim"]:
            break
        if not bad[i] and not frozen[i]:
            end = t[i] + 1 / cfg["analysis_fps"]
            break
    tail_zone = (t > end - 0.8) & (t < end)
    if med_motion > 0 and tail_zone.any() and motion[tail_zone].max() > 4 * med_motion + 3:
        spike = t[tail_zone][int(np.argmax(motion[tail_zone]))]
        end = max(spike - 0.05, start + 1.0)
        a.notes.append(f"끝부분 급변(깨짐 의심) 제거 ({spike:.2f}s)")
    for c in a.scene_cuts:
        if end - 0.5 < c < end:
            end = c - 0.03
            a.notes.append(f"후반 장면 튐 제거 ({c:.2f}s)")
    end = min(end, a.duration - cfg["tail_safety"]) if end > start + 1.0 else end
    if end - start < 1.0:   # 너무 많이 잘렸으면 원본 대부분 사용
        start, end = 0.0, a.duration - cfg["tail_safety"]
        a.notes.append("유효 구간이 짧아 트림 최소화")

    # 3) 반응 시점: 움직임이 가장 급격히 증가하는 순간
    zone = (t >= start) & (t <= end)
    tz, mz = t[zone], motion[zone]
    if len(mz) > 4 and mz.max() > freeze_th:
        rise = np.diff(_smooth(mz, 5), prepend=mz[0])
        thr = mz.mean() + 0.5 * mz.std()
        cand = np.where((rise > 0) & (mz > thr))[0]
        r_idx = int(cand[0]) if len(cand) else int(np.argmax(rise))
        # 상승이 시작된 지점까지 거슬러 올라감
        while r_idx > 0 and mz[r_idx - 1] < mz[r_idx] and mz[r_idx - 1] > freeze_th * 1.5:
            r_idx -= 1
        a.reaction_time = float(tz[r_idx])
        a.peak_time = float(tz[int(np.argmax(mz))])
        # 4) 반응까지 너무 오래 기다리면 시작점을 당긴다
        if a.reaction_time - start > cfg["max_wait_before_reaction"]:
            new_start = a.reaction_time - cfg["reaction_lead"]
            a.notes.append(f"반응({a.reaction_time:.2f}s) 전 대기 단축: {start:.2f}→{new_start:.2f}s")
            start = new_start
    a.trim_start, a.trim_end = round(float(start), 3), round(float(end), 3)
    a.motion_level = float(np.mean(motion[zone])) if zone.any() else 0.0
    return a
