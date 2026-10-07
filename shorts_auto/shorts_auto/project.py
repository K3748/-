"""단계별로 실행할 수 있는 편집 프로젝트. GUI와 명령줄이 함께 사용한다.

각 단계는 self.changes 에 "무엇을 / 이전 → 이후 / 왜 / 근거 규칙" 을 기록한다.
"""
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from . import learn, qc
from .analyze import decide, measure
from .captions import text_for_clip
from .ffmpeg_utils import probe
from .plan import build_plan
from .render import finalize, join_segments, render_segment

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

STEPS = [
    ("analyze", "영상 분석", "움직임·밝기·선명도를 측정해 쓸 구간과 반응 시점을 찾습니다"),
    ("plan", "편집 계획", "9:16 화면, 속도, 줌, 전환, 훅, 길이를 결정합니다"),
    ("render", "클립 렌더링", "클립마다 1080×1920 영상으로 만듭니다"),
    ("join", "클립 연결", "전환 효과로 클립을 이어 붙입니다"),
    ("finalize", "자막·BGM·효과음", "자막, 배경음악, 효과음, 음량을 처리합니다"),
    ("qc", "검사·자동 수정", "완성본을 다시 분석해 문제를 찾고 고칩니다"),
    ("export", "MP4 저장", "결과 파일과 편집 기록을 저장합니다"),
]
STEP_KEYS = [s[0] for s in STEPS]
STEP_TITLE = {k: t for k, t, _ in STEPS}


def list_clips(input_dir: Path):
    if not input_dir.is_dir():
        return []
    return sorted(p for p in input_dir.iterdir() if p.suffix.lower() in VIDEO_EXT)


