"""Checks the release against what a person gets from outside — from the tap and from asdf.

Why. In the neighbouring project, flang, the tap fell behind releases 0.7.4-0.7.9: the
file `packaging/homebrew/flang.rb` in the tree kept being edited, and the copy in
`digitable-lol/homebrew-tap` kept not being pushed. All that time `brew install` silently
installed 0.7.3 and complained about nothing — because from `brew`'s point of view all was
well: the formula it looks at is sound. It had fallen behind, not broken. No check inside
the tree sees that: they all look at the tree, and a person does not install from the tree.

Hence the rule, the same one as in `scripts/check_pages_live.py`: **done is when it is
visible from outside**. This check asks the published thing, not the tree:

* the release archive — at the very address written in the formula;
* the formula — from `digitable-lol/homebrew-tap`, where `brew` takes it from;
* the repository tags — the same way `packaging/asdf/bin/list-all` reads them.

What is checked (each rule has a name; a refusal prints exactly what disagreed):

  1. `version in the package`  — `ouroboros.__version__` comes from the installation
                                 metadata rather than being written in as a literal.
                                 Written in as a literal, it stayed `0.1.0` for four
                                 releases running: nobody read the line, so nobody noticed
                                 it was lying. The rule complains even when the literal
                                 AGREES: the disagreement is the symptom, the cause is the
                                 second place the version lives in.
  2. `checksum not reused`     — the declared `sha256` was never declared in the history of
                                 the formula for any OTHER version. Two different archives
                                 are never byte-for-byte equal, so a repeat is proof that
                                 the checksum was not recomputed. The evidence sits in git
                                 and cannot be forged after the fact. That is exactly how
                                 flang v0.4.8 raised the version and the URL while the
                                 `sha256` stayed behind from v0.4.7.
  3. `executable bits`         — `bin/*` and `packaging/asdf/bin/*` have mode 100755 in
                                 git. `asdf plugin add` CLONES the repository, i.e. takes
                                 the modes from git rather than from somebody's working
                                 copy; without the bit `asdf` will not run the plugin at
                                 all.
  4. `release tag exists`      — the package version is among the repository tags. No tag
                                 means no archive for `brew` and no version in
                                 `asdf list all`.
  5. `checksum verified`       — the formula's `sha256` equals the checksum of the archive
                                 that lies at the formula's own address. It is downloaded
                                 and hashed right here.
  6. `tap published`           — the formula in `digitable-lol/homebrew-tap` is
                                 byte-for-byte equal to `packaging/homebrew/ouroboros.rb`.
                                 It is a COPY there; there is no other relation between
                                 these two files.
  7. `tap not stale`           — the version in the published formula's URL equals the
                                 package version. This is the flang landmine itself, named
                                 apart from rule 6: the sixth shows that the copies have
                                 drifted, the seventh that the drift costs a person a stale
                                 installation.

Exit codes: 0 — outside matches the tree; 1 — they disagree; 2 — unreachable (the check did
NOT happen, and that is not the same as "all is well").

Run::

    uv run python scripts/check_release_published.py              # ask the outside
    uv run python scripts/check_release_published.py --offline    # only what the tree shows
    uv run python scripts/check_release_published.py --self-test  # check the check itself

`--self-test` exists because a check that catches nothing looks exactly like a check that
found nothing wrong. It feeds every rule knowingly broken evidence and demands that the
rule go red; and it feeds every rule sound evidence and demands that they all stay silent.
A rule that stays silent on breakage is broken, and `--self-test` refuses just as the check
itself would have.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORMULA = ROOT / "packaging" / "homebrew" / "ouroboros.rb"

#: The tap `brew` takes the formula from. The file in the tree is the source, this
#: one is the copy; `brew` looks only at the copy.
TAP_REPO = "digitable-lol/homebrew-tap"
TAP_PATH = "Formula/ouroboros.rb"

#: The package's own repository: its tags are the versions, and both `brew` and
#: `asdf` install its archives.
REPO_URL = "https://github.com/digitable-lol/ouroboros.git"

#: The files `asdf` calls after cloning; without the executable bit it calls none.
ASDF_SCRIPTS = (
    "bin/download",
    "bin/install",
    "bin/list-all",
    "packaging/asdf/bin/download",
    "packaging/asdf/bin/install",
    "packaging/asdf/bin/list-all",
)

#: `url "…/archive/refs/tags/v1.2.3.tar.gz"` — the version inside the URL.
URL_TAG = re.compile(r"/archive/refs/tags/v([0-9][0-9A-Za-z.]*)\.tar\.gz")

#: `sha256 "…"` at the start of a line (the formula indents with spaces), not the
#: word inside a comment.
SHA_LINE = re.compile(r"^\s*sha256\s+\"([0-9a-f]{64})\"", re.MULTILINE)

#: `url "…"` at the start of a line.
URL_LINE = re.compile(r"^\s*url\s+\"([^\"]+)\"", re.MULTILINE)

TIMEOUT = 60


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Evidence:
    """Everything the rules look at. Deliberately separated from the collecting.

    A rule goes neither to the network nor to git: it receives finished evidence
    and answers with a list of complaints. That is what lets `--self-test` hand it
    broken evidence and confirm that it goes red on it, without touching the
    network or the repository.

    A field left as `None` means "not asked, or not reachable". On such a field a
    rule STAYS SILENT and `main` returns 2: not checked is not "all is well".
    """

    #: `version` from `pyproject.toml` — the one place a version is declared.
    version: str
    #: Where `ouroboros.__version__` gets its number: ("metadata"|"literal"|"none", value).
    version_source: tuple[str, str]
    #: The bytes of `packaging/homebrew/ouroboros.rb` in the tree.
    formula: bytes
    #: The version and the checksum read out of the tree's formula.
    formula_tag: str
    formula_sha: str
    #: Checksum -> the versions it was declared for in the formula's history.
    sha_history: dict[str, set[str]] = field(default_factory=dict)
    #: Path -> mode in the git index (`100755` / `100644`).
    git_modes: dict[str, str] = field(default_factory=dict)
    #: The repository tags, without the leading `v`.
    tags: list[str] | None = None
    #: The checksum of the archive downloaded from the formula's URL.
    archive_sha: str | None = None
    #: The bytes of the formula published in the tap.
    published: bytes | None = None


Rule = Callable[[Evidence], list[str]]


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #


def rule_package_version(e: Evidence) -> list[str]:
    """1. The package knows its version, and it is not written in as a literal twice.

    The rule complains even when the literal AGREES with the current version. That
    is deliberate: the disagreement is the symptom, the cause is that the number
    lives in two places and is kept in step by nothing but somebody's attention.
    That is where it went wrong: `__version__` stayed `0.1.0` for four releases
    running, because nobody read it.
    """

    kind, value = e.version_source
    if kind == "metadata":
        return []
    if kind == "none":
        return [
            "ouroboros/__init__.py declares no __version__ — the installed package "
            "cannot name its own version, and a person cannot find out what they have"
        ]
    if value != e.version:
        return [
            f"the package is written in as {value!r} while {e.version!r} is being "
            "released — ouroboros/__init__.py parted ways with pyproject.toml"
        ]
    return [
        f"the version is written into ouroboros/__init__.py as a literal ({value}). It "
        "agrees with pyproject.toml right now, but nothing holds it there except "
        "attention, and once it already failed to hold. Read it from the installation "
        'metadata instead: importlib.metadata.version("ouroboros-logger")'
    ]


def rule_sha_not_reused(e: Evidence) -> list[str]:
    """2. The declared checksum was never declared for any other version."""

    others = sorted(e.sha_history.get(e.formula_sha, set()) - {e.formula_tag})
    if not others:
        return []
    return [
        f"sha256 {e.formula_sha[:16]}… is declared for version {e.formula_tag}, and in "
        f"the formula's history it was already declared for {others} — so it was not "
        "recomputed: two different archives are never byte-for-byte equal. Run "
        "`shasum -a 256` on the archive at the URL in the formula and write that in"
    ]


def rule_exec_bits(e: Evidence) -> list[str]:
    """3. `asdf` clones the repository, so the executable bit comes from git."""

    bad = []
    for path in ASDF_SCRIPTS:
        mode = e.git_modes.get(path)
        if mode is None:
            bad.append(f"{path}: not in git, while asdf expects it in the clone")
        elif mode != "100755":
            bad.append(
                f"{path}: mode {mode} in git, 100755 is needed — asdf clones the "
                "repository and takes the mode from there, so the plugin will not run. "
                "Fix with: git update-index --chmod=+x " + path
            )
    return bad


def rule_tag_exists(e: Evidence) -> list[str]:
    """4. The package version is among the tags: otherwise there is nothing to install."""

    if e.tags is None:
        return []
    if e.version in e.tags:
        return []
    return [
        f"version {e.version} is not among the repository tags (there are {e.tags[-5:]}) "
        f"— which means there is no archive for brew and no {e.version} line in "
        f"`asdf list all ouroboros`. Either the release has not been made yet (a tag is "
        "needed), or the version in pyproject.toml has run ahead"
    ]


def rule_archive_matches(e: Evidence) -> list[str]:
    """5. The formula's checksum belongs to the archive the formula points at."""

    if e.archive_sha is None:
        return []
    if e.archive_sha == e.formula_sha:
        return []
    return [
        f"sha256 in the formula is {e.formula_sha}, while the archive at the formula's "
        f"own URL has {e.archive_sha} — brew will refuse at the checksum check; write "
        "the second one in"
    ]


