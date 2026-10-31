# #621 P2: 여러 염기 REF의 창 중앙

- 결론: `alphagenome_variant`의 점수 창이 변이의 첫 염기가 아니라 REF 구간(`Query.start0`~`end`)의 가운데를 중심으로 잡힌다. 1,000 bp 결실이면 REF 전체가 창 안에 양쪽 같은 여유로 들어간다(예전에는 중심이 500 bp 왼쪽).
- 한 염기 REF는 `(pos-1 + pos) // 2 = pos-1`이라 창이 그대로다. 염색체 시작 근처는 여전히 0으로 잘린다.
- 바꾼 것: `labhq/tools/annot_alphagenome.py`(한 줄과 주석), `tests/test_annot_alphagenome_variant.py`(시험 하나: 결실 중앙, 한 염기 불변, 시작 근처 0). 새 시험은 옛 코드에서 실패함을 확인했다.
- 알려진 것: 같은 시험 묶음의 `tests/test_annot_regulatory.py::test_with_a_key_the_server_offers_alphagenome…`가 이 PC에서 `CallToolResult.is_error` 속성 오류로 실패한다(설치된 mcp SDK와 시험 코드의 속성 이름 차이, 이 변경과 무관).
- 한계: 염색체 끝을 넘는 창은 바꾸지 않았다. D(Atlas)는 PI 결정 대기(#621 댓글).
