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

if __name__ == '__main__' and not __package__:
    # Keep the documented script entrypoint as well as python -m working.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPOSITORY = "ehojune/bioinfo-team-3d"
WORKFLOW = ".github/workflows/test.yml"
POLICY_FILES = (WORKFLOW, "scripts/ci_skip.py", "scripts/ci_suite.py", "scripts/ci_selection.py",
                "scripts/ci_isolation.py", "config/ci/windows-boundary-v1.json", "pyproject.toml",
                "tests/conftest.py", "labhq/test_guard.py", "scripts/ci_windows_phases.py")
VERSION = 8
GATE_IDS = ("pytest", "pytest-windows", "clean-install", "ro-crate-validator", "pytest-slow")
COVERAGE = dict(zip(GATE_IDS, ("linux-full-v1", "windows-boundary-v6", "clean-install-v1",
                              "official-validator-v1", "long-mcp-v1")))
REQUIRED_JOBS = ("pytest (3.10)", "pytest (3.12)", "pytest-windows", "clean-install",
                 "ro-crate-validator", "pytest-slow", "changes", "readme", "patch-notes", "validation-manifest")
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_BYTES = 256 * 1024
MAX_API_BYTES = 2 * 1024 * 1024
MAX_AGE = timedelta(days=7)

# Modality, not a subject-noun inventory: an unfamiliar subject can still carry
# a requirement, permission or prohibition. Ambiguous capability/advice is full.
NORMATIVE = re.compile(
    r"\b(?:(?:must|shall|should|may|might|can|could|would|will|ought|need)(?:n['’]t)?|needs|needed|needing|"
    r"always|never|do(?:es)?\s+not|"
    r"cannot|requir\w*|(?:un)?necessar\w*|mandator\w*|optional\w*|compulsor\w*|"
    r"oblig\w*|prohibit\w*|forbid\w*|permi\w*|recommend\w*)\b|"
    r"필수|의무|선택|금지|불가|필요|가능|생략|면제|반드시|권장|"
    r"[가-힣]+야\s*(?:하|합|한|할|함|되|된|됩)|"
    r"[가-힣]+도\s*(?:되|된|됩|괜찮|좋)|"
    r"[가-힣]+(?:면|서는|선)\s*안\s*(?:되|된|됩)|"
    r"[가-힣]+지\s*(?:말|마|못)|(?:할|될|[가-힣]+을)\s*수\s*(?:있|없)", re.IGNORECASE)


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
    return all(isinstance(row, dict) and set(row) in ({"sha", "at", "text"}, {"sha", "at", "text", "summary"})
               and all(isinstance(value, str) for value in row.values())
               and re.fullmatch(r"[0-9a-f]{7}", row["sha"])
               and re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", row["at"])
               and bool(row["text"].strip()) and "\n" not in row["text"]
               and ("summary" not in row or (bool(row["summary"].strip()) and "\n" not in row["summary"]))
               for row in data["rows"])


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


def inline_signature(children) -> list:
    """Bind wording and inline structure while treating soft wrapping as spaces."""
    atoms, plain = [], ""
    for child in children:
        if child.type in {"text", "softbreak"}:
            plain += child.content if child.type == "text" else " "
        else:
            atoms.append(("text", re.sub(r"[ \t\r\n]+", " ", plain)))
            plain = ""
            atoms.append((child.type, child.tag, child.nesting, child.markup, child.content, child.attrs))
    atoms.append(("text", re.sub(r"[ \t\r\n]+", " ", plain)))
    return atoms


