## 단계
#1063 r10 · PR #1064의 synthetic 계약을 main `232e3abf`와 통합합니다. 실제 보호는 UNVERIFIED입니다.

## 한 일
- 충돌 4건 해결: main의 Windows inventory·offline HMAC adapter·최신 manual 한영판을 보존했습니다.
- seal suite를 Windows 경계 검사에 추가하고 reference의 adapter 설명을 현 구현에 맞췄습니다. Reference 5,134→5,812자.
- 리뷰 P2 수정: 알려진 failure·stop 이벤트를 unknown으로 분류하지 않습니다. 원인 없는 stop·잘못된 child·임의 이벤트 거부는 유지합니다.
- corpus·기존 test·다른 담당자의 변경을 보존했습니다. code-only 병합 범위이며 실제 보호·실행 승인은 별도입니다.

## 테스트 결과
- seal·observer·offline adapter synthetic 618건, CI 경계·phase 계약 182건 PASS.
- 별도 offline probe 24건으로 failure·stop 분류와 잘못된 stop 거부를 확인했습니다. 기존 test 파일은 claim 범위 밖이라 보존했습니다.
- inventory·notes_index·patch_notes·public·diff 검사 PASS. 최종 head CI·실제 리뷰는 PR에서 최종 확인합니다.

## 남은 조건
- 최종 head CI·실제 리뷰와 미해결 지적 확인 후 draft/hold 해제·병합합니다.
- OS seal·실제 producer 신원·독립 cleanup은 UNVERIFIED. provisioning·provider·연구/HPC 실행은 없습니다.

Refs #1063; parent #666. PR #1052는 별도 claim 대상입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
