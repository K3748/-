"""Freesound(https://freesound.org)에서 분위기·효과음에 맞는 CC0 음원을 검색해 받아온다.

- CC0(퍼블릭 도메인) 음원만 검색 → 상업적 사용 가능, 출처 표기 의무 없음 (기록은 credits.json에 남김)
- API 키는 data/secrets.json 에만 저장 (깃허브에 올라가지 않음)
- 받은 파일은 캐시해 두고 다음 제작 때 재사용
"""
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .knowledge import DATA

API = "https://freesound.org/apiv2/search/text/"
SECRETS = DATA / "secrets.json"

BGM_QUERIES = {
    "happy": "happy upbeat music loop",
    "sad": "sad piano music",
    "surprise": "suspense tension music",
    "calm": "calm ambient music",
}
SFX_QUERIES = {
    "whoosh": "whoosh transition",
    "pop": "pop cartoon",
    "ding": "notification ding",
    "boing": "boing cartoon",
    "sadtrombone": "sad trombone",
    "sparkle": "magic sparkle chime",
}


def get_key():
    import os
    if os.environ.get("FREESOUND_API_KEY"):
        return os.environ["FREESOUND_API_KEY"].strip()
    try:
        return json.loads(SECRETS.read_text(encoding="utf-8")).get("freesound_api_key", "").strip()
    except Exception:
        return ""


def set_key(key):
    DATA.mkdir(parents=True, exist_ok=True)
    data = {}
    if SECRETS.exists():
        try:
            data = json.loads(SECRETS.read_text(encoding="utf-8"))
        except ValueError:
            pass
    data["freesound_api_key"] = key.strip()
    SECRETS.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "shorts-auto/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def search(query, min_dur, max_dur, key, page_size=15, http=_get):
    params = {
        "query": query,
        "filter": f'license:"Creative Commons 0" duration:[{min_dur} TO {max_dur}]',
        "fields": "id,name,username,license,duration,previews,avg_rating,num_downloads,url",
        "sort": "rating_desc",
        "page_size": page_size,
        "token": key,
    }
    data = json.loads(http(API + "?" + urllib.parse.urlencode(params)))
    return data.get("results", [])


def pick(results):
    """평점과 다운로드 수를 함께 고려해 하나 고른다."""
    if not results:
        return None
    return max(results, key=lambda r: (r.get("avg_rating") or 0) * 2 + min((r.get("num_downloads") or 0) / 500, 3))


def test_key(key, http=_get):
    try:
        search("ding", 0, 3, key, page_size=1, http=http)
        return True, "연결 성공"
    except urllib.error.HTTPError as e:
        return False, "API 키가 올바르지 않습니다 (401)" if e.code == 401 else f"오류 {e.code}"
    except Exception as e:
        return False, f"연결 실패: {e}"


class Library:
    """종류별로 받아 둔 음원 캐시 (assets/_freesound)."""

    def __init__(self, assets: Path, log=print, http=_get):
        self.dir = Path(assets) / "_freesound"
        self.credits_file = self.dir / "credits.json"
        self.log, self.http = log, http
        try:
            self.credits = json.loads(self.credits_file.read_text(encoding="utf-8"))
        except Exception:
            self.credits = {}

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        self.credits_file.write_text(json.dumps(self.credits, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, slot, query, min_dur, max_dur, refresh=False):
        """slot 예: 'sfx:ding', 'bgm:sad'. 성공하면 (경로, 정보), 실패하면 (None, None)."""
        info = self.credits.get(slot)
        if info and not refresh and (self.dir / info["file"]).exists():
            return self.dir / info["file"], info
        key = get_key()
        if not key:
            return None, None
        try:
            r = pick(search(query, min_dur, max_dur, key, http=self.http))
            if not r:
                self.log(f"  Freesound: '{query}' 검색 결과 없음")
                return None, None
            prev = r["previews"].get("preview-hq-mp3") or r["previews"].get("preview-lq-mp3")
            fname = f"{slot.replace(':', '_')}_{r['id']}.mp3"
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / fname).write_bytes(self.http(prev))
            info = {"file": fname, "id": r["id"], "name": r["name"], "username": r["username"],
                    "license": r.get("license", "Creative Commons 0"), "duration": r.get("duration"),
                    "url": r.get("url") or f"https://freesound.org/s/{r['id']}/", "query": query,
                    "at": time.strftime("%Y-%m-%d %H:%M")}
            self.credits[slot] = info
            self._save()
            self.log(f"  Freesound: '{r['name']}' by {r['username']} 받음 ({slot})")
            return self.dir / fname, info
        except Exception as e:
            self.log(f"  Freesound 실패 ({slot}): {e} → 자동 생성 소리 사용")
            return None, None

    def sfx(self, kind, refresh=False):
        return self.get(f"sfx:{kind}", SFX_QUERIES[kind], 0.1, 4.0, refresh)

    def bgm(self, mood, refresh=False):
        return self.get(f"bgm:{mood}", BGM_QUERIES[mood], 20, 240, refresh)
