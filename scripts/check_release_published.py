"""Сверяет выпуск с тем, что человек получает снаружи — из крана и из asdf.

Зачем. У соседнего проекта, flang, хранилище формул отстало от выпусков 0.7.4—0.7.9:
файл `packaging/homebrew/flang.rb` в дереве правили, а копию в `digitable-lol/homebrew-tap`
выложить забывали. Всё это время `brew install` молча ставил 0.7.3 и ни на что не
ругался — потому что с точки зрения `brew` всё было в порядке: формула, на которую он
смотрит, исправна. Отстала она, а не сломалась. Ни одна проверка в дереве такого не
видит: они все смотрят в дерево, а человек ставит не из дерева.

Отсюда правило, то же самое, что у `scripts/check_pages_live.py`: **сделано — это когда
видно снаружи**. Эта проверка спрашивает не дерево, а выложенное:

* архив выпуска — по тому самому адресу, который написан в формуле;
* формулу — из `digitable-lol/homebrew-tap`, откуда её берёт `brew`;
* теги хранилища — тем же способом, каким их читает `packaging/asdf/bin/list-all`.

Что проверяется (каждое правило названо; в отказе печатается ровно то, что разошлось):

  1. `версия в пакете`      — `ouroboros.__version__` берётся из метаданных установки, а
                              не вписан числом. Вписанный числом, он четыре выпуска подряд
                              оставался `0.1.0`: строку никто не читал, поэтому никто и не
                              заметил, что она врёт. Правило ругается и на СОВПАДАЮЩЕЕ
                              число: расхождение — следствие, а причина — второе место,
                              где версия живёт.
  2. `отпечаток не чужой`   — объявленный `sha256` не объявлялся в истории формулы ни
                              для какой ДРУГОЙ версии. Два разных архива побайтово равны
                              не бывают, значит повтор — доказательство, что отпечаток не
                              пересчитали. Улика лежит в git, задним числом её не подделать.
                              Ровно так у flang в v0.4.8 подняли версию и адрес, а `sha256`
                              остался от v0.4.7.
  3. `биты исполнимости`    — у `bin/*` и `packaging/asdf/bin/*` в git стоит режим 100755.
                              `asdf plugin add` КЛОНИРУЕТ хранилище, то есть берёт режимы
                              из git, а не с чьей-то рабочей копии; без бита `asdf` не
                              запустит плагин вовсе.
  4. `тег выпуска есть`     — версия пакета есть среди тегов хранилища. Нет тега — нет ни
                              архива для `brew`, ни версии в `asdf list all`.
  5. `отпечаток сверен`     — `sha256` формулы совпадает с отпечатком архива, который
                              лежит по её же адресу. Скачивается и считается здесь.
  6. `кран выложен`         — формула в `digitable-lol/homebrew-tap` побайтово равна
                              `packaging/homebrew/ouroboros.rb`. Она там КОПИЯ, других
                              отношений между этими файлами нет.
  7. `кран не отстал`       — версия в адресе выложенной формулы равна версии пакета.
                              Это и есть мина flang, названная отдельно от правила 6:
                              по шестому видно, что копии разошлись, а по седьмому — что
                              расхождение стоит человеку протухшей установки.

Коды возврата: 0 — снаружи то же, что в дереве; 1 — разошлось; 2 — не достучались
(проверка НЕ состоялась, и это не то же самое, что «всё хорошо»).

Запуск::

    uv run python scripts/check_release_published.py              # спросить снаружи
    uv run python scripts/check_release_published.py --offline    # только то, что видно в дереве
    uv run python scripts/check_release_published.py --self-test  # проверить саму проверку

`--self-test` нужен потому, что проверка, которая ничего не ловит, выглядит ровно как
проверка, которая всё прошла. Он скармливает каждому правилу заведомо испорченные улики
и требует, чтобы правило покраснело; и он же скармливает всем правилам исправные улики и
требует, чтобы все промолчали. Правило, которое молчит на порче, — сломано, и `--self-test`
отказывает так же, как отказала бы сама проверка.
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

#: Хранилище формул, из которого `brew` берёт формулу. Файл в дереве — источник,
#: этот — копия; `brew` смотрит только на копию.
TAP_REPO = "digitable-lol/homebrew-tap"
TAP_PATH = "Formula/ouroboros.rb"

#: Хранилище самого пакета: его теги — это версии, его архивы ставит и `brew`, и `asdf`.
REPO_URL = "https://github.com/digitable-lol/ouroboros.git"

#: Файлы, которые `asdf` зовёт после клонирования; без бита исполнимости не зовёт никак.
ASDF_SCRIPTS = (
    "bin/download",
    "bin/install",
    "bin/list-all",
    "packaging/asdf/bin/download",
    "packaging/asdf/bin/install",
    "packaging/asdf/bin/list-all",
)

#: `url "…/archive/refs/tags/v1.2.3.tar.gz"` — версия внутри адреса.
URL_TAG = re.compile(r"/archive/refs/tags/v([0-9][0-9A-Za-z.]*)\.tar\.gz")

#: `sha256 "…"` в начале строки (отступ формулы — пробелы), а не слово в примечании.
SHA_LINE = re.compile(r"^\s*sha256\s+\"([0-9a-f]{64})\"", re.MULTILINE)

#: `url "…"` в начале строки.
URL_LINE = re.compile(r"^\s*url\s+\"([^\"]+)\"", re.MULTILINE)

TIMEOUT = 60


# --------------------------------------------------------------------------- #
# улики
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Evidence:
    """Всё, на что смотрят правила. Отделено от сбора нарочно.

    Правило не ходит ни в сеть, ни в git: оно получает готовые улики и отвечает
    списком жалоб. Поэтому `--self-test` может подсунуть ему испорченные улики и
    убедиться, что оно на них краснеет, — не трогая ни сети, ни хранилища.

    Поля, которые остались `None`, означают «не спросили или не достучались».
    Правило на таком поле МОЛЧИТ, а `main` возвращает 2: не проверено — это не
    «всё хорошо».
    """

    #: `version` из `pyproject.toml` — единственное место, где заводится версия.
    version: str
    #: Откуда `ouroboros.__version__` берёт число: ("metadata"|"literal"|"none", значение).
    version_source: tuple[str, str]
    #: Байты `packaging/homebrew/ouroboros.rb` в дереве.
    formula: bytes
    #: Версия и отпечаток, вычитанные из формулы дерева.
    formula_tag: str
    formula_sha: str
    #: Отпечаток -> версии, для которых он объявлялся в истории формулы.
    sha_history: dict[str, set[str]] = field(default_factory=dict)
    #: Путь -> режим в индексе git (`100755` / `100644`).
    git_modes: dict[str, str] = field(default_factory=dict)
    #: Теги хранилища без ведущей `v`.
    tags: list[str] | None = None
    #: Отпечаток архива, скачанного по адресу из формулы.
    archive_sha: str | None = None
    #: Байты формулы, выложенной в хранилище формул.
    published: bytes | None = None


Rule = Callable[[Evidence], list[str]]


# --------------------------------------------------------------------------- #
# правила
# --------------------------------------------------------------------------- #


def rule_package_version(e: Evidence) -> list[str]:
    """1. Пакет знает свою версию, и она не вписана числом второй раз.

    Правило ругается и тогда, когда вписанное число СОВПАДАЕТ с нынешним. Это
    нарочно: расхождение — следствие, а причина — то, что число живёт в двух местах
    и сходится только чьим-то вниманием. Здесь оно и разошлось: `__version__`
    оставался `0.1.0` четыре выпуска подряд, потому что его никто не читал.
    """

    kind, value = e.version_source
    if kind == "metadata":
        return []
    if kind == "none":
        return [
            "ouroboros/__init__.py не объявляет __version__ — установленный пакет не "
            "умеет назвать свою версию, и человек не может узнать, что у него стоит"
        ]
    if value != e.version:
        return [
            f"пакет вписан числом {value!r}, а выпускается {e.version!r} — "
            "ouroboros/__init__.py разошёлся с pyproject.toml"
        ]
    return [
        f"версия вписана в ouroboros/__init__.py числом ({value}). Сейчас оно совпадает "
        "с pyproject.toml, но держится это только вниманием, и однажды уже не удержалось. "
        "Читайте её из метаданных установки: importlib.metadata.version(\"ouroboros-logger\")"
    ]


def rule_sha_not_reused(e: Evidence) -> list[str]:
    """2. Объявленный отпечаток не объявлялся ни для какой другой версии."""

    others = sorted(e.sha_history.get(e.formula_sha, set()) - {e.formula_tag})
    if not others:
        return []
    return [
        f"sha256 {e.formula_sha[:16]}… объявлен для версии {e.formula_tag}, а в истории "
        f"формулы он уже объявлялся для {others} — значит его не пересчитали: два разных "
        "архива побайтово равны не бывают"
    ]


def rule_exec_bits(e: Evidence) -> list[str]:
    """3. `asdf` клонирует хранилище, поэтому бит исполнимости берётся из git."""

    bad = []
    for path in ASDF_SCRIPTS:
        mode = e.git_modes.get(path)
        if mode is None:
            bad.append(f"{path}: файла нет в git, а asdf ждёт его в клоне")
        elif mode != "100755":
            bad.append(
                f"{path}: режим в git {mode}, а нужен 100755 — asdf клонирует хранилище "
                "и берёт режим оттуда, так что плагин не запустится"
            )
    return bad


def rule_tag_exists(e: Evidence) -> list[str]:
    """4. Версия пакета есть среди тегов: иначе ставить нечего."""

    if e.tags is None:
        return []
    if e.version in e.tags:
        return []
    return [
        f"версии {e.version} нет среди тегов хранилища (есть {e.tags[-5:]}) — значит нет "
        f"ни архива для brew, ни строки {e.version} в `asdf list all ouroboros`. "
        "Либо выпуск ещё не сделан (нужен тег), либо версия в pyproject.toml забежала вперёд"
    ]


def rule_archive_matches(e: Evidence) -> list[str]:
    """5. Отпечаток в формуле — от того архива, на который она показывает."""

    if e.archive_sha is None:
        return []
    if e.archive_sha == e.formula_sha:
        return []
    return [
        f"sha256 в формуле {e.formula_sha}, а у архива по её же адресу {e.archive_sha} — "
        "brew откажет на проверке отпечатка; впишите второй"
    ]


def rule_tap_published(e: Evidence) -> list[str]:
    """6. Выложенная формула — побайтовая копия той, что в дереве."""

    if e.published is None:
        return []
    if e.published == e.formula:
        return []
    mine = hashlib.sha256(e.formula).hexdigest()[:16]
    theirs = hashlib.sha256(e.published).hexdigest()[:16]
    lines_mine = e.formula.decode("utf-8", "replace").splitlines()
    lines_theirs = e.published.decode("utf-8", "replace").splitlines()
    where = "длиной"
    for n, (a, b) in enumerate(zip(lines_mine, lines_theirs, strict=False), start=1):
        if a != b:
            where = f"начиная со строки {n}: в дереве {a.strip()!r}, в кране {b.strip()!r}"
            break
    return [
        f"формула в {TAP_REPO} разошлась с деревом ({mine}… против {theirs}…), {where}. "
        f"Она там копия: скопируйте {FORMULA.relative_to(ROOT)} в {TAP_PATH} и отправьте"
    ]


def rule_tap_not_stale(e: Evidence) -> list[str]:
    """7. Та самая мина: кран отстал, и `brew install` молча ставит старое."""

    if e.published is None:
        return []
    tags = URL_TAG.findall(e.published.decode("utf-8", "replace"))
    if not tags:
        return [
            f"в выложенной формуле ({TAP_REPO}) не нашлось адреса архива с тегом версии — "
            "проверка не может сказать, что именно ставит brew"
        ]
    if set(tags) == {e.version}:
        return []
    return [
        f"brew ставит {sorted(set(tags))}, а нынешний выпуск {e.version}: хранилище формул "
        f"{TAP_REPO} отстало. Молча — brew не считает это ошибкой, формула-то исправна. "
        "Ровно так flang раздавал 0.7.3 при выпущенных 0.7.4—0.7.9"
    ]


#: Правила по порядку. Первые три обходятся деревом, остальные спрашивают снаружи.
OFFLINE_RULES: tuple[tuple[str, Rule], ...] = (
    ("версия в пакете", rule_package_version),
    ("отпечаток не чужой", rule_sha_not_reused),
    ("биты исполнимости", rule_exec_bits),
)

ONLINE_RULES: tuple[tuple[str, Rule], ...] = (
    ("тег выпуска есть", rule_tag_exists),
    ("отпечаток сверен", rule_archive_matches),
    ("кран выложен", rule_tap_published),
    ("кран не отстал", rule_tap_not_stale),
)

ALL_RULES = OFFLINE_RULES + ONLINE_RULES


# --------------------------------------------------------------------------- #
# сбор улик: дерево
# --------------------------------------------------------------------------- #


def _git(*args: str) -> str:
    """git в дереве проекта. Чужая настройка не должна на это влиять."""

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
    """Как пакет узнаёт свою версию: из метаданных, из вписанного числа или никак.

    Читается исходник, а не импорт: импортировать пришлось бы УСТАНОВЛЕННЫЙ пакет,
    а проверяется тот, что в дереве, — правка в дереве до установки ещё не доехала.
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
    """Адрес, версия в нём и отпечаток — из тела формулы, минуя примечания."""

    urls = URL_LINE.findall(text)
    shas = SHA_LINE.findall(text)
    if not urls:
        raise SystemExit(f"{FORMULA}: нет объявления url — образец проверки устарел")
    if not shas:
        raise SystemExit(f"{FORMULA}: нет объявления sha256 — образец проверки устарел")
    tags = URL_TAG.findall(urls[0])
    if not tags:
        raise SystemExit(f"{FORMULA}: в адресе {urls[0]!r} нет тега версии")
    return urls[0], tags[0], shas[0]