def rule_tap_published(e: Evidence) -> list[str]:
    """6. The published formula is a byte-for-byte copy of the one in the tree."""

    if e.published is None:
        return []
    if e.published == e.formula:
        return []
    mine = hashlib.sha256(e.formula).hexdigest()[:16]
    theirs = hashlib.sha256(e.published).hexdigest()[:16]
    lines_mine = e.formula.decode("utf-8", "replace").splitlines()
    lines_theirs = e.published.decode("utf-8", "replace").splitlines()
    where = "in length"
    for n, (a, b) in enumerate(zip(lines_mine, lines_theirs, strict=False), start=1):
        if a != b:
            where = (f"from line {n} on: the tree has {a.strip()!r}, "
                     f"the tap has {b.strip()!r}")
            break
    return [
        f"the formula in {TAP_REPO} parted ways with the tree ({mine}… against "
        f"{theirs}…), {where}. It is a copy there: copy "
        f"{FORMULA.relative_to(ROOT)} over {TAP_PATH} and push"
    ]


def rule_tap_not_stale(e: Evidence) -> list[str]:
    """7. The landmine itself: the tap is behind and `brew install` silently
    installs the old thing."""

    if e.published is None:
        return []
    tags = URL_TAG.findall(e.published.decode("utf-8", "replace"))
    if not tags:
        return [
            f"the published formula ({TAP_REPO}) has no archive URL with a version tag "
            "— the check cannot say what exactly brew installs"
        ]
    if set(tags) == {e.version}:
        return []
    return [
        f"brew installs {sorted(set(tags))} while the current release is {e.version}: the "
        f"tap {TAP_REPO} is behind. Silently — brew does not consider this an error, the "
        f"formula being sound. That is exactly how flang kept handing out 0.7.3 while "
        f"0.7.4-0.7.9 were released. Copy {FORMULA.relative_to(ROOT)} over {TAP_PATH} "
        f"and push"
    ]


