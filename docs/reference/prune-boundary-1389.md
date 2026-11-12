# #1389 boundary removal

PI decision: [approved removal](https://github.com/ehojune/bioinfo-team-3d/issues/1389#issuecomment-6812064276). Five experimental modules and their dedicated tests are removed; the opt-in still refuses before preparation, credentials and spawn. `codex_sandbox.py` and its doctor/runner users remain.

`boundary_probe/probe.py` and its independent tests remain: it exercises retained hook and local job functions. Its integration harness and seal fixture tests addressed the removed broker and boundary and were removed. Repository import search is the scope of the survey.

Windows inventory excludes only removed tests. CI, review and the #1375 comparison remain required; actual OS protection is unverified.
