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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FORMULA = ROOT / "packaging" / "homebrew" / "ouroboros.rb"
ASDF_INSTALL = ROOT / "packaging" / "asdf" / "bin" / "install"


def _pyproject() -> dict:
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _scripts() -> set[str]:
    """The command names the package actually installs into bin/."""
    return set(_pyproject()["project"]["scripts"])


def _subcommands() -> set[str]:
    """The subcommands `ouroboros` actually understands."""
    from ouroboros import cli

    names: set[str] = set()
    for action in cli._build_parser()._actions:
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


def main() -> int:
    problems: list[str] = []
    scripts = _scripts()
    body = _formula_body()

    # 1. Everything the formula refers to as an installed command.
    #    bin/"ouroboros" — but not opt_bin/"python3.12", not libexec/"bin/python".
    for pattern in (r'(?<![A-Za-z_])bin/"([^"]+)"', r'#\{bin\}/([A-Za-z0-9._-]+)'):
        found = set(re.findall(pattern, body))
        if not found:
            problems.append(f"the formula has no name matching {pattern!r} — the "
                            "pattern is out of date and the check has stopped "
                            "checking anything")
            continue
        for name in sorted(found - scripts):
            problems.append(f"the formula calls the command {name!r}, which the "
                            f"package does not install; it installs only "
                            f"{sorted(scripts)}")

    # 2. Every command of the package must be exposed: the formula exposes them
    #    with the pattern Dir[libexec/"bin/<prefix>*"].
    globs = re.findall(r'Dir\[libexec/"bin/([^"]*)\*"\]', body)
    if not globs:
        problems.append('the formula has no bin.install_symlink Dir[libexec/"bin/…*"] line')
    else:
        for name in sorted(scripts):
            if not any(name.startswith(g) for g in globs):
                problems.append(f"the command {name!r} matches none of the patterns "
                                f"{globs} — after installation it will not be on PATH")

    # 3. The subcommands the formula calls in test do and suggests in caveats.
    known = _subcommands()
    used = set(re.findall(r"\bouroboros ([a-z][a-z-]+)\b", body))
    used |= set(re.findall(r'bin/"ouroboros",\s*"([a-z][a-z-]+)"', body))
    if not used:
        problems.append("the formula calls no subcommand at all — the pattern is "
                        "out of date")
    for name in sorted(used - known):
        problems.append(f"the formula calls the subcommand {name!r}, which does not "
                        f"exist; the ones that do: {sorted(known)}")

    # 4. The command name in the MCP server configuration the caveats print.
    commands = set(re.findall(r'"command":\s*"([A-Za-z0-9._-]+)"', body))
    if not commands:
        problems.append("the caveats hold no MCP configuration with a command field")
    for name in sorted(commands - scripts):
        problems.append(f"the MCP configuration names {name!r}, which the package "
                        f"does not have")

    # 5. The tag in the archive URL is the package version.
    version = _pyproject()["project"]["version"]
    tags = set(re.findall(r"/tags/v([0-9][0-9A-Za-z.]*)\.tar\.gz", body))
    if not tags:
        problems.append("the formula has no archive URL with a version tag")
    elif tags != {version}:
        problems.append(f"the formula pulls version {sorted(tags)}, while the package "
                        f"is now {version}")

    # 6. The asdf plugin exposes exactly the package commands. Its list is
    #    explicit: the environment also receives the dependencies' commands
    #    (httpx, uvicorn, dotenv) and asdf would make a shim for every one.
    text = ASDF_INSTALL.read_text(encoding="utf-8")
    listing = re.search(r"for name in ([^;\n]+); do", text)
    if not listing:
        problems.append("packaging/asdf/bin/install has no list of names to expose")
    else:
        listed = set(listing.group(1).split())
        if listed != scripts:
            problems.append(f"the plugin exposes {sorted(listed)}, while the package "
                            f"installs {sorted(scripts)}")

    # 7. asdf expects bin/ at the repository root: three files, present and executable.
    for name in ("download", "install", "list-all"):
        shim = ROOT / "bin" / name
        real = ROOT / "packaging" / "asdf" / "bin" / name
        for path in (shim, real):
            if not path.is_file():
                problems.append(f"no such file: {path.relative_to(ROOT)}")
            elif not path.stat().st_mode & 0o111:
                problems.append(f"{path.relative_to(ROOT)} is not executable — asdf "
                                f"will not call it")
        if shim.is_file() and f"packaging/asdf/bin/{name}" not in shim.read_text(encoding="utf-8"):
            problems.append(f"bin/{name} does not hand the work to "
                            f"packaging/asdf/bin/{name}")

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
