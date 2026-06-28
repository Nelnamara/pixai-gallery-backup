"""Regression guard: the gallery's JS is embedded in Python triple-quoted strings,
so an unescaped '\\n' (or stray quote/backtick) silently turns into invalid JS that
breaks the WHOLE <script> block at runtime (lightbox, keyboard nav, selection
restore all die). Render each page and syntax-check the embedded <script> blocks
with Node. Skips cleanly if Node isn't installed."""
import os
import re
import shutil
import subprocess

import pytest

from pixai_gallery import CATALOG_FIELDS, create_app, save_catalog

NODE = shutil.which("node")


def _row(**kw):
    return {f: "" for f in CATALOG_FIELDS} | kw


@pytest.fixture
def client(tmp_path):
    save_catalog(tmp_path / "catalog.db", [
        _row(media_id="1", filename="a_1.png", prompt_preview="x",
             created_at="2025-01-01T00:00:00"),
        _row(media_id="2", filename="b_2.png", prompt_preview="y",
             created_at="2025-01-02T00:00:00"),
    ])
    return create_app(tmp_path).test_client()


def _scripts(html):
    return re.findall(r"<script>(.*?)</script>", html, flags=re.S)


@pytest.mark.skipif(NODE is None, reason="node not installed")
@pytest.mark.parametrize("path", ["/", "/image/1", "/health", "/duplicates"])
def test_embedded_js_is_valid(client, tmp_path, path):
    html = client.get(path).get_data(as_text=True)
    blocks = _scripts(html)
    assert blocks, f"no <script> found on {path}"
    js = "\n;\n".join(blocks)
    f = tmp_path / "page.js"
    f.write_text(js, encoding="utf-8")
    out = tmp_path / "node.out"
    # Redirect to real files + DEVNULL stdin: some sandboxes can't duplicate
    # pytest's captured std handles (WinError 50). Skip if the OS blocks spawn.
    try:
        with open(out, "w", encoding="utf-8") as fh, open(os.devnull) as nul:
            rc = subprocess.call([NODE, "--check", str(f)],
                                 stdin=nul, stdout=fh, stderr=subprocess.STDOUT)
    except OSError as e:
        pytest.skip(f"cannot spawn node in this environment: {e}")
    assert rc == 0, f"{path} has invalid JS:\n{out.read_text(encoding='utf-8')}"


def test_no_real_newline_inside_confirm_string(client):
    """Even without Node: the cloud-delete confirm must keep its escaped newline as
    the two chars backslash-n, not an actual line break that splits the literal."""
    html = client.get("/").get_data(as_text=True)
    m = re.search(r"confirm\('Delete '.*?\)\)", html, flags=re.S)
    assert m, "confirmBulkDeleteCloud string not found"
    assert "\n" not in m.group(0).split("typed")[0][:200] or "\\n\\n" in html
