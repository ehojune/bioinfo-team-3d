## 결론

Fixes #619. PR #649. 편집자 정리본의 내부 주소 우회와 긴 절 목록의 입력 한도 초과를 막았습니다.

## 바뀐 것

- HTTP(S) 역슬래시와 ASCII 공백·제어문자를 정규화한 뒤 host를 판정합니다.
- percent encoding·userinfo·legacy IPv4·IPv6·끝 점으로 쓴 내부 host도 같은 표에서 차단합니다.
- 개요·본문·생략 절 목록이 문서별 한도를 함께 쓰며, 긴 목록은 앞뒤 제목과 중간 개수로 줄입니다.

## 확인

- 공개 링크 회귀는 수정 전 7 failed, 수정 후 63 passed
- 5,000절 회귀는 수정 전 30,000자 한도에 199,089자, 수정 후 한도 이내
- 관련 pytest 85 passed, `scripts/check_public.sh` 통과

## 남은 것

CI 결과는 push 뒤 확인합니다. 리뷰 요청과 병합은 하지 않습니다.
