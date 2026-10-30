## 단계
#1061: DOI·URL 참조 요청의 이어 가기 재사용 거부(input proof). 설계안과 B안 결정은 [#1061 댓글](https://github.com/ehojune/bioinfo-team-3d/issues/1061#issuecomment-6516819730).

## 한 일
1. `labhq/research/input_proof.py` `capture`: `doi`·`pmid` 참조는 `doi:<소문자>`·`pmid:<번호>`로 정규화해 `unreceipted`(정렬·중복 제거)에 담고 영수증을 만든다. `url`·`github`가 하나라도 있으면 이전처럼 `None`. path 참조는 파일 hash 증명을 그대로 유지하고, path만 있는 요청의 영수증에는 `unreceipted` 키가 없어 기존 기록의 hash가 바뀌지 않는다.
2. `continuation.proof_problems`는 바꾸지 않았다: 기록된 영수증과 현재 영수증이 같으면 통과, 다르거나 기록이 `None`이면 거부한다. 게이트웨이와 러너가 같은 `capture`를 써서 `request_input_sha256`이 일치한다.
3. 시험은 `tests/test_request_input_proof_959.py`에 7건(식별자 영수증·url/github 거부·path 혼합·path-only 형식·`proof_problems`·이어 가기 한 바퀴): 이 파일 27 passed.
4. manual 한·영 이어 가기 절.

## 알아 둘 것(설계안과 다른 점)
- 영수증 `descriptor`가 `references`를 입력 그대로 담아서 참조 **순서·표기**가 바뀌어도 hash가 달라져 재사용하지 않는다(안전한 쪽). 설계안의 "순서·표기가 달라도 hash 동일"은 구현하지 않았다.
- 카드에 "내용은 확인하지 않음"을 표시하는 일은 `cso.py`가 #1058 범위라 이번에 뺐다(총괄 지시). manual에만 적었다.

## 테스트 결과
- Windows `test_request_input_proof_959`·`test_continuation_797`·`test_continuation_review_807` 84 passed.
- 미검증: 실제 DOI 참조 요청의 CP2 revise 이어 가기, DOI 본문 개정의 영향.