def descriptive_prose(text: str, *, heading: bool) -> bool:
    """A small positive grammar, not a detector for arbitrary CLI/NL syntax.

    Unclassified blocks retain their complete Markdown identity. New tools or
    imperative verbs therefore do not need to be added to a command denylist.
    """
    text = re.sub(r"[ \t\r\n]+", " ", text).strip()
    if heading and re.fullmatch(r"[A-Za-z]+", text):
        return True  # single-word headings; argv-shaped headings stay protected
    subject = r"(?:The|A|An|This|That|These|Those) (?:[A-Za-z]+ ){1,6}"
    descriptive = (
        subject + r"(?:is|are|was|were) (?:(?:more|less|much) )?"
        r"(?:clear|clearer|concise|short|shorter|long|longer|detailed|readable|helpful|easier to read)|"
        + subject + r"contains (?:more|fewer|clearer) (?:examples|diagrams|illustrations|details)|"
        r"(?:Old|New|More|Plain|Ordinary) (?:prose|result|row)|"
        r"(?:A|An) [A-Za-z]+ (?:description|explanation)"
    )
    for sentence in re.split(r"(?<=[.!?。]) ", text):
        if re.fullmatch(r"(?:" + descriptive + r")[.!?。]", sentence):
            continue
        # One document subject + optional degree + one adjective. An arbitrary
        # prefix or a reporting tail can conceal action chains, even unspaced.
        if re.fullmatch(r"(?:설명|문서|문단|그림|도표|개요)(?:은|는|이|가) *"
                        r"(?:(?:더|덜|매우) *)?(?:명확(?:합니다|해졌습니다)|"
                        r"간결합니다|짧(?:습니다|아졌습니다))[.!?。]", sentence):
            continue
        return False
    return bool(text)


def markdown_parts(text: str) -> tuple:
    # This is already a project dependency. Import lazily: manifest creation
    # needs only PyYAML, while the changes job also installs markdown-it-py.
    from markdown_it import MarkdownIt
    lines = text.splitlines(keepends=True)
    env = {}
    tokens = MarkdownIt("commonmark").enable("table").parse(text, env)
    protected, prose = [], []
    containers = []
    html = BalancedHTML()
    heading = False
    for token in tokens:
        if token.nesting == 1:
            containers.append((token.type, token.tag, token.markup, token.attrs))
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
            if NORMATIVE.search(plain):
                # Protect the complete inline block on BOTH sides, including
                # context/conditions/emphasis; no subject noun is assumed safe.
                protected.append(("normative", tuple(containers), inline_signature(token.children)))
            elif not descriptive_prose(plain, heading=heading):
                # Protect the whole block, including surrounding words and
                # structure; line diffs must not hide an unfamiliar instruction.
                protected.append(("unclassified", tuple(containers), inline_signature(token.children)))
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
        if token.nesting == -1:
            containers.pop()
    html.close()
    if html.stack:
        raise ValueError("unclosed HTML element")
    # Definitions do not produce inline tokens. Bind their destinations too.
    references = {key: {k: v for k, v in value.items() if k != "map"}
                  for key, value in env.get("references", {}).items()}
    if env.get("duplicate_refs"):
        raise ValueError("ambiguous link definitions")
    return protected, references, prose


