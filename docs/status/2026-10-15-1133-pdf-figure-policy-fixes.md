## 결론

Refs #608. #611 리뷰의 P2 세 건을 고쳤습니다. 게시 PDF는 PI가 검토한 정리본이 담을 수 있는 그림만 보여 주고(크기 제한만 풉니다), 보고서의 graphical abstract는 첫 그림이 되며, `labhq verify`는 그려 넣을 abstract 자리를 셉니다.

## 바뀐 것

- **PDF 그림 자격** (`labhq/result_storage.py`): `edition_assets()`의 판정을 `edition_candidates()`로 꺼냈습니다(manifest가 `copied`, `steps/<id>/outputs/` 경로, 정리본이 담는 형식, 가림이 필요 없는 이름). 정리본 복사는 여기에 크기 한도(파일당 1 MB·합계 5 MB)를 더하고, `pdf_report_text()`는 크기 한도 없이 같은 목록만 링크로 남깁니다. 미보고 묶음 파일, manifest에 없는 파일, gif 같은 형식은 PDF에서도 `*(그림 생략: 설명)*`입니다. 기존 `_BundleFigures`(일반 파일이면 통과)는 없앴습니다.
- **abstract 위치** (`labhq/report_abstract.py`, `abstract_first`): 보고서가 링크한 유효한 abstract가 첫 그림이 아니면 첫 제목(코드 밖) 아래 첫 그림으로 옮깁니다. 캡션·제목·대상은 그대로입니다. PDF(`build_report_html`)와 정리본 `report.md`·README 발췌에 적용하고, 묶음의 `report.md`는 건드리지 않습니다.
- **verify** (`labhq/report_figures.py`, `bundle_figure_check`): 묶음에 있는 자체 abstract가 없으면 결과 그림 한도를 10장으로 둡니다. 자체 abstract가 있으면 11장 그대로입니다. 메시지는 두 경우를 따로 적습니다.
- `docs/manual.md`(그림 정책, abstract, 정리본 PDF 문단).

## 확인

- 새 test 8건(`tests/test_pdf_figure_review_fixes.py`)은 main 코드에서 모두 실패하고 이 branch에서 통과합니다. main 쪽은 새 함수의 import를 비운 채 돌려 동작으로 실패하는 것을 확인했습니다.
- 의도해서 고친 기존 assertion 3건: #611에서 미보고 그림이 PDF에 남는 것과 11장 문구를 고정했던 `test_pdf_figure_policy.py` 2건과 `test_result_storage_pdf.py` 1건.
- 관련 Python test 1139건과 Node test 58개 파일 통과, `check_public.sh` 통과.

## 남은 것

- 없음.
