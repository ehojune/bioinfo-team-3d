## #865 main 통합

- base: `386edba` (#868 merge `3e1b787` 포함). 충돌 없이 합쳤고 tree `e35868b`는 자동 merge-tree와 같다.
- 예약 제품·테스트·예제 설정 8개 파일은 검토판 `bc0c4a2`와 byte 단위로 같다. Windows 경로 비용·WMI 수정은 main에서 받았다.
- 로컬 검증: 예약 동기 fake 6건과 논문 스킬 순수 계약 12건을 통과했다. 실제 gateway·runner·프로세스·WMI·Job 검사는 실행하지 않았다.
- 예산 예약은 공급자 청구 hard cap이 아니며 Codex의 진행 중 비용 초과를 막지는 못한다. 새 head CI·리뷰·병합은 총괄이 맡는다.
