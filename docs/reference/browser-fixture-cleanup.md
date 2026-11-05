# 브라우저 fixture 종료 계약

소유한 CDP 연결로 `Browser.close`를 먼저 보낸다. child/stdio·socket·HTTP server의 닫힘을 모두 확인한 뒤 profile을 지운다.

연결이 없거나 종료가 지연되면 소유 child와 socket 정리를 시도한다. 5초 초과는 실패이며 늦은 완료도 이를 성공으로 바꾸지 않는다.

`chrome-stdio` 시간 초과만으로 pipe를 잡은 process를 확정하지 않는다. 가짜 핸들 검사와 실제 Windows CI의 증거를 구분한다.

근거: [CDP 정의](https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/pdl/domains/Browser.pdl), [Node close 계약](https://nodejs.org/api/child_process.html#event-close).
