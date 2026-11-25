# #1490 r14 · PR 4

Refs #1490

- `LABHQ_AUDIT_REPAIR_BEFORE_CP2=1`과 `LABHQ_AUDIT_BEFORE_CP2=1`을 함께 켜면 원래 분석 세션과 runner에서 P1만 최대 두 번 고칩니다. 한 번에 최대 $5를 요청 예산에 반영합니다. 수정본을 별도 폴더에서 수집하고, 계획과 산출물을 확인한 뒤 기존 리뷰를 비워 다시 감사합니다.
- CP2 카드와 영수증은 마지막 감사 판정·남은 지적·수정 결과를 담습니다. 설계 변경, 후속 단계가 쓰는 산출물, 세션 재개 실패와 비용 상한을 강제할 수 없는 엔진은 CP2로 넘깁니다. 실제 요청과 다른 runner 간 코드·표 전달은 미검증입니다.
- 아래 명령은 수정 옵션 미설정과 `0`에서 각각 306개 통과했습니다. 옵션 ON 시험은 같은 세션과 재감사, 두 번 상한, 세션 재개 실패, 계획 이탈, 실행 중 재시작과 예산을 확인했습니다. runner 입력·전달 시험 78개와 `node --test tests/web_cp2_evidence.cjs` 14개가 통과했습니다.

```sh
python -m pytest -q --disable-warnings tests/test_research_cp2.py tests/test_research_cp2_revise.py tests/test_research_report.py tests/test_research_protocol.py tests/test_research_review.py
```

- 리뷰 P2는 감사 응답에서 설계 변경 여부를 필수로 받아 해결했습니다. 별도 작성한 초안의 수정 지시도 원장·산출물 반환에 맞췄습니다. `notes_index.py --validate`와 `scripts/check_public.sh`가 통과했습니다. [리뷰 지적](https://github.com/ehojune/bioinfo-team-3d/pull/1503#discussion_r4688296404)
- `cso.py`·`server.py`·`daemon.py`는 main `4af3d365`에서 총 21,836줄입니다. 이번 변경 뒤 총 21,870줄(+34), 기준 22,125줄 대비 누계 −255줄입니다. 제품 코드 순증은 237줄입니다.
