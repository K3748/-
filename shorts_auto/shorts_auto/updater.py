"""GitHub에서 최신 버전을 받아 덮어쓴다. git 폴더면 git pull, ZIP으로 받았으면 ZIP을 내려받는다."""
import io
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

REPO = "k3748/-"
BRANCH = "claude/practical-bardeen-dtxukf"
SUBDIR = "shorts_auto"
# 사용자 데이터는 절대 덮어쓰지 않는다
KEEP = {"input", "output", "config.json", "data"}
KEEP_ASSET_DIRS = ("assets/bgm", "assets/sfx")


def _git_root(app_dir: Path):
    for d in (app_dir, *app_dir.parents):
        if (d / ".git").exists():
            return d
    return None


def update(app_dir: Path, log=print) -> bool:
    """업데이트가 적용되면 True."""
    root = _git_root(app_dir)
    if root and shutil.which("git"):
        log("git으로 최신 버전을 가져옵니다...")
        before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout
        proc = subprocess.run(["git", "pull", "--ff-only", "origin", BRANCH], cwd=root,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        log(proc.stdout.strip() or proc.stderr.strip())
        if proc.returncode != 0:
            raise RuntimeError("git pull 실패. 직접 수정한 코드 파일이 있으면 되돌린 뒤 다시 시도하세요.\n"
                               + proc.stderr[-800:])
        after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True).stdout
        return before != after

    url = f"https://codeload.github.com/{REPO}/zip/refs/heads/{BRANCH}"
    log(f"ZIP 내려받는 중: {url}")
    data = urllib.request.urlopen(url, timeout=120).read()
    changed = 0
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            parts = name.split("/", 1)
            if len(parts) < 2 or not parts[1].startswith(SUBDIR + "/"):
                continue
            rel = parts[1][len(SUBDIR) + 1:]
            if not rel or name.endswith("/") or rel.split("/")[0] in KEEP \
                    or rel.startswith(KEEP_ASSET_DIRS):
                continue
            dest = app_dir / rel
            content = z.read(name)
            if dest.exists() and dest.read_bytes() == content:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)
            changed += 1
            log(f"  갱신: {rel}")
    log(f"{changed}개 파일 갱신")
    return changed > 0
