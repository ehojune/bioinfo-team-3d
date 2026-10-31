# AlphaGenome Atlas integration — #621

Lookup-only implementation evidence, checked 2026-10-31. Live Atlas access is **UNVERIFIED** pending PI's key-location answer.

Integration hold: manuals are owned by another worker. The Codex adapter now forwards `ALPHAGENOME_API_KEY` by name to built-in `labhq_annot` when present in `staff_env(ctx)`. Shared MCP specs remain unchanged; no value or placeholder is added to argv or generated files. Parent/engine/task fake-key tests cover forwarding, absent/empty keys and unrelated MCP preservation. This does not establish live Atlas access.

| Contract | Implementation |
|---|---|
| Credentials | Atlas reads `ALPHAGENOME_API_KEY` from its environment; no key-file reader or key creation. The Codex parent also holds this key: shell isolation is NOT established (open P1). Doctor checks process plus expanded Codex engine environment, with engine overrides including empty values. |
| Inputs | GRCh38 SNVs, 1–10 per call; scorer names 1–10 (default `RNA_SEQ`); ontology CURIEs and Ensembl gene IDs up to 20 each. No interval/bulk or automatic inference fallback. |
| Output | Raw scores, available quantiles, signed direction, tissue/gene metadata, explicit precomputed source and usage terms. Empty filtered scores do not establish no effect. AVI is supported only if the service exposes that requested scorer; its live name/availability is unverified. |
| Bounds | Sequential queries; existing 300-second call wait and 20-minute step deadline. After timeout/cancellation, no later RPC starts from the abandoned worker; an already-running SDK RPC may finish on a bounded daemon thread. At most 1 million returned score cells processed; 1,000 saved rows and 100 displayed per variant. |
| Records/cache | Existing counts/hash ledger and full-result paths. Cache separates SDK version, filters, schema and UTC day. Dataset release is not exposed by the SDK; daily reuse is not proof of a frozen server release. |
| Request-wide limit | Existing admission gate refuses AlphaGenome SDK transports while enabled; no uncounted bypass. |

## SDK and primary sources

- [Released PyPI package](https://pypi.org/project/alphagenome/0.10.0/): installed only in the worktree `.venv`, Python 3.12. The wheel contains `alphagenome.atlas.atlas`, `create(api_key, timeout=...)`, `scorer_metadata()` and `query_variant(..., requested_scorers, ontology_terms, gene_ids)`.
- Wheel SHA-256: `ed90696f7f0900b2e0d68e28e36ab7d6d95000f59cd19b969fec0bea806bd2c2` (PyPI metadata). Tests run an actual 0.10.0 `AtlasClient` against an in-memory protobuf stub; no credentials or service calls.
- [Official client](https://github.com/google-deepmind/alphagenome/blob/main/src/alphagenome/atlas/atlas.py): scorer-keyed AnnData, scores in `X`, quantiles in `layers['quantiles']`; metadata supplies signedness. Server-side ontology filtering exempts scores without ontology metadata, which the local check preserves.
- [Official README terms and citation](https://github.com/google-deepmind/alphagenome#terms): Atlas API information is non-commercial, not for training other ML models or clinical decisions, subject to explicit permitted-artifact exceptions. [Full terms](https://alphagenome.google/terms) require a script-rendered page; its text was not independently extracted. Citation: Cheng et al. 2026, [doi:10.64898/2026.09.16.26363192](https://doi.org/10.64898/2026.09.16.26363192).

## Open shell boundary (rev6)

- Installed CLI reports `0.165.0-alpha.1.2` (`codex --version`). No model/agent or live Atlas call was started for this diagnosis.
- `CodexAdapter.build_command` forwards the key name through MCP `env_vars`; `AgentAdapter.staff_env` also puts its value in the CLI parent environment. Existing tests verify argv/file non-disclosure, not actual CLI shell isolation.
- [Official shell policy](https://learn.chatgpt.com/docs/config-file/config-advanced#shell-environment-policy) applies `set` after exclusions, allowing restoration. `build_command` appends engine `extra_args` after generated flags. An exclude-only patch is therefore insufficient; installed-version shell/override behavior remains **UNVERIFIED**.
- Proposed next scope: runner-owned credential service, with the Atlas key removed from all staff CLI environments and injected only in the trusted lookup process. Candidate files: `runner/daemon.py`, `adapters/base.py`, `tools/annot_mcp.py`, plus a broker module and transport tests. Keep credentials in memory/environment, preserve admission/cancellation, and demonstrate fake-key delivery to MCP with absence from real shell, argv and files. This requires a new claim; no such files were edited in rev6.
- P1 stays open. Do not treat this diagnosis or wording correction as a security fix. Manual/inventory ownership and live key-location gates remain.

## Proposed manual wording (awaiting scoped ownership)

KO: `alphagenome_atlas`는 GRCh38 SNV를 한 번에 10개까지 사전 계산 Atlas에서 조회합니다. runner의 `ALPHAGENOME_API_KEY` 환경변수와 `alphagenome>=0.10.0`이 필요하며 키 파일은 쓰지 않습니다. scorer·ontology·gene 필터와 출처·사용 조건을 반환합니다. 미수록 변이를 자동으로 모델 추론에 넘기지 않습니다. 실제 Atlas 접근은 키 권한 확인 전까지 미검증입니다.

EN: `alphagenome_atlas` queries precomputed Atlas scores for up to 10 GRCh38 SNVs. It requires `ALPHAGENOME_API_KEY` in the runner environment and `alphagenome>=0.10.0`; it does not read a key file. Scorer, ontology and gene filters are supported, with provenance and usage terms in the result. Missing variants never trigger model inference automatically. Live access remains unverified until the key's Atlas permissions are checked.
