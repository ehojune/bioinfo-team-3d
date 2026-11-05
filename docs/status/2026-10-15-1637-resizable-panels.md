## 결론

PR #635에서 데스크톱 2.5D 패널 경계를 마우스와 키보드로 조정하게 했습니다. 더블클릭하면 기본 크기로 돌아가고, 데모·라이브·instance별로 저장합니다. 폰과 3D는 그대로입니다. Refs #633.

## 바뀐 것

- 사무실–Command Center 세로 경계, 작업 영역–직원 시트 가로 경계
- 최소·최대 크기, 화살표, 접근성 이름, localStorage 실패 시 기본 배치
- [데스크톱 전](../design/2026-10-15/resizable-panels/desktop-before.png) · [후](../design/2026-10-15/resizable-panels/desktop-after.png) / [폰 전](../design/2026-10-15/resizable-panels/phone-before.png) · [후](../design/2026-10-15/resizable-panels/phone-after.png)

## 확인

- 새 Node test는 main 소스에서 실패하고 branch에서 3건 통과
- 관련 pytest 84 passed, 1 skipped
- Node 전체, 공개 저장소 검사, 화면 기준선 32 views 통과

## 남은 것

- CI 결과는 push 뒤 확인합니다. 리뷰 요청과 병합은 하지 않습니다.
