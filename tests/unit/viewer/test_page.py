"""The page's own files (``viewer/static``): text never becomes HTML, three.js is as vendored.

The JavaScript is not run here (there is no browser in the suite); these tests read it.
"""

import hashlib
import json
import re
from pathlib import Path

import pytest

from ifc_sql_mcp.viewer.http import MEDIA_TYPES, PAGE, STATIC

VENDOR = STATIC / "vendor" / "three"
NUNITO = STATIC / "vendor" / "nunito"
OWN_SCRIPTS = sorted(STATIC.glob("*.js"))

BANNED = {
    "innerHTML": re.compile(r"\binnerHTML\b"),
    "outerHTML": re.compile(r"\bouterHTML\b"),
    "insertAdjacentHTML": re.compile(r"\binsertAdjacentHTML\b"),
    "document.write": re.compile(r"\bdocument\s*\.\s*write(ln)?\b"),
    "srcdoc": re.compile(r"\bsrcdoc\b"),
    "createContextualFragment": re.compile(r"\bcreateContextualFragment\b"),
    "setAttribute('on…')": re.compile(r"""\bsetAttribute\(\s*['"`]on""", re.IGNORECASE),
    "javascript:": re.compile(r"javascript:", re.IGNORECASE),
    "eval": re.compile(r"\beval\s*\("),
    "new Function": re.compile(r"\bnew\s+Function\b"),
}
"""What turns a string into markup or code (ADR 14, decision 3)."""


def banned_calls(source: str) -> list[str]:
    return [name for name, pattern in BANNED.items() if pattern.search(source)]


@pytest.mark.parametrize(
    ("source", "found"),
    [
        ("node.innerHTML = name;", "innerHTML"),
        ("const html = node.outerHTML;", "outerHTML"),
        ("list.insertAdjacentHTML('beforeend', row);", "insertAdjacentHTML"),
        ("document . writeln(text);", "document.write"),
        ("frame.srcdoc = page;", "srcdoc"),
        ("range.createContextualFragment(text);", "createContextualFragment"),
        ("button.setAttribute( 'onclick', code);", "setAttribute('on…')"),
        ('link.href = "JavaScript:alert(1)";', "javascript:"),
        ("eval (code);", "eval"),
        ("const run = new  Function('return 1');", "new Function"),
    ],
)
def test_each_banned_call_is_found(source: str, found: str) -> None:
    assert banned_calls(source) == [found]


def test_the_page_has_its_own_modules() -> None:
    assert [path.name for path in OWN_SCRIPTS] == [
        "api.js",
        "details.js",
        "dom.js",
        "feed.js",
        "main.js",
        "scene.js",
        "words.js",
    ]


@pytest.mark.parametrize("script", OWN_SCRIPTS, ids=lambda path: path.name)
def test_the_pages_own_javascript_never_turns_text_into_markup_or_code(script: Path) -> None:
    assert banned_calls(script.read_text(encoding="utf-8")) == []


def test_textcontent_is_how_the_page_writes_text() -> None:
    dom = (STATIC / "dom.js").read_text(encoding="utf-8")

    assert "node.textContent = String(options.text)" in dom
    # The one setAttribute takes names from the page's own code, and refuses handlers.
    assert "if (/^on/i.test(name)) throw" in dom


def _page() -> str:
    return (STATIC / PAGE).read_text(encoding="utf-8")


def test_the_pages_one_inline_script_is_its_import_map() -> None:
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", _page(), re.DOTALL)

    assert [(attributes, body.strip() != "") for attributes, body in scripts] == [
        (' type="importmap"', True),
        (' type="module" src="static/main.js"', False),
    ]


def test_the_page_has_no_inline_handlers_or_styles() -> None:
    page = _page()

    assert re.search(r"\son\w+\s*=", page) is None
    assert re.search(r"\sstyle\s*=", page) is None
    assert "<style" not in page


