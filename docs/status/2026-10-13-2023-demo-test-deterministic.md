## 결론

Windows CI에서 진행 중인 데모 요청을 30초 `wait_for`가 취소하던 테스트를 완료 신호로 동기화했습니다. 제품 코드 변경은 없습니다.

## 바뀐 것

- 같은 파일의 서버 준비·runner 등록 폴링을 실제 startup·등록 이벤트로 대체했습니다.
- CLI에만 clock/sleep 대역을 주고 요청 task를 기다립니다. 실제 서버·runner·URL 경고·done 검증은 유지합니다.
- 비종료 상태로 끝난 요청은 즉시 실패하며 교착 검출용 바깥 상한은 120초입니다.

## 검증

- CPU 점유 프로세스 4개와 pytest를 동일 코어에 고정: 수정 전 실패 0/5(로컬 미재현), 최종 수정 후 실패 0/30.
- 관련 파일 14 passed. CI 실패 근거: runs 38363193704·38383002690의 첫 실행 로그.
- Windows 전체: **6015 passed, 66 skipped**. 첫 실행의 Node shim 오류 42건은 기존 node.exe를 PATH에 지정해 해소했고 전체를 다시 실행했습니다.
- 포트 8787·55921 접근 차단. TEMP 아래 basetemp 한 폴더 사용 후 삭제 확인.
- 공개 검사·patch-notes·notes-index·diff 검사 통과.
- [CI run 38390129364](https://github.com/ehojune/bioinfo-team-3d/actions/runs/38390129364) (`f068eea`): changes·patch-notes·pytest 3.10·3.12·Windows·slow·clean-install·ro-crate-validator 성공, readme 조건부 skip.

## 미해결

- 자동 봇 지적 판단과 병합은 개발 총괄이 이어받습니다.

Refs #539

🤖 Generated with Codex (gpt-6) for the labhq dev lead
