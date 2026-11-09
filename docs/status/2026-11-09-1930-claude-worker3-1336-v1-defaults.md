# 2026-11-09 claude-worker3 — #1336: v1.0 기본값에서 온톨로지·Paper2Agent 채용 끄기

## 결론
설정에 값이 없을 때와 예시 설정, `labhq init`이 쓰는 기준값에서 `recruit.enabled`·`semantics.mode`·`research.semantic_report`가 꺼진다. 코드는 지우지 않았고 설정에서 켜면 예전처럼 동작한다.

## 바뀐 것
- `labhq/settings.py`: `RecruitSettings.enabled`·`ResearchSettings.semantic_report` 기본 `False` (`semantics`는 원래 `None` = off)
- `config/labhq.example.yaml`·`labhq/config/labhq.example.yaml`: `recruit.enabled: false`, `research.semantic_report: false`, `semantics.mode: "off"`(actions: confirm은 남김)
- `labhq/init_defaults.py`: 기준값 `semantics.mode`를 `off`로
- manual 15장 한·영: v1.0 기본값 한 줄, 그리고 #1338 리뷰대로 "고정이 제출 경로에 연결되지 않았다" 문구를 현재 `pin_submit` 동작으로 교체
- `tests/conftest.py`: `pre_v1_defaults` fixture. 이름으로 요청한 시험에서만 두 기본값을 예전처럼 켠다
- 기능 시험 22개 파일은 `pytestmark`로 이 fixture를 요청하고, 옛 기본값을 직접 단언하던 5개 파일은 새 기본값으로 단언을 바꿨다. 지운 시험은 없다
- 새 기본값 시험은 배정의 `tests/test_v1_defaults_1336.py` 대신 `tests/test_init_defaults.py`에 넣었다. 새 시험 파일은 `config/ci/windows-boundary-v1.json` 등록이 필요한데 그 파일은 #1332 범위다

## 실행한 것
- 관련 시험 108개 파일(`recruit|semantic|init_defaults|labhq.example` 언급): 5076 passed, 25 skipped
- 기본값만 바꾼 상태에서는 같은 묶음에서 28개 파일 109건이 실패했다

## 미해결
- 전체 suite는 CI에 맡겼다
