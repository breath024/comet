# COMET-Coder — 로컬 코딩 에이전트 (핸드오프)

클로드 코드 대신 로컬에서 코딩을 시키는 에이전트. 호윤의 RTX 5070 Ti(16GB)에서 작동.
**큰 목표 = "클로드(나)를 대체할 수준"으로 끌어올리기.** (호윤이 시킨 방향)

- **경로:** `C:\Users\USER\Desktop\개인프젝\A.B.B.Y.S\COMET\ai\`
- 최종 업데이트: **2026-08-18** (벤치 Round1/2 점수 확보 = **29/29 동점**. `agent.py` PROTOCOL 검증 문구 구체화 + `llm.py` `num_ctx=32768`. 날짜별 상세는 `logs/`)
- 1단계 완성·검증: 2026-06-23 (더미 테스트 9/9 통과)

---

## ⚠️ 세션 끊김 대비 — 지금 어디까지 왔나 (이어받는 사람 먼저 읽기)

직전 작업 = **COMET-Coder를 클로드 대체 수준으로 끌어올리는 중.** 두 갈래를 동시에 진행하다 (클로드)세션이 API 오류로 끊겼다.

> **★ 기본 모델 결정: `qwen3-coder:30b` vs `devstral` → `qwen3-coder:30b` 채택 (2026-06-24 selftest 판정).**
> 둘 다 ollama에 깔려 있음. `selftest.py`로 같은 5개 시나리오 채점한 결과:
>
> | 시나리오 | qwen3-coder:30b | devstral |
> |---|---|---|
> | 새 스크립트 | ✅ 24s | ✅ 32s |
> | 버그 잡기 | ✅ **1769s** ⚠️ | ✅ 21s |
> | 여러 파일 | ✅ 28s | ✅ 27s |
> | 정밀 수정 | ✅ 11s | ❌ text(수정 안 하고 산문 응답) |
> | 파일 잡일 | ✅ 18s | ❌ ask(안 하고 되물음) |
> | **통과** | **5/5** | **3/5** |
>
> - **qwen3-coder:30b 승**(정확도·JSON 프로토콜 적합도). 이미 `llm.py` 기본값이라 **추가 설정 불필요**(devstral 쓰려면 config 필요).
> - devstral 실패 2건 = JSON 프로토콜 미준수(text)·불필요한 되물음(ask). `MODELS.md`의 "devstral은 네이티브 tool-calling 튜닝이라 이 루프엔 약함"이 실측으로 확인됨.
> - ✅ **해결됨: qwen3-coder "버그 잡기" 1769초(29분) 폭주.**
>   - **원인:** ① VRAM 오버플로우(`ollama ps` 실측 `33%/67% CPU/GPU` — 19GB Q4 모델이 16GB VRAM 초과 → 33%가 CPU). 이게 토큰당 속도를 떨어뜨리는 **증폭기**. ② 한 스텝이 출력 폭주가 **방아쇠**. 느린 속도 × 폭주 길이 = 29분. (오버플로우만으론 설명 안 됨 — 다른 4개는 11~28초였음.)
>   - **떠본 결과(중요):** 같은 디버그 스텝을 직접 재현하니 **정상 동작은 ~100글자·깨끗한 JSON 한 줄·`<think>` 0·반복 0·2초.** 즉 이 모델은 평소 길게 안 토함 → 29분은 평소 수다가 아니라 **드문(확률적) 디코딩 사고**. 유력 메커니즘: 에이전트에 준 **온도 0.1**(모델은 0.7 튜닝) → 거의 그리디 디코딩 → 가끔 반복 루프, `repeat_penalty` 1.05로 낮아 못 빠져나옴(트레이스백 같은 숫자·특수문자 입력이 유발). **매번 안 나서 방아쇠를 못 잡음 → 피해를 묶는 게(출력 상한) 정답.** 빈도 낮추려면 `repeat_penalty`↑(1.1) 옵션 가능하나 상한이 이미 무해화해 필수 아님.
>   - **수정:** `llm.py`에 출력 상한 `max_output_tokens`(기본 2048) 추가 → 로컬 `num_predict` / API `max_tokens` 양쪽에 박음. **검증:** "1~10만 출력" 프롬프트가 539에서 잘리고 66초에 종료(상한 작동 확인). 이제 한 스텝/한 콜이 물리적으로 폭주 불가.
>   - ⚠️ 부작용 주의: 정당하게 큰 파일을 쓰다 2048토큰에서 잘릴 수 있음 → 그러면 `coder_config.json`의 `max_output_tokens` 상향.

1. **클로드와 정면 벤치마크** (얼마나 따라왔나 객관 측정)
   - `compare.py` = **Round 1**(쉬움→어려움): 중복제거(해시불가 함정)·LRU 캐시·수식 파서(eval 금지). 3과제.
   - `compare2.py` = **Round 2**(격차를 노린 어려운 유형): LIS O(n log n) 성능제약·크로스파일 버그추적·텍스트 양쪽정렬(LC68). 3과제.
   - 방식: **같은 프롬프트**를 COMET 에이전트에 주고 실제로 파일을 만들게 한 뒤, **클로드가 손으로 짠 정답(`CLAUDE_*` 문자열)과 동일한 숨은 테스트**(subprocess 격리+타임아웃)로 둘 다 채점 → 공정 비교. 점수는 콘솔 출력만, **파일로 저장 안 됨** → 다시 보려면 재실행.
   - 실행: `python compare.py` / `python compare2.py` (둘 다 `agent`,`dev_tools`,`llm` import. ollama에 코딩 모델 깔려 있어야 함).
   - ⚠️ 점수표를 못 봤거나 재현하려면 위 둘을 다시 돌려라. 격차가 큰 과제 = 다음에 보강할 약점.

   **벤치 스크립트 3개 역할 구분(헷갈리기 쉬움):**
   - `compare.py` / `compare2.py` = **모델 고정(현재 백엔드) · 클로드 vs COMET.** 난이도만 다름(Round1/2). 모델은 안 바꿈.
   - `selftest.py` = **모델 비교용.** `python selftest.py <모델>`로 인자 받아 그 모델로 5개 시나리오 채점(`backend=local` 강제). 모델 A vs B는 여기서. `selftest_hard.py`는 더 어려운 버전.

2. **API 승급층 + 비용 사고방지** (로컬이 막히는 어려운 일은 API로)
   - `llm.py` = 로컬(ollama)·API를 한 `chat()`으로 통일. `backend: auto`면 키 있을 때만 API.
   - `agent.run_smart()` = **로컬 먼저(공짜) → 막히면(limit/loop/stuck/error) 키 있고 가드 통과 시에만 API로 한 번 재시도.**
   - `cost_guard.py` = 과제당/하루 호출·토큰 상한, 무한루프·연속실패 차단. 사용량은 `comet_usage.json`에 누적.
   - **★ 비용 폭주 차단(2026-06-24 추가):** API 본문에 `max_tokens`(=`max_output_tokens`, 기본 2048) 박음 → **한 콜이 폭주해도 출력이 물리적으로 잘려 과금이 묶임.** 사전검문도 출력 상한까지 비관적으로 계산해 하루 토큰 천장이 진짜 천장. (이전엔 API에 출력 상한이 없어 폭주 시 다 과금되는 구멍이 있었음 — 닫음.)
   - **현재 상태(중요):** `coder_config.json` **없음** + `COMET_API_KEY` **미설정** → 지금은 **로컬 전용으로만 돈다.** API 승급 경로는 코드만 완성, **라이브 검증 안 됨.**
   - `comet_usage.json`에 `2026-06-23: 3콜/30토큰`만 찍힘 = API를 켜고 잠깐 시험한 흔적(아주 소량).
   - **끊긴 원인 = COMET 문제 아님.** 작업하던 **클로드(나) 세션이 API 오류로 끊긴 것**이라 호윤이 이 핸드오프를 부탁. COMET의 DeepSeek/승급 경로는 멀쩡. 즉 "코드는 멈춘 데 없고, 작업자(클로드)만 교체되면 되는 상태."

### 다시 시작하면 할 일 (순서)
1. ollama에 코딩 모델 있는지 확인: `ollama list` → 없으면 `MODELS.md`대로 `ollama pull qwen3-coder:30b`(또는 `devstral`).
2. `python compare.py` → `python compare2.py` 돌려 **현재 점수 확보**(클로드 대비 어디서 깨지나).
3. API 승급을 실제로 쓸지 결정 → 쓸 거면 `coder_config.json` 만들고(아래 예시) `setx COMET_API_KEY "키"` 후 `python llm.py`로 백엔드 확인. 안 쓸 거면 로컬만으로 충분(평소 비용 0).
4. 깨진 과제 유형을 스킬(`skills/*.md`) 지침·프롬프트로 보강하거나, 어려운 건 API로 승급.

---

## 실행
```powershell
# 런처(권장): 폴더를 run_coder.bat 에 드래그하면 그 폴더가 작업 폴더. 더블클릭하면 바탕화면.
run_coder.bat

# 또는 직접:
python coder.py "C:\작업할\폴더"
```
- **Python 3.12 고정** (COMET 본체와 동일 이유 — torch/whisper 등 3.12에만 깔림. `run_coder.bat`이 3.12 경로 박아둠).

콘솔에서:
- `/code 계산기 만들어줘`  — 새 작성
- `/debug`  로 모드 바꾸고  `이거 왜 에러나`
- `/study` `/automate` `/strategy`
- 명령: `/skills`(목록) `/cd <경로>` `/auto`(쓰기·실행 확인 끄기) `/backend`(현재 백엔드) `/pwd` `종료`
- 에이전트가 `❓`로 물으면, 답을 입력하면 **이어서 진행**. 다른 스킬로 바꾸면 취소.

## 구조 (파일)
| 파일 | 역할 |
|---|---|
| `coder.py` | 콘솔 진입점. 스킬 선택·모드 분기·이어가기. 작업은 `agent.run_smart`(자동 승급)로 돌림 |
| `agent.py` | 에이전트 루프. `run/resume`(JSON 프로토콜 ReAct) + `run_smart`(로컬→API 승급 오케스트레이터) + 무한루프/연속실패 차단 |
| `dev_tools.py` | ToolBox: read/list/search/grep/write/edit/run_shell. 파괴적 작업=확인+백업(.comet_bak), 셸 UTF-8 강제 |
| `llm.py` | 모델 백엔드. 로컬(ollama) 기본, `COMET_API_KEY` 있으면 API 자동 승급. JSON/`<think>` 파싱 헬퍼 포함 |
| `cost_guard.py` | API 비용 사고방지(과제당/하루 호출·토큰 상한). 사용량 → `comet_usage.json` |
| `skills.py` + `skills/*.md` | 스킬 로더. .md 하나=스킬 하나 |
| `coder_bridge.py` | comet.py·데몬(폰)에서 "코딩 ..." 명령 → 에이전트 실행(비대화형 auto, comet 미의존=순환 import 없음) |
| `compare.py` / `compare2.py` | **클로드 vs COMET 벤치마크**(Round1/Round2, 동일 숨은테스트 채점). 결과는 콘솔만 |
| `selftest.py` / `selftest_hard.py` | 자가 테스트 + 자동 채점. `python selftest.py [모델]` |
| `MODELS.md` | 모델 다운로드/선택 안내 |
| `coder_config.json` | (선택·**현재 없음**) 설정 덮어쓰기 |
| `comet_usage.json` | API 사용량 날짜별 누적(가드가 읽고 씀) |

## 스킬 추가법
`skills/` 에 `이름.md` 만 떨어뜨리면 끝(코드 수정 불필요).
- 첫 줄 `# 한 줄 설명` 이 목록에 뜸.
- 본문 = 그 모드의 지침(에이전트 system 에 덧붙음).
- 도구 안 쓰고 그냥 대화시키려면 어딘가에 `<!-- mode: chat -->` 한 줄.

## 모델
- 기본 = `qwen3-coder:30b` (MoE, 코딩 1위급, 16GB에 살짝 흘러도 빠름).
- 폴백 = devstral → qwen2.5-coder:14b → qwen3:14b → gemma3 (llm.py `local_fallbacks`).
- 바꾸려면 `coder_config.json`:  `{"local_model": "devstral"}`
- **API 승급**(돈 여유 시): `setx COMET_API_KEY "키"` → DeepSeek 등으로 자동. 코딩 훨씬 좋아짐.
- 모델 비교: qwen3-coder:30b 가 우리 JSON 프로토콜과 궁합 최고(devstral 은 네이티브 tool-calling 튜닝이라 이 루프에선 약함).

### API 승급 설정 예시 (`coder_config.json`, 안 만들면 로컬 전용)
```json
{
  "backend": "auto",
  "api_base": "https://openrouter.ai/api/v1",
  "api_model": "deepseek/deepseek-chat",
  "auto_escalate": true,
  "api_calls_per_task": 30,
  "api_calls_per_day": 300,
  "api_tokens_per_day": 1500000
}
```
- `backend`: `auto`(로컬 먼저, 막히면 API) / `local`(로컬만) / `api`(항상 API). 키 없으면 auto여도 로컬.
- 제공자(OpenAI 호환): OpenRouter(키 하나로 300+, 무료모델 有)·DeepSeek(최저가)·GLM-Flash/Qwen(무료티어). 자세한 건 `MODELS.md`.
- 키는 **파일에 박지 말고** `COMET_API_KEY` 환경변수로. 확인: `python llm.py`.

## 검증된 것 / 한계
- 검증: 새 스크립트·버그수정·여러파일·정밀수정·파일잡일·에러반복수정·크로스파일 리팩터·빠진 모듈 생성·큰 파일 정밀수정 = 전부 통과(1단계).
- 한계: 14B/30B 로컬은 복잡한 설계·긴 추론은 클로드만 못함. 큰 일은 작은 단위로 쪼개면 잘함. 어려운 건 API 승급으로.
- **클로드 대체율(2026-08-18 실측):** Round 1 **COMET 15/15 · 클로드 15/15**, Round 2 **14/14 · 14/14**.
  단 이건 **벤치가 포화됐다는 뜻**이지 동급이라는 뜻이 아니다 — 세 과제 다 빈 폴더에 새 파일
  쓰기라 난이도가 천장에 닿았다. 실제 레포 기준 벤치가 필요하다(위 「다음에 해야 할 것」).
  점수는 콘솔 출력만이라 파일로 안 남는다 → 다시 보려면 재실행.

## COMET 본체/폰에서 코딩 시키기 (연결 완료)
COMET 콘솔(comet.py)·데몬(폰 웹UI) 어디서든 메시지로:
```
코딩 [@작업폴더경로] [/code|/debug|/study|/automate] <할 일>
예) 코딩 @C:\Users\USER\Desktop\myproject /debug main.py 실행하면 나는 에러 고쳐줘
예) 코딩 바탕화면에 정리 스크립트 만들어줘     (폴더 생략 시 바탕화면 기준)
```
- 콘솔/폰의 "코딩" 명령은 **비대화형 = auto 승인**으로 돈다(폰은 y/n 확인 불가). 대신 **모든 쓰기는 .comet_bak 백업**되어 되돌릴 수 있음.
- y/n 확인을 받으며 신중히 하려면 PC에서 `run_coder.bat`(coder.py) 쪽을 써라.
- 구현: comet.py respond() 앞단 분기 + coder_bridge.py (run_smart 경유 = 폰에서도 자동 승급).

## 다음에 해야 할 것

- [ ] **★벤치를 실제 레포로 다시 짜기 (1순위).** 지금 벤치는 **포화됐다** — Round 1·2 모두
      COMET 29/29 · 클로드 29/29 동점이라 더 이상 차이를 못 본다. 게다가 세 과제 다
      **빈 폴더에 새 파일 쓰기**라 실사용과 거리가 멀다. 수천 줄 규모의
      코드베이스에서 **함수 찾아 고치기** 형태로 다시 짜야 진짜 약점이 보인다.
- [ ] **PROTOCOL 효과 재확인(n=1).** 2026-08-18에 중복제거가 0/4 → 4/4로 올라간 게
      PROTOCOL 문구 덕이라고 봤는데 **한 판씩만 재봤다.** 옛 문구로 되돌려 2~3회 더
      돌리면 확정된다(판당 약 2분).
- [ ] **API 승급 라이브 검증:** 키 꽂고 로컬 막힘→API 재시도 경로가 실제로 도는지
      (비용가드 차단·기록 포함) 확인. ⚠️ 키는 **환경변수에 넣지 말 것**
      (`ANTHROPIC_API_KEY`가 있으면 클로드 코드의 `/login`을 덮어써 호윤 크레딧으로 과금된다).
- [ ] 안전 UX 다듬기(diff 미리보기, 폰에서 위험작업 2단계 확인).
- [ ] 실전 프로젝트에서 돌려보며 약점 보강.
- [ ] (선택) 안 쓰는 ollama 모델 ~40GB 정리 — 호윤 확인 후.

### ⚠️ 이미 재보고 결론 난 것 (다시 제안하기 전에 읽을 것)

- **"컨텍스트를 키우면 좋아진다" = 아니다.** `num_ctx`를 키우면 KV 캐시가 VRAM을 먹어
  모델이 CPU로 밀린다: 4096에서 81.5 tok/s → 131072에서 31.2 tok/s(실측).
  **32768을 쓰는 이유는 속도가 아니라 조용한 잘림 방지다** — 넘친 프롬프트는 경고 없이
  버려진다(같은 입력이 2048에선 1,026토큰, 16384에선 8,194토큰으로 들어갔다).
  `dev_tools.MAX_READ=20000`(≈5천 토큰)이라 4096이면 지시문부터 날아간다.
- **컨텍스트는 벤치 점수를 안 올린다.** 4096으로 내려도 15/15가 그대로 나왔다.
  점수를 올린 건 `agent.py`의 PROTOCOL 문구다.
- **로컬 모델 교체 = 지금 쓰는 게 최선.** `qwen3-coder:30b`보다 나은 걸 찾다가 세 번
  헛짚었다(`qwen3-coder-next` 52GB · `devstral-2` 123B/75GB · `qwen3.8:27b`는 dense 24GB).
  16GB 한 장에 들어가면서 이보다 나은 건 지금 없다. **모델 스펙은 검색 요약 말고
  ollama.com 태그 페이지에서 파일 크기로 확인할 것.**

## 기록

날짜별로 그날 바꾼 것·터진 것·실측 수치는 `logs/`에 쌓는다(이 파일은 현재·미래만 담는다).

- [2026-08-18](logs/2026-08-18-log.md) — 벤치 3판으로 원인 분리(컨텍스트 X, PROTOCOL O).
  중복제거 0/4의 진짜 원인 = `return` 자리의 `yield`. `num_ctx` 속도표 실측.
