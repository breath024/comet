# COMET

로컬에 상주하는 개인 AI 비서. ollama로 돌아간다.

## 3단 라우터

질문마다 큰 모델을 부르면 느리고 아깝다. 그래서 앞에 문지기(gemma3:4b)를 두고
난이도를 판정한 뒤 세 등급으로 나눠 보낸다.

| 등급 | 모델 | 쓰는 곳 |
|---|---|---|
| light | gemma3:4b | 잡담, 즉답 |
| medium | qwen3:14b | 기본 작업, 도구 처리 |
| heavy | gemma3:27b | 복잡한 코드, 신중한 추론 |

클라우드(claude/gpt/deepseek)로 넘기는 경로는 `cloud.py`에 있고,
`allow_cloud=True`로 부른 곳에서만 열린다. 기본값은 로컬이다.

## 도구

텍스트에서 키워드가 잡히면 도구 경로로 분기한다.

| 모듈 | 하는 일 |
|---|---|
| `web.py` | 웹 검색 (DuckDuckGo, 원천 추적) |
| `analyst.py` | 종목 분석 2패스 — 컨센서스를 뽑고 반대편에서 다시 본다 |
| `financials.py` | 재무 실적 (Yahoo, 키 없이 crumb으로) |
| `prices.py` | 환율·코인·주가·공포탐욕 지수 |
| `marketlog.py` | 정세 로그, 날짜별 흐름 축적 |
| `vision.py` | 화면·이미지 인식 (gemma3 멀티모달) |
| `files.py` | 로컬 파일 읽기·탐색 |
| `memory_db.py` | 기억 저장 (SQLite) |
| `coder_bridge.py` | 코딩 에이전트로 넘기기 |
| `council.py` | 여러 에이전트를 붙여 토론시키기 |
| `daemon.py` | 상주 서버 (HTTPS + Tailscale, 폰에서 웹UI로 접속) |

## 실행

```
ai/run.bat          # 대화 (comet.py, Python 3.12)
ai/run_daemon.bat   # 상주 서버
```

`ai/telegram_config.example.json`을 `telegram_config.json`으로 복사하고
봇 토큰을 채우면 텔레그램으로 브리핑을 받을 수 있다.

## 로컬 설정 (`ai/local_config.json`)

개인 경로와 계정은 코드에 박지 않고 이 파일에서 읽는다.
`ai/local_config.example.json`을 `local_config.json`으로 복사해서 채운다.
파일이 없어도 실행은 되지만, 아래 두 가지가 비어 있는 상태로 돈다.

| 항목 | 없을 때 |
|---|---|
| `allowed_logins` | 원격(tailscale serve) 접속이 전부 거부된다. 로컬 접속은 영향 없음. |
| `extra_projects` | 그 프로젝트는 브리핑 목록에 뜨지 않는다. |

`allowed_logins`는 환경변수 `COMET_ALLOWED_LOGINS`(쉼표 구분)로도 넣을 수 있고,
`local_config.json`이 있으면 그쪽이 우선한다.

## 저장소에 없는 것

개인 기억과 대화 기록은 올리지 않았다.
`ai/profile/`, `comet_history.json`, `*.db`, `telegram_config.json`,
`daemon_token.txt`, `local_config.json`,
그리고 TTS 모델 가중치(`.onnx`)가 제외돼 있다.
