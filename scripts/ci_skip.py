"""Fail-closed document classification and exact-tree PR/main CI reuse (#788).

No check-name-only or first-parent inference. See docs/reference/ci-reuse.md.
Downloaded ZIP evidence is bounded JSON, never extracted or executed.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import yaml

REPOSITORY = "ehojune/bioinfo-team-3d"
WORKFLOW = ".github/workflows/test.yml"
POLICY_FILES = (WORKFLOW, "scripts/ci_skip.py")
VERSION = 2
GATE_IDS = ("pytest", "pytest-windows", "clean-install", "ro-crate-validator", "pytest-slow")
REQUIRED_JOBS = ("pytest (3.10)", "pytest (3.12)", "pytest-windows", "clean-install",
                 "ro-crate-validator", "pytest-slow", "changes", "readme", "patch-notes", "validation-manifest")
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_BYTES = 256 * 1024
MAX_API_BYTES = 2 * 1024 * 1024
MAX_AGE = timedelta(days=7)


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8",
                          check=True, timeout=30).stdout.rstrip("\n")


class UniqueLoader(yaml.SafeLoader):
    """Duplicate YAML keys are ambiguous, even in data-only patch records."""


def unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in result:
            raise ValueError("duplicate record key")
        result[key] = loader.construct_object(value_node)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def record(text: str) -> bool:
    if not text:  # addition/deletion: the other side is still checked
        return True
    data = yaml.load(text, Loader=UniqueLoader)
    if not isinstance(data, dict) or set(data) != {"pr", "rows"}:
        return False
    if data["pr"] is not None and (type(data["pr"]) is not int or data["pr"] <= 0):
        return False
    if not isinstance(data["rows"], list):
        return False
    return all(isinstance(row, dict) and set(row) == {"sha", "at", "text"}
               and all(isinstance(value, str) for value in row.values())
               and re.fullmatch(r"[0-9a-f]{7}", row["sha"])
               and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", row["at"])
               and bool(row["text"].strip()) and "\n" not in row["text"] for row in data["rows"])


class BalancedHTML(HTMLParser):
    """Unchanged HTML is protected; incomplete/optional-close markup runs full."""
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass

    def handle_endtag(self, tag):
        if not self.stack or self.stack.pop() != tag:
            raise ValueError("ambiguous HTML nesting")


def markdown_parts(text: str) -> tuple:
    # This is already a project dependency. Import lazily: manifest creation
    # needs only PyYAML, while the changes job also installs markdown-it-py.
    from markdown_it import MarkdownIt
    lines = text.splitlines(keepends=True)
    env = {}
    tokens = MarkdownIt("commonmark").enable("table").parse(text, env)
    protected, prose = [], []
    html = BalancedHTML()
    heading = False
    for token in tokens:
        if token.type == "heading_open":
            heading = True
        elif token.type == "heading_close":
            heading = False
        elif token.type in {"fence", "code_block", "html_block"}:
            raw = "".join(lines[token.map[0]:token.map[1]])
            if token.type in {"fence", "code_block"} and token.level:
                raise ValueError("nested block examples require full CI")
            if token.type == "fence" and (token.map[1] - token.map[0] < 2 or not re.fullmatch(
                    r" {0,3}" + re.escape(token.markup[0]) + "{" + str(len(token.markup)) + r",}\s*",
                    lines[token.map[1] - 1].rstrip("\r\n"))):
                raise ValueError("unclosed code fence")
            protected.append((token.type, token.info, raw))
            if token.type == "html_block":
                if raw.count("<!--") != raw.count("-->"):
                    raise ValueError("unclosed HTML comment")
                html.feed(raw)
        elif token.type == "inline":
            # CommonMark normalizes spaces/newlines in inline code. Protect
            # the original spans as well, so normalization cannot hide edits.
            spans = re.findall(r"(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)", token.content, re.DOTALL)
            if len(spans) != sum(c.type == "code_inline" for c in token.children):
                raise ValueError("ambiguous inline code source")
            protected.extend(("inline_source", delimiter, content) for delimiter, content in spans)
            plain = ""
            for child in token.children:
                if child.type == "text":
                    # Unmatched delimiters may hide an incomplete example.
                    if re.search(r"[`<]", child.content):
                        raise ValueError("ambiguous inline markup")
                    plain += child.content
                elif child.type in {"softbreak", "hardbreak"}:
                    plain += "\n"
                elif child.type in {"code_inline", "html_inline", "image", "link_open"}:
                    protected.append((child.type, child.markup, child.content, child.attrs))
                    plain += " "
                    if child.type == "html_inline":
                        if child.content.count("<!--") != child.content.count("-->"):
                            raise ValueError("unclosed HTML comment")
                        html.feed(child.content)
                elif child.type not in {"link_close", "em_open", "em_close", "strong_open", "strong_close"}:
                    raise ValueError("unsupported inline token")
            for line in plain.splitlines():
                if re.match(r"\s*\.\.\s", line):
                    raise ValueError("unsupported directive")
                if line.strip():
                    prose.append((line.strip(), heading))
        elif token.type not in {
                "paragraph_open", "paragraph_close", "bullet_list_open", "bullet_list_close",
                "ordered_list_open", "ordered_list_close", "list_item_open", "list_item_close",
                "blockquote_open", "blockquote_close", "hr", "table_open", "table_close",
                "thead_open", "thead_close", "tbody_open", "tbody_close", "tr_open", "tr_close",
                "th_open", "th_close", "td_open", "td_close"}:
            raise ValueError("unsupported block token")
    html.close()
    if html.stack:
        raise ValueError("unclosed HTML element")
    # Definitions do not produce inline tokens. Bind their destinations too.
    references = {key: {k: v for k, v in value.items() if k != "map"}
                  for key, value in env.get("references", {}).items()}
    if env.get("duplicate_refs"):
        raise ValueError("ambiguous link definitions")
    return protected, references, prose


def prose_change(before: str, after: str) -> bool:
    old, new = markdown_parts(before), markdown_parts(after)
    if old[:2] != new[:2]:
        return False
    code = (r"[{}=:;|]|#!|\w+\(|^(?:[$>%&!]|~/|\.\.?/|\.\. |"
            r"(?:python\d*|pip\d*|pytest|git|bash|sh|npm|node|uv|curl|wget|echo|exec|sudo|"
            r"make|cmake|docker|chmod|rm|cp|mv|cat|taskkill|cmd|pwsh|powershell|labhq|import|[a-z]+-[a-z]+)\b)")
    for tag, a, b, c, d in difflib.SequenceMatcher(a=old[2], b=new[2], autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for line, heading in old[2][a:b] + new[2][c:d]:
            if re.search(code, line, re.IGNORECASE) or (not heading and len(line.split()) < 2):
                return False
            if not heading and not line.endswith((".", "!", "?", "。")):
                return False
    return True


def document_change(path: str, before: str, after: str) -> bool:
    """Prose may change only while protected examples/markup remain identical."""
    try:
        if max(len(before), len(after)) > MAX_BYTES or "\x00" in before + after:
            return False
        if re.fullmatch(r"patch_notes/entries/[^/]+\.yaml", path):
            return record(before) and record(after)
        if path not in {"README.md", "README.en.md", "STATUS.md", "patch_notes/README.md"}:
            if not re.fullmatch(r"docs/(?:[\w-]+/)*[\w-]+\.md", path):
                return False
            if path == "docs/manual.md" or re.search(
                         r"example|policy|ci[-_. /]|guard|approval|safety|security|operations|instruction|contract|protocol|"
                         r"config|package|runtime|dependenc|(?:^|/)(?:tests?|agents|handoff)(?:[./_-]|$)",
                         path, re.IGNORECASE):
                return False
        return prose_change(before, after)
    except Exception:
        return False  # malformed records also run full


def can_skip(head: str, base: str) -> bool:
    """Classify the entire base-to-checkout diff, never just the last notes commit."""
    parts = git("diff", "--raw", "--no-abbrev", "--no-renames", "-z", base, head).split("\x00")
    if parts[-1] != "" or len(parts) < 3 or len(parts) > 2001 or len(parts) % 2 != 1:
        return False
    for header, path in zip(parts[:-1:2], parts[1:-1:2]):
        fields = header.split()
        if len(fields) != 5 or fields[0] not in {":100644", ":000000"} or fields[1] not in {"100644", "000000"}:
            return False
        if fields[-1] not in {"A", "M", "D"}:
            return False
        before = "" if fields[0] == ":000000" else git("show", f"{base}:{path}")
        after = "" if fields[1] == "000000" else git("show", f"{head}:{path}")
        if not document_change(path, before, after):
            return False
    return True


def identity() -> dict:
    return {"checkout_sha": git("rev-parse", "HEAD"), "tree": git("rev-parse", "HEAD^{tree}"),
            "policy": {p: git("rev-parse", f"HEAD:{p}") for p in POLICY_FILES}}


class ArtifactRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urllib.parse.urlsplit(newurl)
        if url.scheme != "https" or not (url.hostname or "").endswith((".blob.core.windows.net", ".githubusercontent.com")):
            raise ValueError("unrecognized artifact storage")
        # Signed storage URLs need no GitHub credential. Never forward Authorization.
        return urllib.request.Request(newurl, headers={"Accept": "application/octet-stream"})


def api(path: str, binary: bool = False):
    token = os.environ.get("GH_TOKEN")
    if not token or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        raise ValueError("missing same-repository API context")
    req = urllib.request.Request(f"https://api.github.com/repos/{REPOSITORY}/{path}",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    opener = urllib.request.build_opener(ArtifactRedirect())
    limit = MAX_BYTES if binary else MAX_API_BYTES
    # Explicit binary HTTP response API, using the same redirect/auth handlers.
    with urllib.request.OpenerDirector.open(opener, req, timeout=15) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("oversized CI evidence")
    return data if binary else json.loads(data.decode("utf-8"))


def manifest_json(data: bytes) -> dict:
    if len(data) > MAX_BYTES:
        raise ValueError("oversized archive")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        files = archive.infolist()
        if len(files) != 1 or files[0].filename != "ci-result.json" or files[0].file_size > 32768:
            raise ValueError("unexpected archive content")
        def unique(pairs):
            result = dict(pairs)
            if len(result) != len(pairs):
                raise ValueError("duplicate manifest key")
            return result
        return json.loads(archive.read(files[0]), object_pairs_hook=unique)


def pr_snapshot(pr: dict, *, rest: bool = False) -> dict:
    repo = pr["base"]["repo"]
    # Actions REST associations contain id/name/url, not full repository objects.
    # The webhook producer and commits/{sha}/pulls use full_name instead.
    repositories = (repo, pr["head"]["repo"])
    names_match = (all(r["url"] == f"https://api.github.com/repos/{REPOSITORY}"
                       and r["name"] == REPOSITORY.split("/")[1] for r in repositories) if rest
                   else all(r["full_name"] == REPOSITORY for r in repositories))
    if not (pr["base"]["ref"] == "main" and names_match
            and type(repo["id"]) is int and repo["id"] > 0 and repo["id"] == pr["head"]["repo"]["id"]
            and type(pr["number"]) is int and pr["number"] > 0
            and SHA.fullmatch(pr["base"]["sha"]) and SHA.fullmatch(pr["head"]["sha"])):
        raise ValueError("untrusted PR snapshot")
    return {"number": pr["number"], "base_sha": pr["base"]["sha"],
            "head_sha": pr["head"]["sha"], "repository_id": repo["id"]}


def expected_manifest(run: dict, current: dict, snapshot: dict, checkout_sha: str) -> dict:
    return {"version": VERSION, "repository": REPOSITORY, "run_id": run["id"], "attempt": run["run_attempt"],
            "event": "pull_request", "checkout_sha": checkout_sha, "workflow_sha": checkout_sha,
            "workflow_ref": f"{REPOSITORY}/{WORKFLOW}@refs/pull/{snapshot['number']}/merge",
            "workflow_blob": current["policy"][WORKFLOW], "pr": snapshot,
            "tree": current["tree"], "policy": current["policy"],
            "gates": {name: {"result": "success", "tree": current["tree"]} for name in GATE_IDS}}


def valid_run(run: dict, pr: dict, current: dict, workflow_id: int, request, now: datetime) -> bool:
    repo_id = pr["base"]["repo"]["id"]
    if not (run["event"] == "pull_request" and run["path"] == WORKFLOW and run["workflow_id"] == workflow_id
            and run["status"] == "completed" and run["conclusion"] == "success"
            and run["repository"]["id"] == run["head_repository"]["id"] == repo_id
            and run["repository"]["full_name"] == run["head_repository"]["full_name"] == REPOSITORY
            and type(run["run_attempt"]) is int and run["run_attempt"] > 0
            and timedelta(0) <= now - datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00")) <= MAX_AGE):
        return False
    prs = run["pull_requests"]
    if len(prs) != 1 or prs[0]["number"] != pr["number"] or prs[0]["head"]["sha"] != pr["head"]["sha"]:
        return False
    snapshot = prs[0]
    if not (snapshot["base"]["ref"] == "main"
            and snapshot["base"]["repo"]["id"] == snapshot["head"]["repo"]["id"] == repo_id):
        return False
    snapshot = pr_snapshot(snapshot, rest=True)
    if run["head_sha"] != snapshot["head_sha"]:
        return False
    # The producer must already have been trusted on main before this PR ran.
    # Workflow/helper-changing PRs cannot certify their own new reuse policy.
    for path, blob_sha in current["policy"].items():
        source = request(f"contents/{path}?ref={snapshot['base_sha']}")
        if source.get("type") != "file" or source.get("sha") != blob_sha:
            return False
    jobs = request(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    rows = jobs["jobs"]
    if (jobs["total_count"] != len(rows) or len(rows) != len(REQUIRED_JOBS)
            or {j["name"] for j in rows} != set(REQUIRED_JOBS)
            or any(j["status"] != "completed" or j["conclusion"] != "success"
                   or j["run_attempt"] != run["run_attempt"] or j["run_id"] != run["id"]
                   or j["head_sha"] != run["head_sha"] for j in rows)):
        return False
    artifacts = request(f"actions/runs/{run['id']}/artifacts?per_page=100")
    if artifacts["total_count"] != len(artifacts["artifacts"]):
        return False
    name = f"ci-full-v{VERSION}-{run['id']}-{run['run_attempt']}"
    candidates = [a for a in artifacts["artifacts"] if a["name"] == name]
    if len(candidates) != 1 or candidates[0]["expired"] or candidates[0]["size_in_bytes"] > MAX_BYTES:
        return False
    artifact = candidates[0]
    data = request(f"actions/artifacts/{artifact['id']}/zip", binary=True)
    if artifact.get("digest") != "sha256:" + hashlib.sha256(data).hexdigest():
        return False
    manifest = manifest_json(data)
    checkout = manifest["checkout_sha"]
    if not SHA.fullmatch(checkout) or checkout in {snapshot["base_sha"], snapshot["head_sha"]}:
        return False
    if json.dumps(manifest, sort_keys=True) != json.dumps(expected_manifest(run, current, snapshot, checkout), sort_keys=True):
        return False
    # REST run/job head_sha denotes the branch commit, NOT GITHUB_SHA. Only
    # the producer's immutable checkout may prove the tested synthetic merge.
    commit = request("git/commits/" + checkout)
    if not (commit["sha"] == checkout and commit["tree"]["sha"] == current["tree"]
            and [p["sha"] for p in commit["parents"]] == [snapshot["base_sha"], snapshot["head_sha"]]):
        return False
    branch = request("git/commits/" + run["head_sha"])
    if branch["sha"] != snapshot["head_sha"]:
        return False
    # Normal pull_request workflows use the synthetic merge workflow source.
    # Other source identities deliberately miss, even if their blobs match.
    # Bind both source and executed helper to the already-trusted base policy.
    for path, blob in current["policy"].items():
        source = request(f"contents/{path}?ref={manifest['workflow_sha']}")
        if source.get("type") != "file" or source.get("sha") != blob:
            return False
    return True


def reuse_main(current: dict, request=api, now: datetime | None = None) -> bool:
    """Bounded search of full PR runs for the PR merged at this exact main SHA."""
    try:
        now = now or datetime.now(timezone.utc)
        workflow = request("actions/workflows/test.yml")
        if workflow["path"] != WORKFLOW or workflow["state"] != "active":
            return False
        prs = request(f"commits/{current['checkout_sha']}/pulls")
        allowed = [pr for pr in prs if pr.get("merged_at") and pr["merge_commit_sha"] == current["checkout_sha"]
                   and pr["base"]["ref"] == "main"
                   and pr["base"]["repo"]["full_name"] == pr["head"]["repo"]["full_name"] == REPOSITORY
                   and pr["base"]["repo"]["id"] == pr["head"]["repo"]["id"]]
        if len(allowed) != 1:
            return False
        runs = request(f"actions/workflows/{workflow['id']}/runs?event=pull_request&per_page=20")["workflow_runs"]
        matches = [run for run in runs if any(p.get("number") == allowed[0]["number"]
                                              for p in run.get("pull_requests", []))]
        if matches:
            # An older run may have been rerun more recently than a newly created one.
            latest = max(matches, key=lambda r: (datetime.fromisoformat(r["updated_at"].replace("Z", "+00:00")), r["id"]))
            valid = valid_run(latest, allowed[0], current, workflow["id"], request, now)
            if valid:
                print(f"ci_skip: reuse run {latest['id']} attempt {latest['run_attempt']} tree {current['tree']}", file=sys.stderr)
            return valid
    except Exception as exc:
        print(f"ci_skip: evidence unavailable ({type(exc).__name__}); run full", file=sys.stderr)
    return False


def decide(event_name: str, event: dict, current: dict) -> str:
    if current["checkout_sha"] != os.environ.get("GITHUB_SHA"):
        return "full"
    if event_name == "pull_request":
        pr = event["pull_request"]
        parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()[1:]
        base = pr["base"]["sha"]
        if parents != [base, pr["head"]["sha"]]:
            return "full"
    elif event_name == "push" and event.get("ref") == "refs/heads/main":
        if reuse_main(current):
            return "reuse"
        base = event.get("before", "")
    else:
        return "full"
    if not SHA.fullmatch(base) or base == "0" * 40:
        return "full"
    return "documents" if can_skip(current["checkout_sha"], base) else "full"


def produce_manifest(current: dict, needs: dict, event: dict) -> dict:
    if not (os.environ["GITHUB_EVENT_NAME"] == "pull_request"
            and os.environ["GITHUB_REPOSITORY"] == REPOSITORY
            and current["checkout_sha"] == os.environ["GITHUB_SHA"]
            and needs["changes"]["outputs"]["skip"] == "false"
            and all(needs[j]["result"] == "success" for j in (*GATE_IDS, "changes", "readme", "patch-notes"))
            and all(needs[j]["outputs"]["tree"] == current["tree"] for j in GATE_IDS)):
        raise ValueError("incomplete full gate receipts")
    snapshot = pr_snapshot(event["pull_request"])
    checkout = current["checkout_sha"]
    run = {"id": int(os.environ["GITHUB_RUN_ID"]), "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
    result = expected_manifest(run, current, snapshot, checkout)
    if not (SHA.fullmatch(checkout) and run["id"] > 0 and run["run_attempt"] > 0
            and os.environ["GITHUB_WORKFLOW_SHA"] == checkout
            and os.environ["GITHUB_WORKFLOW_REF"] == result["workflow_ref"]
            and git("rev-list", "--parents", "-n", "1", "HEAD").split() ==
                [checkout, snapshot["base_sha"], snapshot["head_sha"]]
            and git("rev-parse", f"{os.environ['GITHUB_WORKFLOW_SHA']}:{WORKFLOW}") == result["workflow_blob"]):
        raise ValueError("unbound checkout/workflow identity")
    return result


def main() -> None:
    if sys.argv[1:] == ["identity"]:
        print("tree=" + identity()["tree"])
    elif sys.argv[1:] == ["manifest"]:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        result = produce_manifest(identity(), json.loads(os.environ["CI_NEEDS"]), event)
        Path("ci-result.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    else:
        try:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
            reason = decide(os.environ["GITHUB_EVENT_NAME"], event, identity())
        except Exception as exc:
            print(f"ci_skip: decision unavailable ({type(exc).__name__}); run full", file=sys.stderr)
            reason = "full"
        print(f"ci_skip: {reason}", file=sys.stderr)
        print(f"skip={'false' if reason == 'full' else 'true'}")
        print(f"reason={reason}")


if __name__ == "__main__":
    main()
