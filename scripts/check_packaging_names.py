"""Checks the names in the Homebrew formula and the asdf plugin against the package.

Why. The Homebrew formula once carried the line

    assert_path_exists bin/"ouroboros-mcp-router"

The package never had a command by that name. The line looked like a check and
checked nothing: execution never reached it, and to the eye it reads as entirely
plausible. Command names are declared in exactly one place — `[project.scripts]`
in `pyproject.toml` — and subcommands in the parser in `ouroboros/cli.py`.
Everything else that names a command has to agree with those, and it is a machine
that verifies it.

What is not here. The installation itself: it needs Homebrew, asdf and the
network, and it is verified by actually running it, not by this check (see
`docs/install.md`). Here there are only the names — that is, precisely the kind of
mistake that survives any run until execution finally reaches it.

    uv run python scripts/check_packaging_names.py
"""
from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FORMULA = ROOT / "packaging" / "homebrew" / "ouroboros.rb"
ASDF_INSTALL = ROOT / "packaging" / "asdf" / "bin" / "install"


def _pyproject() -> dict[str, Any]:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _scripts() -> set[str]:
    """The command names the package actually installs into bin/."""
    return set(_pyproject()["project"]["scripts"])


def _subcommands() -> set[str]:
    """The subcommands `ouroboros` actually understands."""
    from ouroboros import cli

    names: set[str] = set()
    # SLF001: the list of subcommands lives in the parser and nowhere else. Asking
    # the parser is the point — a second list here is the drift this file exists
    # to catch.
    for action in cli._build_parser()._actions:  # noqa: SLF001
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            names.update(choices)
    return names


def _formula_body() -> str:
    """The body of the formula with the comment lines removed.

    The comments are dropped on purpose: in them the formula also talks about
    things the package does not have — the invented name above, for one. A line
    inside caveats that starts with a hash would be dropped as well; there are
    none right now, and if any appear the check will skip them, but it will not
    lie.
    """
    lines = FORMULA.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


# --------------------------------------------------------------------------- #
# The rules. Each takes its evidence as an argument and answers with a list of
# complaints — no file is opened inside one, so a rule can be handed a formula
# that does not exist anywhere and asked what it thinks of it. That is what lets
# the negative control (tests/test_check_packaging_names.py) prove each rule
# goes red, which a rule that reads the tree itself cannot be made to do.
# --------------------------------------------------------------------------- #

#: bin/"ouroboros" — but not opt_bin/"python3.12", not libexec/"bin/python".
COMMAND_PATTERNS = (r'(?<![A-Za-z_])bin/"([^"]+)"', r"#\{bin\}/([A-Za-z0-9._-]+)")


def rule_commands_installed(body: str, scripts: set[str]) -> list[str]:
    """1. Every name the formula calls as an installed command is one."""

    problems: list[str] = []
    for pattern in COMMAND_PATTERNS:
        found = set(re.findall(pattern, body))
        if not found:
            problems.append(f"the formula has no name matching {pattern!r} — the "
                            "pattern is out of date and the check has stopped "
                            "checking anything")
            continue
        problems += [f"the formula calls the command {name!r}, which the "
                     f"package does not install; it installs only {sorted(scripts)}"
                     for name in sorted(found - scripts)]
    return problems


def rule_commands_exposed(body: str, scripts: set[str]) -> list[str]:
    """2. Every command of the package reaches PATH.

    The formula exposes them with the pattern Dir[libexec/"bin/<prefix>*"], so a
    command whose name matches no prefix is installed and unreachable.
    """

    globs = re.findall(r'Dir\[libexec/"bin/([^"]*)\*"\]', body)
    if not globs:
        return ['the formula has no bin.install_symlink Dir[libexec/"bin/…*"] line']
    return [f"the command {name!r} matches none of the patterns {globs} — after "
            f"installation it will not be on PATH"
            for name in sorted(scripts)
            if not any(name.startswith(g) for g in globs)]


