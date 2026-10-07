"""쇼츠 자동 편집기 — 화면 프로그램.
실행: start_gui.bat 더블클릭 (또는 py gui.py)

화면 배치는 영상 편집기 공통 관례와 사용성 원칙을 따른다 (UI_RATIONALE 참고).
"""
import base64
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

import cv2

from shorts_auto import knowledge, learn, updater
from shorts_auto.config import DEFAULTS, base_config, load_config, user_overrides
from shorts_auto.ffmpeg_utils import probe
from shorts_auto.project import STEPS, STEP_KEYS, STEP_TITLE, Project, list_clips

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
STATE = ROOT / "data" / "gui_state.json"

# ---- 색상 (어두운 중간 회색: 영상 색 판단을 방해하지 않음) ----------------------
C = {
    "bg": "#1e1f22", "panel": "#2b2d31", "panel2": "#35373c", "border": "#43464d",
    "fg": "#e8e8e8", "muted": "#a3a7ae", "accent": "#4f8cff", "accent_hi": "#6b9fff",
    "ok": "#3fb950", "warn": "#d29922", "err": "#f85149", "sel": "#3b5fa8",
    "trim": "#244a33", "motion": "#6fa8ff", "react": "#ff6b6b",
}
STATUS = {"idle": ("○", C["muted"], "대기"), "running": ("◐", C["warn"], "진행 중"),
          "done": ("●", C["ok"], "완료"), "error": ("✖", C["err"], "오류")}

UI_RATIONALE = [
    ("미리보기는 가운데, 타임라인은 아래, 세부 정보는 오른쪽",
     "Premiere·DaVinci·CapCut 등 영상 편집기의 공통 배치입니다. 보는 대상(영상)을 시선의 중심에 두고 "
     "도구는 가장자리에 둡니다. 사용자는 이미 익숙한 프로그램과 같은 배치를 기대합니다(Jakob의 법칙).",
     ["https://docs.vidrush.ai/docs/video-editor-overview", "https://chatcut.io/docs/editor-overview",
      "https://answers.atlassian.syr.edu/wiki/spaces/whit/pages/152406783"]),
    ("어두운 회색 배경",
     "밝거나 색이 있는 UI는 영상의 색·밝기 판단을 왜곡합니다. 편집기들은 중간~어두운 회색을 써서 "
     "영상이 시각적으로 가장 두드러지게 하고 장시간 작업의 눈부심을 줄입니다.",
     ["https://community.adobe.com/questions-729/premiere-pro-interface-color-light-mode-ui-interest-gauge-1349360",
      "https://factually.co/fact-checks/electronics-tech/dark-video-search-intent-0af802"]),
    ("왼쪽 단계 표시기와 단계별 상태(대기/진행 중/완료/오류)",
     "NN/g의 1번 사용성 원칙 '시스템 상태의 가시성': 지금 무엇이 진행 중인지 항상 알려야 합니다. "
     "순서가 있는 작업은 마법사(wizard)형 단계 표시기로 현재 위치와 남은 단계를 보여줍니다.",
     ["https://uxdesign.cc/part-one-of-10-jakob-nielsens-10-usability-heuristics-for-user-interface-design-571d75aba3c5",
      "https://blog.logrocket.com/ux-design/creating-setup-wizard-when-you-shouldnt/"]),
    ("'전체 자동 실행'은 오른쪽 위에 크게, 강조색은 이 버튼 하나만",
     "Fitts의 법칙: 자주 누르는 주요 버튼은 크고 찾기 쉬워야 합니다. 편집기들의 '내보내기' 버튼도 "
     "오른쪽 위에 있어 같은 위치를 택했습니다. 강조색을 하나만 써서 주요 동작이 바로 구분됩니다.",
     ["https://www.uxtoast.com/ux-laws/fittss-law", "https://helio.zurb.com/ux-research/laws-of-ux/fitts-law/"]),
    ("자주 안 바꾸는 항목(출력·BGM 폴더, 세부 수치)은 설정 창으로",
     "선택지가 많을수록 결정이 느려집니다(Hick의 법칙). 자주 쓰는 것만 보이고 나머지는 필요할 때 "
     "펼치는 점진적 공개(progressive disclosure) 방식입니다.",
     ["https://meshworld.in/blog/web-dev/ux/ux-laws-every-designer-should-know/"]),
    ("결과 미리보기에 쇼츠 UI 가림 영역 표시",
     "쇼츠는 하단 약 300px(설명 펼치면 약 400px), 오른쪽 약 96px, 상단 약 120px이 YouTube UI로 "
     "가려집니다. 편집 화면에서 자막이 가려지는지 바로 확인할 수 있게 표시합니다.",
     ["https://www.clipspeed.ai/blog/youtube-shorts-size.html",
      "https://blitzcutai.com/blog/best-caption-placement-short-form-video"]),
    ("모든 자동 결정에 '이전 → 이후 / 이유 / 근거'를 표시",
     "자동화된 결정은 사용자가 이해하고 확인할 수 있어야 신뢰할 수 있습니다. 근거 규칙과 "
     "출처 링크를 함께 보여주어 무엇을 왜 바꿨는지 추적할 수 있습니다.", []),
]

