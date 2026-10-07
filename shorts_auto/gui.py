"""쇼츠 자동 편집기 — 화면 프로그램.
실행: start_gui.bat 더블클릭 (또는 py gui.py)
"""
import base64
import json
import os
import queue
import subprocess
import sys
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import cv2

from shorts_auto import updater
from shorts_auto.config import DEFAULTS, load_config
from shorts_auto.ffmpeg_utils import probe
from shorts_auto.project import STEPS, STEP_KEYS, Project, list_clips

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
STATE = ROOT / "output" / ".gui_state.json"

STATUS_TEXT = {"idle": "대기", "running": "진행 중…", "done": "완료 ✔", "error": "오류 ✖"}
STATUS_COLOR = {"idle": "#888888", "running": "#d08000", "done": "#1a8f3a", "error": "#c62828"}

# 설정 창에 보여줄 항목 (키, 설명)
SETTINGS = [
    ("max_total_sec", "최대 영상 길이(초)"),
    ("framing", "9:16 화면 처리 (auto / crop / blur)"),
    ("zoom_punch", "반응 시점 줌 사용 (true/false)"),
    ("zoom_amount", "줌 배율"),
    ("max_clip_sec", "클립 하나 최대 길이(초)"),
    ("max_speedup", "최대 배속"),
    ("max_head_trim", "앞부분 최대 자르기(초)"),
    ("max_tail_trim", "뒷부분 최대 자르기(초)"),
    ("reaction_lead", "반응 전 남길 여유(초)"),
    ("caption_from_filename", "파일명을 자막으로 사용 (true/false)"),
    ("font", "자막 글꼴 (auto = 맑은 고딕)"),
    ("font_size", "자막 크기"),
    ("caption_margin_v", "자막 아래 여백(px)"),
    ("bgm_volume", "BGM 음량 (0~1)"),
    ("sfx_volume", "효과음 음량 (0~1)"),
    ("target_lufs", "목표 음량(LUFS)"),
    ("qc_max_rounds", "자동 수정 최대 횟수"),
]


