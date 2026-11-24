## #1490 r13 · PR 3b

Refs #1490

- Gateway 환경에 `LABHQ_AUDIT_BEFORE_CP2=1`을 주면 새 간소화 요청에서 claim·표 검사 → 독립 감사 → CP2 순으로 진행합니다. 기존 감사자가 코드와 표를 먼저 읽습니다. CP2 카드에 초안·판정·지적을 싣고 영수증에 판정·지적·해시를 남깁니다.
- 해시 변경 시 재감사하고 파일 변조나 감사 실패 시 CP2 전에 멈춥니다. 저장된 계획 해시가 유지되고 BOM과 그림 링크를 포함한 초안도 감사 대상·카드·영수증 해시가 일치합니다. 이번 승인 메모는 본문에 인용하고, 앞 차수 메모는 초안 작성자와 감사자에게 전달합니다.
- 옵션 미설정과 `LABHQ_AUDIT_BEFORE_CP2=0`에서 아래 명령을 각각 실행해 291개씩 통과했습니다. 변경 전에는 같은 명령으로 기존 시험 279개가 통과했습니다. `node --test tests/web_cp2_evidence.cjs` 14개와 `scripts/check_public.sh`가 통과했습니다. Chrome fixture에서 판정·P1 지적·초안 표시를 확인했습니다. 실제 연구 요청은 미검증입니다.

```sh
python -m pytest -q tests/test_research_cp2.py tests/test_research_cp2_revise.py tests/test_research_report.py tests/test_research_protocol.py tests/test_research_review.py
```

- 기준 main `ec200647`의 `cso.py`·`server.py`·`daemon.py`는 21,771줄입니다. 이번 PR은 `cso.py`에 64줄이 늘어 합계 21,835줄(기준 22,125줄 대비 −290)입니다. 제품 코드 순증 205줄, 구현·기록 6커밋입니다.
