# COMET — 핸드오프 요약

## 실행
- `run.bat` → `comet.py` (Python 3.12)
- `run_daemon.bat` → 상주 서버 (HTTPS + Tailscale, 폰 웹UI)

## 핵심 파일
| 파일 | 역할 |
|---|---|
| `comet.py` | 메인 — 3단 라우터 + 전체 대화 루프 |
| `cloud.py` | 클라우드 두뇌 전환 (claude/gpt/deepseek, 기본=local) |
| `llm.py` | 로컬 ollama 래퍼 |
| `comet_history.json` | 대화 기록 (최근 40개 메시지 사용) |
| `comet_memory.db` | 기억 DB (SQLite, `memory` 테이블) |
| `profile/` | 호윤 메모리 미러 (Claude Code memory → 여기 동기화) |
| `web.py` | 웹 검색 (DuckDuckGo 원천 추적) |
| `analyst.py` | 종목 분석 2패스 (컨센서스→역발상, gemma4:26b 고정) |
| `marketlog.py` | 정세 로그 (날짜별 흐름, market_log.db) |
| `financials.py` | 재무 실적 (Yahoo 무키 crumb) |
| `prices.py` | 라이브 시세 (환율/코인/주가/공포탐욕) |
| `vision.py` | 화면/이미지 인식 (gemma4 멀티모달) |
| `briefing.py` | 프로젝트 브리핑 (개발 상태 요약) |
| `onlymoney_analyst.py` | Only Money 9단 파이프라인 분석 (포트·시세·심리) |
| `autonomy.py` | 자율/상주 모드 (먼저 말 걸기·부르면 듣기) |
| `notify.py` | 텔레그램 전송 (자동 브리핑/뉴스 + "텔레그램으로 보내줘" 온디맨드) |
| `daemon.py` | 상주 서버 (HTTPS, 폰 웹UI 음성) |
| `coder_bridge.py` | "코딩 @폴더 /스킬 할일" 명령 → 코딩 에이전트 |
| `council.py` | "회의 주제" 명령 → 다중 에이전트 토론 |

## 3단 모델 구조
- `light` = gemma4:12b (잡담·즉답)
- `medium` = gemma4:26b (기본 작업, 도구 처리)
- `heavy` = gemma4:26b (복잡 코드·신중한 추론) — medium 과 같은 모델. keep_alive 만 다르다
- 게이트키퍼(분류기): gemma4:12b
- 클라우드 전환: `cloud.py` — `allow_cloud=True` 호출에서만 클라우드로 라우팅

## 도구 게이트 키워드 (FAMILIES)
텍스트에 키워드가 감지되면 `_chat_with_tools`로 분기:
- memory: 기억/메모/적어/저장/할일 등
- files: 폴더/파일/디렉/읽어/찾아 등
- project: 코인봇/주식봇/승률/손익 등
- brief: 브리핑/현황/진행중/brief 등 (영어 brief 포함)
- web: 검색/찾아봐/최신/뉴스 등
- market: 환율/시세/코인/주가/분석/전망 등
- holding: 보유 읽어/포트 스샷 등
- telegram: 텔레그램/텔레그람/폰으로 보내 등 → `send_telegram` (2026-07-10 신설)

## 대화 흐름
1. `respond(text)` 진입
2. 특수 명령어 먼저 체크 (코딩/회의/게임모드/두뇌전환 등)
3. `Router.route()` → 문지기(gemma4:12b)가 tier 판정
4. 키워드 있으면 `_chat_with_tools()`, 없으면 `_chat_stream()`
5. 대화 기록 저장 (`comet_history.json`)

## 알려진 버그 (2026-07-01 수정)
1. **BRIEF_KW에 영어 "brief" 미포함** → "brief 가동해줘" 같은 입력이 일반 대화로 흘러 hallucination 발생 → 수정 완료
2. **cloud(클로드) 사용 시 PERSONA 무시** → 마크다운·장문·번호목록 남발 → PERSONA에 강제 규칙 추가
3. **`_chat_stream`에서 `<think>` 태그 미필터링** → qwen3:14b 사고과정이 그대로 출력 → 필터링 추가
4. **PRICE_KW에 "주식시장"류 미포함** → "주식시장 어때" 입력이 웹 검색으로 빠짐 → "주식시장/미국시장/한국시장/주식장" 추가
5. **도움말 명령어 미구현** → 기능 물어보면 AI가 마크다운 목록 뽑아내거나 hallucination → HELP_TEXT 상수 + "도움말/help/기능/명령어" 명령 처리 추가
6. **PERSONA에 자기인식 힌트 없음** → 기능 질문에 불필요한 AI 설명 생성 → "도움말 이라고 쳐봐" 안내 규칙 추가

## 알려진 버그 (2026-07-10 수정)
1. **텔레그램 온디맨드 전송 자체가 없었음** — `notify.send`는 `autonomy.py`의 자동 브리핑/뉴스 루프에서만 호출됐고, 채팅에서 "텔레그램으로 보내줘"라고 하면 걸리는 도구가 없어 LLM이 "보냈다"고 말만 지어냄 → `TELEGRAM_KW` 계열 + `send_telegram` 액션 신설(비었으면 직전 답변, 시세/브리핑류 지시면 `prices.get_market` 재조회 후 실측 텍스트 전송)
2. **`_stream_filtered`가 여는 `<think>` 태그 없이 유출되는 경우를 못 잡음** — 일부 모델/템플릿은 여는 태그가 프롬프트에 미리 박혀 생성 스트림엔 닫는 `</think>`만 남기는데, 실시간 토큰 필터는 이걸 못 잡고 사고과정(중국어·러시아어 등)이 그대로 새어나감 → 스트리밍 중 필터링 대신 통째로 받은 뒤 web_search 경로와 동일한 이중 정규식(`<think>...</think>` 짝 제거 + `^.*?</think>` 여는 태그 누락 대비)으로 후처리
3. **게이트키퍼가 무관한 긴 텍스트를 `read_file`에 빈 경로로 억지 매칭** → "'' 파일을 못 찾음" 같은 무의미한 실패 응답 → `path`가 비어있으면 `action`을 `none`으로 되돌리는 가드 추가
4. **PRICE_KW에 "증권"류 미포함** → "증권상황/증권뉴스" 입력이 시세(`get_market`) 대신 웹 검색으로 빠져 매번 "검색 결과를 못 찾았어" → "증권/증권시장/증권시황/증권가/주식현황" 추가

## 알려진 이슈 (미해결, 2026-07-10 확인)
- **웹검색(`web.py`)이 100% 실패** — 라우팅 문제 아님. `lite.duckduckgo.com/lite/` 요청이 매번 결과 대신 봇 차단 챌린지 페이지("bots use DuckDuckGo too")를 돌려줘서 `search()`가 결과를 하나도 못 파싱함. 한/영 여러 쿼리로 직접 확인, 100% 재현. 보류 중 — 다음에 손댈 때 옵션: (1) `html.duckduckgo.com/html/` + 세션/쿠키/리퍼러 흉내(확실한 보장 없음) (2) Brave Search API 등 무료 키 발급 후 교체 (3) 그대로 방치.

## 주의
- cloud.py 두뇌 상태 확인: "두뇌" 명령어
- 클라우드 키 설정: "키 클로드 sk-ant-..."
- history 초기화: "리셋" 명령어
- VRAM 비우기: "잠자" 명령어
- 프로필 동기화: "프로필갱신" (Claude Code memory → profile/ 미러)