class Project:
    def __init__(self, input_dir: Path, output: Path, assets: Path, cfg: dict, log=print, seed=None):
        self.input_dir, self.output, self.assets = Path(input_dir), Path(output), Path(assets)
        self.cfg, self.log, self.seed = cfg, log, seed
        self.work = self.output.parent / f"_work_{self.output.stem}"
        self.clips = list_clips(self.input_dir)
        self.analyses, self.captions, self.segs, self.seg_files = [], {}, [], []
        self.mix, self.qc_rounds, self.qc_actions, self.done = None, [], [], set()
        self.changes = {k: [] for k in STEP_KEYS}
        self._cur = None

    # ---- 변경 기록 ---------------------------------------------------
    def change(self, target, item, before, after, reason, basis=""):
        rec = {"step": self._cur, "target": target, "item": item, "before": before, "after": after,
               "reason": reason, "basis": basis}
        self.changes[self._cur].append(rec)

    # ---- 단계 실행 ---------------------------------------------------
    def can_run(self, key):
        idx = STEP_KEYS.index(key)
        return idx == 0 or STEP_KEYS[idx - 1] in self.done

    def run_step(self, key):
        if not self.can_run(key):
            raise RuntimeError(f"먼저 이전 단계를 실행하세요: {STEP_TITLE[STEP_KEYS[STEP_KEYS.index(key) - 1]]}")
        self.work.mkdir(parents=True, exist_ok=True)
        idx = STEP_KEYS.index(key)
        for k in STEP_KEYS[idx:]:
            self.changes[k] = []
        self._cur = key
        getattr(self, f"step_{key}")()
        self.done = {k for k in self.done if STEP_KEYS.index(k) < idx} | {key}

    def run_all(self, on_step=None):
        for key in STEP_KEYS:
            if on_step:
                on_step(key, "running")
            self.run_step(key)
            if on_step:
                on_step(key, "done")

    # ---- 각 단계 -----------------------------------------------------
    def step_analyze(self):
        self.clips = list_clips(self.input_dir)
        if not self.clips:
            raise RuntimeError(f"'{self.input_dir}' 폴더에 영상이 없습니다.")
        self.log(f"영상 {len(self.clips)}개 분석")
        self.analyses, self.captions = [], {}
        for p in self.clips:
            a = decide(measure(p, self.cfg["analysis_fps"]), self.cfg)
            self.analyses.append(a)
            self.captions[a.path] = text_for_clip(p, self.cfg, self.log)
            rt = "없음" if a.reaction_time is None else f"{a.reaction_time:.2f}s"
            self.log(f"  - {p.name}: {a.duration:.2f}s → 사용 {a.trim_start:.2f}~{a.trim_end:.2f}s, 반응 {rt}")
            if a.trim_start > 0.01:
                why = "; ".join(n for n in a.notes if "대기" in n or "초반" in n) or "시작 부분 정지·검은·흐린 프레임"
                basis = "hook_sec" if "대기" in why else "max_head_trim"
                self.change(p.name, "시작점", "0.00s", f"{a.trim_start:.2f}s", why, basis)
            if a.trim_end < a.duration - 0.01:
                why = "; ".join(n for n in a.notes if "끝" in n or "후반" in n) or "끝부분 정지·검은·흐린 프레임"
                self.change(p.name, "끝점", f"{a.duration:.2f}s", f"{a.trim_end:.2f}s", why, "max_tail_trim")
            if a.reaction_time is not None:
                self.change(p.name, "반응 시점", "-", f"{a.reaction_time:.2f}s",
                            "움직임이 가장 급격히 커지는 순간", "hook_sec")
            caps = self.captions[a.path]
            src = "파일명/자막 파일" if caps else "자막 없음(파일명이 자동 생성 이름)"
            self.change(p.name, "자막", "-", " / ".join(caps) or "(없음)", src)

    def step_plan(self):
        self.segs = build_plan(self.analyses, self.captions, self.cfg)
        tgt = self.cfg["width"] / self.cfg["height"]
        for s, a in zip(self.segs, self.analyses):
            label = {"crop": "피사체 중심 크롭", "blur": "블러 배경 + 전체", "scale": "확대"}[s.framing]
            self.change(s.name, "9:16 화면", f"{a.width}x{a.height}", label,
                        next((r for r in s.reasons if "→" in r and ("영상" in r or "원본" in r)), ""))
            if abs(s.src_start - a.trim_start) > 0.01:
                self.change(s.name, "시작점", f"{a.trim_start:.2f}s", f"{s.src_start:.2f}s",
                            next((r for r in s.reasons if "훅" in r), "길이 조정"), "hook_sec")
            if abs(s.src_end - a.trim_end) > 0.01:
                self.change(s.name, "끝점", f"{a.trim_end:.2f}s", f"{s.src_end:.2f}s",
                            "클립/전체 길이 제한", "max_total_sec")
            from .audio_gen import MOOD_KO
            self.change(s.name, "분위기", "-", MOOD_KO[s.mood], s.mood_reason)
            if s.speed != 1.0:
                self.change(s.name, "속도", "1.00x", f"{s.speed:.2f}x",
                            "클립이 길거나 움직임이 적어 템포를 높임")
            if s.zoom_at is not None:
                self.change(s.name, "줌 펀치", "없음", f"{s.zoom_at:.2f}s에 {self.cfg['zoom_amount']}배",
                            "반응 순간을 강조")
            if s.trans_in:
                kind = "빠른 컷" if s.trans_in <= 0.12 else ("디졸브" if s.trans_type == "fade" else "슬라이드")
                self.change(s.name, "들어오는 전환", "-", f"{kind} {s.trans_in:.2f}s",
                            "앞뒤 클립 움직임 크기에 맞춤 (둘 다 크면 빠르게, 잔잔하면 부드럽게)")
            self.log(f"  - {s.name}: {s.out_len:.2f}s, {label}")
        total = sum(s.out_len for s in self.segs) - sum(s.trans_in for s in self.segs)
        self.change("전체", "예상 길이", "-", f"{total:.2f}s",
                    f"최대 {self.cfg['max_total_sec']:.0f}s 이내", "max_total_sec")

    def step_render(self):
        self.seg_files = []
        W, H, fps = self.cfg["width"], self.cfg["height"], self.cfg["fps"]
        for i, s in enumerate(self.segs):
            f = self.work / f"seg_{i:02d}.mp4"
            self.log(f"  렌더링 {i + 1}/{len(self.segs)}: {s.name}")
            src = probe(s.path)
            render_segment(s, self.cfg, f)
            self.seg_files.append(f)
            self.change(s.name, "해상도/프레임", f"{src['width']}x{src['height']} {src['fps']:.0f}fps",
                        f"{W}x{H} {fps}fps", "YouTube Shorts 세로 규격")
            self.change(s.name, "길이", f"{src['duration']:.2f}s", f"{probe(f)['duration']:.2f}s",
                        "분석·계획에서 정한 구간만 사용")

    def step_join(self):
        join_segments(self.segs, self.seg_files, self.work / "joined.mp4")
        before = sum(probe(f)["duration"] for f in self.seg_files)
        after = probe(self.work / "joined.mp4")["duration"]
        self.change("전체", "연결 길이", f"{before:.2f}s(합계)", f"{after:.2f}s",
                    f"전환 {len(self.segs) - 1}곳에서 겹침", "")
        self.log(f"  연결 완료: {after:.2f}s")

    def step_finalize(self):
        self.mix = finalize(self.work / "joined.mp4", self.segs, self.cfg, self.assets, self.work,
                            self.work / "final.mp4", self.seed, self.log)
        n_caps = sum(len(s.captions) for s in self.segs)
        self.change("전체", "자막", "0개", f"{n_caps}개",
                    f"하단 {self.cfg['caption_margin_v']}px, 좌우 {self.cfg['caption_margin_h']}px 안전 영역",
                    "caption_margin_v")
        from .audio_gen import MOOD_KO
        names = {"whoosh": "슉(전환)", "pop": "뿅", "ding": "띵동(알림)", "boing": "띠용(놀람)",
                 "sadtrombone": "빠밤~(슬픔)", "sparkle": "반짝(신남)"}
        if self.mix.get("bgm_generated"):
            flow = " → ".join(MOOD_KO[m] for _, m in self.mix["bgm_generated"])
            fs = {k: v for k, v in self.mix.get("sources", {}).items() if k.startswith("bgm:")}
            how = "Freesound CC0 음원" if fs else "자동 생성"
            self.change("전체", "BGM", "없음", f"{how}: {flow}",
                        "bgm 폴더가 비어 있어 클립별 분위기에 맞춘 음악 사용 (분위기 바뀌는 곳은 크로스페이드)"
                        + "".join(f"\n - {MOOD_KO[k[4:]]}: '{v['name']}' by {v['username']} ({v['url']})"
                                  for k, v in fs.items()))
        fsx = {k: v for k, v in self.mix.get("sources", {}).items() if k.startswith("sfx:")}
        if fsx:
            self.change("전체", "받아온 효과음", "-", f"{len(fsx)}종 (Freesound CC0)",
                        "".join(f"\n - {k[4:]}: '{v['name']}' by {v['username']} ({v['url']})" for k, v in fsx.items()))
        else:
            self.change("전체", "BGM", "없음", self.mix["bgm"] or "없음",
                        "bgm 폴더의 음악 사용 · 원본 소리가 클 때 자동으로 낮춤(더킹)")
        from collections import Counter
        cnt = Counter(k for _, k in self.mix["sfx_cues"])
        self.change("전체", "효과음", "없음", ", ".join(f"{names.get(k, k)} {n}" for k, n in cnt.items()) or "없음",
                    "전환에는 슉, 반응 시점에는 분위기별 효과음, 알림·결제 장면에는 띵동")
        self.change("전체", "음량 목표", "원본", f"{self.cfg['target_lufs']} LUFS"
                    + (f" (1차 보정 {self.cfg['lufs_pre_offset']:+.1f}dB)" if self.cfg['lufs_pre_offset'] else ""),
                    "YouTube 음량 기준", "target_lufs")
        self.log(f"  BGM: {self.mix['bgm'] or '없음'}, 효과음 {len(self.mix['sfx_cues'])}개")

    def step_qc(self):
        self.qc_rounds, self.qc_actions = [], []
        final = self.work / "final.mp4"
        first_lufs = None
        for rnd in range(self.cfg["qc_max_rounds"] + 1):
            result = qc.inspect(final, self.cfg)
            self.qc_rounds.append(result)
            if first_lufs is None and result["lufs"] is not None and result["lufs"] > -60:
                first_lufs = result["lufs"]
            for it in result["issues"]:
                self.log(f"  ! 문제 발견: {it}")
            if not result["issues"]:
                break
            acts = []
            if rnd < self.cfg["qc_max_rounds"] and qc.fix_plan(result["issues"], self.segs, self.cfg, acts):
                for a in acts:
                    zone = {"head": "앞부분", "tail": "뒷부분", "mid": "중간"}[a["zone"]]
                    fix = "속도 높임" if a["zone"] == "mid" else "잘라냄"
                    kind = {"black": "검은 화면", "freeze": "멈춘 화면"}[a["type"]]
                    self.change(a["name"], f"{zone} {kind}", f"{a['start']:.2f}~{a['end']:.2f}s", fix,
                                "완성본 재검사에서 발견", "max_tail_trim" if a["zone"] == "tail" else
                                ("max_head_trim" if a["zone"] == "head" else ""))
                self.qc_actions += acts
                self.log("  → 편집 계획을 고쳐 다시 렌더링합니다")
                cur = self._cur
                for k in ("render", "join", "finalize"):
                    self._cur = k
                    self.changes[k] = []
                    getattr(self, f"step_{k}")()
                self._cur = cur
                continue
            break
        for _ in range(2):
            if not any(i["type"] in ("loudness", "clipping") for i in result["issues"]):
                break
            self.log(f"  → 음량 재보정 ({result['lufs']} LUFS)")
            before = result["lufs"]
            qc.fix_audio(final, self.work / "final_fixed.mp4", self.cfg, result["lufs"])
            (self.work / "final_fixed.mp4").replace(final)
            result = qc.inspect(final, self.cfg)
            self.qc_rounds.append(result)
            self.change("전체", "음량", f"{before} LUFS", f"{result['lufs']} LUFS",
                        "목표와 1.5dB 이상 차이 → 게인 보정 + 리미터", "target_lufs")
        if any(i["type"] == "silent" for i in result["issues"]):
            self.change("전체", "소리 없음", "-", "무음 영상으로 저장",
                        "원본 영상들에 소리가 없고 BGM 폴더도 비어 있습니다. "
                        "BGM·효과음 폴더의 bgm 폴더에 음악 파일(mp3/wav)을 넣고 다시 실행하면 해결됩니다.")
            self.log("  ※ 소리가 없습니다 → 'BGM·효과음 폴더/bgm'에 음악을 넣고 다시 실행하세요.")
            result = dict(result, issues=[i for i in result["issues"] if i["type"] != "silent"])
        left = result["issues"]
        if not self.changes["qc"]:
            self.change("전체", "검사 결과", "-", "문제 없음", "검은 화면·멈춤·음량·피크·규격·길이 검사 통과")
        elif left:
            self.change("전체", "남은 문제", "-", str(left), "자동으로 고치지 못함 — 원본 확인 필요")
        self.log("  검사 통과" if not left else f"  자동으로 고치지 못한 문제: {left}")
        learn.record_qc({
            "clips": len(self.segs), "first_lufs": first_lufs,
            "target_lufs": self.cfg["target_lufs"] + self.cfg["lufs_pre_offset"],
            "tail_fixes": sum(1 for a in self.qc_actions if a["zone"] == "tail"),
            "head_fixes": sum(1 for a in self.qc_actions if a["zone"] == "head"),
            "mid_fixes": sum(1 for a in self.qc_actions if a["zone"] == "mid"),
            "remaining": len(left)})

    def step_export(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.work / "final.mp4", self.output)
        last = self.qc_rounds[-1] if self.qc_rounds else probe(self.work / "final.mp4")
        self.change("전체", "저장", "-", str(self.output), f"길이 {last['duration']:.2f}s")
        report = {"rounds": self.qc_rounds, "mix": self.mix, "changes": self.changes,
                  "segments": [asdict(s) for s in self.segs],
                  "analyses": [a.summary() for a in self.analyses]}
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                    encoding="utf-8")
        self.log(f"완료: {self.output} (길이 {last['duration']:.2f}s)")

    @property
    def report_path(self):
        return self.output.with_suffix(".report.json")
