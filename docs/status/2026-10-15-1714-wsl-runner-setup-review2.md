## 결론

Refs #630. 봇 2차 지적 5건을 수정했다. Windows와 WSL은 같은 작업 폴더를 쓰며 승인·예산 정책도 함께 적용한다.

## 바뀐 것

| 지적 | 수정 |
|---|---|
| P1 4280129662 | 공유 drive의 경로 변환을 한 함수로 모았다. 선행 입력·작업 폴더·산출·bundle을 연결하고 Linux 전용 작업 폴더는 거부한다. 공유 root가 확인된 WSL만 bundle hostname 예외를 받는다. |
| P1 4280129675 | setup마다 Windows 정책을 복사하고 개인·통제 구역 경로를 변환한다. 변환 실패는 거부하며 기본 개인 경로 보호는 두 OS 모두 유지한다. |
| P1 4280232224 | 연결 확인 뒤에만 enabled를 저장한다. WSL이 끊기면 연결된 Windows 직원에게 배정한다. |
| P2 4280129685 | 설정 검증·up·background·직접 runner·WSL start에서 ID 중복을 거부한다. |
| P2 4280232232 | NAT fallback의 기본 포트를 ws 80·wss 443으로 나눈다. |

## 확인

- 원래 PR head `6fc5c69`: 새 회귀 22 failed / 2 passed. 수정 후 24 passed.
- 관련 pytest: 463 passed / 32 skipped. 시작 경로·CSO 추가 확인: 257 passed.
- 격리 실측: Windows 단계 → WSL 단계 파일 전달과 최종 bundle 수집 성공. 누락 0건, WSL 시험 runner exit 0. 실제 모델 호출 없이 CLI 파일 작업으로 확인했다.
- WSL manual 1,226자 → 1,506자. public·patch-notes·notes-index 검사는 기록 커밋과 함께 확인한다. CI는 push 뒤 PR checks에서 확인한다.

## 남은 것

- Windows 정책 변경 뒤 WSL을 멈추고 setup을 다시 실행해야 한다.
- 리뷰 요청·병합은 하지 않았다. 실측 원본과 테스트 로그는 clone의 `.pr-drafts/`에 보관한다.
