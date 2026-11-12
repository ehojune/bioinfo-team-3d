# Gateway 동기 메서드 삭제 근거 (#1390)

기준 `f30179b9`에서 `git grep`으로 저장소 전체의 정확한 이름과 문자열 참조를 확인했다. 아래 다섯 이름은 각 정의 외에 참조가 없었다. 동적 이름 조합이나 저장소 밖 호출은 이 검색만으로 증명할 수 없다.

| 삭제한 동기 메서드 | 남긴 async 경로 | 전용 시험 |
|---|---|---|
| `_final_review` | `_afinal_review` | 없음 |
| `_remove_quota_wait` | `_aremove_quota_wait` | 없음 |
| `_remove_login_wait` | `_aremove_login_wait` | 없음 |
| `_record_resume_cp1` | `_arecord_resume_cp1` | 없음 |
| `_finish_cancel` | `_afinish_cancel` | 없음 |

`retire_resume_cards`는 `Hub.__init__`, `create_request`는 bench와 CLI에서 호출한다. `create_revised_request`는 CSO의 동적 검사에 사용한다. 이들과 전이 의존성, 시험에서 호출하는 다른 동기 메서드는 유지했다. 제품 53줄 삭제, 시험 삭제 0줄이다.
