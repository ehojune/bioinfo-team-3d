## 단계
#1063 r10 · PR #1064의 synthetic 계약을 main `232e3abf`와 통합합니다. 실제 보호는 UNVERIFIED입니다.

## 한 일
- 충돌 4건 해결: main의 Windows inventory·offline HMAC adapter·최신 manual 한영판을 보존했습니다.
- seal suite를 Windows 경계 검사에 추가하고 reference의 adapter 설명을 현 구현에 맞췄습니다. Reference 5,134→5,812자.
- corpus·기존 test·다른 담당자의 변경을 보존했습니다. code-only 병합 범위이며 실제 보호·실행 승인은 별도입니다.

## 테스트 결과
- seal·observer·offline adapter synthetic 618건, CI 경계·phase 계약 182건 PASS.
- inventory·notes_index·public·diff 검사 PASS. patch_notes와 최종 head CI·실제 리뷰는 PR에서 최종 확인합니다.
- 첫 pytest 명령은 없는 `test_ci_selection.py` 경로로 수집 전에 실패했고 올바른 경로로 수정했습니다.

## 남은 조건
- 최종 head CI·실제 리뷰와 미해결 지적 확인 후 draft/hold 해제·병합합니다.
- OS seal·실제 producer 신원·독립 cleanup은 UNVERIFIED. provisioning·provider·연구/HPC 실행은 없습니다.

Refs #1063; parent #666. PR #1052는 별도 claim 대상입니다.

🤖 Co-Authored-By: Codex (gpt-6.1-sol) <199175422+chatgpt-codex-connector[bot]@users.noreply.github.com>
