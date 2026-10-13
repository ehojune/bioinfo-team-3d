## 결론

PI 시운전에서 데모가 실제 사무실과 같은 port를 쓰던 결함을 고쳤습니다. 웹 데모의 표시·진행 속도·결정 기록도 분리했습니다.

## 바뀐 것

- 원하는 port에 loopback 응답이 있거나 IPv4·IPv6 bind 검사 중 하나라도 실패하면 다른 빈 port를 고르고 한국어로 주소를 안내합니다. runner도 그 데모 서버에 연결합니다.
- 데모 BOOT에 `demo: true`를 넣고 2.5D·3D에 `데모(mock)`와 상단 안내 띠를 표시합니다. 브라우저 저장 키를 라이브와 나눕니다.
- 웹 mock 단계는 2초, 자동 승인은 1초 뒤 진행합니다. 요청 사이에는 남은 시간을 표시하며 CLI·일반 test 지연과 폰 승인 대기는 유지합니다.
- README·manual의 데모 절에 실제 사무실과 함께 여는 법을 안내했습니다.

## 실행한 것

- main helper에서 주소 충돌 회귀 3건 실패, 수정본의 새 Python test 14건 통과.
- Windows 전체 pytest: 5,517 passed · 66 skipped (mock e2e 포함).
- `for f in tests/*.cjs; do node --test "$f"; done`: 34개 파일 통과.
- 빈 port의 실제 웹 데모: 요청 29.10·22.90·29.56초. Chrome에서 2.5D·3D 표시와 대기 안내 확인.
- 공개 검사·diff 검사·패치노트·목차 검사 통과. pytest 임시·상태는 한 TEMP 폴더에 격리하고 8787 socket 통신을 차단했습니다.

## 미해결

- 없음. 자동 봇 리뷰 판단과 병합은 개발 총괄이 이어받습니다.

Refs #435

🤖 Generated with Codex (gpt-6) for the labhq dev lead