def prose_change(before: str, after: str, *, reflow: bool = False) -> bool:
    old, new = markdown_parts(before), markdown_parts(after)
    if old[:2] != new[:2]:
        return False
    code = (r"[{}=:;|]|#!|\w+\(|^(?:[$>%&!]|~/|\.\.?/|\.\. )|"
            r"(?<![\w-])(?:python\d*|pip\d*|pytest|git|bash|sh|npm|node|uv|curl|wget|echo|exec|sudo|"
            r"make|cmake|docker|chmod|rm|cp|mv|cat|taskkill|cmd|pwsh|powershell|labhq|import|[a-z]+-[a-z]+)\b")
    # CLI names are unbounded. Instructions and argv-shaped text are ambiguous
    # even when the executable is absent from the known-command pattern above.
    instruction = (r"\b(?:run|execute|invoke|launch|start|use|call|try|type|enter|install)\b|"
                   r"(?:실행|호출|입력|사용)[가-힣]*|[\w-]+[./\\][\w./\\-]+|"
                   r"(?<!\w)--?[A-Za-z]\w*|['\"]")
    policy = (r"\b(?:approv\w*|authoriz\w*|permission\w*|polic\w*|safety|security|redact\w*|"
              r"publish\w*|publication|credential\w*|secret\w*|token\w*|guard\w*|gate\w*|"
              r"sandbox\w*|restrict\w*|config\w*|timeout\w*|deadline\w*|budget\w*|assert\w*|"
              r"validat\w*|allow\w*|deny|denied|cancel\w*|dependenc\w*|workflow\w*|ci|test\w*)\b|"
              r"승인|권한|정책|안전|보안|비밀|게시|통제|설정|상한|예산|검증|허용|거부|취소|의존성")
    for tag, a, b, c, d in difflib.SequenceMatcher(a=old[2], b=new[2], autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        for line, heading in old[2][a:b] + new[2][c:d]:
            if (re.search(code, line, re.IGNORECASE) or re.search(instruction, line, re.IGNORECASE)
                    or (not reflow and re.search(policy, line, re.IGNORECASE))
                    or (not reflow and not heading and len(line.split()) < 2)):
                return False
            if not reflow and not heading and not line.endswith((".", "!", "?", "。")):
                return False
    return True


def reflow_change(before: str, after: str) -> bool:
    """Policy-bearing documents permit only identical Markdown semantics, never rewritten policy."""
    from markdown_it import MarkdownIt
    if not before.strip() or not after.strip() or not prose_change(before, after, reflow=True):
        return False

    def signature(text):
        result = []
        for token in MarkdownIt("commonmark").enable("table").parse(text):
            content = token.content
            if token.type == "inline":
                content = inline_signature(token.children)
            result.append((token.type, token.tag, token.nesting, token.hidden,
                           token.markup, token.info, token.attrs, content))
        return result

    return signature(before) == signature(after)


def document_change(path: str, before: str, after: str) -> bool:
    """Prose may change only while protected examples/markup remain identical."""
    try:
        if max(len(before), len(after)) > MAX_BYTES or "\x00" in before + after:
            return False
        if re.fullmatch(r"patch_notes/entries/[^/]+\.yaml", path):
            return record(before) and record(after)
        if path in {"AGENTS.md", "HANDOFF.md"}:
            return reflow_change(before, after)
        if path not in {"README.md", "README.en.md", "STATUS.md", "patch_notes/README.md"}:
            if not re.fullmatch(r"docs/(?:[\w-]+/)*[\w-]+\.md", path):
                return False
            if path == "docs/manual.md" or re.search(
                         r"example|policy|ci[-_. /]|guard|approval|safety|security|operations|instruction|contract|protocol|"
                         r"config|package|runtime|dependenc|(?:^|/)(?:tests?|agents|handoff)(?:[./_-]|$)",
                         path, re.IGNORECASE):
                return reflow_change(before, after)
        return prose_change(before, after) or reflow_change(before, after)
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


def expected_manifest(run: dict, current: dict, snapshot: dict, checkout_sha: str, windows_execution=None) -> dict:
    return {"version": VERSION, "repository": REPOSITORY, "run_id": run["id"], "attempt": run["run_attempt"],
            "event": "pull_request", "checkout_sha": checkout_sha, "workflow_sha": checkout_sha,
            "workflow_ref": f"{REPOSITORY}/{WORKFLOW}@refs/pull/{snapshot['number']}/merge",
            "workflow_blob": current["policy"][WORKFLOW], "pr": snapshot,
            "tree": current["tree"], "policy": current["policy"], "coverage": COVERAGE,
            "windows_execution": windows_execution,
            "gates": {name: {"result": "success", "tree": current["tree"]} for name in GATE_IDS}}


def valid_run(run: dict, pr: dict, current: dict, workflow_id: int, request, now: datetime) -> bool:
    repo_id = pr["base"]["repo"]["id"]
    if not (run["event"] == "pull_request" and run["path"] == WORKFLOW and run["workflow_id"] == workflow_id
            and run["status"] == "completed" and run["conclusion"] == "success"
            and run["repository"]["id"] == run["head_repository"]["id"] == repo_id
            and run["repository"]["full_name"] == run["head_repository"]["full_name"] == REPOSITORY
            and type(run["id"]) is int and run["id"] > 0
            and type(run["run_attempt"]) is int and run["run_attempt"] > 0
            and timedelta(0) <= now - datetime.fromisoformat(run["updated_at"].replace("Z", "+00:00")) <= MAX_AGE):
        return False
    prs = run["pull_requests"]
    if not isinstance(prs, list):
        return False
    if not prs:
        # REST can omit the association even for a completed pull_request run.
        # An empty list is a discovery gap, not proof: independently re-read the
        # merged PR, then require the unchanged authenticated manifest, ordered
        # checkout parents, trusted-base policy and complete gate checks below.
        snapshot = pr_snapshot(pr)
        if not pr.get("merged_at") or pr["merge_commit_sha"] != current["checkout_sha"]:
            return False
        authoritative = request(f"pulls/{snapshot['number']}")
        if not (authoritative["merged"] is True
                and authoritative["merge_commit_sha"] == current["checkout_sha"]
                and authoritative["merged_at"] == pr["merged_at"]
                and pr_snapshot(authoritative) == snapshot):
            return False
    else:
        if len(prs) != 1 or prs[0]["number"] != pr["number"] or prs[0]["head"]["sha"] != pr["head"]["sha"]:
            return False
        snapshot = pr_snapshot(prs[0], rest=True)
    if snapshot["repository_id"] != repo_id or run["head_sha"] != snapshot["head_sha"]:
        return False
    # v4 includes changed tests as well as the static Windows cohort. Equal
    # trees alone cannot prove equal base-to-tree change sets: require the
    # main push's actual before snapshot to equal the tested PR base too.
    if current.get("selection_base") != snapshot["base_sha"]:
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
    execution = manifest['windows_execution']
    validate_windows_execution(execution, current, run, snapshot['head_sha'], checkout, manifest['workflow_ref'])
    if json.dumps(manifest, sort_keys=True) != json.dumps(expected_manifest(run, current, snapshot, checkout, execution), sort_keys=True):
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
                                              for p in run.get("pull_requests", []))
                   or (run.get("pull_requests") == [] and run.get("head_sha") == allowed[0]["head"]["sha"])]
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
        if reuse_main({**current, "selection_base": event.get("before")}):
            return "reuse"
        base = event.get("before", "")
    else:
        return "full"
    if not SHA.fullmatch(base) or base == "0" * 40:
        return "full"
    return "documents" if can_skip(current["checkout_sha"], base) else "full"


