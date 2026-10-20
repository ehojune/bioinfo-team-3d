## 2026-10-20 13:36 — `gemini-manual-architecture`

**[Gemini]**

## 단계
문서 및 아키텍처 매뉴얼 정리 — `docs/manual.md` · `README.md` · `README.en.md` · `AGENTS.md`

## 한 일
- **매뉴얼(`docs/manual.md`) 내 개발 에이전트 전용 규칙 분리**:
  - `## 설정 포인트`에 섞여 있던 개발용 `pytest` 홈/캐시 격리 규칙 및 Ubuntu Python 3.10 CI 진단·대기 상한 안내, `signatures.yaml`·`topic_checklists.yaml`의 테스트/fixture 작성 지시를 `AGENTS.md`로 이관하고 매뉴얼은 `labhq`를 깊이 공부·운영하려는 사용자 관점 설명으로 정리했습니다.
  - 목차에서 누락되어 있던 `[PI에게 보이는 글](#pi에게-보이는-글)`을 목차에 추가하고, 프롬프트 지시문 어조 대신 `labhq/pi_language.py`와 `labhq/korean_qc.py`의 평문 변환·문체 점검 동작 설명으로 다듬었습니다.
- **전체 계층 구조와 모듈 아키텍처 및 다크 다이어그램 추가**:
  - `docs/manual.md`의 `## 구조와 이벤트` 아래에 `### 전체 계층 구조와 모듈 아키텍처` 절을 신설하고, 검정 바탕·흰색 글씨(`theme: dark`, `#0b0f19`/`#111827`)의 4계층(`PI 접점` ↔ `Gateway·SQLite WAL·CSO·Policy` ↔ `Research & Meaning Devices 6종` ↔ `Runner·CLI Adapters·HPC`) Mermaid 다이어그램과 계층별 핵심 모듈·책임 표를 반영했습니다.
- **오래된 수치 및 파일 구조 최신화**:
  - `labhq/vocab/output_types.yaml` 및 `topic_checklists.yaml`(`docs/vocabulary.md`)의 현행 기준인 **45개 분야·135항목**, **어휘 125개 key(79개 EDAM 연결)**를 `README.md`, `README.en.md`, `docs/manual.md`에 일치시켰습니다.
  - `docs/manual.md`의 `## 파일 구조` 트리에 `supervisor.py`, `durable.py`, `verify.py`, `reproduction_receipt.py`, `portfolio.py`, `result_storage.py`, `korean_qc.py`, `labhq/research/`, `labhq/evidence/`, `labhq/recruit/paper_skills`, `labhq/skills/` 등 v0.5 모듈 구성을 반영했습니다.

## 테스트 결과
- [x] `pytest -q tests/test_integrations.py tests/test_direct_outputs.py tests/test_nfcore_default_732.py tests/test_pdf_figure_policy.py tests/test_staff_writing_skills.py tests/test_vocab_tables.py`: `115 passed, 2 skipped`
- [x] `python scripts/integrations.py --check` · `python scripts/vocab_tables.py --check` · `python scripts/patch_notes.py check` · `python scripts/notes_index.py --validate`: 통과

## 바꾼 파일
- `docs/manual.md`
- `README.md`
- `README.en.md`
- `AGENTS.md`
- `patch_notes/entries/gemini-manual-architecture.yaml`
- `docs/status/2026-10-20-1336-gemini-manual-architecture.md`

## 막힌 점
- 없음

## PI·Claude에게 물을 것
- 없음

## 확인
- [x] `scripts/check_public.sh` 통과 (비밀값, 실제 설정, 데이터 파일 없음)
- [x] `docs/status/2026-10-20-1336-gemini-manual-architecture.md`에 같은 보고 추가
- [x] ⛔ 체크포인트에 해당하면 PI 확인을 받았음

<!-- author:gemini -->