def rule_subcommands(body: str, known: set[str]) -> list[str]:
    """3. The subcommands the formula calls in `test do` and suggests in caveats."""

    used = set(re.findall(r"\bouroboros ([a-z][a-z-]+)\b", body))
    used |= set(re.findall(r'bin/"ouroboros",\s*"([a-z][a-z-]+)"', body))
    if not used:
        return ["the formula calls no subcommand at all — the pattern is out of date"]
    return [f"the formula calls the subcommand {name!r}, which does not exist; "
            f"the ones that do: {sorted(known)}"
            for name in sorted(used - known)]


def rule_mcp_command(body: str, scripts: set[str]) -> list[str]:
    """4. The command name in the MCP server configuration the caveats print."""

    commands = set(re.findall(r'"command":\s*"([A-Za-z0-9._-]+)"', body))
    if not commands:
        return ["the caveats hold no MCP configuration with a command field"]
    return [f"the MCP configuration names {name!r}, which the package does not have"
            for name in sorted(commands - scripts)]


def rule_archive_tag(body: str, version: str) -> list[str]:
    """5. The tag in the archive URL is the package version."""

    tags = set(re.findall(r"/tags/v([0-9][0-9A-Za-z.]*)\.tar\.gz", body))
    if not tags:
        return ["the formula has no archive URL with a version tag"]
    if tags != {version}:
        return [f"the formula pulls version {sorted(tags)}, while the package "
                f"is now {version}"]
    return []


def rule_asdf_listing(text: str, scripts: set[str]) -> list[str]:
    """6. The asdf plugin exposes exactly the package commands.

    Its list is explicit because the environment also receives the dependencies'
    commands (httpx, uvicorn, dotenv) and asdf would make a shim for every one.
    """

    listing = re.search(r"for name in ([^;\n]+); do", text)
    if not listing:
        return ["packaging/asdf/bin/install has no list of names to expose"]
    listed = set(listing.group(1).split())
    if listed != scripts:
        return [f"the plugin exposes {sorted(listed)}, while the package "
                f"installs {sorted(scripts)}"]
    return []


def rule_asdf_shims(root: Path) -> list[str]:
    """7. asdf expects bin/ at the repository root: three files, present and executable.

    The only rule that reads the tree, because the thing it checks IS the tree:
    `asdf plugin add` clones the repository and runs these three by path.
    """

    problems: list[str] = []
    for name in ("download", "install", "list-all"):
        shim = root / "bin" / name
        real = root / "packaging" / "asdf" / "bin" / name
        for path in (shim, real):
            if not path.is_file():
                problems.append(f"no such file: {path.relative_to(root)}")
            elif not path.stat().st_mode & 0o111:
                problems.append(f"{path.relative_to(root)} is not executable — asdf "
                                f"will not call it")
        if shim.is_file() and f"packaging/asdf/bin/{name}" not in shim.read_text(
                encoding="utf-8"):
            problems.append(f"bin/{name} does not hand the work to "
                            f"packaging/asdf/bin/{name}")
    return problems


def main() -> int:
    scripts = _scripts()
    known = _subcommands()
    version: str = _pyproject()["project"]["version"]
    body = _formula_body()
    asdf = ASDF_INSTALL.read_text(encoding="utf-8")

    problems = [
        *rule_commands_installed(body, scripts),
        *rule_commands_exposed(body, scripts),
        *rule_subcommands(body, known),
        *rule_mcp_command(body, scripts),
        *rule_archive_tag(body, version),
        *rule_asdf_listing(asdf, scripts),
        *rule_asdf_shims(ROOT),
    ]

    if problems:
        print("The names in the packaging parted ways with the package:\n")
        for b in problems:
            print(f"  - {b}")
        print("\nCommand names are declared in pyproject.toml ([project.scripts]), "
              "subcommands in ouroboros/cli.py.")
        return 1

    print(f"The packaging names agree with the package: commands {sorted(scripts)}, "
          f"{len(known)} subcommands, version {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
