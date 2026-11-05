# Engine canary 경계

Windows에서는 bootstrap을 Job에 할당하고 확인한 다음 모델 호출을 허용한다. 부모 deadline 후에도 Job 종료와 active process 0을 확인하지 못하면 실패한다.

Linux canary는 미지원이다. 지원하려면 provider의 직접 cgroup 이동과 systemd manager를 통한 이동을 막는 관리자 소유 경계, 시작 barrier, 경계 밖 부모의 bounded cleanup을 검증해야 한다. [cgroup v2 delegation containment](https://docs.kernel.org/admin-guide/cgroup-v2.html#delegation-containment)
