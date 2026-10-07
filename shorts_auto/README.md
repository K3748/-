# shorts_auto — AI 영상 → YouTube Shorts 자동 편집기

`input/` 폴더에 영상을 넣고 실행하면, 프로그램이 영상을 분석해서 편집 결정을 스스로 내리고
`output/short.mp4`(1080x1920, 30fps)를 만듭니다.

## 설치
```bash
python check_env.py            # 환경 검사 (부족한 것과 설치 명령을 알려줌)
pip install -r requirements.txt
```
- FFmpeg 필요 — Windows: `winget install Gyan.FFmpeg` / macOS: `brew install ffmpeg`
- (선택) 대사 자막 자동 인식: `pip install faster-whisper`

## 사용
```bash
python make_short.py                                  # input/ → output/short.mp4
python make_short.py -i D:/쿠키/에피소드1 -o output/ep1.mp4
python tools/make_samples.py                          # 테스트용 샘플 클립 생성
```
- 클립 순서: 파일명 순 (`01_`, `02_` … 처럼 번호를 붙이세요)
- BGM: `assets/bgm/`에 mp3/wav를 넣으면 자동 사용 (여러 개면 무작위, `--seed`로 고정)
- 효과음: `assets/sfx/`에 이름에 `whoosh`(전환), `pop`(반응)이 들어간 파일. 없으면 자동 합성

## 자막 정하는 법 (우선순위)
1. 영상과 같은 이름의 `.txt` (`03_결제.mp4` ↔ `03_결제.txt`, 한 줄 = 자막 하나)
2. `input/captions.txt` — `파일명 | 첫 자막 / 두 번째 자막`
3. 영상 속 음성 인식 (faster-whisper 설치 시)
4. 파일명 (`01_월급 들어왔다.mp4` → "월급 들어왔다"). `kling_2026…` 같은 자동 파일명은 무시

자막이 2줄 이상이면 두 번째 자막이 **반응 시점**에 맞춰 나옵니다.

## 프로그램이 자동으로 결정하는 것
| 단계 | 내용 |
|---|---|
| 분석 | OpenCV로 프레임별 움직임·밝기·선명도·장면 튐·움직임 중심 측정 |
| 트림 | 시작/끝의 정지·검은·흐린 프레임, 끝부분 모핑 깨짐, 첫/끝 장면 튐 제거 |
| 컷 타이밍 | 움직임이 급증하는 '반응 시점'을 찾고, 그 전 대기가 길면 시작을 당김 |
| 9:16 | 원본 비율과 움직임 분포를 보고 피사체 중심 크롭 / 블러 배경 선택 |
| 연출 | 반응 시점 줌 펀치, 긴 클립·정적인 클립 속도 업 |
| 전환 | 앞뒤 움직임 크기에 따라 빠른 컷 / 디졸브 / 슬라이드 |
| 오디오 | BGM 더킹(원본 소리 클 때 자동 감소), 전환·반응 효과음, -14 LUFS 정규화 |
| 검사 | 완성본의 검은 화면·멈춤·음량·피크·규격·길이를 재검사하고 트림/속도/음량을 고쳐 재렌더 |

모든 결정과 이유는 `output/short.report.json`에 기록됩니다. 세부 값은 `config.json`에서 조정합니다.