#: The rules in order. The first three make do with the tree, the rest ask outside.
OFFLINE_RULES: tuple[tuple[str, Rule], ...] = (
    ("version in the package", rule_package_version),
    ("checksum not reused", rule_sha_not_reused),
    ("executable bits", rule_exec_bits),
)

ONLINE_RULES: tuple[tuple[str, Rule], ...] = (
    ("release tag exists", rule_tag_exists),
    ("checksum verified", rule_archive_matches),
    ("tap published", rule_tap_published),
    ("tap not stale", rule_tap_not_stale),
)

ALL_RULES = OFFLINE_RULES + ONLINE_RULES


# --------------------------------------------------------------------------- #
# collecting the evidence: the tree
# --------------------------------------------------------------------------- #


def _git(*args: str) -> str:
    """git inside the project tree. Nobody else's configuration may affect this."""

    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    return subprocess.run(
        ["git", *args], cwd=ROOT, env=env, check=True,
        capture_output=True, text=True,
    ).stdout


def pyproject_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        version = tomllib.load(fh)["project"]["version"]
    return str(version)


def version_source() -> tuple[str, str]:
    """How the package learns its version: from metadata, from a literal, or not at all.

    The source is read rather than imported: importing would reach the INSTALLED
    package, while the one under check is in the tree — an edit in the tree has not
    reached the installation yet.
    """

    text = (ROOT / "ouroboros" / "__init__.py").read_text(encoding="utf-8")
    if not re.search(r"^\s*__version__\s*=", text, re.MULTILINE):
        return ("none", "")
    if "importlib.metadata" in text and re.search(
            r"^\s*__version__\s*=\s*_?\w*version\(", text, re.MULTILINE):
        return ("metadata", "")
    for value in re.findall(r"^\s*__version__\s*=\s*\"([^\"]+)\"", text, re.MULTILINE):
        if re.match(r"^\d+\.\d+", value):
            return ("literal", value)
    return ("metadata", "")


