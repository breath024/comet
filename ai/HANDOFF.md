# COMET — 핸드오프

호윤의 상주 비서 AI (자비스형). **완전 로컬**(ollama) 두뇌 + 음성 + 장기기억 + 파일/프로젝트 인식 + 화면 인식 + 이미지 역검색 + 폰 원격 접속.
A.B.B.Y.S 프로젝트의 두뇌 레이어 (VOID의 형제).

- **경로:** `C:\Users\USER\Desktop\개인프젝\A.B.B.Y.S\COMET\ai\`
- **기계:** RTX 5070 Ti 16GB VRAM, Ryzen 9800X3D, 32GB RAM
- 최종 업데이트: 2026-06-24 (**안정성/믿고맡기기**: comet.py의 모든 모델 호출 6곳을 `_gen()` 단일 통로로 통일 — **출력 상한(num_predict 1024) + 타임아웃(180s)** 일괄. 한 턴이 폭주(만 토큰)하거나 ollama가 얼어도 그 턴만 끊기고 `_lock`을 오래 안 쥐어 **데몬 전체가 안 얼어붙음**(이전엔 상한·타임아웃 0이라 폭주 한 번에 모든 요청 무한대기). 스트리밍 토큰 `.get()` 방어도 추가. 검증됨(num_predict=20→20토큰서 잘림, timeout=180 확인). **vision.py(300s+상한 1024/1536)·analyst.py(600s, 기존 상한 1300/1800 유지+플래너 512)도 동일 적용 완료** — 데몬 `_lock` 밑 모든 모델 호출(comet·vision·analyst)이 출력·시간 양쪽으로 묶여 어떤 생성도 데몬을 영구 정지 못 시킴. **+ 게임/바쁨 모드**: GPU가 게임으로 바쁘면(nvidia-smi 자동 감지: 사용률 55%↑ 또는 여유 VRAM 4.5GB↓) 무거운 등급을 light(4b)로 강등 + keep_alive 20s로 빠른 언로드 → 16GB 한 장에서 게임 중 27b 올리다 끊기는 것 방지. `게임모드 켜/꺼/자동` 수동 토글(콘솔·폰), auto여도 '무겁게' 명시는 존중. (※한계: analyst 분석은 27b 고정이라 게임모드 영향 안 받음 — 명시 호출이라 그대로 둠) **+ 자율모드 레이어 신설(autonomy.py 로 분리 = comet 안 불림)**: 모드 선택식 — `proactive`(주기적 뉴스감시→판단게이트→텔레그램 푸시, **모델 호출 0**=GPU 안 씀) · `wake`(부르면 듣기, openwakeword 엔진 필요). 켠 모드는 autonomy_config.json 저장·복원(검증됨). 명령 '자율모드'(상태)·'자율모드 proactive 켜/꺼'·'자율모드 wake 켜/꺼'. ⚠️**활성화 남은 셋업**: 텔레그램 chat_id는 **연결 완료(06-24)**. proactive 실제 푸시엔 watchlist 키워드 등록만 남음(비어 있으면 보류 로그). wake는 엔진 미설치라 게이트(설치 시 리스너 연결됨). + proactive 콘텐츠는 단순 뉴스묶음 대신 `market_brief`(일일 증시 브리핑)로 갈 예정 — 위 '📌 이어서 할 일' 참고. ※코더 감사 교훈: Explore 에이전트 발견 5개 중 3개가 거짓(코인/뉴스 크래시·데몬 즉사는 실제론 안전)이었음 — 검증 후 진짜 구멍 1개만 고침. 06-20 (**웹검색 자동발동**: 키워드 없이도 문지기 gemma3:4b가 '인터넷 사실 조회 필요'를 판정해 스스로 web_search. 추가 모델호출 0=기존 라우터 판정에 web 필드 얹음. medium 고정·기존 원천추적/trace/출처footer 재활용). 06-19: financials.py 재무·실적[Yahoo 무키 crumb], 실측 국면·밸류에이션 프롬프트 강제주입, 뉴스→정세로그, 토픽 별칭, 보유 스샷읽기(vision), web_search 트레이스. + 다국어 국가타겟 수집, marketlog 정세로그+가짜뉴스필터, trace.py, analyst 27b. 06-18: web.py+prices.py

---

## 📌 다음에 해야 할 것 (2026-08-30 기준)

1. **데몬을 한 번 띄워 원격 접속을 확인할 것.** 8/30 에 `daemon.py` 의 허용 계정과
   `briefing.py` 의 프로젝트 목록을 `ai/local_config.json`(gitignore) 에서 읽도록 바꿨다.
   구문·로딩은 검사했지만 **라이브로는 안 돌렸다.**
   `local_config.json` 이 없으면 원격 접속이 전부 거부된다(로컬은 영향 없음).
   설정법은 `README.md` 의 「로컬 설정」 절, 예시는 `local_config.example.json`.

2. **`briefing.py` 의 경로가 낡았다.** 항목 대부분이 `Desktop\창업\...` 를 가리키는데
   실제 폴더는 `Desktop\A.창업\...` 다. 코인봇·주식봇 브리핑이 문서를 못 찾고 있을 것.
   8/30 에는 시킨 범위 밖이라 손대지 않았다.

※ 8/30 작업 상세(무엇을 왜 바꿨는지, 못 살린 것)는 `logs/2026-08-30-log.md`.
## ✅ 2026-06-29 한 것 — 기능 디스패치 테이블 통합 리팩터 (★다음작업 완료)
- **왜:** 라우팅이 **병렬 레지스트리 2개**(키워드 튜플 `MEMORY_KW…HOLD_KW` ↔ `DECIDE_PROMPT`의 액션 18개 목록)라 손으로 맞춰야 했고, `DECIDE_PROMPT`가 매 도구턴마다 18개 명세를 통째로 14b에 주입 → 길고 느림.
- **무엇(comet.py):**
  - **`FAMILIES` 선언 테이블 하나** 신설(계열=키워드그룹 → 그 계열이 올리는 동작들). `TOOL_KW`는 여기서 **파생**(더 이상 수동 합치기 X). 새 기능 = 테이블 한 줄.
  - `DECIDE_PROMPT` 단일 상수 → **`ACTION_SPECS`(액션별 한 줄 명세) + `DECIDE_HEADER`/`DECIDE_RULES` 분리 + `build_decide_prompt(text)`**. **온디맨드**: 입력에 키워드가 스친 계열의 동작 명세만 메뉴에 올림(파일질의=10개 vs 풀메뉴 21개로 단축).
  - 시세/분석/재무/정세는 서로 헷갈려 **한 계열(market)로 묶음** — 하나만 스쳐도 함께 올라와 기존 판별 보존.
  - **실행 핸들러(if/elif)는 그대로 둠**(인자·후처리 제각각 + 날조차단 직답 바이패스가 많아 일부러 표에 안 합침 — HANDOFF '하지 말 것' 따름).
- **동작 보존(검증됨, ollama 불필요 결정론 28검사 전부 통과):** ① `TOOL_KW` 집합 = 옛 합집합(172개 동일=게이트 그대로) ② 모든 계열 걸리면 후보 = 원래 21개·**원래 순서 그대로**(풀메뉴 동일) ③ **read_holdings 보존**: HOLD_KW가 전부 공백 포함이라 키워드 게이트로 'holding' 계열은 원래도 안 걸리고 "내 보유 **읽어**"의 '읽어'(files)→풀메뉴에서 모델이 골랐음 → read_holdings를 web_search/none과 함께 **항상 메뉴 바닥**에 둬 그 도달경로 보존 ④ 교차계열 13케이스 정답 액션 후보 포함. 검증 스크립트=scratchpad/verify_dispatch.py.
- **⚠️ 남은 한 가지(라이브):** 결정론 레벨(게이트·후보집합·순서)은 옛 동작과 동일 보장. 단 **14b가 짧아진 메뉴로 실제 같은 액션을 고르는지는 ollama 띄워야 최종 확인**(HANDOFF 검증 지침의 '기존 대화 같은 라우팅'). 데몬 재시작 후 "비트코인 사도 돼?"(→analyze_stock)·"비트코인 시세"(→get_crypto)·"내 보유 읽어"(→read_holdings) 몇 개만 눌러보면 됨. 명세·규칙 텍스트는 옛것 그대로라 선택은 같거나 더 정확할 것(메뉴 짧을수록 정확↑).

## ✅ 2026-06-29 (2) — analyst 고도화: 한국종목 그라운딩 + RSI/ATR 셋업지표
- **① 한국 종목 가격 그라운딩(.KS/.KQ)** — `NAME2TICKER_KR`(삼성전자·SK하이닉스·카카오·한화에어로 등 코스피/코스닥 40여 개) + **6자리 종목코드 자동감지**. `_history`가 한국 티커도 Yahoo 무키 1년 일봉으로 실계산(KRW 통화 표시). 이제 "삼성전자 사도 돼"·"005930 어때"도 가격·52주·이평선·국면 다 그라운딩됨(전엔 미국 티커만, 한국은 웹자료로만).
  - **⚠️함정 잡음:** 6자리 코드는 `.KS`/`.KQ` 양쪽에서 데이터가 나오는데 **한쪽이 거래량 없는 '유령' 펀드 listing**(247540.KS=유령 vs .KQ=진짜 에코프로비엠). → `regularMarketVolume` 있는 쪽을 고르게 함. 검증: 247540→.KQ, 005930→.KS, 068270(셀트리온)→.KS, 999999→None 정상.
- **③ 셋업 지표(RSI/ATR)** — `_history`에 **Wilder RSI(14)·ATR(14)** 추가(둘 다 파이썬 실계산=날조불가). `_px_lines`에 "📐 셋업 지표: RSI NN(과매수/과매도/중립) · ATR N.N%/일 ‖ 2×ATR 손절선 ≈ 가격 ‖ 눌림 참고선(50일선)" 표시. ATR은 정렬된 high/low/close 삼중조로 계산(행 정렬 추가). 진입(과매수/눌림)·손절폭의 **계산된 근거**를 제공(명령조 아님=참고선). 1차 프롬프트에 "셋업 있으면 진입타이밍·손절폭에 그 계산값 인용(임의 숫자 금지)" 넛지 한 줄.
- **② 보유 연결 = 이미 완성돼 있었음**(`_load_holdings`가 holdings.txt를 evidence·2차 프롬프트에 주입, "보유 종목·수량·평단·손익 기준" 참조). 추가 작업 안 함.
- **검증:** py_compile OK + 데이터레이어 라이브 테스트(티커감지·KR그라운딩·RSI/ATR·유령필터·6자리 disambig) 전부 통과. **2패스 판단(27b)은 라이브 데몬 스팟체크 필요**(가산적 변경이라 모델 경로 동일). 안 한 것: 답 길이·속도 튜닝, 오답시 3패스 심판, Only Money prompts.json 역수입(별건, ⓪에 남김).

## ✅ 2026-06-30 — 클라우드 두뇌(클로드/GPT/OpenAI호환) 메인 COMET에 연결
- **`cloud.py` 신설(멀티 프로바이더)**: 메인 대화의 **최종 답변만** 클라우드로 보낼 수 있게. `claude`(Anthropic `/v1/messages`, x-api-key) · `gpt`(OpenAI `/chat/completions`, Bearer) · `deepseek`(OpenAI호환·저렴). raw HTTP(urllib, SDK 0 — llm.py와 같은 방식). 응답을 **ollama와 같은 dict/제너레이터 모양**으로 반환 → comet 답변 호출부 무수정.
- **연결(comet.py)**: `_gen(..., allow_cloud=True)` 가 붙은 **최종 답변 3곳**(일반대화 스트림 · 웹검색 합성 · 도구결과 합성)만 클라우드 후보. **문지기·DECIDE·analyst·vision은 로컬 고정**(allow_cloud=False) = 매 턴 과금 방지. `fmt="json"`(구조화)도 로컬 강제.
- **토글**(콘솔·폰 공용): `두뇌`(상태) · `클로드로`/`지피티로`/`딥시크로`/`로컬로`. cloud_config.json 저장. **기본=로컬(월 0원)**. ⚠️**구독≠API**: 클로드Pro/ChatGPT Plus로는 못 부름 — **종량 API 키** 필요(console.anthropic.com / platform.openai.com). **키 없으면 자동 로컬 폴백**(is_active()=None).
- **★키 언제든 갈아끼우기(재시작 불필요)**: 키 우선순위 = **환경변수 → `cloud_keys.json`(파일)**, 파일은 매 호출 새로 읽어 즉시 반영. 폰/콘솔 명령 `키 클로드 sk-ant-…` / `모델 클로드 claude-haiku-4-5` / `키삭제 클로드`. 키는 **대화기록에 안 남김**(history.add 전 토글블록 처리) + 응답 마스킹. 프로바이더로 해석될 때만 가로채 일반대화("키 큰 사람" 등) 안 먹음. ⚠️`cloud_keys.json`=비밀(공유 금지, daemon_token.txt와 동급). 환경변수(`ANTHROPIC_API_KEY` 등)도 그대로 우선 지원.
- **돈 가드**: 기존 `cost_guard`(코더용) 재사용 — 답변 전 `before_api_call` 검문 + `record` 누적. 답변마다 `reset_task()`(코더 과제상한이 답변 누적에 안 걸리게) → 하루 상한(300콜·150만토큰, coder_config.json)이 진짜 지갑 가드. 출력상한 max_tokens=1024(폭주 차단).
- **클로드 주의(스킬 확인)**: opus-4-8/4.7은 `temperature` 보내면 **400** → 클로드 경로는 temperature 안 보냄. system은 top-level(messages에서 분리·첫 턴 user 강제). 기본 모델 `claude-opus-4-8`(저렴=haiku-4-5/중간=sonnet-4-6, cloud_config.json `providers.claude.model`로 교체). GPT 기본 `gpt-4o`는 **본인 계정 모델로 바꿔야**(레지스트리 추측값).
- **검증**: py_compile(cloud+comet) OK. 로컬폴백·system분리·토글·**스트리밍 SSE 파싱(클로드+OpenAI 목)**·비스트림 추출·**런타임 키/모델 교체(재시작 없이)·일반대화 비가로채기** 전부 통과. **남은 건 호윤이 키 뽑아서 `키 클로드 sk-ant-…` 한 줄 → `클로드로`. 끝(재시작도 불필요).** 그 전엔 로컬 그대로.
- ※범위: analyst(27b 2패스)·vision·image_search·게임모드 강등은 로컬 유지(특화 파이프라인). 원하면 나중에 그쪽도 allow_cloud 확장 가능.

## ✅ 2026-06-30 — council(회의) 엔진: 멀티에이전트 분업·토론 (Coder)
- **왜(호윤)**: "일 시키면 에이전트 여럿 소환→회의→결과 도출". 사실 **씨앗이 analyst**(컨센서스↔역발상 2패스=2에이전트 토론, 할루시네이션·약점 잡아 품질↑ 검증됨) → 그 패턴을 코딩용 N역할로 일반화.
- **`council.py` 신설**: `설계자→구현자→반론·검증관→합성자` 순차 회의. analyst 사상 그대로(반론관이 구현을 적대적으로 부숨→합성자가 반영). 반환 {ok, result, transcript(plan/impl/critic/final)}.
- **하이브리드 두뇌**(호윤 선택): `ROLE_BRAIN`={planner·implementer=local, critic·synth=cloud}. cloud 역할은 **키 있는 클라우드(클로드/GPT/DeepSeek) 자동선택→llm API→로컬** 순 폴백(`_cloud_keyed`/`brain_label`). 키 없으면 전부 로컬=월 0원. cost_guard는 cloud/llm 경유라 그대로 적용.
- **연결**: ① coder.py 콘솔 `/council <할 일>` ② comet "코딩" 경로 — `coder_bridge.parse`가 `회의`/`/council` → skill="council" → `run_council_text`(진행+반론요지+최종해답 텍스트). 폰/콘솔 공용.
- ⚠️**제약(솔직)**: 로컬 GPU 1장이라 회의는 **동시 아니라 순차**(역할별 차례로, 한 번에 몇 분). 품질천장=로컬모델(그래서 핵심역할 클라우드 승급이 하이브리드의 핵심).
- **검증**: py_compile(council/coder/coder_bridge) OK + 두뇌 라우팅(키없음→critic/synth 로컬폴백) + parse 인식(회의/council/code/일반) + run_text→council 분기 전부 통과. **4역할 LLM 실행은 ollama 떠야 라이브 확인**(작성시 꺼져 있어 보류, 엔진은 검증된 llm.chat 경유).
- **고도화 3종(2026-06-30, 순서대로):** ① **토론 라운드 반복** — 반론관이 결함 찾으면 `reviser`가 재구현→재반론, 깨끗(`_is_clean`)하거나 max_rounds까지(목 검증: R1 결함→재구현→R2 깨끗→조기종료). ② **역할 가감(`council_config.json`)** — `max_rounds`·역할별 `role_brain`·**`lenses`**(보안·성능·엣지 등 관점별 전문 반론을 메인 반론과 함께, 하나라도 결함이면 재구현)(목 검증). ③ **COMET 일반 노출** — `mode='general'` 역할셋(분석가→초안→반론↔보강→합성, 전략·판단용) + comet `회의 <주제>` 명령(코딩은 '코딩 회의 …', "회의록"은 안 가로챔 검증). 전부 목/라우팅 검증, 라이브는 ollama 띄워서.

## ✅ 2026-06-30 — 실행중 업데이트(리로드/재시작) + Only Money 라이브 업데이트
- **COMET `리로드`(핫리로드)**: 데몬 안 끄고 `profile_sync.sync()` + `reload_system()`로 **프로필·SYSTEM(인격) 즉시 재구성**(메모리/인격 변경 라이브 반영). SYSTEM 구성을 `_build_system()`으로 분리→`reload_system()`이 전역 SYSTEM 재할당(메서드가 매 턴 전역 읽어 다음 턴부터 적용). 설정(클라우드 키·게임/자율모드·cost_guard)은 원래 매 호출 파일 읽어 **이미 hot**.
- **COMET `재시작`**: 코드(.py 함수) 변경은 핫스왑이 상태(_comet·history) 깨질 위험이라 안 하고, **클린 재기동**으로 반영 — 응답 직후 `threading.Timer(1.2)`→`os.execv(sys.executable, [..]+sys.argv)`(소켓은 Python 기본 CLOEXEC로 exec 시 닫혀 포트 재바인드 OK), 실패 시 `os._exit(0)`→watchdog 부활. 사용자가 직접 치는 명령=동의됨.
- **왜**: 호윤 "실행중에도 업그레이드로 실시간 반영". → 프로필/인격=리로드(무중단), 코드=재시작(watchdog 부활), 설정=이미 hot. **전체 코드 핫스왑은 의도적으로 안 함**(`from X import Y` 바인딩이 reload로 안 갱신·상태 손실 위험).
- **Only Money(별도 앱)도 같이**: PWA 코드 자동반영(sw.js HTML 네트워크우선 + Last-Modified HEAD 감지→토스트→탭복귀 자동적용) + 데이터 실시간(탭복귀 시 뉴스·F&G 즉시 새로고침). iCloudDrive/Only Money/HANDOFF.md 2026-06-30 항목 참고. 검증 V8+Playwright 통과.
- 검증(COMET): py_compile OK + reload_system 재구성·멱등 + 명령 매칭(리로드/재시작) + execv 가용 확인. `재시작`은 실제 실행시 데몬 재기동.

## ✅ 2026-06-30 — ⑩ 데몬 watchdog ('항상 켜져 있기' 토대)
- **`watchdog.py` 신설**: 30초마다 `127.0.0.1:8765` 헬스체크 → **죽었을 때만** 새 콘솔로 daemon.py 재기동. **살아있는 프로세스는 절대 안 건드림(kill·강제종료 코드 0)** — 호윤 '파괴적 시스템 명령 금지' 준수. 헬스 판정: HTTP 응답(200/401 등)=살아있음, 연결거부/타임아웃=죽음. 재기동 후 BOOT_GRACE 25s 대기(중복 기동 방지). watchdog 자신도 마커포트(8766)로 중복실행 차단. 로그 watchdog.log.
- **런처**: `run_watchdog.bat`(수동·3.12) + `setup_watchdog.bat`(**옵트인**: 로그인 시 숨김 자동시작 등록 = Startup 폴더에 COMET_watchdog.vbs 생성, 끄려면 그 .vbs 삭제). tailscale serve는 setup_remote.bat로 한 번 켜두면 재시작 넘어 유지됨.
- **검증**: py_compile OK + 헬스체크(라이브 데몬 200 정상 인식)·중복실행 차단·no-kill(코드 검사) 통과. **죽음→재기동 라이브 테스트는 안 함**(돌고 있는 데몬 죽여야 해서 — 금지 준수). 로직은 건전.
- **활성화(호윤)**: `setup_watchdog.bat` 한 번 실행 → 재부팅/재로그인부터 데몬 자동 상주(죽으면 자동 부활). 끄려면 Startup의 .vbs 삭제. ※watchdog는 데몬을 '띄우기만' 하지 코드 바꿀 때 옛 데몬 교체는 여전히 수동(8765 옛 프로세스 호윤이 닫고 재시작).

## ✅ 2026-06-30 — ⑥ profile 자동 동기화 (코멧이 호윤을 최신으로)
- **왜**: profile/는 호윤 메모리의 정적 스냅샷 → 내(클로드) 메모리 갱신돼도 안 따라옴(알려진 한계). 이번에 소스 40 vs profile 34로 6개+ 뒤처져 있었음.
- **`profile_sync.py` 신설**: 소스(`C:\Users\USER\.claude\projects\C--Users-USER\memory`)의 .md → `profile/` 복사·갱신. 명령 `프로필 동기화`(콘솔·폰, 안전=복사/갱신만, stale 삭제 안 함·보고만) / `프로필 동기화 정리`(미러=stale도 삭제). 변경분(추가/갱신/그대로/stale) 요약 반환.
- **실행함**: 추가 6(feedback_no_destructive_system_commands·project_comet_coder·외부 프로젝트 등) + 갱신 6(MEMORY 색인·project_comet 등) → profile 최신화. ⚠️**데몬 재시작해야 새 profile이 SYSTEM에 다시 로드됨**(_PROFILE은 임포트 시 1회 로드).
- 검증: py_compile OK + 실제 동기화 정상(추가6·갱신6·그대로28·stale 0). 안전 기본(미러 아님)이라 호윤 큐레이션 안 날림.
- **★자동화(완성)**: `daemon.py`가 **기동 시 comet import 전에 profile_sync.sync() 자동 호출** → 데몬 (재)시작마다 호윤 최신 메모리가 SYSTEM에 로드됨. watchdog가 데몬을 항상 살리니 = **재시작할 때마다 코멧이 호윤 최신으로 깨어남**(멱등=변경 없으면 no-op). ⑥ '자동' 부분 충족.

## ✅ 2026-06-30 — '미장 어때' 빠른 스냅샷 (get_market, 모델 0)
- **prices.market_snapshot()**: 지수(다우·나스닥·S&P·필반) + 군중심리(CNN F&G, 극단 쏠림 표시) + 주요 종목(엔비디아·테슬라·애플·MS·AMD) 등락을 한 번에. **전부 Yahoo/CNN 실측·LLM 안 거침=날조 0**, ~2.3s. 일일 브리핑(27b 2패스, 느림)의 즉답 카운터파트.
- **라우팅**: 새 액션 `get_market`(market 계열), PRICE_KW에 미장/시황/증시/장분위기 등 추가, get_market DECIDE 명세="특정 종목 말고 전체 시황 빠르게". "미장 어때/오늘 장/시황/시장 분위기"→스냅샷, "엔비디아 주가"→get_stock 으로 모델이 구분(검증). _chat_with_tools 가 result['text'] 직답(모델 안 거침).
- 검증: snapshot 출력·속도(2.3s)·라우팅 후보(미장류=get_market, 특정종목=get_stock 구분) 정상. py_compile OK.

## ✅ 2026-06-30 — 미장 군중심리(CNN Fear&Greed) + analyst 역발상 주입
- **왜(호윤)**: "미장 사람들 군중심리를 꿰뚫어야" → analyst 코어(컨센서스 vs 역발상)에 **진짜 군중심리 숫자**를 물려 분위기 아닌 데이터로 반대편을 보게.
- **prices**: `stock_fear_greed()`(CNN `production.dataviz.cnn.io`, 무키·**브라우저 헤더로 418 우회**) = 종합 + **7개 세부 군중심리 지표**(모멘텀·시장폭·풋콜비율·VIX·정크본드/안전자산 수요). `fear_greed(query)` 분기: 기본=미장(CNN), '코인/크립토/비트' 맥락=alternative.me. fmt가 종합+극단(extreme) 세부 강조. dispatch가 query 전달.
- **analyst**: gather가 CNN 센티멘트 수집→evidence에 '시장 군중심리' 블록 주입 + **역발상(2차) 프롬프트에 저울 한 줄**(극단공포=과매도 기회/극단탐욕=과열 위험, 단 전체시장이라 개별 펀더멘털과 구분·가중치로만, 세부지표 쏠림도 보라). 가산적 변경(모델 경로 동일).
- **market_brief에도 주입**: 일일 미장 브리핑(자율모드 brief=매일 텔레그램 푸시)이 지수·종목·기사에 더해 **군중심리(CNN F&G)**를 근거에 깔고, 종합 프롬프트가 "극단 공포=과매도/극단 탐욕=과열, 세부지표 쏠림도" 거시 해석에 녹이게. 가산적(5-tuple gather). 검증: gather+evidence에 군중심리 블록 주입 확인.
- 검증: 무키 CNN 수신·미장/코인 분기·fmt·**gather가 evidence에 군중심리 블록(extreme fear 세부까지) 주입**(analyst+market_brief 양쪽) 확인. py_compile OK. 2패스 27b 실인용은 라이브 데몬에서.

## ✅ 2026-06-30 — ② 국내주식 시세(prices) + 공용 kr_stocks 모듈
- **`kr_stocks.py` 신설(단일 출처)**: 한국 종목 이름→.KS/.KQ 맵 + `name_to_ticker`(이름/6자리 코드) + `is_bare_code`. analyst의 인라인 맵을 여기로 **이관**(이중 레지스트리 방지, 디스패치 리팩터 철학과 일관) — analyst는 `from kr_stocks import NAME2TICKER_KR`, _detect_ticker 로직·결과 **동일**(회귀 검증됨).
- **`prices.stock` 한국 종목 지원**: "삼성전자 주가"·"005930"도 시세 나옴(전엔 입력 대문자화→Yahoo 직행이라 한국명 실패). 6자리 코드는 `_pick_kr_listing`이 .KS/.KQ 중 **거래량 있는 진짜 listing** 선택(247540→.KQ 검증). get_stock DECIDE 명세도 "해외+한국" 으로 확장(라우팅 인지).
- 검증: prices.stock(AAPL/삼성전자/005930/247540/에코프로비엠/NVDA) 전부 정상 + analyst 티커감지 회귀 동일. py_compile 4파일 OK.
- **+지수·미국 한글명 확장(2026-06-30)**: `get_stock` 한 경로가 이제 **지수**(코스피 ^KS11·코스닥·나스닥·S&P·다우·니케이·VIX, 통화 없이 포인트로 표시 = 전용 kind "지수") + **미국 종목 한글명/ETF**(엔비디아→NVDA·테슬라·TQQQ·우주ETF→ARKX 등)까지. 미국 이름맵은 **`us_stocks.py`로 이관**(analyst와 단일 출처, kr_stocks와 대칭). 라우팅: PRICE_KW에 코스피/나스닥/지수 등 추가, get_stock 명세에 지수 명시. 티커에 공백·한글 섞여도 토큰만 뽑아 URL크래시 방지. 검증: 엔비디아/테슬라/TQQQ/우주/나스닥/코스피/삼성전자/팔란티어 전부 정상 + analyst 회귀 동일.

---

## ✅ 2026-06-28 한 것 — brief 자율모드 신설 (handoff 1·2번 완료)
- `autonomy.py`에 **`BriefMode`(name="brief")** 추가: 매일 설정 시각(`hour`, 기본 7시) 이후 첫 점검에서 `market_brief.generate()` → `notify.send()`로 텔레그램 푸시. 하루 1회 보장 = 전송 성공 시 `last_date`(autonomy_config.json) 잠금, **실패하면 다음 점검(check_sec 기본 600s)에 자동 재시도**.
- **★ 미해결 (d) 해결: GPU-busy 게이트.** `comet._gpu_busy`를 `Autonomy(gpu_busy_fn=...)`로 주입 → brief가 27b를 띄우기 전 GPU 바쁨(게임 등) 체크, 바쁘면 미룸(하루 1회라 급할 것 없음). `comet.py` 생성부 한 줄 추가.
- 명령: **`자율모드 brief 켜/꺼`** (콘솔·폰 공용, 기존 파서가 자동 인식). 상태에 brief 줄 노출. 켜면 autonomy_config.json 저장·재시작 복원.
- 검증: `python autonomy.py`(토글 ON/OFF·상태 정상), `market_brief`/`notify` import OK, `py_compile autonomy/comet/market_brief` 통과. autonomy_config.json은 전부 off로 원복(테스트 잔재 제거).
- **남은 활성화 한 수(호윤이):** 데몬 재시작(`run_daemon.bat`, 옛 데몬 종료 후) → 폰/콘솔에서 `자율모드 brief 켜`. 텔레그램은 이미 연결됨(chat_id <chat_id>). hour는 미장 마감(한국 아침) 기준 조정 가능(autonomy_config.json `brief.hour`).
- **brief 자동 켜기 ON** (호윤 승인 06-28): autonomy_config.json `brief.enabled=true` → 다음 데몬 재시작 때 `restore()`가 자동 가동. 끄려면 `자율모드 brief 꺼` 또는 config false. 본문추출 노이즈 청소·watchlist·wake는 그대로 남음.

---

## 📌 이어서 할 일 (2026-06-25 기준 — 다음 세션 먼저 읽기)

**큰 목표:** 코멧을 "부르면 듣고 / 알아서 판단해 먼저 말 거는" 비서로. 안정성·게임양보는 끝났고, 지금은 **proactive(먼저 말 걸기)의 콘텐츠 = 일일 미국 증시 브리핑**을 다듬는 중.

**오늘(06-24~25) 한 것:**
1. 안정성: comet·vision·analyst 모든 모델 호출에 타임아웃+출력상한 → 데몬 안 얼어붙음(검증).
2. 게임/바쁨 모드: GPU 바쁘면 대화 라우터를 4b로 강등(comet 한정).
3. `autonomy.py` 신설: proactive/wake 모드 레이어(저장·복원·토글 검증). wake는 openwakeword 미설치라 게이트.
4. **텔레그램 연결 완료**(봇 @<봇핸들>, chat_id 저장(값은 telegram_config.json)). 푸시 통로 살아남.
5. `market_brief.py` 신설: 일일 미증시 브리핑. 숫자=prices(정확), 사건=뉴스RSS(날짜필터로 stale 차단)+**상위 4개 본문 긁기**(헤드라인→web검색 우회). 2패스(종합→사실감사).

**현재 브리핑 품질(솔직):** 숫자 정확 + 오늘 사건 + 일부 본문 깊이("과거 17번 급락 회복" 같은 분석은 본문서 캐옴) + 모르면 "파악 어렵다"로 정직. **SPY 커뮤니티 큐레이터급은 아직 아님.**

**남은 문제:**
- (a) **본문 추출 노이즈** — 일부 사이트(연합인포맥스 등)는 번역메뉴·네비가 본문 앞 900자를 잡아먹어 모델이 알맹이를 못 봄.
- (b) **추출 깊이 천장** — 본문 줘도 27b가 구체 수치(코스피 -10%, BofA 매도경고 등)를 사람만큼 못 뽑음(모델 한계).
- (c) **세션 엇갈림** — 숫자(prices=최신)와 뉴스(직전 마감장)가 다른 날일 수 있음 → **마감 직후 스케줄로 돌리면 자동 정렬**(지금은 아무 때나 돌려서 어긋남).
- (d) ★ **중요 미해결**: `market_brief`·`analyst`는 27b 무거운데 **게임모드를 안 따름** → 호윤 게임(RDR2) 중에 27b 띄우면 게임이랑 싸움. proactive 걸기 전에 반드시 "GPU 바쁘면 미루기" 박아야 함.

**내일 이어서 할 순서:**
1. ~~proactive에 GPU-busy 게이트~~ ✅2026-06-28 — `brief` 모드로 구현(위 ✅블록). gpu_busy_fn 주입.
2. ~~market_brief를 proactive에 연결 + 스케줄~~ ✅2026-06-28 — `brief` 모드가 매일 `hour` 이후 푸시. (c)세션 엇갈림은 hour를 미장 마감 후로 두면 정렬됨. **실제로 매일 받아보며 약점 관찰은 호윤이 `자율모드 brief 켜` 후 시작.**
3. (선택·체감↓) 본문 추출 노이즈 청소(readability), watchlist 키워드 등록(원하면).
4. wake: `pip install openwakeword` 후 리스너 실측. always-on watchdog(부팅 자동시작).

**테스트 명령:** `python market_brief.py`(브리핑 1회 생성·출력) · 콘솔/폰 `자율모드`(상태) · `게임모드`(상태).

---

## ⚠️ 실행 (반드시 읽기)
- **로컬(PC, 음성 포함):** `run.bat`
- **상주 서버(폰/외부 접속):** `run_daemon.bat`
- **원격 HTTPS 설정(최초 1회):** `setup_remote.bat`
- **항상 켜두기(옵트인):** `setup_watchdog.bat`(로그인 시 watchdog 자동시작 등록) · `run_watchdog.bat`(수동). watchdog=데몬 죽으면 자동 재기동(kill 안 함).
- **Python 3.12 고정 필수.** 이 PC에 3.12·3.13 둘 다 있고 의존성(torch/whisper/edge-tts/ollama/Pillow 등)은 **3.12에만** 깔림. `python comet.py`로 직접 켜면 3.13 잡혀 `No module named 'torch'`. 런처 .bat이 3.12 경로 박아둠: `C:\Users\USER\AppData\Local\Programs\Python\Python312\python.exe`
- **데몬 재시작 시:** 코드 바꾸고 다시 켤 때 8765 포트에 옛 데몬이 살아있으면 새 데몬이 바인드 못 하고 옛 코드가 응답함. 기존 `python.exe`(daemon.py) 완전 종료 확인 후 재시작.

---

## 모델 (3단 라우터)
- **light = gemma3:4b** — 문지기(난이도 판정)·잡담·화면인식(vision)
- **medium = qwen3:14b** — 기본 작업·도구·기억질문·종목분석. (2026-06-17 qwen2.5:14b→qwen3:14b 교체, 추론력↑. 비판적 톤 유지)
- **heavy = gemma3:27b** — 신중한 큰 작업. (2026-06-17 qwen2.5:32b→gemma3:27b. 구형 qwen2.5·exaone3.5는 삭제됨)
- 문지기 gemma3:4b가 매 입력을 light/medium/heavy로 판정. "무겁게/가볍게"로 수동 오버라이드.
- 호흡: 등급별 keep_alive로 무거운 모델은 곧 VRAM에서 자동 언로드.

## 파일 구성
- `comet.py` — 본체(라우터·페르소나·대화·도구 분기·화면 분기). 진입점.
- `voice.py` — 듣기(Whisper STT, 로컬 CPU) / 말하기(Edge-TTS InJoon). `synth_mp3()`=데몬용 mp3 바이트.
- `memory_db.py` — 장기기억 SQLite(`comet_memory.db`). 도구: remember/recall/list_todos/complete_todo.
- `files.py` — 파일/폴더 읽기(읽기전용): list_dir/read_file/search_files. 한국어 경로 별칭 해석.
- `projects.py` — 코인봇/주식봇 거래 CSV 통계(승률·손익). trade_stats/list_sources.
- `vision.py` — PC 화면 캡처(Pillow) → gemma3 멀티모달.
- `web.py` — **웹 검색(DuckDuckGo lite, 키 0) + 페이지 본문 추출.** search/fetch/research, **kl(지역·언어코드) 인자로 나라별 현지 결과 수집 가능**(kr-ko/us-en/cn-zh/jp-jp…), source에 lang(국가) 태그. 도구: web_search.
- `prices.py` — **라이브 시세(무키 JSON API 직접 호출, LLM 안 거침=날조 불가).** 환율(Yahoo)·코인(CoinGecko+업비트)·해외주가(Yahoo)·공포탐욕(alternative.me). 도구: get_fx/get_crypto/get_stock/get_fear_greed.
- `financials.py` — **재무·실적(Yahoo quoteSummary, 무키).** crumb+쿠키 우회로 매출성장·마진·선행PER/PEG/PBR·ROE·애널리스트 목표가·어닝 서프라이즈·매출 추세(ASCII 스파크라인) 추출. 숫자는 API 그대로(LLM 안 거침=날조 불가). 도구 get_financials. analyst가 자동 수집해 evidence·프롬프트에 주입(블로그 PER보다 우선). ⚠️미국/해외 티커 위주, 한국 공시(DART)는 키 필요→별도.
- `trace.py` — **진행 트레이스(콘솔 전용, 의존성0).** analyst의 검색→읽기→필터→추론→검증을 단계(◆)·세부(·국가깃발)·완료(✔)로 "진짜 AI처럼" 표시. 비판적 사고 단계(역발상·감사)는 호박색 think()로 별도 강조. ANSI 색은 Windows VT 켜질 때만(파이프/미지원이면 평문 폴백, 안 깨짐). 데몬/폰은 최종 답만 받고 트레이스는 서버 콘솔에만.
- `marketlog.py` — **정세 로그(SQLite market_log.db) + 가짜뉴스 필터.** web/analyst가 긁은 출처를 날짜·토픽·등급·링크로 적재(중복방지=내용 해시 UNIQUE). **신뢰 필터 assess(): 중대사안(암살·전쟁·디폴트 등)인데 듣보/단독/특보+과장어로 '말로만 확신' 주는 약한출처(등급3)는 rejected(가짜 의심)로 안 들임, URL 없으면 거부, 일반 등급3은 unconfirmed(참고), 등급1/2는 confirmed.** 도구 market_log(토픽 흐름 타임라인+링크 조회). analyst가 분석 때마다 log_research()로 자동 적재. (날짜로 '정세 흐름' 추적 + 나중 검토용 링크 보존)
- `finance_news.py` — **금융 뉴스 소스(Only Money 엔진과 동일 피드, 키0·의존성0).** 2026-06-28 신설. 종목별 Yahoo Finance RSS(`headline?s=티커`) + 거시(CNBC·MarketWatch·Investing) RSS를 받아 **analyst의 1차 근거**로 쓴다. **왜:** analyst가 web.py(덕덕고)로 "엔비디아 사도될까"를 검색하면 티스토리·블로그(등급3)만 긁혀 컨센서스가 부실(TQQQ는 출처 0개로 빈손)했음. Only Money 대시보드(index.html)가 쓰는 진짜 금융매체 RSS를 그대로 가져옴 → 이제 출처가 **전부 등급2 보도언론(fool/yahoo/247wallst)·날짜부착**. 반환 dict는 web.research source 와 동형이라 analyst._src_block/_sources_footer/_evidence 무수정 소비. 도메인당 3건까지 허용(단일 양질피드라 블로그식 1도메인=1건 안 함), FRESH_DAYS=30 신선필터. 검증: NVDA 6건(Vera Rubin 발표 등)·TQQQ 4건(구조적 감쇠비용)·블로그 0건, 속도 3분→1분53초. ⚠️Yahoo 종목피드는 가끔 섹터 일반기사(SpaceX·Vanguard 등)도 섞임(종목 100% 특정은 아님). **이미지 역검색(imagesearch.py)은 web.py를 '이게 뭔 사진인지' 식별 뒤 일반검색으로만 쓰므로 그대로 둠 — 금융 소스만 여기로 분리.**
- `analyst.py` — **종목·테마 분석 브레인(2패스 이중판단).** **(2026-06-28 소스 교체: gather()의 1차 근거가 덕덕고 다국어 → `finance_news`(금융 RSS). 덕덕고 다국어는 금융RSS가 텅 빌 때만 폴백.)** 수집(티커감지→Yahoo 1년일봉 실계산 52주·수익률·이평선 + **다국어 수집**: _plan_languages가 종목 공급망·시장 엮인 나라 2~3곳 골라 현지어 쿼리 생성[엔비디아→미국en·중국zh·한국ko]→나라별 web.research→도메인 중복제거. 영중일 검색이 fool.com·investing.com 등 진짜 금융매체를 끌어와 출처품질↑[한국검색만 하면 죄다 블로그]) → ①컨센서스 분석가(시장 다수의견+2차파급효과 지도) → ②역발상 검증관(컨센서스 역심리 + **1차의 미근거 수치 적발=할루시네이션 차단** + ✅확인/🤔추론/❓미확인 분리 최종콜). 가격은 파이썬이 직접 박아 그라운딩 보장. web.py+prices.py 재활용, 의존성 추가 0. 도구: analyze_stock. (Only Money 9단 파이프라인 사상의 로컬 압축판)
- `news.py` / `notify.py` — 뉴스 감시(구글뉴스 RSS, 날짜순) + **텔레그램** 푸시(send/format_digest). **2026-06-24 chat_id 연결 완료**(봇 @<봇핸들>, chat_id <chat_id>). 상시 스케줄러 = autonomy proactive.
- `market_brief.py` — **일일 미국 증시 브리핑 생성기(좁게: 지수·반도체·특징주).** 숫자=prices(Yahoo 실측, 날조0), 사건=구글뉴스 RSS(최근 FRESH_DAYS=2일·날짜필터로 stale 차단), 종합·해석=27b 2패스(①종합 ②사실감사). gather()→generate(). proactive가 미장 마감 후 호출 예정(아직 미연결). ⚠️숫자 그라운딩 검증됨. 해석 깊이는 튜닝 중(사건 소스를 DuckDuckGo→뉴스RSS로 바꿔 stale 9월블로그 섞이던 문제 해결).
- `autonomy.py` — **자율/상주 모드 레이어(comet 비대화 격리).** 모드=스레드+예외격리+가용성게이트. `ProactiveMode`(news.collect→판단게이트→notify.send, 모델0) · `WakeMode`(openwakeword 상시대기→voice STT→respond, 엔진없으면 available()=False). `Autonomy` 컨트롤러=enable/disable/status/restore + '자율모드 …' 명령 파서. 설정 autonomy_config.json. comet.py 가 `self.autonomy`로 생성·복원·토글만.
- `cloud.py` — **클라우드 두뇌(멀티 프로바이더): 클로드(`/v1/messages`)·GPT·OpenAI호환.** 메인 대화 최종 답변만 클라우드로(로컬 기본·키 없으면 폴백). cost_guard 검문. `chat()`이 ollama와 동형 반환. 설정 cloud_config.json(모델), 키=환경변수 또는 cloud_keys.json(런타임 `키 클로드 …`로 재시작 없이 교체).
- `kr_stocks.py` — **한국 종목 이름/코드 → .KS(코스피)/.KQ(코스닥) 공용 해석.** analyst·prices 단일 출처(NAME2TICKER_KR + name_to_ticker + is_bare_code).
- `daemon.py` — HTTP 서버(상주). GET / = 폰 웹UI, POST /chat, POST /tts, POST /imgsearch.
- `watchdog.py` — **데몬 상주 감시. 죽으면 자동 재기동(kill 안 함·살아있으면 안 건드림).** 헬스 8765, 마커 8766, 로그 watchdog.log. 부팅 등록=setup_watchdog.bat(옵트인).
- `us_stocks.py` — **미국 종목·ETF 이름 → 티커 공용**(엔비디아→NVDA·TQQQ·우주 등). analyst·prices 단일 출처.
- `profile_sync.py` — **호윤 최신 메모리 → profile/ 동기화**(코멧이 호윤 최신 인식). 명령 '프로필 동기화'(안전=복사/갱신, '정리'=미러).
- `client.py` — 콘솔 클라이언트(데몬 접속).
- `profile/` — 호윤 메모리 29개(.md). `MEMORY.md` 색인이 매 턴 SYSTEM에 로드돼 코멧이 호윤을 앎.
- `run.bat` / `run_daemon.bat` / `setup_remote.bat` — 런처.
- `daemon_token.txt` — 콘솔 클라이언트용 토큰(공유 금지).
- (구 프로토타입: `main.py`(Space), `ai_core.py`(ARIA), `shared.py` — 미사용)

## 기능
- **장기기억:** "기억해/할일 추가/목록/완료" → SQLite. format=json으로 동작 선택→실행→결과로만 답(거짓말 차단). 삭제는 미구현(실수 방지).
- **파일:** "바탕화면 뭐 있어/이 파일 읽어/~파일 찾아". 읽기전용. 쓰기·이동·삭제 없음.
- **라이브 시세(정확·날조불가):** "환율/비트코인/이더리움/주가(테슬라 등)/공포탐욕 얼마" → prices.py가 데이터 API 직접 호출해 **숫자를 LLM 안 거치고 그대로** 답(원달러·엔화·코인 USD+업비트원화·해외주가·공포탐욕). 검증됨.
- **웹 검색/사실확인 (정형화·원천추적·추론중심):** "~ 검색해/알아봐/사실확인/최신/날씨/뉴스" **+ 자동발동(키워드 없이도)** → DuckDuckGo 검색 → **도메인당 1개 중복제거**(블로그 머릿수 방지) → **원천 근접도로 분류**(1=원천·공식, 2=보도언론, 3=참고=블로그·위키·나무위키) + 추정날짜·원천인용단서 부착 → qwen3가 **매체로 믿지 말고 뿌리를 따라가 추론**. 답 방식 = 확인된 사실[출처N] 깔고 → **"제 추론으로는 ~로 보입니다" + 근거 + 확신도 + 미확인 분리**. 사실/추론 구분, 추론에서도 수치는 출처있는 것만. ⚠️**한계: 본문에 글자로 있는 사실=신뢰 OK(KTX·기준금리 등), JS로 그리는 라이브 숫자는 prices.py가 담당. 14b는 비핵심 수치(횟수 등)를 가끔 과장**하니 중요한 건 출처 확인. 잡담은 짧게. **자동발동(2026-06-20): 검색 키워드가 없어도, 문지기(gemma3:4b)가 입력을 보고 '시점민감·특정사실·수치·고유명사·사건처럼 가중치 지식만으론 틀릴 수 있다'고 판정하면 스스로 web_search를 띄운다(ROUTER_PROMPT가 tier와 web을 동시 판정→Router.route가 auto_web 검색어 반환→comet이 forced=("web_search",q)로 _chat_with_tools 호출).** 인사·하소연·의견·코딩·계산은 false(검색 안 함). 추가 LLM 호출 0(기존 라우터 판정에 얹음). 자동발동은 항상 medium(qwen3:14b)·기존 원천추적 경로 그대로. ⚠️4b 판정이라 가끔 과발동(아는 걸 검색=몇 초 지연)·미발동 가능하나, 명시 키워드("검색해")는 종전대로 항상 발동. 검증: 무키워드 '기준금리/미국 대통령/엔비디아 시총/RTX 출시일'=발동, '안녕/지쳤어/리스트 뒤집기/곱셈/왜 꿈을 꿀까'=비발동.
- **종목·테마 분석(이중판단·역심리):** "엔비디아 지금 사도 돼?/반도체 전망/TQQQ 비중" 등 가격이 아닌 '판단'을 물으면 analyze_stock. 1차=컨센서스(시장이 믿는 스토리)+**선반영 점검**(_regime()이 52주·이평선으로 국면을 계산해 단정→프롬프트에 리터럴 주입해 모델이 블로그 묵은 강세 대신 실측 우선하게. 검증: PLTR 강세85%→약세60% 교정)+**2차효과(실제 종목명+↑/↓)**→2차=사실감사(1차 미근거 수치 적발=할루시네이션 차단)+**컨센서스 틀릴 지점**(역발상)+확인/추론/미확인 분리+**뒤집을 트리거**+호윤 보유(엔비디아·TQQQ·우주株) 기준 갈아탈지. 가격·52주·수익률·이평선·**RSI(14)·ATR(14) 셋업지표**는 Yahoo 실계산값을 파이썬이 직접 박음(2×ATR 손절선·50일선 눌림 참고선 제시). **모델=gemma3:27b 고정**(2026-06-19 A/B/C 실측: 27b/27b가 추론깊이·할루시네이션감사 최고, 하이브리드는 14b 감사관이 약한고리. 정확>속도라 27b 고정). ~1.5분. **한국 종목 그라운딩 ✅2026-06-29**(NAME2TICKER_KR 종목명 + 6자리 코드 자동감지→.KS/.KQ 중 거래량 있는 진짜 listing 선택, KRW 실계산). 미국+한국 둘 다 실계산. ⚠️감사관이 가끔 반박용 미출처 숫자 끌어옴(방향은 맞음, 그래서 확인/추론 분리가 안전장치). get_stock(가격숫자)와 구분. 답 끝에 **🔗 출처 링크 footer**(검토용), 분석 때 수집자료가 **정세 로그에 자동 적재**됨. **Yahoo 펀더멘털(PER·목표가·매출추세)·실측 국면(_regime)·보유(holdings.txt)를 evidence와 프롬프트에 주입 — 블로그 PER/강세 풍문보다 실측 우선**(검증: 1차가 블로그 'PER 60배' 대신 실측 16.55/32.26 사용, PLTR 블로그강세→실측 약세 교정).
- **정세 로그(흐름 추적·가짜뉴스 필터):** "반도체 정세 / 엔비디아 흐름 어떻게 변해왔어" → marketlog가 그동안 쌓인 자료를 **날짜순 타임라인+링크**로. analyst·web·**news(자동수집)**가 긁은 출처가 자동 누적(중복방지·날짜·국가). **신뢰 필터**: 중대사안인데 듣보/단독/특보+과장어 약한출처는 가짜 의심으로 안 들이고, URL 없으면 거부, 등급1/2만 확정, 약한건 ?(미확인). 토픽은 종목/테마/키워드(엔비디아↔NVDA **별칭 통합 조회**). news.collect(log=True)가 신규 기사를 자동 적재.
- **재무·실적 조회:** "엔비디아 실적/재무제표/PER" → get_financials = Yahoo 무키 펀더멘털(매출성장·마진·PER/PEG·목표가·어닝 서프라이즈·매출 ASCII 그래프) 숫자 그대로.
- **보유 스샷 읽기:** "내 보유 읽어/포트 스샷" → read_holdings = 화면(증권사 MTS) 캡처→gemma3:27b로 보유종목 표 추출→holdings.txt 저장. 이후 종목 분석 시 그 보유 기준으로 갈아탈지/줄일지 조언(analyst가 holdings.txt 로드). ⚠️gemma3:4b보다 27b가 표 정밀.
- **화면 인식:** "화면 뭐 보여?" → PC 화면 캡처 후 gemma3가 설명. 폰에서 물어도 PC 화면을 봄.
- **이미지 역검색(2026-06-27):** "이 사진 역검색" / "이거 뭔 사진" → 이미지 식별 + 원본·출처추적. `imagesearch.py`. 입력 = **파일경로 → 클립보드 이미지/복사된 파일 → PC 화면** 순(자동). 3단 폴백: **A** 키 있으면 SerpAPI/serper Google Lens로 진짜 원본 출처(키는 `imagesearch_config.json`, 비면 자동 스킵) → **B** 무키 Yandex 최선시도(실측상 자주 막힘 → 조용히 통과) → **C** gemma3 식별→`web.py` 검색·출처추적(항상 됨, 키0). 동일 원본을 직접 못 찾으면 "식별·관련자료까지"라고 **정직하게** 답하고 URL/수치 날조 안 함. 코멧 키워드 `IMGSEARCH_KW`, 핸들러 `comet._image_search`(heavy 27b 종합). 무키 스크래핑 실측결론: 구글렌즈는 업로드 OK·결과페이지 403, 빙은 XHR로딩, 얀덱스는 핸들 안 줌 → "진짜 원본추적은 무료티어 키 1개가 정답", 호윤이 "키 자리만, 나중에" 선택. **폰 입력 추가(2026-06-29, ⑫ 완료):** 폰 웹UI 입력창에 `📷` 버튼 → 사진/카메라 선택 → base64로 `POST /imgsearch` 업로드(멀티파트 안 씀=의존성0) → 데몬이 `_uploads/`에 임시 저장 후 `comet._image_search(hint, path=tmp, hint=hint)` 호출(heavy 27b 종합) → 끝나면 임시파일 삭제. `_image_search`에 `path`/`hint` 인자 추가(주면 그 파일 직행, 없으면 기존 문장추출→클립보드/화면 폴백=PC만). 입력창에 글자 적고 📷 누르면 그게 hint로 같이 감. 인증·TTS읽어주기는 /chat과 동일 재사용. 검증: py_compile OK + base64→임시파일→`grab_image` 경로수령 격리테스트 통과(전체 식별은 ollama 필요=라이브 데몬에서). **남은 건 ⑬(A단 SerpAPI/serper 키)뿐 — 키 넣으면 폰에서도 진짜 원본추적 켜짐.**
- **음성(로컬):** 콘솔에서 `음성`/`v`=마이크, `말하기`=답 읽기, `소리작게/소리크게`=음량.
- **게임/바쁨 모드(GPU 양보):** "게임모드 켜/꺼/자동" (콘솔·폰 공용). **자동(기본)** = 매 턴 nvidia-smi로 GPU가 바쁜지 보고 바쁘면 무거운 모델 대신 4b로 답하고 모델을 20초 만에 언로드(게임 끊김 방지). **켜** = 항상 4b 고정. **꺼** = 평소 3단. 코드: comet.py `_gpu_busy()`(임계 `BUSY_GPU_UTIL`/`BUSY_VRAM_FREE_MIB`) + `_game_adjust()`. auto에서도 "무겁게/제대로/꼼꼼" 명시하면 강등 안 함.
- **콘솔 명령:** 음성(v) | 말하기 | 소리작게/크게 | 상태 | 잠자 | 요약 | 리셋 | 게임모드 | **두뇌(클로드로/지피티로/딥시크로/로컬로)** | **프로필 동기화** | **리로드(핫)** | **재시작(코드반영)** | 종료
- **페르소나:** 자비스형 — 매끄럽되 비판적. 무지성 칭찬·맞장구·이모지·체크리스트·되묻기 금지, 상태/모순을 근거로 짚음. (few-shot 예시 박음.)

## 폰/외부 접속 (보안)
1. PC에서 `run_daemon.bat`
2. 폰: Tailscale 앱 로그인(breath024@) → 브라우저 **https://desktop-hud9d6o.tailf00100.ts.net**
3. 화면 버튼: 💬 연속 음성대화 / 🎤 한 번 듣기 / 🔊 읽어주기
- **보안 3중:** 데몬은 127.0.0.1만 바인드(외부 직접 노출 0) → `tailscale serve`가 HTTPS로 프록시(타넷 전용) → `Tailscale-User-Login` 본인계정 인증(위조 불가) + 토큰(로컬 콘솔용). 폰은 토큰 입력 불필요(본인인증 자동).
- 음성: 폰도 데몬이 만든 **InJoon mp3** 재생(PC와 동일 목소리).

## 알려진 한계 / 주의
- **iOS:** 음성 입력(STT) 미지원(브라우저 한계) → 아이폰은 타이핑. 음성 출력은 됨(가끔 자동재생 막히면 한 번 탭).
- **코인봇 승률:** profile의 `project_coin_bot.md`엔 "승률 80%"(호윤 옛 주장), 실제 데이터 계산은 **46%**. 도구(trade_stats)가 답할 땐 46%(정확). 일반 대화에서 80% 나오면 profile 때문 — 도구 경유가 맞음.
- **도구 단일 동작:** 한 턴에 동작 하나 → "백테스트가 실거래보다 나아?" 같은 비교는 일반론으로 빠짐(따로 물으면 정확).
- **profile 정적 스냅샷:** 내 메모리 갱신돼도 자동 동기화 안 됨 → `profile/`에 .md 재복사 필요.
- **화면인식:** gemma3:4b라 디테일 한계. 정밀히 = `vision.py`의 `VISION_MODEL`을 gemma3:27b로(느림).
- **CLI/컴퓨터 제어:** 호윤이 "에바"라며 보류. 안 만듦.
- torch가 CPU 빌드(CUDA False) — Whisper STT는 CPU. 로컬 XTTS 목소리복제 하려면 Blackwell CUDA 먼저 고쳐야.

## 다음 후보

~~★ **(다음 작업·2026-06-29 합의) 기능 디스패치 테이블 통합 리팩터**~~ ✅**2026-06-29 완료**(위 ✅블록 참고). 아래는 당시 설계 메모(보존):

★ **기능 디스패치 테이블 통합 리팩터 — 동작 동일, 유지보수·DECIDE 비용만 개선.**
- **왜:** 지금 본체 기능 분기가 **병렬 레지스트리 2개**라 손으로 맞춰야 함 → ① 키워드 튜플(`MEMORY_KW`…`HOLD_KW`, 합쳐 `TOOL_KW`) ② `DECIDE_PROMPT`의 액션 18개 목록. 어긋나기 쉽고 실제로 imgsearch는 DECIDE에 없음(별도 분기). + `DECIDE_PROMPT`가 매 도구턴마다 액션 18개 명세를 통째로 14b에 주입 → 기능 늘수록 프롬프트 길어져 추론 느려지고 선택 정확도↓.
- **무엇:** `기능 = {keywords, 한줄설명, handler}` **선언적 테이블 하나**로 합침. 키워드 매칭(0비용)은 그대로, DECIDE엔 **키워드가 스친 후보 액션만** 명세 주입(온디맨드)해서 프롬프트 단축. 새 기능 추가 = 테이블 한 줄(두 군데 안 고침).
- **하지 말 것(효율 안 남):** 본체 기능 **전면 스킬화는 X.** 따져본 결론 — VRAM(최대 병목)은 tier가 정하지 기능이 아니라 0 절약, 지연임포트는 이미 핸들러 안에서 함(`import imagesearch`/`vision`), 닫힌 파라메트릭 도구(시세·환율 등 함수호출)는 '지침 로딩' 스킬 틀에 안 맞음. 스킬은 Coder(코딩=긴 지침)에 맞는 모양, 본체 도구는 디스패치 테이블이 맞는 모양 — 대칭 맞추려고 욱여넣지 말 것.
- **제약:** 동작·라우팅 결과는 **그대로**(리팩터지 기능변경 아님). `imgsearch`/`screen` 선검사 순서, 게임모드/자율모드 토글 분기 유지. 검증 = 기존 대화들 같은 액션으로 라우팅되는지 확인 후 교체.

⓪ **analyst 고도화**: ~~한국 종목 가격 그라운딩(.KS 야후)~~ ✅2026-06-29, ~~보유 포트폴리오 자동 연결~~ ✅(holdings.txt로 이미 됨), ~~셋업 지표(RSI/ATR 진입·손절가)~~ ✅2026-06-29. **남은 것:** 답 길이·속도 튜닝, 오답시 3패스(심판) 추가, Only Money 9단 prompts.json 사상 추가 역수입, (선택) 한국 종목 NAME2TICKER_KR 확장·6자리 외 종목명 더 넣기.
① **길찾기/대중교통**(카카오·네이버 지도 API, 키 발급 필요 — 버스 경로·정류장 겹침 같은 실시간은 웹검색으론 안 됨) ② 시세 확장(코스피·환율 더 많은 통화·국내주식 by 이름 — prices.py에 한 줄씩 추가) ~~③ 웹검색 자동발동~~ ✅완료(2026-06-20) ④ 도구 비교(multi-action) ④ Only Money(iCloud HTML) 연결 ⑤ iOS 음성입력(서버 Whisper+ffmpeg) ⑥ profile 자동 동기화 ⑦ 화면인식 정밀도(27b 옵션) ⑧ 데몬 자동 시작(부팅 시) ⑨ 뉴스 알림 디스코드 웹훅 연결 + 상시 스케줄러 ⑩ **데몬 watchdog**(부팅 자동시작 + 죽으면 재시작) — '항상 켜져 있기'(autonomy의 토대, 아직 미구현). + **자율모드 활성화**: proactive용 watchlist 키워드 등록 명령 + 텔레그램 chat_id 연결 명령, wake용 openwakeword 설치 후 리스너 실측. ~~vision/analyst 출력상한+타임아웃~~ ✅2026-06-24 완료 ⑪ 환각 다듬기: projects.py가 '파일없음'과 '거래 0건'을 구분해 반환(모델이 옛 80% 승률 안 꺼내게) ~~⑫ **이미지 역검색 폰(데몬) 입력**~~ ✅2026-06-29 완료 — daemon.py `/imgsearch`(base64) 엔드포인트 + 폰 웹UI 📷 버튼. 위 '이미지 역검색' 항목 끝의 '폰 입력 추가' 참고. ⑬ 이미지 역검색 A단 키 발급(SerpAPI 월100 무료 or serper) → `imagesearch_config.json`에 넣기만 하면 진짜 원본추적 켜짐.
