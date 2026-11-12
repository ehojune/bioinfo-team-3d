[← Manual index](../manual.en.md)

## Publishing to your own repository

Request bundles configured with an HPC site profile or `ssh_host` apply the job-log path redaction policy to text copies, including suffixless configs, `.out`/`.err`, execution records and manifests. Sources remain unchanged; `MANIFEST.tsv` labels transformed copies with `redacted`, `source_sha256` and `source_size`, and recalculates copy sizes and hashes. Scripts with redacted site operands receive the `documented` grade. A hash-bound final checkpoint requiring redaction prevents bundle creation ([validation scope](../reference/fix-1416.md)).

Binary formats such as PDF retain their original bytes; scripts also redact shared absolute paths. Bundle text is processed incrementally. Exceeding 16 MiB of JSON, 1M characters per line, or 10,000 remaining paths/16M path characters refuses the bundle with an explicit error. Invalid UTF-8 or NUL bytes in text also refuse publication.

A Windows selection refusal is shown with a dedicated error. If a prerequisite step fails, the coverage report is skipped and diagnostic artifacts are preserved. A successful run goes through both coverage and receipt verification.

Development CI reduces only prose with identical protected examples and markup, and only specified records. For the manual, development instructions and policy, only sentence-boundary line breaks with the same content and structure are candidates. Reuse on main needs the same tree, workflow and policy, coverage, and success evidence of the required gates. Linux 3.10/3.12 and the Windows nightly and workflow_dispatch are exhaustive. The routine Windows run applies one required OS-boundary check and a 10-minute job cap. If optional evidence is wrong, it fails fast and does not widen to exhaustive. A timeout is also a failure. The clean-install, official validator and MCP gates are kept ([judgment and evidence boundary](../reference/ci-reuse.md)). The required 3D screen check gives each screen up to 30 seconds for browser start and up to 20 seconds for the UI check, and finishes within 60 seconds in total including cleanup.

```bash
./scripts/publish_github.sh          # create the public repository <your account>/labhq with the gh CLI + push
./scripts/check_public.sh            # pre-publication check: secrets, real config files, large and genomic data files
```