def formula_fields(text: str) -> tuple[str, str, str]:
    """The URL, the version inside it and the checksum — from the formula body,
    skipping the comments."""

    urls = URL_LINE.findall(text)
    shas = SHA_LINE.findall(text)
    if not urls:
        raise SystemExit(f"{FORMULA}: no url declaration — the check's pattern is "
                         f"out of date")
    if not shas:
        raise SystemExit(f"{FORMULA}: no sha256 declaration — the check's pattern is "
                         f"out of date")
    tags = URL_TAG.findall(urls[0])
    if not tags:
        raise SystemExit(f"{FORMULA}: the URL {urls[0]!r} carries no version tag")
    return urls[0], tags[0], shas[0]


def sha_history() -> dict[str, set[str]]:
    """Which checksum was declared for which version, from the formula's own history."""

    history: dict[str, set[str]] = {}
    rel = str(FORMULA.relative_to(ROOT))
    try:
        commits = _git("log", "--format=%H", "--", rel).split()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return history
    for commit in commits:
        try:
            text = _git("show", f"{commit}:{rel}")
        except subprocess.CalledProcessError:
            continue
        urls = URL_LINE.findall(text)
        shas = SHA_LINE.findall(text)
        if not urls or not shas:
            continue
        tags = URL_TAG.findall(urls[0])
        if tags:
            history.setdefault(shas[0], set()).add(tags[0])
    return history


def git_modes() -> dict[str, str]:
    try:
        out = _git("ls-files", "-s", "--", *ASDF_SCRIPTS)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    modes: dict[str, str] = {}
    for line in out.splitlines():
        head, _, path = line.partition("\t")
        parts = head.split()
        if parts and path:
            modes[path] = parts[0]
    return modes


# --------------------------------------------------------------------------- #
# collecting the evidence: what is visible from outside
# --------------------------------------------------------------------------- #


class UnreachableError(Exception):
    """The published thing could not be reached. Neither "all is well" nor "they differ"."""


