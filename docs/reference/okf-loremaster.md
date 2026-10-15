# OKF Loremaster 검토 (#620)

[PFreda-Lab/okf-loremaster](https://github.com/PFreda-Lab/okf-loremaster)를 읽기만 하고 본 기록이다. 코드를 실행하지 않았고 가져오지도 않았다. 채택은 후속 issue에서 한다.

| 항목 | 값 |
|---|---|
| 본 커밋 | `ead3819` (2026-09-04, 태그 v0.4.1) |
| 라이선스 | Apache-2.0, NOTICE 파일 없음 |
| 성숙도 | Alpha, 메인테이너 한 명, LangGraph·litellm·자체 LLM 키에 묶임 |

## 무엇인가

질문 하나로 PubMed를 검색하고, 초록 스크리닝과 PMC 전문 추출을 거쳐 주제별 마크다운 코퍼스(OKF)로 내보내는 멀티 에이전트다. 추출한 수치·인용문은 코드가 원문과 대조하고, 원문에 없으면 행을 지우지 않고 신뢰도만 낮춘다. 논문마다 읽은 본문 범위(`full_text`·`abstract`)와 재배포 가능 여부(`license`·`export_safe`)를 적는다.

## labhq가 가져가는 것과 이유

핵심은 원문 대조 검증이다. 지금 labhq 검증기는 출처 ID가 실존하는지까지만 보고, 그 논문이 정말 그렇게 말하는지는 `ClaimCheck.support_review`가 늘 사람 판단(`reviewer_required`)에 남긴다. 인용문·수치가 본문에 없는 날조는 가장 싸게 거를 수 있으니, reviewer가 보기 전에 기계가 표시한다. 판정은 원문에 글자가 있다 없다까지이고 뒷받침한다는 증명은 아니다.

| 종류 | 가져올 것 | labhq 쪽 | 크기 | 후속 |
|---|---|---|---|---|
| 코드 이식 | `verification.py`의 원문 대조 순수 함수. 수치는 주장한 표기 정밀도로, 인용문은 대소문자·구두점·공백을 접고 단어 창으로 대조한다. 한글을 지우는 `normalize`, Python 3.11 전용 문법, okf 스키마 의존은 고친다 | `labhq/evidence/literal_check.py` | small | #622 1단계 |
| 구조 | 선행 연구 조사에서 모델은 인용문 머리말만 쓰고 코드가 본문에서 전체 문장을 채운다. 검증기는 PMID·PMCID로 초록·전문을 직접 받아 대조하고, 못 찾은 행은 지우지 않고 표시만 한다 | `labhq/orchestrator/cso.py`, `agents/core/lit_scout.yaml`, `labhq/evidence/verify.py` | medium | #622 2단계 |
| 구조 | `test_domain_agnostic.py` 방식. 벤치 사례의 정답 식별자가 비벤치 코드·프롬프트에 나오면 실패하는 test | `tests/test_bench_no_answer_leak.py` | small | #623 |
| 참고 | 읽은 본문 범위, `license`, `export_safe` 기록. #609에서 풍부한 reference를 v1 이후로 미뤘으니 설계 메모로 둔다 | 보고서 '참고' 절(#609), `SOURCES.md`(#607) | small | #622 2단계에서 범위 값만 먼저 |

## 가져오지 않는 것

- 저장소 통째 이식과 스킬 채용: Alpha 단독 프로젝트이고 LangGraph·litellm·자체 LLM 키에 묶여 있다.
- Retry-After 재시도, 비용 unknown 표기, 결과 없음에 검색 query 기록: labhq에 이미 있다.

## 라이선스

- 코드를 옮기면 Apache-2.0 §4를 지킨다. 라이선스 전문 사본을 동봉하고, 옮긴 파일 머리에 변경을 표시하고, 원본 출처(저장소·커밋 `ead3819`·파일 경로)를 남긴다. 원본에 저작권 줄과 NOTICE가 없어 옮길 NOTICE 문구는 없다.
- Apache-2.0은 GPL-3.0-or-later 저장소에 넣을 수 있다(GPLv3와 호환). `labhq/evidence/NOTICE.md`는 `labhq/vocab/NOTICE.md` 형식을 따르고, `docs/manual.md` 라이선스 표에는 three.js 선례처럼 예외 행을 더한다.
- 구조와 아이디어만 참고하는 항목은 의무가 없다. 선행 연구 조사 적용은 이식한 함수를 호출하므로 이식 쪽 의무가 따른다.
- OKF가 만든 번들은 원 논문 저작권의 영향을 받을 수 있다. OKF는 ND·NC·미상 라이선스 본문을 내보내기에서 거른다.

코드를 실제로 옮기면 이 노트와 `docs/references.md`·`docs/references.en.md`의 행을 "코드를 옮겼다"로 고친다.