def sha_history() -> dict[str, set[str]]:
    """Какой отпечаток для какой версии объявлялся, по истории самой формулы."""

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
# сбор улик: то, что видно снаружи
# --------------------------------------------------------------------------- #


class Unreachable(Exception):
    """До выложенного не достучались. Не «всё хорошо» и не «разошлось»."""


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
            raise Unreachable(f"{url}: {e.code} — GitHub ограничил частоту запросов") from e
        raise Unreachable(f"{url}: ответ {e.code}") from e
    except (urllib.error.URLError, OSError) as e:
        raise Unreachable(f"{url}: {e}") from e


def remote_tags() -> list[str]:
    """Теги хранилища — тем же способом, каким их читает плагин asdf."""

    env = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    try:
        out = subprocess.run(
            ["git", "ls-remote", "--tags", "--refs", REPO_URL],
            env=env, check=True, capture_output=True, text=True, timeout=TIMEOUT,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise Unreachable(f"git ls-remote {REPO_URL}: {e}") from e
    versions = []
    for line in out.splitlines():
        name = line.rsplit("/", 1)[-1]
        if name.startswith("v"):
            versions.append(name[1:])
    return sorted(versions, key=lambda v: [int(p) for p in re.findall(r"\d+", v)])


def archive_sha(url: str) -> str:
    """Отпечаток архива, который человек на самом деле скачает."""

    return hashlib.sha256(fetch(url)).hexdigest()


def published_formula() -> bytes:
    """Формула из хранилища формул — через API, а не raw.

    `raw.githubusercontent.com` отдаёт кэш до пяти минут, и сразу после отправки
    проверка бы врала в обе стороны. API отвечает нынешним деревом.
    """

    url = f"https://api.github.com/repos/{TAP_REPO}/contents/{TAP_PATH}?ref=main"
    payload = json.loads(fetch(url, accept="application/vnd.github+json").decode("utf-8"))
    if payload.get("encoding") != "base64":
        raise Unreachable(f"{url}: неожиданная упаковка {payload.get('encoding')!r}")
    return base64.b64decode(payload["content"])


def collect(online: bool) -> tuple[Evidence, list[str]]:
    """Улики и список того, что спросить не удалось."""

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
    except Unreachable as e:
        missed.append(f"теги хранилища: {e}")
    try:
        evidence = replace(evidence, archive_sha=archive_sha(url))
    except Unreachable as e:
        missed.append(f"архив выпуска: {e}")
    try:
        evidence = replace(evidence, published=published_formula())
    except Unreachable as e:
        missed.append(f"формула из {TAP_REPO}: {e}")
    return evidence, missed


# --------------------------------------------------------------------------- #
# проверка самой проверки
# --------------------------------------------------------------------------- #


def sound_evidence() -> Evidence:
    """Заведомо исправные улики: на них обязаны молчать все правила."""

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
    """По порче на правило: что испортили, улики и КТО обязан на это ругаться.

    Ожидаемых правил обычно одно. Отставший кран — исключение, и оно настоящее: он
    и разошёлся с деревом побайтово, и раздаёт не ту версию. Оба правила говорят о
    нём правду, поэтому оба здесь и перечислены. Если бы список был «ровно одно
    правило», отрицательный контроль требовал бы от проверки соврать.
    """

    ok = sound_evidence()
    stale_formula = ok.formula.replace(b"v9.9.9.tar.gz", b"v9.9.8.tar.gz")
    return [
        ("версия в пакете: вписана числом и разошлась",
         replace(ok, version_source=("literal", "0.1.0")), {"версия в пакете"}),
        ("версия в пакете: вписана числом, пока совпадает",
         replace(ok, version_source=("literal", "9.9.9")), {"версия в пакете"}),
        ("версия в пакете: пакет её не объявляет",
         replace(ok, version_source=("none", "")), {"версия в пакете"}),
        ("отпечаток не чужой", replace(ok, sha_history={"a" * 64: {"9.9.9", "9.9.8"}}),
         {"отпечаток не чужой"}),
        ("биты исполнимости", replace(ok, git_modes={**ok.git_modes, "bin/install": "100644"}),
         {"биты исполнимости"}),
        ("тег выпуска есть", replace(ok, tags=["9.9.7", "9.9.8"]),
         {"тег выпуска есть"}),
        ("отпечаток сверен", replace(ok, archive_sha="b" * 64),
         {"отпечаток сверен"}),
        ("кран выложен", replace(ok, published=ok.formula + "# лишняя строка\n".encode()),
         {"кран выложен"}),
        ("кран не отстал", replace(ok, published=stale_formula),
         {"кран выложен", "кран не отстал"}),
    ]


def self_test() -> int:
    """Отрицательный контроль: каждое правило обязано покраснеть на своей порче."""

    bad: list[str] = []

    ok = sound_evidence()
    for name, rule in ALL_RULES:
        complaints = rule(ok)
        if complaints:
            bad.append(f"{name}: ругается на исправные улики — {complaints[0]}")

    for name, evidence, expected in spoiled():
        fired = {rule_name for rule_name, rule in ALL_RULES if rule(evidence)}
        for missing in sorted(expected - fired):
            bad.append(f"порча «{name}»: правило «{missing}» НЕ заметило её — "
                       "оно ничего не проверяет")
        # Правило, которое ругается на чужую порчу, назовёт в отказе не ту причину,
        # и человек пойдёт чинить не то.
        for extra in sorted(fired - expected):
            bad.append(f"порча «{name}»: ругается «{extra}», а не должно — "
                       "отказ назовёт не ту причину")

    # Не спросили — значит молчим, а не зеленеем: правила на пустых полях немы.
    silent = replace(ok, tags=None, archive_sha=None, published=None)
    for name, rule in ONLINE_RULES:
        if rule(silent):
            bad.append(f"{name}: судит по тому, чего не спрашивало")

    if bad:
        print("Проверка сломана:\n", file=sys.stderr)
        for b in bad:
            print(f"  - {b}", file=sys.stderr)
        return 1

    print(f"Проверка проверена: правил {len(ALL_RULES)}, каждое краснеет на своей порче, "
          f"молчит на чужой и на неспрошенном.")
    return 0


# --------------------------------------------------------------------------- #


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--offline", action="store_true",
                        help="только правила, которым хватает дерева")
    parser.add_argument("--self-test", action="store_true", dest="self_test",
                        help="проверить сами правила на заведомой порче")
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
        print(f"Выпуск разошёлся с тем, что видно снаружи (версия пакета {evidence.version}):\n",
              file=sys.stderr)
        print("\n".join(bad), file=sys.stderr)
        if missed:
            print("\nА ещё не спросили:", file=sys.stderr)
            for m in missed:
                print(f"  - {m}", file=sys.stderr)
        return 1

    if missed:
        print(f"Проверка НЕ состоялась — до выложенного не достучались:", file=sys.stderr)
        for m in missed:
            print(f"  - {m}", file=sys.stderr)
        print("Это не то же самое, что «снаружи всё в порядке».", file=sys.stderr)
        return 2

    checked = len(rules)
    where = "в дереве" if args.offline else "снаружи"
    print(f"Выпуск {evidence.version} сходится {where}: правил проверено {checked}, "
          f"расхождений нет.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