def fetch(url: str, accept: str | None = None) -> bytes:
    headers = {"User-Agent": "ouroboros-release-check"}
    if accept:
        headers["Accept"] = accept
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body: bytes = response.read()
            return body
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise UnreachableError(f"{url}: {e.code} — GitHub is rate-limiting us") from e
        raise UnreachableError(f"{url}: answered {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise UnreachableError(f"{url}: {e}") from e


def remote_tags() -> list[str]:
    """The repository tags — the same way the asdf plugin reads them."""

    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", REPO_URL],
            env=env, check=True, capture_output=True, text=True, timeout=TIMEOUT,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise UnreachableError(f"git ls-remote {REPO_URL}: {e}") from e
    versions = []
    for line in out.splitlines():
        name = line.rsplit("/", 1)[-1]
        if name.startswith("v"):
            versions.append(name[1:])
    return sorted(versions, key=lambda v: [int(p) for p in re.findall(r"\d+", v)])


def archive_sha(url: str) -> str:
    """The checksum of the archive a person will actually download."""

    return hashlib.sha256(fetch(url)).hexdigest()


def published_formula() -> bytes:
    """The formula from the tap — through the API, not through raw.

    `raw.githubusercontent.com` serves a cache for up to five minutes, so right
    after a push the check would lie in both directions. The API answers with the
    current tree.
    """

    url = f"https://api.github.com/repos/{TAP_REPO}/contents/{TAP_PATH}?ref=main"
    payload = json.loads(fetch(url, accept="application/vnd.github+json").decode("utf-8"))
    if payload.get("encoding") != "base64":
        raise UnreachableError(f"{url}: unexpected encoding {payload.get('encoding')!r}")
    return base64.b64decode(payload["content"])


def collect(*, online: bool) -> tuple[Evidence, list[str]]:
    """The evidence, plus a list of what could not be asked."""

    version = pyproject_version()
    formula_bytes = FORMULA.read_bytes()
    url, tag, sha = formula_fields(formula_bytes.decode("utf-8"))
    evidence = Evidence(
        version=version,
        version_source=version_source(),
        formula=formula_bytes,
        formula_tag=tag,
        formula_sha=sha,
        sha_history=sha_history(),
        git_modes=git_modes(),
    )
    if not online:
        return evidence, []

    missed: list[str] = []
    try:
        evidence = replace(evidence, tags=remote_tags())
    except UnreachableError as e:
        missed.append(f"repository tags: {e}")
    try:
        evidence = replace(evidence, archive_sha=archive_sha(url))
    except UnreachableError as e:
        missed.append(f"release archive: {e}")
    try:
        evidence = replace(evidence, published=published_formula())
    except UnreachableError as e:
        missed.append(f"the formula from {TAP_REPO}: {e}")
    return evidence, missed


# --------------------------------------------------------------------------- #
# checking the check itself
# --------------------------------------------------------------------------- #


def sound_evidence() -> Evidence:
    """Knowingly sound evidence: every rule must stay silent on it."""

    formula = (
        b'class Ouroboros < Formula\n'
        b'  url "https://github.com/digitable-lol/ouroboros/archive/refs/tags/v9.9.9.tar.gz"\n'
        b'  sha256 "' + b"a" * 64 + b'"\n'
        b'end\n'
    )
    return Evidence(
        version="9.9.9",
        version_source=("metadata", ""),
        formula=formula,
        formula_tag="9.9.9",
        formula_sha="a" * 64,
        sha_history={"a" * 64: {"9.9.9"}},
        git_modes=dict.fromkeys(ASDF_SCRIPTS, "100755"),
        tags=["9.9.8", "9.9.9"],
        archive_sha="a" * 64,
        published=formula,
    )


def spoiled() -> list[tuple[str, Evidence, set[str]]]:
    """One breakage per rule: what was broken, the evidence, and WHO must complain.

    Usually exactly one rule is expected. The stale tap is the exception, and a
    genuine one: it both differs from the tree byte for byte and hands out the
    wrong version. Both rules tell the truth about it, so both are listed here. If
    the list demanded "exactly one rule", the negative control would be demanding
    that the check lie.
    """

    ok = sound_evidence()
    stale_formula = ok.formula.replace(b"v9.9.9.tar.gz", b"v9.9.8.tar.gz")
    return [
        ("version in the package: a literal, and it disagrees",
         replace(ok, version_source=("literal", "0.1.0")), {"version in the package"}),
        ("version in the package: a literal that agrees for now",
         replace(ok, version_source=("literal", "9.9.9")), {"version in the package"}),
        ("version in the package: the package does not declare it",
         replace(ok, version_source=("none", "")), {"version in the package"}),
        ("checksum not reused", replace(ok, sha_history={"a" * 64: {"9.9.9", "9.9.8"}}),
         {"checksum not reused"}),
        ("executable bits", replace(ok, git_modes={**ok.git_modes, "bin/install": "100644"}),
         {"executable bits"}),
        ("release tag exists", replace(ok, tags=["9.9.7", "9.9.8"]),
         {"release tag exists"}),
        ("checksum verified", replace(ok, archive_sha="b" * 64),
         {"checksum verified"}),
        ("tap published", replace(ok, published=ok.formula + b"# one extra line\n"),
         {"tap published"}),
        ("tap not stale", replace(ok, published=stale_formula),
         {"tap published", "tap not stale"}),
    ]


def self_test() -> int:
    """The negative control: every rule must go red on its own breakage."""

    bad: list[str] = []

    ok = sound_evidence()
    for name, rule in ALL_RULES:
        complaints = rule(ok)
        if complaints:
            bad.append(f"{name}: complains about sound evidence — {complaints[0]}")

    for name, evidence, expected in spoiled():
        fired = {rule_name for rule_name, rule in ALL_RULES if rule(evidence)}
        for missing in sorted(expected - fired):
            bad.append(f"breakage \u2018{name}\u2019: the rule \u2018{missing}\u2019 "
                       "did NOT notice it — it checks nothing")
        # A rule that complains about somebody else's breakage names the wrong cause
        # in the refusal, and a person goes off to fix the wrong thing.
        for extra in sorted(fired - expected):
            bad.append(f"breakage \u2018{name}\u2019: \u2018{extra}\u2019 complains "
                       "and should not — the refusal would name the wrong cause")

    # Not asked means silent, not green: on empty fields the rules are mute.
    silent = replace(ok, tags=None, archive_sha=None, published=None)
    for name, rule in ONLINE_RULES:
        if rule(silent):
            bad.append(f"{name}: judges by what it never asked about")

    if bad:
        print("The check is broken:\n", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        return 1

    print(f"The check is checked: {len(ALL_RULES)} rules, each goes red on its own "
          f"breakage, stays silent on somebody else's and on what was never asked.")
    return 0


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--offline", action="store_true",
                        help="only the rules that make do with the tree")
    parser.add_argument("--self-test", action="store_true", dest="self_test",
                        help="check the rules themselves against known breakage")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    evidence, missed = collect(online=not args.offline)
    rules = OFFLINE_RULES if args.offline else ALL_RULES

    bad: list[str] = []
    for name, rule in rules:
        for complaint in rule(evidence):
            bad.append(f"  [{name}] {complaint}")

    if bad:
        print(f"The release parted ways with what is visible from outside "
              f"(package version {evidence.version}):\n", file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        if missed:
            print("\nAnd these were not asked either:", file=sys.stderr)
            for m in missed:
                print(f"  - {m}", file=sys.stderr)
        return 1

    if missed:
        print("The check did NOT happen — the published thing was unreachable:",
              file=sys.stderr)
        for m in missed:
            print(f"  - {m}", file=sys.stderr)
        print("That is not the same as 'everything outside is in order'.", file=sys.stderr)
        return 2

    checked = len(rules)
    where = "in the tree" if args.offline else "outside"
    print(f"Release {evidence.version} agrees {where}: {checked} rules checked, "
          f"no disagreements.")
    return 0


if __name__ == "__main__":  # pragma: no cover — the entry point, not a rule
    raise SystemExit(main())
