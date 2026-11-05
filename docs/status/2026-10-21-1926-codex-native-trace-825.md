Refs #825

CI artifact 환경이 있을 때만 native fixture의 대기·본문·정리 경계를 case별 JSON으로 기록합니다. 최대64 entries/16 KiB이며 고정 phase·역할·제한된 시간/결과/signature flag만 남깁니다. 진단 기록 시간을 제외한 callable duration도 구분합니다.

Fake/static37건·Python3.10 AST와 원래 assertion/API 인자 대조를 통과했습니다. 기존 native5/web20 계약·대기 시간·retained-handle 정리는 유지합니다. 실제 native 성공이나 제품 결함 해결의 증거는 아니며 통합 CI는 미확인입니다. 로컬 커밋만 준비했습니다.
