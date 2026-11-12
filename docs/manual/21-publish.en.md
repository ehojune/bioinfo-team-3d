[← Manual index](../manual.en.md)

## Publishing to your own repository

When an HPC site profile names `remote_workdir`, request bundles replace every path that starts at that folder with `<remote-path>` in UTF-8 text copies: job outputs, scripts, suffixless configs, `.out`/`.err`, the execution record and the manifest. The account and group names inside that path go with it. Sources remain unchanged; `MANIFEST.tsv` labels transformed copies with `redacted`, `source_sha256` and `source_size`, and recalculates copy sizes and hashes. Scripts with a hidden site path receive the `documented` grade. A hash-bound final checkpoint that holds the path prevents bundle creation ([validation scope](../reference/fix-1416.md)).

Known limit: only the configured folder is hidden. The report (`report.md`, `report_appendix.md`), `README.md`, HTML files and binary files keep their bytes. Other absolute paths, a resolved path that differs from `remote_workdir`, a path that continues after a space, and host, user or queue names outside that path are not guessed; `labhq verify` still checks the report.

A Windows selection refusal is shown with a dedicated error. If a prerequisite step fails, the coverage report is skipped and diagnostic artifacts are preserved. A successful run goes through both coverage and receipt verification.

Development CI reduces only prose with identical protected examples and markup, and only specified records. For the manual, development instructions and policy, only sentence-boundary line breaks with the same content and structure are candidates. Reuse on main needs the same tree, workflow and policy, coverage, and success evidence of the required gates. Linux 3.10/3.12 and the Windows nightly and workflow_dispatch are exhaustive. The routine Windows run applies one required OS-boundary check and a 10-minute job cap. If optional evidence is wrong, it fails fast and does not widen to exhaustive. A timeout is also a failure. The clean-install, official validator and MCP gates are kept ([judgment and evidence boundary](../reference/ci-reuse.md)). The required 3D screen check gives each screen up to 30 seconds for browser start and up to 20 seconds for the UI check, and finishes within 60 seconds in total including cleanup.

```bash
./scripts/publish_github.sh          # create the public repository <your account>/labhq with the gh CLI + push
./scripts/check_public.sh            # pre-publication check: secrets, real config files, large and genomic data files
```