def open_path(path):
    path = str(path)
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def frame_image(path, t, max_w, max_h):
    """영상의 t초 프레임을 Tk 이미지로."""
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        return None
    h, w = frame.shape[:2]
    scale = min(max_w / w, max_h / h)
    frame = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", frame)
    return tk.PhotoImage(data=base64.b64encode(buf.tobytes())) if ok else None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("쇼츠 자동 편집기")
        self.geometry("1360x880")
        self.minsize(1100, 720)
        self.cfg = load_config(CONFIG)
        self.q = queue.Queue()
        self.busy = False
        self.project = None
        self.preview_img = None
        self.sel = None              # 선택된 클립 경로
        self.preview_t = 0.0
        self.step_status = {k: "idle" for k in STEP_KEYS}

        state = self._load_state()
        self.var_in = tk.StringVar(value=state.get("input", str(ROOT / "input")))
        self.var_out = tk.StringVar(value=state.get("output", str(ROOT / "output" / "short.mp4")))
        self.var_assets = tk.StringVar(value=state.get("assets", str(ROOT / "assets")))

        self._style()
        self._build()
        self.reload_clips()
        self.after(100, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---------------------------------------------------------------- UI
    def _style(self):
        st = ttk.Style(self)
        if "vista" in st.theme_names():
            st.theme_use("vista")
        elif "clam" in st.theme_names():
            st.theme_use("clam")
        base = ("Malgun Gothic", 10) if sys.platform.startswith("win") else ("TkDefaultFont", 10)
        self.option_add("*Font", base)
        st.configure("Big.TButton", font=(base[0], 13, "bold"), padding=(10, 10))
        st.configure("Head.TLabel", font=(base[0], 11, "bold"))
        st.configure("Treeview", rowheight=26)

    def _build(self):
        # 상단: 폴더 설정 + 큰 버튼
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        paths = ttk.Frame(top)
        paths.pack(side="left", fill="x", expand=True)
        for r, (label, var, kind) in enumerate([
            ("원본 영상 폴더", self.var_in, "dir"),
            ("완성 파일", self.var_out, "save"),
            ("BGM·효과음 폴더", self.var_assets, "dir"),
        ]):
            ttk.Label(paths, text=label, width=14).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(paths, textvariable=var).grid(row=r, column=1, sticky="ew", padx=4)
            ttk.Button(paths, text="찾기", width=6,
                       command=lambda v=var, k=kind: self._browse(v, k)).grid(row=r, column=2)
            ttk.Button(paths, text="열기", width=6,
                       command=lambda v=var, k=kind: self._open_var(v, k)).grid(row=r, column=3, padx=(4, 0))
        paths.columnconfigure(1, weight=1)

        btns = ttk.Frame(top)
        btns.pack(side="right", padx=(16, 0))
        self.btn_all = ttk.Button(btns, text="▶  전체 자동 실행", style="Big.TButton", command=self.run_all)
        self.btn_all.grid(row=0, column=0, rowspan=3, sticky="ns", padx=(0, 8))
        ttk.Button(btns, text="⚙ 설정", command=self.open_settings).grid(row=0, column=1, sticky="ew")
        ttk.Button(btns, text="⟳ 업데이트", command=self.do_update).grid(row=1, column=1, sticky="ew", pady=(4, 0))
        ttk.Button(btns, text="🔄 목록 새로고침", command=self.reload_clips).grid(row=0, column=2, sticky="ew", padx=(4, 0))
        ttk.Button(btns, text="? 사용법", command=self.show_help).grid(row=1, column=2, sticky="ew", padx=(4, 0), pady=(4, 0))
        ttk.Button(btns, text="🎬 완성 영상 재생", command=self.play_output).grid(row=0, column=3, sticky="ew", padx=(12, 0))
        ttk.Button(btns, text="📁 결과 폴더", command=self.open_work).grid(row=1, column=3, sticky="ew", padx=(12, 0), pady=(4, 0))
        ttk.Button(btns, text="📄 편집 기록", command=self.open_report).grid(row=2, column=3, sticky="ew", padx=(12, 0), pady=(4, 0))

        # 아래: 로그 (먼저 배치해서 항상 보이게)
        logf = ttk.Frame(self, padding=(10, 0, 10, 8))
        logf.pack(side="bottom", fill="x")
        ttk.Label(logf, text="진행 기록").pack(anchor="w")
        self.logbox = ScrolledText(logf, height=7, state="disabled", bg="#1e1e1e", fg="#e0e0e0",
                                   insertbackground="white")
        self.logbox.pack(fill="x")

        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10)

        # 왼쪽: 단계 패널
        left = ttk.Frame(body, padding=(0, 4, 8, 4))
        body.add(left, weight=0)
        ttk.Label(left, text="작업 단계", style="Head.TLabel").pack(anchor="w")
        ttk.Label(left, text="단계별로 하나씩 실행하거나\n위의 '전체 자동 실행'을 누르세요.",
                  foreground="#666").pack(anchor="w", pady=(0, 6))
        self.step_widgets = {}
        for key, title, desc in STEPS:
            fr = ttk.Frame(left, padding=(6, 2), relief="groove")
            fr.pack(fill="x", pady=1)
            b = ttk.Button(fr, text=title, width=18, command=lambda k=key: self.run_step(k))
            b.grid(row=0, column=0, sticky="w")
            lbl = tk.Label(fr, text=STATUS_TEXT["idle"], fg=STATUS_COLOR["idle"], width=8, anchor="e")
            lbl.grid(row=0, column=1, sticky="e")
            ttk.Label(fr, text=desc, foreground="#666", wraplength=270, font=("TkDefaultFont", 8)).grid(row=1, column=0, columnspan=2, sticky="w")
            self.step_widgets[key] = (b, lbl)
        self.progress = ttk.Progressbar(left, mode="indeterminate")
        self.progress.pack(fill="x", pady=(8, 0))

        # 가운데: 클립 목록 + 미리보기
        center = ttk.Frame(body, padding=4)
        body.add(center, weight=1)
        ttk.Label(center, text="클립 목록 (클릭하면 분석 결과를 볼 수 있어요)", style="Head.TLabel").pack(anchor="w")
        cols = ("len", "use", "react", "frame", "speed", "cap")
        self.tree = ttk.Treeview(center, columns=cols, height=5, selectmode="browse")
        self.tree.heading("#0", text="파일")
        self.tree.column("#0", width=230)
        for c, t, w in zip(cols, ("길이", "사용 구간", "반응", "화면", "속도", "자막"),
                           (60, 110, 60, 60, 55, 220)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center" if c != "cap" else "w")
        self.tree.pack(fill="x")
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        pv = ttk.Frame(center)
        pv.pack(fill="both", expand=True, pady=(6, 0))
        self.canvas_img = tk.Label(pv, bg="#111", width=300, height=300)
        self.canvas_img.pack(side="left", padx=(0, 8))
        self.canvas_img.pack_propagate(False)
        info = ttk.Frame(pv)
        info.pack(side="left", fill="both", expand=True)
        ttk.Label(info, text="움직임 그래프  (초록=사용 구간, 빨강=반응 시점, 클릭=그 장면 보기)").pack(anchor="w")
        self.graph = tk.Canvas(info, height=110, bg="white", highlightthickness=1, highlightbackground="#ccc")
        self.graph.pack(fill="x")
        self.graph.bind("<Button-1>", self._on_graph_click)
        self.graph.bind("<Configure>", lambda e: self._draw_graph())
        self.time_lbl = ttk.Label(info, text="")
        self.time_lbl.pack(anchor="w")
        row = ttk.Frame(info)
        row.pack(fill="x", pady=2)
        ttk.Button(row, text="원본 재생", command=self.play_source).pack(side="left")
        ttk.Button(row, text="편집된 구간 재생", command=self.play_segment).pack(side="left", padx=4)
        ttk.Button(row, text="◀ 시작점", command=lambda: self._jump("start")).pack(side="left")
        ttk.Button(row, text="반응 ▶", command=lambda: self._jump("react")).pack(side="left", padx=4)
        ttk.Button(row, text="끝점 ▶", command=lambda: self._jump("end")).pack(side="left")

        ttk.Label(info, text="프로그램이 내린 결정").pack(anchor="w", pady=(6, 0))
        self.reason = tk.Text(info, height=5, wrap="word", bg="#fafafa", relief="flat")
        self.reason.pack(fill="both", expand=True)
        capf = ttk.Frame(info)
        capf.pack(fill="x", pady=(6, 0))
        caph = ttk.Frame(capf)
        caph.pack(fill="x")
        ttk.Label(caph, text="자막 (한 줄 = 자막 하나, 두 번째 줄은 반응 시점에 표시)").pack(side="left")
        ttk.Button(caph, text="자막 저장", command=self.save_caption).pack(side="right")
        self.cap_text = tk.Text(capf, height=2, wrap="word")
        self.cap_text.pack(fill="x")


    # ----------------------------------------------------------- 상태/로그
    def log(self, msg):
        self.q.put(("log", str(msg)))

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", payload + "\n")
                    self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                elif kind == "step":
                    self._set_step(*payload)
                elif kind == "refresh":
                    self._fill_tree()
                elif kind == "finished":
                    self._set_busy(False)
                    if payload:
                        messagebox.showinfo("완료", payload)
                elif kind == "error":
                    self._set_busy(False)
                    messagebox.showerror("오류", payload)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _set_step(self, key, status):
        self.step_status[key] = status
        _, lbl = self.step_widgets[key]
        lbl.configure(text=STATUS_TEXT[status], fg=STATUS_COLOR[status])

    def _reset_steps(self, from_key=None):
        idx = STEP_KEYS.index(from_key) if from_key else 0
        for k in STEP_KEYS[idx:]:
            self._set_step(k, "idle")

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_all.configure(state=state)
        for b, _ in self.step_widgets.values():
            b.configure(state=state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    # ----------------------------------------------------------- 프로젝트
    def _paths(self):
        return Path(self.var_in.get()), Path(self.var_out.get()), Path(self.var_assets.get())

    def _ensure_project(self, fresh=False):
        inp, out, assets = self._paths()
        p = self.project
        if fresh or p is None or (p.input_dir, p.output, p.assets) != (inp, out, assets):
            self.project = Project(inp, out, assets, self.cfg, self.log)
            self._reset_steps()
        self.project.cfg = self.cfg
        self._save_state()
        return self.project

    def _worker(self, fn, done_msg=None):
        if self.busy:
            return
        self._set_busy(True)

        def run():
            try:
                fn()
                self.q.put(("refresh", None))
                self.q.put(("finished", done_msg))
            except Exception as e:
                self.log(traceback.format_exc())
                self.q.put(("refresh", None))
                self.q.put(("error", str(e)[:1500]))
        threading.Thread(target=run, daemon=True).start()

    def run_step(self, key):
        p = self._ensure_project(fresh=(key == "analyze"))
        if not p.can_run(key):
            prev = STEPS[STEP_KEYS.index(key) - 1][1]
            messagebox.showwarning("순서", f"먼저 '{prev}' 단계를 실행하세요.")
            return
        self._reset_steps(key)

        def job():
            self.q.put(("step", (key, "running")))
            self.log(f"\n=== {STEPS[STEP_KEYS.index(key)][1]} ===")
            try:
                p.run_step(key)
            except Exception:
                self.q.put(("step", (key, "error")))
                raise
            self.q.put(("step", (key, "done")))
        self._worker(job)

    def run_all(self):
        p = self._ensure_project(fresh=True)
        if not list_clips(p.input_dir):
            messagebox.showwarning("영상 없음", f"'{p.input_dir}' 폴더에 영상을 넣어 주세요.")
            return

        def job():
            current = [None]

            def on_step(k, st):
                current[0] = k
                self.q.put(("step", (k, st)))
                if st == "running":
                    self.log(f"\n=== {STEPS[STEP_KEYS.index(k)][1]} ===")
            try:
                p.run_all(on_step)
            except Exception:
                if current[0]:
                    self.q.put(("step", (current[0], "error")))
                raise
            self.q.put(("log", "\n모든 단계 완료! '완성 영상 재생'을 눌러 확인하세요."))
        self._worker(job, "쇼츠 제작이 끝났습니다.\n'🎬 완성 영상 재생'으로 확인하세요.")

    # ----------------------------------------------------------- 클립 목록
    def reload_clips(self):
        self.project = None
        self._reset_steps()
        self._fill_tree()

    def _analysis_for(self, path):
        if not self.project:
            return None, None
        a = next((x for x in self.project.analyses if x.path == str(path)), None)
        s = next((x for x in self.project.segs if x.path == str(path)), None)
        return a, s

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        clips = self.project.clips if self.project else list_clips(Path(self.var_in.get()))
        for p in clips:
            a, s = self._analysis_for(p)
            if a:
                dur = f"{a.duration:.1f}s"
                use = f"{a.trim_start:.2f}~{a.trim_end:.2f}"
                react = "-" if a.reaction_time is None else f"{a.reaction_time:.2f}s"
            else:
                try:
                    dur = f"{probe(p)['duration']:.1f}s"
                except Exception:
                    dur = "?"
                use = react = "분석 전"
            if s:
                use = f"{s.src_start:.2f}~{s.src_end:.2f}"
            frame = {"crop": "크롭", "blur": "블러배경", "scale": "확대"}.get(s.framing, "") if s else ""
            speed = f"{s.speed:.2f}x" if s else ""
            caps = self.project.captions.get(str(p)) if self.project and self.project.analyses else None
            cap = " / ".join(caps) if caps else ("(없음)" if caps is not None else "")
            self.tree.insert("", "end", iid=str(p), text=p.name, values=(dur, use, react, frame, speed, cap))
        if self.sel and self.tree.exists(self.sel):
            self.tree.selection_set(self.sel)
        elif clips:
            self.tree.selection_set(str(clips[0]))

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        self.sel = sel[0]
        a, s = self._analysis_for(self.sel)
        self.preview_t = a.reaction_time if a and a.reaction_time is not None else (a.trim_start if a else 0.5)
        self._show_frame()
        self._draw_graph()
        self.reason.delete("1.0", "end")
        if s:
            self.reason.insert("end", "\n".join("• " + r for r in s.reasons))
        elif a:
            self.reason.insert("end", "\n".join("• " + n for n in a.notes) or "• 특이사항 없음")
            self.reason.insert("end", "\n\n(② 편집 계획을 실행하면 더 자세한 결정이 표시됩니다)")
        else:
            self.reason.insert("end", "아직 분석 전입니다. '① 영상 분석' 또는 '전체 자동 실행'을 누르세요.")
        self.cap_text.delete("1.0", "end")
        caps = self.project.captions.get(self.sel) if self.project else None
        if caps is None:
            side = Path(self.sel).with_suffix(".txt")
            caps = side.read_text(encoding="utf-8").splitlines() if side.exists() else []
        self.cap_text.insert("end", "\n".join(caps))

    def _show_frame(self):
        if not self.sel:
            return
        img = frame_image(self.sel, self.preview_t, 300, 300)
        if img:
            self.preview_img = img
            self.canvas_img.configure(image=img, width=300, height=300)
        self.time_lbl.configure(text=f"미리보기 시각: {self.preview_t:.2f}s")

    def _draw_graph(self):
        g = self.graph
        g.delete("all")
        a, s = self._analysis_for(self.sel) if self.sel else (None, None)
        W, H = max(g.winfo_width(), 200), int(g["height"])
        if not a or not a.times:
            g.create_text(W / 2, H / 2, text="① 영상 분석 후 표시됩니다", fill="#999")
            return
        dur = max(a.duration, 0.1)
        x = lambda t: 4 + (W - 8) * t / dur  # noqa
        st, en = (s.src_start, s.src_end) if s else (a.trim_start, a.trim_end)
        g.create_rectangle(x(st), 0, x(en), H, fill="#dff3e3", outline="")
        top = max(max(a.motion), 1e-6)
        pts = []
        for t, m in zip(a.times, a.motion):
            pts += [x(t), H - 6 - (H - 20) * m / top]
        if len(pts) >= 4:
            g.create_line(*pts, fill="#3366cc", width=2)
        for c in a.scene_cuts:
            g.create_line(x(c), 0, x(c), H, fill="#999", dash=(2, 2))
        if a.reaction_time is not None:
            g.create_line(x(a.reaction_time), 0, x(a.reaction_time), H, fill="#d32f2f", width=2)
            g.create_text(x(a.reaction_time) + 3, 8, text="반응", anchor="w", fill="#d32f2f")
        g.create_line(x(self.preview_t), 0, x(self.preview_t), H, fill="black")
        for t in range(int(dur) + 1):
            g.create_text(x(t), H - 2, text=f"{t}s", anchor="s", fill="#777", font=("TkDefaultFont", 7))

    def _on_graph_click(self, e):
        a, _ = self._analysis_for(self.sel) if self.sel else (None, None)
        if not a:
            return
        W = max(self.graph.winfo_width(), 200)
        self.preview_t = max(0.0, min(a.duration, (e.x - 4) / (W - 8) * a.duration))
        self._show_frame()
        self._draw_graph()

    def _jump(self, where):
        a, s = self._analysis_for(self.sel) if self.sel else (None, None)
        if not a:
            return
        st, en = (s.src_start, s.src_end) if s else (a.trim_start, a.trim_end)
        self.preview_t = {"start": st, "end": max(st, en - 0.05),
                          "react": a.reaction_time if a.reaction_time is not None else st}[where]
        self._show_frame()
        self._draw_graph()

    def save_caption(self):
        if not self.sel:
            return
        lines = [l.strip() for l in self.cap_text.get("1.0", "end").splitlines() if l.strip()]
        side = Path(self.sel).with_suffix(".txt")
        if lines:
            side.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif side.exists():
            side.unlink()
        if self.project:
            self.project.captions[self.sel] = lines
            for s in self.project.segs:
                if s.path == self.sel:
                    s.captions = lines
            # 자막만 바뀌었으니 ⑤단계부터 다시 하면 됨
            self.project.done -= {"finalize", "qc", "export"}
            self._reset_steps("finalize")
        self._fill_tree()
        self.log(f"자막 저장: {side.name} → {lines or '(삭제)'}")
        if self.project and "join" in self.project.done:
            self.log("  ⑤ 자막·BGM·효과음 단계부터 다시 실행하면 반영됩니다.")

    # ----------------------------------------------------------- 재생/열기
    def play_source(self):
        if self.sel:
            open_path(self.sel)

    def play_segment(self):
        if not (self.project and self.sel):
            return
        for i, s in enumerate(self.project.segs):
            f = self.project.work / f"seg_{i:02d}.mp4"
            if s.path == self.sel and f.exists():
                open_path(f)
                return
        messagebox.showinfo("안내", "'③ 클립 렌더링'을 실행한 뒤에 볼 수 있습니다.")

    def play_output(self):
        out = Path(self.var_out.get())
        if out.exists():
            open_path(out)
        else:
            messagebox.showinfo("안내", "아직 완성 영상이 없습니다. '전체 자동 실행'을 눌러 주세요.")

    def open_work(self):
        work = Path(self.var_out.get()).parent
        work.mkdir(parents=True, exist_ok=True)
        open_path(work)

    def open_report(self):
        rp = Path(self.var_out.get()).with_suffix(".report.json")
        if rp.exists():
            open_path(rp)
        else:
            messagebox.showinfo("안내", "'⑦ 최종 MP4 저장' 후에 생성됩니다.")

    def _browse(self, var, kind):
        if kind == "dir":
            d = filedialog.askdirectory(initialdir=var.get() or str(ROOT))
            if d:
                var.set(d)
                if var is self.var_in:
                    self.reload_clips()
        else:
            f = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4", "*.mp4")],
                                             initialfile=Path(var.get()).name)
            if f:
                var.set(f)
        self._save_state()

    def _open_var(self, var, kind):
        p = Path(var.get())
        p = p if kind == "dir" else p.parent
        p.mkdir(parents=True, exist_ok=True)
        open_path(p)

    # ----------------------------------------------------------- 설정
    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("설정")
        win.transient(self)
        frm = ttk.Frame(win, padding=12)
        frm.pack(fill="both", expand=True)
        entries = {}
        for r, (key, label) in enumerate(SETTINGS):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=json.dumps(self.cfg[key]) if isinstance(self.cfg[key], bool)
                             else str(self.cfg[key]))
            ttk.Entry(frm, textvariable=v, width=16).grid(row=r, column=1, padx=8)
            ttk.Label(frm, text=f"기본값 {DEFAULTS[key]}", foreground="#888").grid(row=r, column=2, sticky="w")
            entries[key] = v

        def save():
            new = dict(self.cfg)
            try:
                for key, v in entries.items():
                    raw, default = v.get().strip(), DEFAULTS[key]
                    if isinstance(default, bool):
                        new[key] = raw.lower() in ("true", "1", "yes", "예", "on")
                    elif isinstance(default, int):
                        new[key] = int(float(raw))
                    elif isinstance(default, float):
                        new[key] = float(raw)
                    else:
                        new[key] = raw
            except ValueError:
                messagebox.showerror("설정", f"'{key}' 값이 올바르지 않습니다.", parent=win)
                return
            self.cfg = new
            CONFIG.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
            self.log("설정을 저장했습니다. 다음 실행부터 적용됩니다.")
            win.destroy()

        def reset():
            for key, v in entries.items():
                v.set(json.dumps(DEFAULTS[key]) if isinstance(DEFAULTS[key], bool) else str(DEFAULTS[key]))
        bar = ttk.Frame(frm)
        bar.grid(row=len(SETTINGS), column=0, columnspan=3, pady=(10, 0), sticky="e")
        ttk.Button(bar, text="기본값으로", command=reset).pack(side="left")
        ttk.Button(bar, text="저장", command=save).pack(side="left", padx=6)

    # ----------------------------------------------------------- 업데이트
    def do_update(self):
        if not messagebox.askyesno("업데이트", "GitHub에서 최신 버전을 받아올까요?\n"
                                   "(영상, 설정, BGM 등 내 파일은 그대로 유지됩니다)"):
            return

        def job():
            changed = updater.update(ROOT, self.log)
            if changed:
                self.q.put(("finished", "업데이트했습니다. 프로그램을 닫고 다시 실행해 주세요."))
            else:
                self.q.put(("finished", "이미 최신 버전입니다."))
        self._set_busy(True)

        def run():
            try:
                job()
            except Exception as e:
                self.log(traceback.format_exc())
                self.q.put(("error", f"업데이트 실패: {e}"))
        threading.Thread(target=run, daemon=True).start()

    def show_help(self):
        messagebox.showinfo("사용법", (
            "1) '원본 영상 폴더'에 AI 영상을 넣습니다. 파일명 순서대로 이어집니다 (01_, 02_ …).\n"
            "   파일명이 자막이 됩니다. 예) 01_월급 들어왔다.mp4\n\n"
            "2) '▶ 전체 자동 실행'을 누르면 분석부터 저장까지 한 번에 진행됩니다.\n"
            "   왼쪽 단계 버튼으로 하나씩 실행하며 결과를 확인할 수도 있습니다.\n\n"
            "3) 클립을 클릭하면 움직임 그래프와 프로그램이 내린 결정을 볼 수 있습니다.\n"
            "   그래프를 클릭하면 그 시점의 장면이 보입니다.\n\n"
            "4) 자막을 고치려면 아래 칸에서 수정하고 '자막 저장' → ⑤단계부터 다시 실행.\n\n"
            "5) BGM은 'BGM·효과음 폴더/bgm', 효과음은 '/sfx' 폴더에 넣으세요.\n"
            "   (효과음 파일명에 whoosh = 전환, pop = 반응)\n\n"
            "6) '⟳ 업데이트'로 최신 버전을 받을 수 있습니다."))

    # ----------------------------------------------------------- 상태 저장
    def _load_state(self):
        try:
            return json.loads(STATE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _save_state(self):
        try:
            STATE.parent.mkdir(parents=True, exist_ok=True)
            STATE.write_text(json.dumps({"input": self.var_in.get(), "output": self.var_out.get(),
                                         "assets": self.var_assets.get()}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _on_close(self):
        if self.busy and not messagebox.askyesno("종료", "작업 중입니다. 그래도 닫을까요?"):
            return
        self._save_state()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
