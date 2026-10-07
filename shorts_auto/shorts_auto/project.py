"""단계별로 실행할 수 있는 편집 프로젝트. GUI와 명령줄이 함께 사용한다."""
import json
import shutil
from dataclasses import asdict
from pathlib import Path

from . import qc
from .analyze import decide, measure
from .captions import text_for_clip
from .ffmpeg_utils import probe
from .plan import build_plan
from .render import finalize, join_segments, render_segment

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

STEPS = [
    ("analyze", "① 영상 분석", "움직임·밝기·선명도를 측정하고 트림 구간과 반응 시점을 찾습니다"),
    ("plan", "② 편집 계획", "9:16 프레이밍, 속도, 줌, 전환, 자막 배치를 결정합니다"),
    ("render", "③ 클립 렌더링", "클립마다 1080x1920으로 잘라 붙일 준비를 합니다"),
    ("join", "④ 클립 연결", "전환 효과로 클립들을 이어 붙입니다"),
    ("finalize", "⑤ 자막·BGM·효과음", "자막, 배경음악, 효과음을 넣고 음량을 맞춥니다"),
    ("qc", "⑥ 검사·자동 수정", "완성본을 다시 분석해 문제를 찾고 고칩니다"),
    ("export", "⑦ 최종 MP4 저장", "결과 파일과 편집 기록을 저장합니다"),
]
STEP_KEYS = [s[0] for s in STEPS]


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
        self.mix, self.qc_rounds, self.done = None, [], set()

    # ---- 단계 실행 ---------------------------------------------------
    def can_run(self, key):
        idx = STEP_KEYS.index(key)
        return idx == 0 or STEP_KEYS[idx - 1] in self.done

    def run_step(self, key):
        if not self.can_run(key):
            raise RuntimeError(f"먼저 이전 단계를 실행하세요: {STEPS[STEP_KEYS.index(key) - 1][1]}")
        self.work.mkdir(parents=True, exist_ok=True)
        getattr(self, f"step_{key}")()
        # 이 단계 이후 결과는 무효화
        idx = STEP_KEYS.index(key)
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
            self.log(f"  - {p.name}: {a.duration:.2f}s → 사용 {a.trim_start:.2f}~{a.trim_end:.2f}s, "
                     f"반응 {rt}, 자막 {self.captions[a.path] or '없음'}")
            for n in a.notes:
                self.log(f"      · {n}")

    def step_plan(self):
        self.segs = build_plan(self.analyses, self.captions, self.cfg)
        for s in self.segs:
            self.log(f"  - {s.name}: {s.out_len:.2f}s, {s.framing}, 전환 {s.trans_type} {s.trans_in:.2f}s")

    def step_render(self):
        self.seg_files = []
        for i, s in enumerate(self.segs):
            f = self.work / f"seg_{i:02d}.mp4"
            self.log(f"  렌더링 {i + 1}/{len(self.segs)}: {s.name}")
            render_segment(s, self.cfg, f)
            self.seg_files.append(f)

    def step_join(self):
        join_segments(self.segs, self.seg_files, self.work / "joined.mp4")
        self.log(f"  연결 완료: {probe(self.work / 'joined.mp4')['duration']:.2f}s")

    def step_finalize(self):
        self.mix = finalize(self.work / "joined.mp4", self.segs, self.cfg, self.assets, self.work,
                            self.work / "final.mp4", self.seed)
        self.log(f"  BGM: {self.mix['bgm'] or '없음 (assets/bgm 폴더에 넣으면 사용)'}, "
                 f"효과음 {len(self.mix['sfx_cues'])}개")

    def step_qc(self):
        self.qc_rounds = []
        final = self.work / "final.mp4"
        for rnd in range(self.cfg["qc_max_rounds"] + 1):
            result = qc.inspect(final, self.cfg)
            self.qc_rounds.append(result)
            for it in result["issues"]:
                self.log(f"  ! 문제 발견: {it}")
            if not result["issues"]:
                self.log("  문제 없음")
                return
            if rnd < self.cfg["qc_max_rounds"] and qc.fix_plan(result["issues"], self.segs, self.cfg):
                self.log("  → 편집 계획을 고쳐 다시 렌더링합니다")
                self.step_render()
                self.step_join()
                self.step_finalize()
                continue
            break
        for _ in range(2):
            if not any(i["type"] in ("loudness", "clipping") for i in result["issues"]):
                break
            self.log(f"  → 음량 재보정 ({result['lufs']} LUFS)")
            qc.fix_audio(final, self.work / "final_fixed.mp4", self.cfg, result["lufs"])
            (self.work / "final_fixed.mp4").replace(final)
            result = qc.inspect(final, self.cfg)
            self.qc_rounds.append(result)
        left = result["issues"]
        self.log("  수정 후 문제 없음" if not left else f"  자동으로 고치지 못한 문제: {left}")

    def step_export(self):
        self.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.work / "final.mp4", self.output)
        report = {"rounds": self.qc_rounds, "mix": self.mix,
                  "segments": [asdict(s) for s in self.segs],
                  "analyses": [a.summary() for a in self.analyses]}
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                    encoding="utf-8")
        last = self.qc_rounds[-1] if self.qc_rounds else probe(self.output)
        self.log(f"완료: {self.output} (길이 {last['duration']:.2f}s)")

    @property
    def report_path(self):
        return self.output.with_suffix(".report.json")
