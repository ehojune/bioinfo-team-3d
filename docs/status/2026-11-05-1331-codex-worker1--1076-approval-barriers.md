# #1076 승인 응답 시험

- 송신 timeout과 unregister는 기존 1초 관찰 제한으로 확인하고 durable publish 완료는 별도로 확인한다.
- 실패 시 writer enqueue, start, commit 시작·완료, finish 시각을 남긴다. 미관찰 값은 None이다.
- 기존 Windows 저장 지연의 원인은 미확인이다.
- 로컬 Windows Python 3.12: 관련 시험 45개 통과.
