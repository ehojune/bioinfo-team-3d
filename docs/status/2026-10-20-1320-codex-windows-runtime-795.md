## encoding·공용 fixture 후속

기존 encoding checker를 바꾸지 않고 `journal.open(mode="rb")`와 native `open_process`로 의미를 명시했습니다. 64 KiB tail bound·ownership/ACK/cleanup·started_here=False는 유지합니다. PR #793의 HTTP binary lint 변경은 가져오거나 덮어쓰지 않았습니다.

승인된 test 원문만 재사용했습니다: `6954ab6aa3ce7314744d754dea16d26e45c0731d`의 recruit/receipt-clock 2파일과 `98f70d96a937e5c6ee8fb650a31d5bf63c0eecbe`의 **second parent 대비 external_wait 1파일 delta**. 세 결과 blob은 원본과 일치합니다. 공용 product 변경·merge commit은 가져오지 않았고 clock/cancellation/progress timeout 단정은 보존했습니다.

후속 검증: 실제 encoding classifier·잘못된 text I/O 반례·bounded-tail fake, receipt-clock fake reconnect/timeout/cancel, external_wait의 mock 3phase와 WMI/runtime mock을 포함해 **110 passed**, guard 3개 유지·blocked 0. 수정 전 실패와 수정 후 전체 출력을 보존했습니다. Python 3.10 AST(embedded CI scripts 포함)·public·diff·patch-notes·notes PASS. Hub/process/WMI 실제 fixture는 로컬 미실행입니다.

reference 3,010→3,702자(LF Unicode), manual 동작 설명은 유지합니다. 실제 Windows survival·Hub 재개·fresh-head CI 증거는 root가 확인해야 합니다. CI 조회·watcher·봇·병합·배포는 하지 않았습니다. root는 이 head 확인 뒤 후속 watcher 하나를 맡습니다.
