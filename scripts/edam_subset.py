"""Generate labhq/vocab/edam_subset.yaml from one pinned EDAM release (#151, #221). Run by hand; CI never downloads.

    python scripts/edam_subset.py --fetch            # download the pinned asset to a temp folder, verify, write
    python scripts/edam_subset.py --owl EDAM.owl     # a copy you downloaded yourself (same checks)
    python scripts/edam_subset.py --owl EDAM.owl --check   # compare with the committed subset; write nothing

The downloaded OWL never enters the repository: only the subset (ids, labels, ancestor ids) and its header
(source URL, release, SHA-256, license, map and script digests). Ids come from the file and nowhere else.

Rule per key of labhq/vocab/edam_map.yaml: the candidate label must be the label or an exact synonym of exactly
one live (not deprecated) term of the key's branch, counted by distinct term URI. Otherwise the key stays local
with a reason: no_candidate, no_match, wrong_branch, deprecated, ambiguous. The chosen term's ancestors must all
exist, stay in the branch and end at a root within the limits (ancestor_missing, ancestor_cycle, ancestor_limit).
Ancestors are recorded; nothing is inferred from them.

The XML is read with expat and refused when it declares a DOCTYPE or any entity, so no external reference,
import or network address inside the file is ever followed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.parsers import expat

import yaml

ROOT = Path(__file__).resolve().parents[1]
VOCAB = ROOT / "labhq" / "vocab"
MAP = VOCAB / "edam_map.yaml"
SUBSET = VOCAB / "edam_subset.yaml"
LOCAL = VOCAB / "output_types.yaml"

RELEASE = "1.25.20260626T1230Z"
VERSION_INFO = "1.25-20260626T1230Z"  # owl:versionInfo inside that release's file
URL = f"https://github.com/edamontology/edamontology/releases/download/{RELEASE}/EDAM.owl"
SHA256 = "278c5e606004c872311cd9d9d385eb578f01c1a3a38cb9ebeaa5e125e818ecc8"  # GitHub release asset digest
LICENSE = "https://creativecommons.org/licenses/by-sa/4.0"
MAX_BYTES = 32 * 1024 ** 2
MAX_DEPTH = 64
MAX_ANCESTORS = 256
MAX_KEYS = 116

NS_EDAM = "http://edamontology.org/"
NS_RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
NS_RDFS = "http://www.w3.org/2000/01/rdf-schema#"
NS_OWL = "http://www.w3.org/2002/07/owl#"
NS_OBO = "http://www.geneontology.org/formats/oboInOwl#"
NS_DCTERMS = "http://purl.org/dc/terms/"
SEP = "\x1f"
ABOUT, RESOURCE = NS_RDF + SEP + "about", NS_RDF + SEP + "resource"
BRANCHES = ("data", "format", "operation", "topic")


class SubsetError(ValueError):
    """The input cannot produce a subset. Messages name the file, never a local path."""


@dataclass
class Term:
    uri: str
    labels: list[str] = field(default_factory=list)
    synonyms: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)
    deprecated: bool = False


@dataclass
class Ontology:
    version_info: str | None = None
    license: str | None = None
    terms: dict[str, Term] = field(default_factory=dict)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                          .encode("utf-8")).hexdigest()


def parse_owl(data: bytes) -> Ontology:
    """Top-level owl:Class terms with rdf:about, their labels, exact synonyms, named parents and deprecation."""
    if len(data) > MAX_BYTES:
        raise SubsetError(f"EDAM.owl: larger than {MAX_BYTES} bytes")
    onto = Ontology()
    stack: list[str] = []
    text: list[str] = []
    current: list[Term | None] = [None]
    in_ontology = [False]

    def refuse(*_: Any) -> None:
        raise SubsetError("EDAM.owl: declares a DOCTYPE or an entity; refused")

    parser = expat.ParserCreate(namespace_separator=SEP)
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = refuse
    parser.EntityDeclHandler = refuse
    parser.UnparsedEntityDeclHandler = refuse
    parser.ExternalEntityRefHandler = refuse

    def start(name: str, attrs: dict[str, str]) -> None:
        depth = len(stack)
        stack.append(name)
        text.clear()
        if depth == 1 and name == NS_OWL + SEP + "Class" and attrs.get(ABOUT):
            uri = attrs[ABOUT]
            current[0] = onto.terms.setdefault(uri, Term(uri))
        elif depth == 1 and name == NS_OWL + SEP + "Ontology":
            in_ontology[0] = True
        elif depth == 2 and current[0] is not None:
            if name == NS_RDFS + SEP + "subClassOf" and attrs.get(RESOURCE):
                current[0].parents.append(attrs[RESOURCE])
                if attrs[RESOURCE] == NS_OWL + "DeprecatedClass":
                    current[0].deprecated = True
            elif name == NS_OBO + SEP + "inSubset" and attrs.get(RESOURCE) == NS_EDAM + "obsolete":
                current[0].deprecated = True
        elif depth == 2 and in_ontology[0] and name == NS_DCTERMS + SEP + "license":
            onto.license = attrs.get(RESOURCE)

    def end(name: str) -> None:
        depth = len(stack) - 1
        value = "".join(text).strip()
        stack.pop()
        term = current[0]
        if depth == 2 and term is not None:
            if name == NS_RDFS + SEP + "label" and value:
                term.labels.append(value)
            elif name == NS_OBO + SEP + "hasExactSynonym" and value:
                term.synonyms.append(value)
            elif name == NS_OWL + SEP + "deprecated" and value.lower() == "true":
                term.deprecated = True
        elif depth == 2 and in_ontology[0] and name == NS_OWL + SEP + "versionInfo":
            onto.version_info = value
        elif depth == 1:
            current[0] = None
            in_ontology[0] = False
        text.clear()

    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = text.append
    try:
        parser.Parse(data, True)
    except expat.ExpatError as exc:
        raise SubsetError(f"EDAM.owl: not well-formed XML ({expat.ErrorString(exc.code)}, line {exc.lineno})") from None
    if stack:
        raise SubsetError("EDAM.owl: ended inside an element")
    return onto


def short(uri: str) -> str:
    return uri[len(NS_EDAM):] if uri.startswith(NS_EDAM) else uri


def branch_of(uri: str) -> str | None:
    name = short(uri)
    prefix, _, number = name.partition("_")
    return prefix if uri.startswith(NS_EDAM) and prefix in BRANCHES and number.isdigit() and len(number) == 4 else None


def ancestors(onto: Ontology, uri: str, branch: str) -> tuple[list[str] | None, str | None]:
    """Every named ancestor up to a root, or (None, reason) for a missing, cross-branch, cyclic or overlong chain."""
    def parents(node: str) -> list[str]:
        return [p for p in onto.terms[node].parents if p.startswith(NS_EDAM)]

    found: set[str] = set()
    state = {uri: 1}  # 1: on the current path, 2: finished (a shared ancestor is walked once)
    stack = [(uri, iter(parents(uri)))]
    while stack:
        node, pending = stack[-1]
        for parent in pending:
            if parent not in onto.terms:
                return None, "ancestor_missing"
            if branch_of(parent) != branch:
                return None, "wrong_branch"
            if state.get(parent) == 1:
                return None, "ancestor_cycle"
            if state.get(parent) == 2:
                continue
            found.add(parent)
            if len(stack) >= MAX_DEPTH or len(found) > MAX_ANCESTORS:
                return None, "ancestor_limit"
            state[parent] = 1
            stack.append((parent, iter(parents(parent))))
            break
        else:
            state[node] = 2
            stack.pop()
    return sorted(short(a) for a in found), None


def resolve(onto: Ontology, branch: str, label: str | None) -> dict[str, Any]:
    if not label:
        return {"branch": branch, "id": "unknown", "reason": "no_candidate", "candidate": None}
    matching = sorted(u for u, t in onto.terms.items() if label in t.labels or label in t.synonyms)
    in_branch = [u for u in matching if branch_of(u) == branch]
    live = [u for u in in_branch if not onto.terms[u].deprecated]
    reason = (("wrong_branch" if matching else "no_match") if not in_branch else
              "deprecated" if not live else "ambiguous" if len(live) > 1 else None)
    if reason:
        return {"branch": branch, "id": "unknown", "reason": reason, "candidate": label}
    (uri,) = live
    chain, problem = ancestors(onto, uri, branch)
    if problem:
        return {"branch": branch, "id": "unknown", "reason": problem, "candidate": label}
    term = onto.terms[uri]
    return {"branch": branch, "id": short(uri), "label": term.labels[0] if term.labels else label,
            "ancestors": chain}


def load_map(path: Path = MAP) -> tuple[dict[str, str | None], str]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, Mapping) or data.get("version") != 1 or not isinstance(data.get("candidates"), Mapping):
        raise SubsetError(f"{path.name}: needs version: 1 and a candidates mapping")
    candidates = {str(k): (None if v is None else str(v)) for k, v in data["candidates"].items()}
    return candidates, canonical_sha(candidates)


def local_branches(path: Path = LOCAL) -> dict[str, str]:
    terms = yaml.safe_load(path.read_text(encoding="utf-8"))["terms"]
    return {k: v["branch"] for k, v in terms.items()}


def script_sha() -> str:
    return hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def build(owl: bytes, *, source_sha: str, expect_sha: str = SHA256, map_path: Path = MAP,
          local_path: Path = LOCAL) -> str:
    """The subset file's text: deterministic for the same OWL bytes, map meaning and script."""
    if source_sha != expect_sha:
        raise SubsetError("EDAM.owl: SHA-256 differs from the pinned release asset; refused")
    onto = parse_owl(owl)
    if onto.version_info != VERSION_INFO:
        raise SubsetError(f"EDAM.owl: versionInfo {onto.version_info!r} is not {VERSION_INFO}")
    if onto.license != LICENSE:
        raise SubsetError(f"EDAM.owl: license {onto.license!r} is not {LICENSE}")
    candidates, map_sha = load_map(map_path)
    branches = local_branches(local_path)
    if set(candidates) != set(branches):
        raise SubsetError(f"{map_path.name}: keys differ from {local_path.name}: "
                          f"{sorted(set(candidates) ^ set(branches))}")
    if len(branches) > MAX_KEYS:
        raise SubsetError(f"{len(branches)} keys; at most {MAX_KEYS}")
    terms = {key: resolve(onto, branches[key], candidates[key]) for key in branches}
    ids = sorted({t["id"] for t in terms.values() if t["id"] != "unknown"})
    if len(ids) > MAX_KEYS:
        raise SubsetError(f"{len(ids)} distinct EDAM terms; at most {MAX_KEYS}")
    body = {
        "source": {"url": URL, "release": RELEASE, "version_info": VERSION_INFO, "sha256": source_sha,
                   "license": LICENSE},
        "generator": {"script": "scripts/edam_subset.py", "script_sha256": script_sha(), "map_sha256": map_sha},
        "counts": {"keys": len(terms), "edam_terms": len(ids),
                   "unknown": sum(1 for t in terms.values() if t["id"] == "unknown")},
        "terms": terms,
    }
    head = ("# Generated by scripts/edam_subset.py from the EDAM release named under source. Do not edit by hand.\n"
            "# EDAM content (ids, labels): CC BY-SA 4.0, see NOTICE.md. Rerun the script after editing edam_map.yaml.\n")
    return head + yaml.safe_dump(body, sort_keys=False, allow_unicode=True, width=120)


