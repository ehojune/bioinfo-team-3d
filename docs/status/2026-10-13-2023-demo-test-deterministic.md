## 결론

Windows CI에서 정상 진행 중인 데모 요청을 30초 `wait_for`가 취소하던 테스트를 완료 신호로 동기화합니다. 제품 코드는 바꾸지 않았습니다.

## 바뀐 것

- 같은 파일의 서버 준비·runner 등록 폴링을 실제 startup·등록 이벤트로 대체했습니다.
- CLI에만 clock/sleep 대역을 주고 실제 요청 task를 기다립니다. 실제 서버·runner·URL 경고·done 검증은 유지합니다.
- 비종료 상태로 끝난 요청은 즉시 실패하며 교착 검출용 바깥 상한은 120초입니다.

## 검증

- 별도 CPU 점유 프로세스 4개와 pytest를 동일 코어에 고정: 수정 전 실패 0/5(로컬 미재현), 수정 후 실패 0/30.
- 관련 파일 14 passed. 두 CI 실패의 첫 실행 로그에서 30초 취소를 확인했습니다: runs 38363193704, 38383002690.
- Windows 전체 첫 실행: 5973 passed, 66 skipped, Node shim 환경 오류 42 failed. 설치된 node.exe를 PATH에 지정한 뒤 해당 42개는 모두 통과했습니다. 전체 재실행은 다른 lane의 전체 pytest 종료를 기다립니다.
- 공개 검사·diff 검사 통과. 포트 8787·55921 접근 차단, 임시 경로는 TEMP의 한 폴더를 쓰고 검사 뒤 삭제합니다.

## 미해결

- 전체 Windows 재실행과 현재 head CI 결과를 추가 기록합니다. 자동 봇 판단·병합은 개발 총괄이 이어받습니다.
- 남은 지적: 없음.

Refs #539

🤖 Generated with Codex (gpt-6) for the labhq dev lead
