## 결론

PR #590 · Refs #546. 시안 E와 캐릭터 1을 2D 사무실에 적용했습니다.

## 바뀐 것

- roster 동물 전신과 직무 소품, 시설팀 햄스터·파견직 배지·중립 fallback.
- 내용에 맞는 2열 구역. 폰 사무실은 분석 구역·지휘부부터 표시합니다. 결정 우선 화면 순서는 유지합니다.
- 상태 점+글자, 결정 대기 강조색, OS 움직임 줄이기. 기존 외부 폰트 링크 제거.
- shared state·3D·API 변경 없음.

## 확인

- 기준 main `7c1686d`: 새 Node 회귀 6 실패·대비 1 통과, Chrome 회귀 1 실패 → branch 모두 통과.
- Node 전체 52개 파일, 174건 통과. Windows 전체 pytest: **1 failed, 6300 passed, 66 skipped, 1382 warnings in 772.02s (0:12:52)**. 실패한 Node 등록 누락 수정 후 관련 Windows **88 passed, 1 skipped**. 전체 재실행은 다른 pytest 프로세스 감지로 차단돼 관련 검사만 실행했습니다.
- PC 1440×900·폰 390×844: 그림 72/64px, 핵심 직원 가림·가로 넘침 없음.
- 실제 글자 대비 light/dark 최저 6.50:1, 그림 윤곽 3:1 이상. reduced-motion animation 0, 외부 이미지·폰트 요청 0.
- 공개·패치노트·기록 목차 검사 통과. main `5a97108`과 충돌(`CONFLICTING`). CI: changes·patch-notes·pytest 3.10/3.12/Windows/slow·clean-install·ro-crate-validator·readme 모두 미생성.

[전후 화면 4장](../design/2026-10-13/office-comparison/README.md) · [검사 근거](../design/2026-10-13/docs/reference/office-verification.md)

## 남은 지적

main 충돌 해소·CI 확인은 총괄에게 넘긴다. 전체 Windows 재실행은 동시 pytest 감지로 생략했다. 관련 검사 TEMP 삭제는 자동 승인 검토가 `blocked by policy`로 거부해 보존했다. 봇 판단·병합은 총괄에게 넘긴다.
