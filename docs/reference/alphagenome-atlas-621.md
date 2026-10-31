# AlphaGenome Atlas integration — #621

Lookup-only implementation evidence, checked 2026-10-31. Live Atlas access is **UNVERIFIED** pending PI's key-location answer.

Integration hold: manuals are owned by another worker. The runner retains `ALPHAGENOME_API_KEY`; staff CLI/MCP environments receive only a task-bound broker capability and availability flag. The fixed `/annot/atlas` operation accepts Atlas query parameters, never caller URLs, commands or output roots.

| Contract | Implementation |
|---|---|
| Credentials | Runner/expanded engine/task precedence. Remove keys and aliases from staff environments; refuse key-bearing launch overrides and literal staff config copies. Use environment references. Doctor checks active Atlas staff engines, including force_engine, with the runner's resolver; partial key availability warns. Task overrides and live access remain unverified. |
| Roles | chief_of_staff denies Atlas alongside the existing variant/region tools; literature tools remain available. |
| Inputs | GRCh38 SNVs, 1–10 per call; scorer names 1–10 (default `RNA_SEQ`); ontology CURIEs and Ensembl gene IDs up to 20 each. No interval/bulk or automatic inference fallback. |
| Output | Raw scores, available quantiles, signed direction, tissue/gene metadata, explicit precomputed source and usage terms. Empty filtered scores do not establish no effect. AVI is supported only if the service exposes that requested scorer; its live name/availability is unverified. |
| Bounds | One active Atlas query per task; 16 KiB request cap, 300-second SDK wait, 20-minute step deadline. Task finish, HTTP disconnect and timeout cancel the operation and disable later RPCs. An already-running SDK RPC may finish on its bounded daemon thread. At most 1 million score cells, 1,000 saved rows and 100 displayed per variant. |
| Records/cache | Existing counts/hash ledger and full-result paths. Cache separates SDK version, filters, schema and UTC day. Dataset release is not exposed by the SDK; daily reuse is not proof of a frozen server release. |
| Request-wide limit | Existing admission gate refuses AlphaGenome SDK transports while enabled; no uncounted bypass. |

## SDK and primary sources

- [Released PyPI package](https://pypi.org/project/alphagenome/0.10.0/): installed only in the worktree `.venv`, Python 3.12. The wheel contains `alphagenome.atlas.atlas`, `create(api_key, timeout=...)`, `scorer_metadata()` and `query_variant(..., requested_scorers, ontology_terms, gene_ids)`.
- Wheel SHA-256: `ed90696f7f0900b2e0d68e28e36ab7d6d95000f59cd19b969fec0bea806bd2c2` (PyPI metadata). Tests run an actual 0.10.0 `AtlasClient` against an in-memory protobuf stub; no credentials or service calls.
- [Official client](https://github.com/google-deepmind/alphagenome/blob/main/src/alphagenome/atlas/atlas.py): scorer-keyed AnnData, scores in `X`, quantiles in `layers['quantiles']`; metadata supplies signedness. Server-side ontology filtering exempts scores without ontology metadata, which the local check preserves.
- [Official README terms and citation](https://github.com/google-deepmind/alphagenome#terms): Atlas API information is non-commercial, not for training other ML models or clinical decisions, subject to explicit permitted-artifact exceptions. [Full terms](https://alphagenome.google/terms) require a script-rendered page; its text was not independently extracted. Citation: Cheng et al. 2026, [doi:10.64898/2026.09.16.26363192](https://doi.org/10.64898/2026.09.16.26363192).

## Credential boundary verification (rev7)

- `tests/test_annot_alphagenome_atlas.py`: real MCP Python child → loopback HTTP → runner SDK fixture receives the fake key. Child environments, argv, responses and generated files contain no key. Direct PowerShell child tests cover parent/engine/task sources, case variants, aliases and empty overrides. Broker regressions share this inventoried module; no Windows coverage inventory change is needed.
- `shell_environment_policy.set`, engine `extra_args`, MCP env and expanded prefix restoration are refused before generated files or process launch. Identity spoofing, arbitrary operation fields, admission, busy requests, disconnect, deadline and runner success/error/cancel cleanup have regressions.
- Installed Codex `0.165.0-alpha.1.2` sandbox probe remains **UNVERIFIED**: isolated HOME, `:workspace`, unelevated mode, no model/login/service calls. PowerShell and cmd probes timed out at 30 seconds; the owned process tree was ended. The opt-in regression retains this check; normal CI skips it.
- The credential boundary separates process environments and broker operations. It does not establish OS isolation against same-account process inspection. Live Atlas access, installed Codex shell execution and final integration remain separate gates; keep the PR on hold.

## Proposed manual wording (awaiting scoped ownership)

KO: `alphagenome_atlas`는 GRCh38 SNV를 한 번에 10개까지 사전 계산 Atlas에서 조회합니다. runner의 `ALPHAGENOME_API_KEY` 환경변수와 `alphagenome>=0.10.0`이 필요하며 키 파일은 쓰지 않습니다. scorer·ontology·gene 필터와 출처·사용 조건을 반환합니다. 미수록 변이를 자동으로 모델 추론에 넘기지 않습니다. 실제 Atlas 접근은 키 권한 확인 전까지 미검증입니다.

EN: `alphagenome_atlas` queries precomputed Atlas scores for up to 10 GRCh38 SNVs. It requires `ALPHAGENOME_API_KEY` in the runner environment and `alphagenome>=0.10.0`; it does not read a key file. Scorer, ontology and gene filters are supported, with provenance and usage terms in the result. Missing variants never trigger model inference automatically. Live access remains unverified until the key's Atlas permissions are checked.