SETTINGS = [
    ("framing", "9:16 화면 처리 (auto / crop / blur)"),
    ("zoom_punch", "반응 시점 줌 사용 (true/false)"),
    ("zoom_amount", "줌 배율"),
    ("max_clip_sec", "클립 하나 최대 길이(초)"),
    ("max_speedup", "최대 배속"),
    ("reaction_lead", "반응 전 남길 여유(초)"),
    ("caption_from_filename", "파일명을 자막으로 사용 (true/false)"),
    ("font", "자막 글꼴 (auto = 맑은 고딕)"),
    ("font_size", "자막 크기"),
    ("bgm_volume", "BGM 음량 (0~1)"),
    ("sfx_volume", "효과음 음량 (0~1)"),
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


def read_frame(path, t):
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, frame = cap.read()
    cap.release()
    return frame if ok else None


def to_photo(frame, max_w, max_h):
    h, w = frame.shape[:2]
    s = min(max_w / w, max_h / h)
    frame = cv2.resize(frame, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".png", frame)
    return (tk.PhotoImage(data=base64.b64encode(buf.tobytes())), frame.shape[1], frame.shape[0]) if ok else None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("쇼츠 자동 편집기")
        self.geometry("1440x900")
        self.minsize(1200, 760)
        self.configure(bg=C["bg"])
        self.cfg = load_config(CONFIG)
        self.q = queue.Queue()
        self.busy = False
        self.project = None
        self.sel = None
        self.preview_t = 0.0
        self.photo = None
        self.step_status = {k: "idle" for k in STEP_KEYS}
        self.step_filter = "all"

        st = self._load_state()
        self.var_in = tk.StringVar(value=st.get("input", str(ROOT / "input")))
        self.var_out = tk.StringVar(value=st.get("output", str(ROOT / "output" / "short.mp4")))
        self.var_assets = tk.StringVar(value=st.get("assets", str(ROOT / "assets")))
        self.var_mode = tk.StringVar(value="result")
        self.var_safe = tk.BooleanVar(value=True)

        self._style()
        self._build()
        self.reload_clips()
        self.refresh_knowledge()
        self.after(100, self._poll)
        self.after(1500, self._auto_learn)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ================================================================ 스타일
    def _style(self):
        self.font = "Malgun Gothic" if sys.platform.startswith("win") else "TkDefaultFont"
        f = self.font
        self.option_add("*Font", (f, 10))
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=C["panel"], foreground=C["fg"], fieldbackground=C["panel2"],
                     bordercolor=C["border"], lightcolor=C["panel"], darkcolor=C["panel"],
                     troughcolor=C["bg"], focuscolor=C["accent"], font=(f, 10))
        st.configure("TFrame", background=C["panel"])
        st.configure("Bg.TFrame", background=C["bg"])
        st.configure("TLabel", background=C["panel"], foreground=C["fg"])
        st.configure("Muted.TLabel", foreground=C["muted"])
        st.configure("Head.TLabel", font=(f, 11, "bold"))
        st.configure("Title.TLabel", font=(f, 13, "bold"), background=C["bg"])
        st.configure("Bg.TLabel", background=C["bg"], foreground=C["muted"])
        st.configure("TButton", background=C["panel2"], foreground=C["fg"], padding=(10, 5),
                     bordercolor=C["border"])
        st.map("TButton", background=[("active", C["border"]), ("disabled", C["panel"])],
               foreground=[("disabled", C["muted"])])
        st.configure("Accent.TButton", background=C["accent"], foreground="white",
                     font=(f, 12, "bold"), padding=(18, 9), bordercolor=C["accent"])
        st.map("Accent.TButton", background=[("active", C["accent_hi"]), ("disabled", C["border"])])
        st.configure("Small.TButton", padding=(6, 2))
        st.configure("Toggle.TRadiobutton", background=C["panel2"], padding=(10, 4))
        st.map("Toggle.TRadiobutton", background=[("selected", C["sel"])])
        st.configure("TCheckbutton", background=C["panel"])
        st.configure("TEntry", fieldbackground=C["panel2"], foreground=C["fg"], insertcolor=C["fg"])
        st.configure("Treeview", background=C["panel2"], fieldbackground=C["panel2"],
                     foreground=C["fg"], rowheight=24, bordercolor=C["border"])
        st.map("Treeview", background=[("selected", C["sel"])])
        st.configure("Treeview.Heading", background=C["panel"], foreground=C["muted"], relief="flat")
        st.configure("TNotebook", background=C["panel"], borderwidth=0)
        st.configure("TNotebook.Tab", background=C["panel"], foreground=C["muted"], padding=(12, 5))
        st.map("TNotebook.Tab", background=[("selected", C["panel2"])], foreground=[("selected", C["fg"])])
        st.configure("Horizontal.TProgressbar", background=C["accent"], troughcolor=C["bg"])
        st.configure("TPanedwindow", background=C["bg"])

    def _text(self, parent, **kw):
        t = tk.Text(parent, bg=C["panel2"], fg=C["fg"], insertbackground=C["fg"], relief="flat",
                    wrap="word", padx=8, pady=6, highlightthickness=0, **kw)
        t.tag_configure("muted", foreground=C["muted"])
        t.tag_configure("head", font=(self.font, 10, "bold"))
        t.tag_configure("link", foreground=C["accent_hi"], underline=True)
        t.tag_configure("good", foreground=C["ok"])
        return t

    # ================================================================ 화면 구성
    def _build(self):
        # ---- 상단 도구 막대 ----
        bar = ttk.Frame(self, style="Bg.TFrame", padding=(14, 10))
        bar.pack(fill="x")
        ttk.Label(bar, text="쇼츠 자동 편집기", style="Title.TLabel").pack(side="left")
        ttk.Label(bar, text="  원본 폴더", style="Bg.TLabel").pack(side="left", padx=(18, 4))
        ttk.Entry(bar, textvariable=self.var_in, width=34).pack(side="left")
        ttk.Button(bar, text="변경", style="Small.TButton", command=self._pick_input).pack(side="left", padx=4)
        ttk.Button(bar, text="열기", style="Small.TButton",
                   command=lambda: self._open_dir(self.var_in.get())).pack(side="left")

        self.btn_all = ttk.Button(bar, text="▶  전체 자동 실행", style="Accent.TButton", command=self.run_all)
        self.btn_all.pack(side="right")
        ttk.Button(bar, text="🎬 완성본 재생", command=self.play_output).pack(side="right", padx=(0, 12))
        ttk.Button(bar, text="?", width=3, command=self.show_help).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="⟳ 업데이트", command=self.do_update).pack(side="right", padx=(0, 6))
        ttk.Button(bar, text="⚙ 설정", command=self.open_settings).pack(side="right", padx=(0, 6))

        # ---- 하단: 타임라인 / 진행 기록 ----
        bottom = ttk.Notebook(self, height=190)
        bottom.pack(side="bottom", fill="x", padx=10, pady=(0, 10))
        tl = ttk.Frame(bottom, padding=6)
        bottom.add(tl, text="  타임라인  ")
        self.timeline = tk.Canvas(tl, height=56, bg=C["panel2"], highlightthickness=0)
        self.timeline.pack(fill="x")
        self.timeline.bind("<Button-1>", self._on_timeline_click)
        self.timeline.bind("<Configure>", lambda e: self._draw_timeline())
        ttk.Label(tl, text="선택한 클립의 움직임  ·  초록 = 사용 구간  ·  빨강 = 반응 시점  ·  클릭하면 그 장면 미리보기",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        self.graph = tk.Canvas(tl, height=80, bg=C["panel2"], highlightthickness=0)
        self.graph.pack(fill="x")
        self.graph.bind("<Button-1>", self._on_graph_click)
        self.graph.bind("<Configure>", lambda e: self._draw_graph())
        logf = ttk.Frame(bottom, padding=6)
        bottom.add(logf, text="  진행 기록  ")
        self.logbox = ScrolledText(logf, height=8, state="disabled", bg=C["bg"], fg="#d0d0d0",
                                   relief="flat", insertbackground="white")
        self.logbox.pack(fill="both", expand=True)
        self.bottom = bottom

        # ---- 가운데 3단 ----
        body = ttk.PanedWindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        # 왼쪽: 단계
        left = ttk.Frame(body, padding=10, width=250)
        body.add(left, weight=0)
        ttk.Label(left, text="작업 단계", style="Head.TLabel").pack(anchor="w")
        ttk.Label(left, text="단계를 클릭하면 그 단계의 변경 내역이\n오른쪽에 표시됩니다. ▶로 단계만 실행.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 8))
        self.step_rows = {}
        for i, (key, title, desc) in enumerate(STEPS, 1):
            row = tk.Frame(left, bg=C["panel"], highlightthickness=1, highlightbackground=C["border"],
                           cursor="hand2")
            row.pack(fill="x", pady=2)
            icon = tk.Label(row, text="○", fg=C["muted"], bg=C["panel"], font=(self.font, 13), width=2)
            icon.grid(row=0, column=0, rowspan=2, padx=(4, 2))
            name = tk.Label(row, text=f"{i}. {title}", fg=C["fg"], bg=C["panel"], anchor="w",
                            font=(self.font, 10, "bold"))
            name.grid(row=0, column=1, sticky="w")
            info = tk.Label(row, text="대기", fg=C["muted"], bg=C["panel"], anchor="w", font=(self.font, 9))
            info.grid(row=1, column=1, sticky="w")
            run = ttk.Button(row, text="▶", width=3, style="Small.TButton", command=lambda k=key: self.run_step(k))
            run.grid(row=0, column=2, rowspan=2, padx=4, pady=3)
            row.columnconfigure(1, weight=1)
            for w in (row, icon, name, info):
                w.bind("<Button-1>", lambda e, k=key: self.select_step(k))
            self.step_rows[key] = (row, icon, name, info, run)
        self.progress = ttk.Progressbar(left, mode="indeterminate")
        self.progress.pack(fill="x", pady=(12, 4))
        self.status_lbl = ttk.Label(left, text="준비됨", style="Muted.TLabel", wraplength=220)
        self.status_lbl.pack(anchor="w")

        # 가운데: 미리보기
        center = ttk.Frame(body, padding=10)
        body.add(center, weight=1)
        self.clip_title = ttk.Label(center, text="클립을 선택하세요", style="Head.TLabel")
        self.clip_title.pack(anchor="w")
        top = ttk.Frame(center)
        top.pack(fill="x", pady=(4, 0))
        for val, txt in (("result", "결과 9:16"), ("source", "원본")):
            ttk.Radiobutton(top, text=txt, value=val, variable=self.var_mode, style="Toggle.TRadiobutton",
                            command=self._show_frame).pack(side="left", padx=(0, 2))
        ttk.Checkbutton(top, text="쇼츠 UI 가림 영역", variable=self.var_safe,
                        command=self._show_frame).pack(side="left", padx=8)
        self.view = tk.Canvas(center, bg="#000000", highlightthickness=0)
        self.view.pack(fill="both", expand=True, pady=8)
        self.view.bind("<Configure>", lambda e: self._show_frame())
        ctr = ttk.Frame(center)
        ctr.pack(fill="x")
        for txt, cmd in (("◀ 시작점", lambda: self._jump("start")), ("반응", lambda: self._jump("react")),
                         ("끝점 ▶", lambda: self._jump("end"))):
            ttk.Button(ctr, text=txt, style="Small.TButton", command=cmd).pack(side="left", padx=(0, 4))
        ctr2 = ttk.Frame(center)
        ctr2.pack(fill="x", pady=(4, 0))
        ttk.Button(ctr2, text="원본 재생", style="Small.TButton", command=self.play_source).pack(side="left")
        ttk.Button(ctr2, text="결과 클립 재생", style="Small.TButton", command=self.play_segment).pack(side="left", padx=4)
        self.time_lbl = ttk.Label(center, text="", style="Muted.TLabel")
        self.time_lbl.pack(anchor="w", pady=(4, 0))

        # 오른쪽: 인스펙터 탭
        right = ttk.Frame(body, padding=(6, 10, 6, 6), width=420)
        body.add(right, weight=0)
        nb = ttk.Notebook(right)
        nb.pack(fill="both", expand=True)
        self.nb = nb
        self._build_clip_tab(nb)
        self._build_changes_tab(nb)
        self._build_knowledge_tab(nb)
        self._build_rationale_tab(nb)
        self.after(50, lambda: self._init_sash(body))

    def _init_sash(self, body):
        try:
            W = body.winfo_width()
            body.sashpos(0, 270)
            body.sashpos(1, max(700, W - 470))
        except tk.TclError:
            pass

    def _build_clip_tab(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text=" 클립 ")
        ttk.Label(f, text="클립 목록", style="Head.TLabel").pack(anchor="w")
        self.tree = ttk.Treeview(f, columns=("len", "use", "react"), height=6, selectmode="browse")
        self.tree.heading("#0", text="파일")
        self.tree.column("#0", width=180)
        for c, t, w in (("len", "길이", 52), ("use", "사용 구간", 92), ("react", "반응", 56)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="center")
        self.tree.pack(fill="x", pady=(4, 8))
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        ttk.Label(f, text="프로그램이 내린 결정", style="Head.TLabel").pack(anchor="w")
        self.reason = self._text(f, height=9)
        self.reason.pack(fill="both", expand=True, pady=(4, 8))
        h = ttk.Frame(f)
        h.pack(fill="x")
        ttk.Label(h, text="자막", style="Head.TLabel").pack(side="left")
        ttk.Label(h, text="  한 줄 = 자막 하나 · 둘째 줄은 반응 시점에", style="Muted.TLabel").pack(side="left")
        ttk.Button(h, text="저장", style="Small.TButton", command=self.save_caption).pack(side="right")
        self.cap_text = self._text(f, height=3)
        self.cap_text.pack(fill="x", pady=(4, 0))

    def _build_changes_tab(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text=" 변경 내역 ")
        h = ttk.Frame(f)
        h.pack(fill="x")
        self.chg_title = ttk.Label(h, text="전체 단계", style="Head.TLabel")
        self.chg_title.pack(side="left")
        ttk.Button(h, text="전체 보기", style="Small.TButton",
                   command=lambda: self.select_step("all")).pack(side="right")
        self.chg = ttk.Treeview(f, columns=("target", "item", "change"), show="headings", height=12)
        for c, t, w in (("target", "대상", 120), ("item", "항목", 80), ("change", "이전 → 이후", 190)):
            self.chg.heading(c, text=t)
            self.chg.column(c, width=w, anchor="w")
        self.chg.pack(fill="both", expand=True, pady=(6, 6))
        self.chg.bind("<<TreeviewSelect>>", self._on_change_select)
        ttk.Label(f, text="이유와 근거", style="Head.TLabel").pack(anchor="w")
        self.chg_detail = self._text(f, height=8)
        self.chg_detail.pack(fill="x", pady=(4, 0))
        self.chg_items = {}

    def _build_knowledge_tab(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text=" 학습 지식 ")
        ttk.Label(f, text="편집 규칙은 인터넷 자료와 내 영상 검사 결과로 주기적으로 갱신됩니다.",
                  style="Muted.TLabel", wraplength=380).pack(anchor="w")
        self.learn_lbl = ttk.Label(f, text="")
        self.learn_lbl.pack(anchor="w", pady=(2, 0))
        self.kn = ttk.Treeview(f, columns=("value", "base", "state"), height=8, selectmode="browse")
        self.kn.heading("#0", text="규칙")
        self.kn.column("#0", width=170)
        for c, t, w in (("value", "현재", 70), ("base", "기본", 60), ("state", "상태", 60)):
            self.kn.heading(c, text=t)
            self.kn.column(c, width=w, anchor="center")
        self.kn.pack(fill="x", pady=6)
        self.kn.bind("<<TreeviewSelect>>", self._on_rule_select)
        self.kn_detail = self._text(f, height=10)
        self.kn_detail.pack(fill="both", expand=True)
        b = ttk.Frame(f)
        b.pack(fill="x", pady=(6, 0))
        self.btn_learn = ttk.Button(b, text="🧠 지금 학습", command=self.run_learning)
        self.btn_learn.pack(side="left")
        ttk.Button(b, text="기본값으로", command=self.reset_rule).pack(side="left", padx=4)
        ttk.Button(b, text="출처 목록", command=lambda: open_path(learn.SOURCES_FILE)).pack(side="left")
        ttk.Label(b, text="주기(일)").pack(side="left", padx=(10, 2))
        self.var_interval = tk.StringVar(value=str(learn.state().get("interval_days", 7)))
        sp = ttk.Spinbox(b, from_=1, to=60, width=4, textvariable=self.var_interval,
                         command=lambda: learn.set_interval(int(self.var_interval.get())))
        sp.pack(side="left")

    def _build_rationale_tab(self, nb):
        f = ttk.Frame(nb, padding=8)
        nb.add(f, text=" UI 근거 ")
        t = self._text(f)
        t.pack(fill="both", expand=True)
        for i, (title, why, links) in enumerate(UI_RATIONALE, 1):
            t.insert("end", f"{i}. {title}\n", "head")
            t.insert("end", why + "\n")
            for u in links:
                tag = f"u{i}{u}"
                t.insert("end", "   " + u + "\n", ("link", tag))
                t.tag_bind(tag, "<Button-1>", lambda e, u=u: open_path(u))
            t.insert("end", "\n")
        t.configure(state="disabled")

    # ================================================================ 로그·상태
    def log(self, msg):
        self.q.put(("log", str(msg)))

    def _poll(self):
        try:
            while True:
                kind, p = self.q.get_nowait()
                if kind == "log":
                    self.logbox.configure(state="normal")
                    self.logbox.insert("end", p + "\n")
                    self.logbox.see("end")
                    self.logbox.configure(state="disabled")
                    last = p.strip().splitlines()[-1] if p.strip() else ""
                    if last:
                        self.status_lbl.configure(text=last[:80])
                elif kind == "step":
                    self._set_step(*p)
                elif kind == "refresh":
                    self.refresh_all()
                elif kind == "knowledge":
                    self.refresh_knowledge()
                elif kind == "finished":
                    self._set_busy(False)
                    self.refresh_all()
                    if p:
                        messagebox.showinfo("완료", p)
                elif kind == "error":
                    self._set_busy(False)
                    self.refresh_all()
                    self.bottom.select(1)
                    messagebox.showerror("오류", p)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _set_step(self, key, status):
        self.step_status[key] = status
        row, icon, name, info, _ = self.step_rows[key]
        sym, color, text = STATUS[status]
        icon.configure(text=sym, fg=color)
        n = len(self.project.changes[key]) if self.project else 0
        info.configure(text=f"{text}" + (f" · 변경 {n}건" if status == "done" else ""),
                       fg=color if status != "idle" else C["muted"])

    def _reset_steps(self, from_key=None):
        idx = STEP_KEYS.index(from_key) if from_key else 0
        for k in STEP_KEYS[idx:]:
            self._set_step(k, "idle")

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_all.configure(state=state)
        for *_, run in self.step_rows.values():
            run.configure(state=state)
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
            self.status_lbl.configure(text="준비됨")

    def select_step(self, key):
        self.step_filter = key
        for k, (row, *_rest) in self.step_rows.items():
            row.configure(highlightbackground=C["accent"] if k == key else C["border"],
                          highlightthickness=2 if k == key else 1)
        self.nb.select(1)
        self._fill_changes()

    # ================================================================ 실행
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

    def _worker(self, job, done_msg=None):
        if self.busy:
            return
        self._set_busy(True)

        def run():
            try:
                job()
                self.q.put(("finished", done_msg))
            except Exception as e:
                self.log(traceback.format_exc())
                self.q.put(("error", str(e)[:1500]))
        threading.Thread(target=run, daemon=True).start()

    def run_step(self, key):
        self.cfg = load_config(CONFIG)
        p = self._ensure_project(fresh=(key == "analyze"))
        if not p.can_run(key):
            prev = STEP_TITLE[STEP_KEYS[STEP_KEYS.index(key) - 1]]
            messagebox.showwarning("순서", f"먼저 '{prev}' 단계를 실행하세요.")
            return
        self._reset_steps(key)

        def job():
            self.q.put(("step", (key, "running")))
            self.log(f"\n=== {STEP_TITLE[key]} ===")
            try:
                p.run_step(key)
            except Exception:
                self.q.put(("step", (key, "error")))
                raise
            self.q.put(("step", (key, "done")))
            self.q.put(("refresh", None))
        self.select_step(key)
        self._worker(job)

    def run_all(self):
        self.cfg = load_config(CONFIG)
        p = self._ensure_project(fresh=True)
        if not list_clips(p.input_dir):
            messagebox.showwarning("영상 없음", f"'{p.input_dir}' 폴더에 영상을 넣어 주세요.")
            return

        def job():
            cur = [None]

            def on_step(k, st):
                cur[0] = k
                self.q.put(("step", (k, st)))
                if st == "running":
                    self.log(f"\n=== {STEP_TITLE[k]} ===")
                else:
                    self.q.put(("refresh", None))
            try:
                p.run_all(on_step)
            except Exception:
                if cur[0]:
                    self.q.put(("step", (cur[0], "error")))
                raise
            self.log("\n모든 단계 완료!")
        self.select_step("all")
        self._worker(job, "쇼츠 제작이 끝났습니다.\n'🎬 완성본 재생'으로 확인하세요.\n"
                          "'변경 내역' 탭에서 단계별로 무엇을 왜 바꿨는지 볼 수 있습니다.")

    # ================================================================ 클립·미리보기
    def reload_clips(self):
        self.project = None
        self._reset_steps()
        self.refresh_all()

    def refresh_all(self):
        self.preview_t = None
        self._fill_tree()
        self._fill_changes()
        self._draw_timeline()
        self._on_select()
        for k in STEP_KEYS:
            self._set_step(k, self.step_status[k])

    def _get(self, path):
        if not self.project or not path:
            return None, None
        a = next((x for x in self.project.analyses if x.path == str(path)), None)
        s = next((x for x in self.project.segs if x.path == str(path)), None)
        return a, s

    def _clips(self):
        return self.project.clips if self.project and self.project.clips else list_clips(Path(self.var_in.get()))

    def _fill_tree(self):
        self.tree.delete(*self.tree.get_children())
        clips = self._clips()
        for p in clips:
            a, s = self._get(p)
            if a:
                dur, react = f"{a.duration:.1f}s", "-" if a.reaction_time is None else f"{a.reaction_time:.2f}s"
                st, en = (s.src_start, s.src_end) if s else (a.trim_start, a.trim_end)
                use = f"{st:.2f}~{en:.2f}"
            else:
                try:
                    dur = f"{probe(p)['duration']:.1f}s"
                except Exception:
                    dur = "?"
                use, react = "분석 전", ""
            self.tree.insert("", "end", iid=str(p), text=p.name, values=(dur, use, react))
        if not (self.sel and self.tree.exists(self.sel)):
            self.sel = str(clips[0]) if clips else None
        if self.sel:
            self.tree.selection_set(self.sel)

    def _on_select(self, _=None):
        sel = self.tree.selection()
        if sel:
            if sel[0] != self.sel:
                self.preview_t = None
            self.sel = sel[0]
        if not self.sel:
            return
        a, s = self._get(self.sel)
        if self.preview_t is None or not a:
            self.preview_t = (a.reaction_time if a and a.reaction_time is not None else
                              (s.src_start if s else (a.trim_start if a else 0.5)))
        self.clip_title.configure(text=Path(self.sel).name)
        self.reason.configure(state="normal")
        self.reason.delete("1.0", "end")
        if s:
            for r in s.reasons:
                self.reason.insert("end", "• " + r + "\n")
        elif a:
            for n in a.notes or ["특이사항 없음"]:
                self.reason.insert("end", "• " + n + "\n")
            self.reason.insert("end", "\n'편집 계획' 단계 후 더 자세히 표시됩니다.", "muted")
        else:
            self.reason.insert("end", "아직 분석 전입니다.\n'▶ 전체 자동 실행' 또는 '1. 영상 분석'을 누르세요.", "muted")
        self.reason.configure(state="disabled")
        self.cap_text.delete("1.0", "end")
        caps = self.project.captions.get(self.sel) if self.project and self.project.analyses else None
        if caps is None:
            side = Path(self.sel).with_suffix(".txt")
            caps = side.read_text(encoding="utf-8").splitlines() if side.exists() else []
        self.cap_text.insert("end", "\n".join(caps))
        self._show_frame()
        self._draw_graph()
        self._draw_timeline()

    def _seg_file(self):
        if not self.project:
            return None, None
        for i, s in enumerate(self.project.segs):
            f = self.project.work / f"seg_{i:02d}.mp4"
            if s.path == self.sel and f.exists() and i < len(self.project.seg_files):
                return f, s
        return None, None

    def _show_frame(self):
        v = self.view
        v.delete("all")
        if not self.sel:
            return
        W, H = max(v.winfo_width(), 100), max(v.winfo_height(), 100)
        seg_file, s = self._seg_file()
        mode = self.var_mode.get()
        if mode == "result" and seg_file:
            t_out = max(0.0, (self.preview_t - s.src_start) / s.speed)
            frame = read_frame(seg_file, min(t_out, s.out_len - 0.05))
            label = f"결과 {t_out:.2f}s (원본 {self.preview_t:.2f}s)"
        else:
            frame = read_frame(self.sel, self.preview_t or 0)
            label = f"원본 {self.preview_t or 0:.2f}s"
            if mode == "result":
                label += "  ·  결과 화면은 '3. 클립 렌더링' 후 표시"
        self.time_lbl.configure(text=label)
        if frame is None:
            return
        res = to_photo(frame, W - 8, H - 8)
        if not res:
            return
        self.photo, pw, ph = res
        x0, y0 = (W - pw) / 2, (H - ph) / 2
        v.create_image(W / 2, H / 2, image=self.photo)
        if mode == "result" and seg_file and self.var_safe.get():
            k = ph / 1920
            red = "#ff4d4d"
            v.create_rectangle(x0, y0, x0 + pw, y0 + 120 * k, outline="", fill=red, stipple="gray25")
            v.create_rectangle(x0, y0 + ph - 300 * k, x0 + pw, y0 + ph, outline="", fill=red, stipple="gray25")
            v.create_line(x0, y0 + ph - 400 * k, x0 + pw, y0 + ph - 400 * k, fill=red, dash=(4, 3))
            v.create_rectangle(x0 + pw - 96 * k, y0 + ph * 0.45, x0 + pw, y0 + ph - 300 * k,
                               outline="", fill=red, stipple="gray25")
            cy = y0 + ph - self.cfg["caption_margin_v"] * k
            v.create_line(x0, cy, x0 + pw, cy, fill="#ffd54f", dash=(2, 2))
            v.create_text(x0 + 4, cy - 2, text="자막 기준선", anchor="sw", fill="#ffd54f",
                          font=(self.font, 8))
            v.create_text(x0 + 4, y0 + ph - 2, text="YouTube UI 가림", anchor="sw", fill="white",
                          font=(self.font, 8))

    # ================================================================ 타임라인·그래프
    def _layout_timeline(self):
        """[(path, start, length, seg)] 출력 기준 (계획 전이면 원본 길이)."""
        items, t = [], 0.0
        if self.project and self.project.segs:
            for i, s in enumerate(self.project.segs):
                start = s.out_start if s.out_start or i == 0 else t - s.trans_in
                if i and not s.out_start:
                    start = t - s.trans_in
                items.append((s.path, start, s.out_len, s))
                t = start + s.out_len
        else:
            for p in self._clips():
                a, _ = self._get(p)
                try:
                    d = a.duration if a else probe(p)["duration"]
                except Exception:
                    d = 3.0
                items.append((str(p), t, d, None))
                t += d
        return items, t

    def _draw_timeline(self):
        c = self.timeline
        c.delete("all")
        items, total = self._layout_timeline()
        W, H = max(c.winfo_width(), 200), int(c["height"])
        if not items or total <= 0:
            c.create_text(W / 2, H / 2, text="원본 폴더에 영상을 넣으세요", fill=C["muted"])
            return
        sx = (W - 16) / total
        c.create_text(8, 4, anchor="nw", fill=C["muted"], font=(self.font, 8),
                      text="결과 타임라인" if self.project and self.project.segs else "원본 클립 (계획 전)")
        c.create_text(W - 8, 4, anchor="ne", fill=C["muted"], font=(self.font, 8), text=f"{total:.1f}s")
        palette = ["#3d5a80", "#5c4d7d", "#2a6f6f", "#7d5a3c", "#4d6b3c", "#6b3c4d"]
        self._tl_hit = []
        for i, (path, st, ln, s) in enumerate(items):
            x0, x1 = 8 + st * sx, 8 + (st + ln) * sx
            sel = path == self.sel
            c.create_rectangle(x0, 20, x1, H - 4, fill=palette[i % len(palette)],
                               outline=C["accent_hi"] if sel else C["bg"], width=3 if sel else 1)
            c.create_text(x0 + 6, 24, anchor="nw", text=Path(path).name[:max(3, int((x1 - x0) / 7))],
                          fill="white", font=(self.font, 8))
            if s and s.reaction_at is not None:
                rx = 8 + (st + s.reaction_at) * sx
                c.create_line(rx, 20, rx, H - 4, fill=C["react"], width=2)
            self._tl_hit.append((x0, x1, path))

    def _on_timeline_click(self, e):
        for x0, x1, path in getattr(self, "_tl_hit", []):
            if x0 <= e.x <= x1 and self.tree.exists(path):
                self.tree.selection_set(path)
                self.tree.see(path)
                return

    def _draw_graph(self):
        g = self.graph
        g.delete("all")
        a, s = self._get(self.sel)
        W, H = max(g.winfo_width(), 200), int(g["height"])
        if not a or not a.times:
            g.create_text(W / 2, H / 2, text="'1. 영상 분석' 후 표시됩니다", fill=C["muted"])
            return
        dur = max(a.duration, 0.1)
        x = lambda t: 8 + (W - 16) * t / dur  # noqa
        st, en = (s.src_start, s.src_end) if s else (a.trim_start, a.trim_end)
        g.create_rectangle(x(st), 0, x(en), H, fill=C["trim"], outline="")
        top = max(max(a.motion), 1e-6)
        pts = []
        for t, m in zip(a.times, a.motion):
            pts += [x(t), H - 14 - (H - 24) * m / top]
        if len(pts) >= 4:
            g.create_line(*pts, fill=C["motion"], width=2)
        if a.reaction_time is not None:
            g.create_line(x(a.reaction_time), 0, x(a.reaction_time), H, fill=C["react"], width=2)
        if self.preview_t is not None:
            g.create_line(x(self.preview_t), 0, x(self.preview_t), H, fill="white")
        for t in range(int(dur) + 1):
            g.create_text(x(t), H - 1, text=f"{t}s", anchor="s", fill=C["muted"], font=(self.font, 7))

    def _on_graph_click(self, e):
        a, _ = self._get(self.sel)
        if not a:
            return
        W = max(self.graph.winfo_width(), 200)
        self.preview_t = max(0.0, min(a.duration, (e.x - 8) / (W - 16) * a.duration))
        self._show_frame()
        self._draw_graph()

    def _jump(self, where):
        a, s = self._get(self.sel)
        if not a:
            return
        st, en = (s.src_start, s.src_end) if s else (a.trim_start, a.trim_end)
        self.preview_t = {"start": st + 0.02, "end": max(st, en - 0.08),
                          "react": a.reaction_time if a.reaction_time is not None else st}[where]
        self._show_frame()
        self._draw_graph()

    # ================================================================ 변경 내역
    def _fill_changes(self):
        self.chg.delete(*self.chg.get_children())
        self.chg_items = {}
        keys = STEP_KEYS if self.step_filter == "all" else [self.step_filter]
        self.chg_title.configure(text="전체 단계" if self.step_filter == "all"
                                 else f"{STEP_KEYS.index(self.step_filter) + 1}. {STEP_TITLE[self.step_filter]}")
        if not self.project:
            return
        for k in keys:
            items = self.project.changes.get(k, [])
            if self.step_filter == "all" and items:
                pid = self.chg.insert("", "end", values=(f"── {STEP_TITLE[k]}", f"{len(items)}건", ""), open=True)
            for i, c in enumerate(items):
                iid = self.chg.insert("", "end", values=(c["target"][:22], c["item"], f"{c['before']} → {c['after']}"))
                self.chg_items[iid] = c
        self.chg_detail.configure(state="normal")
        self.chg_detail.delete("1.0", "end")
        if not self.chg_items:
            self.chg_detail.insert("end", "이 단계를 실행하면 무엇을 왜 바꿨는지 여기에 표시됩니다.", "muted")
        else:
            self.chg_detail.insert("end", "항목을 클릭하면 이유와 근거가 표시됩니다.", "muted")
        self.chg_detail.configure(state="disabled")

    def _on_change_select(self, _=None):
        sel = self.chg.selection()
        if not sel or sel[0] not in self.chg_items:
            return
        c = self.chg_items[sel[0]]
        t = self.chg_detail
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", f"{c['target']} · {c['item']}\n", "head")
        t.insert("end", f"{c['before']}  →  {c['after']}\n\n")
        t.insert("end", "이유: ", "head")
        t.insert("end", (c["reason"] or "-") + "\n")
        if c.get("basis"):
            t.insert("end", "\n근거 규칙: ", "head")
            for n, line in enumerate(knowledge.basis_text(c["basis"]).split("\n")):
                url = line.strip()[2:] if line.strip().startswith("- ") else ""
                if ": http" in url:
                    href = url[url.index("http"):]
                    tag = f"l{n}{href}"
                    t.insert("end", line + "\n", ("link", tag))
                    t.tag_bind(tag, "<Button-1>", lambda e, u=href: open_path(u))
                else:
                    t.insert("end", line + "\n")
        t.configure(state="disabled")
        p = next((str(x) for x in self._clips() if x.name == c["target"]), None)
        if p and p != self.sel and self.tree.exists(p):
            self.tree.selection_set(p)

    # ================================================================ 학습 지식
    def refresh_knowledge(self):
        self.cfg = load_config(CONFIG)
        sel = self.kn.selection()
        self.kn.delete(*self.kn.get_children())
        for rid, r in knowledge.load_rules().items():
            state = "학습됨" if r["learned"] else "기본"
            self.kn.insert("", "end", iid=rid, text=r["title"],
                           values=(f"{r['value']:g}{r['unit']}", f"{r['base_value']:g}", state))
        if sel and self.kn.exists(sel[0]):
            self.kn.selection_set(sel[0])
        st = learn.state()
        last = time.strftime("%m-%d %H:%M", time.localtime(st["last_run"])) if st.get("last_run") else "아직 없음"
        self.learn_lbl.configure(text=f"🧠 마지막 학습 {last} · {st.get('interval_days', 7)}일마다")
        self._on_rule_select()

    def _on_rule_select(self, _=None):
        sel = self.kn.selection()
        t = self.kn_detail
        t.configure(state="normal")
        t.delete("1.0", "end")
        if not sel:
            hist = knowledge.history()[-6:]
            t.insert("end", "규칙을 클릭하면 이유·출처·변경 이력이 표시됩니다.\n\n", "muted")
            if hist:
                t.insert("end", "최근 학습 변경\n", "head")
                for h in reversed(hist):
                    t.insert("end", f"{h['at']}  {h['title']}: {h['before']} → {h['after']} ({h['origin']})\n")
            t.configure(state="disabled")
            return
        r = knowledge.load_rules()[sel[0]]
        t.insert("end", f"{r['title']}  =  {r['value']:g}{r['unit']}\n", "head")
        t.insert("end", f"허용 범위 {r['min']:g}~{r['max']:g}{r['unit']} · 한 번에 최대 ±{r['max_step']:g}\n\n", "muted")
        t.insert("end", r["reason"] + "\n\n")
        srcs = (r.get("learned_sources") or []) if r["learned"] else []
        srcs = srcs or r["sources"]
        if srcs:
            t.insert("end", "출처\n", "head")
            for i, s in enumerate(srcs):
                tag = f"k{i}{s['url']}"
                t.insert("end", f" • {s['title']}\n", ("link", tag))
                t.tag_bind(tag, "<Button-1>", lambda e, u=s["url"]: open_path(u))
        if r["history"]:
            t.insert("end", "\n변경 이력\n", "head")
            for h in reversed(r["history"]):
                t.insert("end", f" {h['at']}  {h['before']} → {h['after']}  [{h['origin']}] {h['reason']}\n")
        t.configure(state="disabled")

    def run_learning(self, auto=False):
        if getattr(self, "_learning", False):
            return
        self._learning = True
        self.btn_learn.configure(state="disabled")

        def job():
            try:
                changes = learn.run(self.log)
                if changes:
                    self.log(f"학습으로 규칙 {len(changes)}개가 바뀌었습니다. 다음 제작부터 적용됩니다.")
            except Exception as e:
                self.log(f"학습 실패: {e}")
            finally:
                self._learning = False
                self.q.put(("knowledge", None))
                self.after(0, lambda: self.btn_learn.configure(state="normal"))
        threading.Thread(target=job, daemon=True).start()

    def _auto_learn(self):
        if learn.is_due():
            self.log("자동 학습 주기가 되어 백그라운드에서 학습을 시작합니다.")
            self.run_learning(auto=True)

    def reset_rule(self):
        sel = self.kn.selection()
        if not sel:
            messagebox.showinfo("안내", "되돌릴 규칙을 선택하세요.")
            return
        knowledge.reset(sel[0])
        self.refresh_knowledge()

    # ================================================================ 기타 동작
    def save_caption(self):
        if not self.sel:
            return
        lines = [ln.strip() for ln in self.cap_text.get("1.0", "end").splitlines() if ln.strip()]
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
            self.project.done -= {"finalize", "qc", "export"}
            self._reset_steps("finalize")
        self.log(f"자막 저장: {side.name} → {lines or '(삭제)'}  ('5. 자막·BGM·효과음'부터 다시 실행하면 반영)")

    def play_source(self):
        if self.sel:
            open_path(self.sel)

    def play_segment(self):
        f, _ = self._seg_file()
        if f:
            open_path(f)
        else:
            messagebox.showinfo("안내", "'3. 클립 렌더링' 후에 볼 수 있습니다.")

    def play_output(self):
        out = Path(self.var_out.get())
        if out.exists():
            open_path(out)
        else:
            messagebox.showinfo("안내", "아직 완성 영상이 없습니다. '▶ 전체 자동 실행'을 눌러 주세요.")

    def _pick_input(self):
        d = filedialog.askdirectory(initialdir=self.var_in.get() or str(ROOT))
        if d:
            self.var_in.set(d)
            self._save_state()
            self.reload_clips()

    def _open_dir(self, p):
        p = Path(p)
        p.mkdir(parents=True, exist_ok=True)
        open_path(p)

    def open_settings(self):
        win = tk.Toplevel(self)
        win.title("설정")
        win.configure(bg=C["panel"])
        win.transient(self)
        frm = ttk.Frame(win, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="폴더", style="Head.TLabel").grid(row=0, column=0, sticky="w")
        r = 1
        for label, var, kind in (("완성 파일", self.var_out, "save"), ("BGM·효과음 폴더", self.var_assets, "dir")):
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=2)
            ttk.Entry(frm, textvariable=var, width=40).grid(row=r, column=1, columnspan=2, sticky="ew", padx=6)
            ttk.Button(frm, text="찾기", style="Small.TButton",
                       command=lambda v=var, k=kind: self._browse(v, k, win)).grid(row=r, column=3)
            r += 1
        ttk.Label(frm, text="BGM은 bgm 폴더, 효과음은 sfx 폴더 (파일명에 whoosh=전환, pop=반응)",
                  style="Muted.TLabel").grid(row=r, column=0, columnspan=4, sticky="w")
        r += 1
        from shorts_auto import freesound
        ttk.Label(frm, text="Freesound API 키", style="Head.TLabel").grid(row=r, column=0, sticky="w", pady=(12, 2))
        ttk.Label(frm, text="있으면 분위기에 맞는 CC0 음원을 받아 사용 (freesound.org/apiv2/apply)",
                  style="Muted.TLabel").grid(row=r, column=1, columnspan=3, sticky="w", pady=(12, 2))
        r += 1
        var_key = tk.StringVar(value=freesound.get_key())
        ttk.Entry(frm, textvariable=var_key, width=40, show="•").grid(row=r, column=0, columnspan=2, sticky="ew")

        def test_key():
            ok, msg = freesound.test_key(var_key.get().strip())
            if ok:
                freesound.set_key(var_key.get())
            (messagebox.showinfo if ok else messagebox.showerror)("Freesound", msg + (" · 키 저장됨" if ok else ""), parent=win)
        ttk.Button(frm, text="연결 테스트·저장", style="Small.TButton", command=test_key).grid(row=r, column=2, columnspan=2)
        r += 1
        ttk.Label(frm, text="편집 수치", style="Head.TLabel").grid(row=r, column=0, sticky="w", pady=(12, 2))
        ttk.Label(frm, text="음량·자막 위치·길이 등은 '학습 지식'이 자동 관리합니다",
                  style="Muted.TLabel").grid(row=r, column=1, columnspan=3, sticky="w", pady=(12, 2))
        r += 1
        base = base_config()
        over = user_overrides(CONFIG)
        entries = {}
        for key, label in SETTINGS:
            cur = over.get(key, base[key])
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", pady=2)
            v = tk.StringVar(value=json.dumps(cur) if isinstance(cur, bool) else str(cur))
            ttk.Entry(frm, textvariable=v, width=14).grid(row=r, column=1, sticky="w", padx=6)
            ttk.Label(frm, text=f"기본 {base[key]}", style="Muted.TLabel").grid(row=r, column=2, sticky="w")
            entries[key] = v
            r += 1

        def save():
            new = {k: v for k, v in over.items() if k not in entries}
            for key, v in entries.items():
                raw, default = v.get().strip(), DEFAULTS[key]
                try:
                    if isinstance(default, bool):
                        val = raw.lower() in ("true", "1", "yes", "예", "on")
                    elif isinstance(default, int):
                        val = int(float(raw))
                    elif isinstance(default, float):
                        val = float(raw)
                    else:
                        val = raw
                except ValueError:
                    messagebox.showerror("설정", f"'{key}' 값이 올바르지 않습니다.", parent=win)
                    return
                if val != base[key]:
                    new[key] = val
            CONFIG.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
            self.cfg = load_config(CONFIG)
            self._save_state()
            self.log("설정 저장 (다음 실행부터 적용)")
            win.destroy()

        def reset():
            for key, v in entries.items():
                v.set(json.dumps(base[key]) if isinstance(base[key], bool) else str(base[key]))
        bar = ttk.Frame(frm)
        bar.grid(row=r, column=0, columnspan=4, pady=(12, 0), sticky="e")
        ttk.Button(bar, text="기본값으로", command=reset).pack(side="left")
        ttk.Button(bar, text="저장", style="Accent.TButton", command=save).pack(side="left", padx=6)

    def _browse(self, var, kind, parent):
        if kind == "dir":
            d = filedialog.askdirectory(initialdir=var.get() or str(ROOT), parent=parent)
        else:
            d = filedialog.asksaveasfilename(defaultextension=".mp4", filetypes=[("MP4", "*.mp4")],
                                             initialfile=Path(var.get()).name, parent=parent)
        if d:
            var.set(d)

    def do_update(self):
        if not messagebox.askyesno("업데이트", "GitHub에서 최신 버전을 받아올까요?\n"
                                   "(영상·설정·학습 기록·BGM 등 내 파일은 유지됩니다)"):
            return

        def job():
            changed = updater.update(ROOT, self.log)
            self.q.put(("finished", "업데이트했습니다. 프로그램을 닫고 다시 실행해 주세요." if changed
                        else "이미 최신 버전입니다."))
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
            "1) 위쪽 '원본 폴더'에 AI 영상을 넣습니다 (파일명 순서: 01_, 02_ …, 파일명이 자막).\n"
            "2) 오른쪽 위 '▶ 전체 자동 실행' → 분석부터 저장까지 한 번에.\n"
            "   왼쪽 단계의 ▶ 로 한 단계씩 실행할 수도 있습니다.\n"
            "3) 단계를 클릭하면 '변경 내역' 탭에 그 단계에서 바뀐 것과 이유·근거가 나옵니다.\n"
            "4) 아래 타임라인에서 클립을 클릭하면 가운데 미리보기와 움직임 그래프가 바뀝니다.\n"
            "5) '학습 지식' 탭: 인터넷 자료와 내 영상 검사 결과로 규칙이 주기적으로 갱신됩니다.\n"
            "6) '⟳ 업데이트'로 프로그램 최신 버전을 받습니다."))

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