def fetch(target: Path) -> Path:
    path = target / "EDAM.owl"
    with urllib.request.urlopen(URL, timeout=60) as response, open(path, "wb") as out:  # noqa: S310 - pinned https
        read = 0
        while chunk := response.read(1 << 20):
            read += len(chunk)
            if read > MAX_BYTES:
                raise SubsetError(f"EDAM.owl: larger than {MAX_BYTES} bytes")
            out.write(chunk)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--owl", help="a local copy of the pinned release's EDAM.owl")
    source.add_argument("--fetch", action="store_true", help="download the pinned asset to a temporary folder")
    parser.add_argument("--check", action="store_true", help="compare with the committed subset; write nothing")
    args = parser.parse_args(argv)
    try:
        with tempfile.TemporaryDirectory(prefix="labhq-edam-") as tmp:
            path = fetch(Path(tmp)) if args.fetch else Path(args.owl)
            if path.stat().st_size > MAX_BYTES:
                raise SubsetError(f"EDAM.owl: larger than {MAX_BYTES} bytes")
            text = build(path.read_bytes(), source_sha=sha256_file(path))
    except SubsetError as exc:
        print(f"edam_subset: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:  # a missing file or a failed download: the reason, never a traceback or a local path
        print(f"edam_subset: cannot read the release file ({type(exc).__name__})", file=sys.stderr)
        return 1
    if args.check:
        same = SUBSET.exists() and SUBSET.read_text(encoding="utf-8").replace("\r\n", "\n") == text
        print("edam_subset: committed subset matches" if same else "edam_subset: committed subset differs")
        return 0 if same else 1
    SUBSET.write_text(text, encoding="utf-8", newline="\n")
    counts = yaml.safe_load(text)["counts"]
    print(f"edam_subset: wrote {SUBSET.relative_to(ROOT).as_posix()} ({counts})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
