Closes #931

Windows 검사의 병렬 단계가 6분 제한에 반복해서 걸려 무관한 PR(#915·#919·#930)을 막던 문제를 측정에 맞춰 풀었습니다.

| 측정 (최근 test.yml 99 run, pytest-windows) | 값 |
|---|---|
| 병렬 단계, 성공 74회 | 중앙값 264초 · p90 319초 · 최댓값 350초 |
| 6분(360초) 제한에 걸린 실패 | 8회(361·361·367·360·343·367·375초 등, 하나는 1080초) |
| 성공 job 전체 | 중앙값 378초 · 최댓값 448초, serial critical 최댓값 79초 |

- 병렬 단계 6분→9분(성공 최댓값 350초의 1.5배 이상), job 10분→14분(setup + critical 3 + 병렬 9 + report/upload에 여유). 테스트의 assert·timeout은 그대로입니다.
- 병렬 단계 시작 시각을 기록하고 `always()` 단계가 소요를 job summary(`### Windows pool step`)에 남깁니다. 성공 시 `summary.json`의 setup/pytest 시간은 기존 그대로입니다.
- 10분 목표는 `over_ten_minutes`로 계속 잽니다. 제한이 늘어도 느려진 날을 숨기지 않습니다.
- 정책 고정 테스트 `test_ci_windows10m`·`test_ci_windows_phases`의 6/10을 9/14로 갱신했고 `docs/reference/ci-reuse.md`에 반영했습니다. CI 정책 테스트 526건 통과(로컬).
- 미검증: 제한을 올린 뒤의 실제 run(이 PR의 CI가 첫 근거), 느려진 근본 원인(Windows runner 변동·WMI/Chrome 시작 지연 가설은 확정하지 못함).

🤖 Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>
