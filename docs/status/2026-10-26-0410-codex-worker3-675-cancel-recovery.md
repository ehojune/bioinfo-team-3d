Refs #675. runner 부재 중 취소를 복구한 runner에 재전송하는 통합 회귀검사를 보존했습니다. production 변경은 없습니다.

- 무해한 Python job 2개로 취소한 요청의 wrapper·자식 잔류 0, 완료 깨움 없음, 다른 요청 보존, 연결 실패만으로 취소하지 않음을 확인합니다.
- Windows CI mandatory node로 등록했습니다. 실제 프로세스를 쓰며 transport만 대체합니다.
- 관련 pytest: 4 passed, 27 deselected. Windows selection 계약·public·diff 검사 통과.
- CI inventory 예상 목록도 갱신했습니다. 해당 계약 pytest 3 passed.
- 실제 WebSocket 재연결·gateway OS 재시작·연구·WSL은 미검증입니다. #675의 WSL·진행률 범위는 남습니다. 필수 CI·동일 head 봇 리뷰 뒤 병합합니다.