class NonReusableBaseDrift(ValueError):
    """Independently confirmed full-test checkout, never reusable evidence."""


class NonReusablePartialRerun(ValueError):
    """Validated partial rerun, without a same-attempt reusable manifest."""


def windows_artifact(run, head, repository_id, request=None):
    """Bind the effective Windows execution to its original upload, not a copied job.

    GitHub copies successful jobs into later attempts with new IDs/created_at.
    Their execution times stay unchanged. Only an original job whose creation
    precedes execution and whose upload falls inside that execution is accepted.
    """
    from datetime import datetime
    request = request or api
    def jobs(attempt):
        data = request(f"actions/runs/{run['id']}/attempts/{attempt}/jobs?per_page=100")
        if data['total_count'] != len(data['jobs']):
            raise ValueError('incomplete producer jobs')
        rows = [j for j in data['jobs'] if j['name'] == 'pytest-windows']
        if len(rows) != 1:
            raise ValueError('missing/ambiguous Windows producer')
        job = rows[0]
        if not (job['run_id'] == run['id'] and job['run_attempt'] == attempt
                and job['head_sha'] == head):
            raise ValueError('unbound Windows producer')
        return job
    def time(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    effective = jobs(run['run_attempt'])
    if effective['status'] != 'completed' or effective['conclusion'] != 'success':
        raise ValueError('failed/incomplete effective Windows producer')
    data = request(f"actions/runs/{run['id']}/artifacts?per_page=100")
    if data['total_count'] != len(data['artifacts']):
        raise ValueError('incomplete producer artifacts')
    matches = []
    for artifact in data['artifacts']:
        match = re.fullmatch(r'coverage-windows-' + str(run['id']) + r'-(\d+)', artifact['name'])
        if not match:
            continue
        attempt = int(match[1])
        if not 0 < attempt <= run['run_attempt'] or artifact['expired'] is not False:
            continue
        origin = artifact['workflow_run']
        if not (origin['id'] == run['id'] and origin['head_sha'] == head
                and origin['repository_id'] == origin['head_repository_id'] == repository_id):
            raise ValueError('unbound Windows artifact')
        job = effective if attempt == run['run_attempt'] else jobs(attempt)
        if job['status'] != 'completed' or job['conclusion'] != 'success':
            continue  # always() uploads from old failed jobs are not candidates.
        if (job['started_at'], job['completed_at']) != (effective['started_at'], effective['completed_at']):
            continue  # An older success is not the effective execution.
        if (time(job['created_at']) <= time(job['started_at'])
                <= time(artifact['created_at']) <= time(artifact['updated_at']) <= time(job['completed_at'])):
            matches.append((artifact['id'], attempt))
    if len(matches) != 1 or type(matches[0][0]) is not int or matches[0][0] <= 0:
        raise ValueError('missing/ambiguous bound Windows artifact')
    return matches[0]


def confirmed_base_drift(current: dict, snapshot: dict, parents: list[str], run: dict) -> bool:
    """Recognize only the observed forward-main/event snapshot drift.

    This does NOT repair the event base, certify a policy, or return a manifest.
    A moved PR/main, partial API response or any uncertain binding stays an error.
    """
    checkout = current["checkout_sha"]
    if not (len(parents) == 3 and all(SHA.fullmatch(p) for p in parents)
            and parents[0] == checkout and parents[2] == snapshot["head_sha"]
            and len(set(parents)) == 3 and parents[1] != snapshot["base_sha"]):
        return False
    base = parents[1]
    try:
        workflow = api("actions/workflows/test.yml")
        actual = api(f"actions/runs/{run['id']}")
        if not (type(workflow["id"]) is int and workflow["id"] > 0
                and workflow["path"] == WORKFLOW and workflow["state"] == "active"
                and type(actual["id"]) is int and actual["id"] == run["id"]
                and type(actual["run_attempt"]) is int and actual["run_attempt"] == run["run_attempt"]
                and actual["workflow_id"] == workflow["id"] and actual["path"] == WORKFLOW
                and actual["event"] == "pull_request" and actual["status"] == "in_progress"
                and actual["conclusion"] is None and actual["head_sha"] == snapshot["head_sha"]
                and all(r["id"] == snapshot["repository_id"] and r["full_name"] == REPOSITORY
                        for r in (actual["repository"], actual["head_repository"]))
                and len(actual["pull_requests"]) == 1
                and pr_snapshot(actual["pull_requests"][0], rest=True) == snapshot):
            return False
        commit = api("git/commits/" + checkout)
        pr = api(f"pulls/{snapshot['number']}")
        main = api("git/ref/heads/main")
        if not (commit["sha"] == checkout and commit["tree"]["sha"] == current["tree"]
                and [p["sha"] for p in commit["parents"]] == parents[1:]
                and pr_snapshot(pr) == snapshot and pr["merge_commit_sha"] == checkout
                and main["ref"] == "refs/heads/main" and main["object"]["type"] == "commit"
                and SHA.fullmatch(main["object"]["sha"])):
            return False
        if main["object"]["sha"] != base:
            # main moved again while this job ran (the index commit lands during a run): the checkout parent must be a
            # commit main already contains, and main may only be ahead of it. A diverged, behind or unrelated main
            # stays an error, as does any partial answer.
            moved = api(f"compare/{base}...{main['object']['sha']}")
            if not (moved["status"] == "ahead" and type(moved["behind_by"]) is int and moved["behind_by"] == 0
                    and moved["base_commit"]["sha"] == moved["merge_base_commit"]["sha"] == base):
                return False
        forward = api(f"compare/{snapshot['base_sha']}...{base}")
        count = forward["total_commits"]
        if not (forward["status"] == "ahead" and type(forward["behind_by"]) is int and forward["behind_by"] == 0
                and type(count) is int and 0 < count <= 250
                and type(forward["ahead_by"]) is int and forward["ahead_by"] == count == len(forward["commits"])
                and forward["base_commit"]["sha"] == forward["merge_base_commit"]["sha"] == snapshot["base_sha"]
                and forward["commits"][-1]["sha"] == base):
            return False
        jobs = api(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
        rows = jobs["jobs"]
        return (jobs["total_count"] == len(rows) == len(REQUIRED_JOBS)
                and {j["name"] for j in rows} == set(REQUIRED_JOBS)
                and all(type(j["run_id"]) is int and j["run_id"] == run["id"]
                        and type(j["run_attempt"]) is int and j["run_attempt"] == run["run_attempt"]
                        and j["head_sha"] == snapshot["head_sha"]
                        and ((j["status"] == "in_progress" and j["conclusion"] is None)
                             if j["name"] == "validation-manifest" else
                             (j["status"] == "completed" and j["conclusion"] == "success")) for j in rows))
    except (KeyError, TypeError, ValueError, IndexError, OSError):
        return False  # API unavailability/malformed evidence cannot green an unbound checkout.


def produce_manifest(current: dict, needs: dict, event: dict) -> dict:
    if not (os.environ["GITHUB_EVENT_NAME"] == "pull_request"
            and os.environ["GITHUB_REPOSITORY"] == REPOSITORY
            and current["checkout_sha"] == os.environ["GITHUB_SHA"]
            and needs["changes"]["outputs"]["skip"] == "false"
            and all(needs[j]["result"] == "success" for j in (*GATE_IDS, "changes", "readme", "patch-notes"))
            and all(needs[j]["outputs"]["tree"] == current["tree"] for j in GATE_IDS)):
        raise ValueError("incomplete full gate receipts")
    if any(needs[j]["outputs"].get("profile") != COVERAGE[j] for j in GATE_IDS):
        raise ValueError("incomplete coverage profile receipts")
    snapshot = pr_snapshot(event["pull_request"])
    checkout = current["checkout_sha"]
    run = {"id": int(os.environ["GITHUB_RUN_ID"]), "run_attempt": int(os.environ["GITHUB_RUN_ATTEMPT"])}
    result = expected_manifest(run, current, snapshot, checkout)
    source = os.environ["GITHUB_WORKFLOW_SHA"]
    ref = os.environ["GITHUB_WORKFLOW_REF"]
    checks = (("checkout-sha", bool(SHA.fullmatch(checkout))), ("run-id", run["id"] > 0),
              ("run-attempt", run["run_attempt"] > 0), ("workflow-sha", source == checkout),
              ("workflow-ref", ref == result["workflow_ref"]))
    failed = [name for name, valid in checks if not valid]
    if failed:
        # Fixed role labels only: never dump environment/event payloads or an
        # arbitrary revision. A head/base source remains rejected, not trusted.
        source_role = next((name for name, value in (("checkout", checkout),
                           ("pr-head", snapshot["head_sha"]), ("pr-base", snapshot["base_sha"]))
                            if source == value), "other")
        head_ref = f"{REPOSITORY}/{WORKFLOW}@refs/heads/{event['pull_request']['head'].get('ref', '')}"
        ref_role = "pr-merge" if ref == result["workflow_ref"] else "pr-head" if ref == head_ref else "other"
        raise ValueError("unbound checkout/workflow identity: " + ", ".join(failed)
                         + f" (source={source_role}, ref={ref_role})")
    parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    expected_parents = [checkout, snapshot["base_sha"], snapshot["head_sha"]]
    if parents != expected_parents:
        # Bounded public commit identifiers only; no raw event/environment dump.
        print("ci_skip: checkout-parents " + json.dumps({
            "actual": [p if SHA.fullmatch(p) else "<invalid>" for p in parents[:3]],
            "expected": expected_parents, "length": len(parents)}), file=sys.stderr)
        if confirmed_base_drift(current, snapshot, parents, run):
            if git("rev-parse", f"{source}:{WORKFLOW}") != result["workflow_blob"]:
                raise ValueError("unbound checkout/workflow identity: workflow-blob")
            raise NonReusableBaseDrift("independently confirmed base drift; no reusable manifest")
        raise ValueError("unbound checkout/workflow identity: checkout-parents")
    if git("rev-parse", f"{source}:{WORKFLOW}") != result["workflow_blob"]:
        raise ValueError("unbound checkout/workflow identity: workflow-blob")
    result['windows_execution'] = windows_receipt(current, needs, run, snapshot['head_sha'])
    # A fresh Windows job says nothing about whether other successful gates
    # were copied. Conservatively keep every rerun out of the reuse cache.
    if run['run_attempt'] != 1:
        raise NonReusablePartialRerun('validated rerun; no reusable manifest')
    return result


def validate_windows_execution(proof, current, run, head, checkout, workflow_ref):
    from scripts import ci_windows_phases as phases
    phases.validate_proof(proof)
    context = proof['context']
    if not (context['checkout_sha'] == checkout and context['head_sha'] == head
            and context['tree'] == current['tree'] and context['policy'] == current['policy']
            and context['run_id'] == run['id'] and context['attempt'] == run['run_attempt']
            and context['workflow_sha'] == checkout and context['workflow_ref'] == workflow_ref
            and context['profile'] == COVERAGE['pytest-windows']):
        raise ValueError('unbound Windows phase proof')


def windows_receipt(current, needs, run, head):
    from scripts import ci_windows_phases as phases
    directory = Path(os.environ['CI_WINDOWS_RECEIPT_DIR'])
    proof = phases.combine(directory)  # Revalidate downloaded JSON, never execute it.
    outputs = needs['pytest-windows']['outputs']
    if (outputs.get('execution') != phases.EXECUTION or outputs.get('receipt') != phases.digest(proof)
            or phases.read(directory / 'windows-proof.json') != proof):
        raise ValueError('missing/mismatched Windows phase receipt')
    producer_run = run
    if proof['context']['attempt'] != run['run_attempt']:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        artifact_id, attempt = windows_artifact(run, head, pr_snapshot(event['pull_request'])['repository_id'])
        if (str(artifact_id) != os.environ.get('CI_WINDOWS_ARTIFACT_ID')
                or attempt != proof['context']['attempt']):
            raise ValueError('unbound prior-attempt Windows receipt')
        producer_run = dict(id=run['id'], run_attempt=attempt)
    validate_windows_execution(proof, current, producer_run, head, current['checkout_sha'], os.environ['GITHUB_WORKFLOW_REF'])
    return proof


def main() -> None:
    if sys.argv[1:] == ['windows-artifact']:
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        snapshot = pr_snapshot(event['pull_request'])
        artifact_id, _ = windows_artifact(dict(id=int(os.environ['GITHUB_RUN_ID']),
            run_attempt=int(os.environ['GITHUB_RUN_ATTEMPT'])), snapshot['head_sha'], snapshot['repository_id'])
        print(f'artifact-id={artifact_id}')
    elif sys.argv[1:] == ['windows-identity']:
        import platform
        import uuid
        from scripts import ci_windows_phases as phases
        current = identity()
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text(encoding='utf-8'))
        context = dict(execution=phases.EXECUTION, repository=os.environ['GITHUB_REPOSITORY'],
            run_id=int(os.environ['GITHUB_RUN_ID']), attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
            **current, head_sha=event['pull_request']['head']['sha'] if 'pull_request' in event else current['checkout_sha'],
            workflow_sha=os.environ['GITHUB_WORKFLOW_SHA'], workflow_ref=os.environ['GITHUB_WORKFLOW_REF'],
            profile=os.environ['CI_COVERAGE_PROFILE'], python_version=platform.python_version(),
            python_executable=os.path.normcase(os.path.abspath(sys.executable)), invocation=uuid.uuid4().hex,
            setup_started=float(os.environ['CI_SETUP_STARTED']))
        phases.validate_identity(context)
        directory = Path(os.environ['CI_SUITE_ARTIFACT_DIR'])
        directory.mkdir(parents=True, exist_ok=True)
        phases.write(directory / 'identity.json', context)
    elif sys.argv[1:] == ["identity"]:
        print("tree=" + identity()["tree"])
    elif sys.argv[1:] == ["manifest"]:
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8"))
        try:
            result = produce_manifest(identity(), json.loads(os.environ["CI_NEEDS"]), event)
        except NonReusableBaseDrift:
            print("ci_skip: confirmed event/tested-base drift; no reusable manifest", file=sys.stderr)
            output = "reusable=false\nreason=base-drift\n"
        except NonReusablePartialRerun:
            print('ci_skip: validated partial rerun; no reusable manifest', file=sys.stderr)
            output = 'reusable=false\nreason=partial-rerun\n'
        else:
            Path("ci-result.json").write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
            output = "reusable=true\nreason=bound\n"
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as stream:
            stream.write(output)
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
