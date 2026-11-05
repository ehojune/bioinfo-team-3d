## 결론

단계 산출 표가 표로 읽히는지와 문헌을 읽은 수준을 runner가 결정적으로 검사하고, CP2 카드와 보고서 한계 절에 한 줄씩 올립니다. Fixes #710. Refs #697.

## 바뀐 것

- `labhq/output_checks.py`: `.tsv`·`.tab`·`.csv` 산출의 칸 구분(글자 `\t`)과 줄별 칸 수 검사, `labhq_annot.fulltext` 조회 기록에서 논문별 최고 접근 수준 집계, 보고서 경고 문장.
- runner가 단계 뒤 두 결과를 `TaskResult.table_warnings`·`literature_access`에 담습니다(비어 있으면 wire에서 빠짐).
- 연구 요청은 `research_contract.result_warnings`(CP2 카드·보고서 `## 한계`), 일반 요청은 `## 보고서 경고`에 들어갑니다.
- 연구 계획 프롬프트: 문헌 갈래는 결론에 쓰는 논문의 본문을 읽고 access_level을 적으며, PI가 원할 때만 '초록만' 경계를 둡니다. 고정 hash를 갱신했습니다.

## 확인

- 새 test 15개(`tests/test_output_checks.py`)는 main에 모듈이 없어 실패하고 이 branch에서 통과합니다. 10-17 시운전의 글자 `\t` 표, R 행 이름 표, 따옴표 CSV, 바깥 경로, 연구 CP2 카드·한계 절까지 봅니다.
- 보고서 경고·wire·연구 흐름·runner 경계 관련 test 59파일 2,016 passed.

## 남은 것

- #697의 '결론 근거 중 초록 수준 논문 수'는 fulltext로 조회한 논문 기준입니다. claim별 근거와 논문을 잇는 집계는 근거 원장에 논문 ID가 생기면 다시 봅니다.
