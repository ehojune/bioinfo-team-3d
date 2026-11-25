# #1482 가: trusted_local 스위치 (claude-worker3)

결론: `policy.approvals.trusted_local`(기본 꺼짐)을 켜면 runner가 Claude 직원의 도구 카드를 허용·거부로 직접 답한다. 기록 재생에서 사람에게 가는 도구 카드는 내부 자료 읽기뿐이다.

바뀐 것
- `labhq/approval_grants.py` `trusted_verdict`(+95): `classify`의 부류·사유와 정책 판정 사유로 allow / deny(사유) / card를 정한다.
- `labhq/runner/daemon.py` `_on_approval`(+18 순증): 도구 승인과 로컬 작업 제출이 모이는 자리에서 한 번의 `classify`를 요청 단위 허용과 같이 쓴다. 허용·거부하면 broker에 바로 답하고 작업 로그를 남긴다.
- `labhq/settings.py`(+4): `policy.approvals.trusted_local: false`.
- 시험 `tests/test_trusted_local_1482.py`(28 사례), 매뉴얼 `11-safety` 한·영, Windows 시험 목록.

재생 (기록된 명령은 실행하지 않음)
| 자료 | 카드 | 허용 | 거부 | 카드 유지 |
|---|---|---|---|---|
| #1445 기록 (PC 126 · 시험 79) | 205 | 183 | 0 | 22 (모두 내부 자료 읽기, PC 요청) |
| 실제 요청 A | 98 | 98 | 0 | 0 |
| 실제 요청 B | 33 | 31 | 2 | 0 |

B 거부 2장은 옛 셸 현재 폴더(`cd ../..` 뒤 상대 경로 쓰기)를 가정한 명령이다. 지금은 폴더가 매번 돌아오므로 실제로 작업 폴더 밖에 쓴다.

실행한 것: 배포 venv에서 새 시험·설정·승인 관련 시험(3,977 통과, 로컬 node shim의 `test_web` 2건 실패는 환경 문제), `tests/test_ci_*.py`(로컬 `filelock` 없음 5건 외 통과), 위 재생.

미해결: Codex 직원 카드는 대상이 아니다(직원 환경 캡처가 Claude에만 있다). 실행 중에 만든 경로로 개인 파일을 여는 스크립트는 막지 못한다(받아들이는 위험).