def test_every_module_the_page_imports_is_one_of_its_files() -> None:
    imports = json.loads(re.search(r'type="importmap">(.*?)</script>', _page(), re.DOTALL)[1])
    prefixes = imports["imports"]
    # Relative to the page: the static copy is served below a folder (ADR 14, decision 7).
    assert prefixes == {
        "three": "./static/vendor/three/three.module.js",
        "three/addons/": "./static/vendor/three/addons/",
    }

    def resolve(specifier: str, importer: Path) -> Path:
        if specifier.startswith("."):
            return (importer.parent / specifier).resolve()
        if specifier == "three":
            return STATIC / prefixes["three"].removeprefix("./static/")
        assert specifier.startswith("three/addons/")
        return (
            STATIC
            / prefixes["three/addons/"].removeprefix("./static/")
            / specifier.removeprefix("three/addons/")
        )

    statement = re.compile(r"""^\s*(?:import|export)\b[^'"]*?from\s+'([^']+)'""", re.MULTILINE)
    for module in [*OWN_SCRIPTS, *VENDOR.rglob("*.js")]:
        for specifier in statement.findall(module.read_text(encoding="utf-8")):
            assert resolve(specifier, module).is_file(), (module.name, specifier)


def _sums(folder: Path) -> tuple[dict[str, str], dict[str, str]]:
    """The SHA256SUMS of ``folder``, and the sums of the files in it."""
    listed = {}
    for line in (folder / "SHA256SUMS").read_text(encoding="ascii").splitlines():
        digest, name = line.split("  ")
        listed[name] = digest
    on_disk = {
        path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in folder.rglob("*")
        if path.is_file() and path.name != "SHA256SUMS"
    }
    return listed, on_disk


def test_the_vendored_files_match_their_sha256sums() -> None:
    listed, on_disk = _sums(VENDOR)

    assert on_disk == listed
    assert sorted(listed) == [
        "LICENSE",
        "addons/controls/OrbitControls.js",
        "addons/loaders/GLTFLoader.js",
        "addons/utils/BufferGeometryUtils.js",
        "addons/utils/SkeletonUtils.js",
        "three.core.js",
        "three.module.js",
    ]


def test_every_vendored_folder_has_a_licence_and_sha256sums() -> None:
    folders = sorted(path.name for path in (STATIC / "vendor").iterdir())

    assert folders == ["nunito", "three"]
    for name in folders:
        listed, on_disk = _sums(STATIC / "vendor" / name)
        assert on_disk == listed, name
        assert "LICENSE" in listed, name


def test_nunito_is_release_5_3_0_under_the_open_font_licence() -> None:
    listed, on_disk = _sums(NUNITO)
    licence = (NUNITO / "LICENSE").read_text(encoding="utf-8")

    assert on_disk == listed
    # The upright variable font of @fontsource-variable/nunito 5.3.0, two of its subsets.
    assert sorted(listed) == [
        "LICENSE",
        "nunito-latin-ext-wght-normal.woff2",
        "nunito-latin-wght-normal.woff2",
    ]
    assert licence.startswith("Copyright 2014 The Nunito Project Authors")
    assert "SIL OPEN FONT LICENSE Version 1.1" in licence
    for name in listed:
        if name.endswith(".woff2"):
            assert (NUNITO / name).read_bytes()[:4] == b"wOF2", name


def test_three_js_is_release_187_under_its_mit_licence() -> None:
    licence = (VENDOR / "LICENSE").read_text(encoding="utf-8")
    core = (VENDOR / "three.core.js").read_text(encoding="utf-8")

    assert licence.startswith("The MIT License")
    assert "three.js authors" in licence
    assert "const REVISION = '187';" in core


def test_every_file_of_the_page_has_a_media_type() -> None:
    suffixes = {path.suffix for path in STATIC.rglob("*") if path.is_file()}

    assert suffixes - set(MEDIA_TYPES) == {".html"}  # the page itself, served at /
