# COMET-Coder 모델 다운로드 / 선택 (2026-06 기준)

RTX 5070 Ti · VRAM 16GB 기준. 에이전트 코딩(읽고·고치고·돌려보는 반복)에 맞는 최신 추천.

## 받을 것 (둘 다 받아서 비교해보길 권함)

```powershell
# 1순위 — 코딩 품질 1위급. MoE(30B 중 3.3B만 활성)라 크지만 빠름. 256K 컨텍스트.
ollama pull qwen3-coder:30b

# 2순위 — Mistral 에이전트 전용 모델. 멀티파일 편집·디버깅 루프·툴콜이 가장 안정적.
#          24B 밀집형이라 16GB에 완전히 들어가서(흘러넘침 없음) 더 빠르고 끊김 없음.
ollama pull devstral
```

받고 나면 `coder.py` 가 자동으로 `qwen3-coder:30b` 를 잡는다(llm.py 기본값). 바꾸려면
`coder_config.json` 의 `"local_model"` 을 `"devstral"` 등으로 적으면 됨.

## 둘 중 뭘 기본으로?
- **qwen3-coder:30b** — 코딩 정답률·복잡한 작업이 더 셈. 단 Q4가 ~18GB라 16GB를 살짝 넘어 일부가 RAM으로 흐름. MoE라 그래도 쓸 만한 속도. **품질 우선이면 이거.**
- **devstral** — 24B가 16GB에 통째로 들어가 더 빠르고, 에이전트 툴 호출(JSON) 형식이 가장 안 깨짐. 우리 에이전트 루프와 궁합이 좋음. **안정·속도 우선이면 이거.**
- 둘 다 느리면 `ollama pull qwen2.5-coder:14b` (가볍고 빠른 구세대, 9GB로 여유).

## 아까 정정
처음에 권한 `qwen2.5-coder:14b` 는 최신이 아님 — 위 두 모델의 전 세대. 위 둘이 같은 16GB에서 더 좋다.

## API로 승급(돈 여유 생기면)
환경변수 하나면 됨:
```powershell
setx COMET_API_KEY "발급받은키"     # 새 콘솔부터 적용. 확인: python llm.py
```

### 자동 승급 (사고 안 나게 설계)
`backend`(coder_config.json) 값에 따라:
- `"auto"`(기본) — **로컬 먼저(공짜)** 시도 → 막히면(무한루프·연속실패·한도초과·모델오류) **그때만 API로 한 번 재시도**. 평소 비용 0, 어려운 것만 돈.
- `"local"` — 로컬만. `"api"` — 항상 API.

### 비용 사고방지 (cost_guard.py)
API(유료) 폭주를 막는 상한. `coder_config.json` 에서 조정, 사용량은 `comet_usage.json` 에 날짜별로 쌓임:
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
- 과제당 호출 상한 → 한 작업이 폭주 못 함
- 하루 호출/토큰 상한 → 넘으면 차단(돈 새는 것 방지)
- 무한루프(같은 동작 3회 반복)·연속 실패(4회)는 자동 중단
- 키는 `COMET_API_KEY` 환경변수(파일에 안 박음)

### 꽂을 수 있는 제공자 (OpenAI 호환)
- **OpenRouter**(추천) `https://openrouter.ai/api/v1` — 키 하나로 DeepSeek/Claude/Qwen 등 300+. 무료 모델도 있음. 어려운 건 api_model 을 `anthropic/claude-...` 로.
- **DeepSeek**(최저가) `https://api.deepseek.com` · `deepseek-chat`
- **GLM-4.x-Flash**(무료) Z.AI · **Qwen**(신규 100만토큰 무료) Alibaba — base URL 은 각 docs 참고.
클로드의 1/50~1/100 가격대.
