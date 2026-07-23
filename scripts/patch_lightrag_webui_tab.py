"""Install URL-driven tab selection into a LightRAG WebUI directory."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys


SCRIPT_NAME = "patent-agent-tab-bootstrap.js"


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: patch_lightrag_webui_tab.py WEBUI_DIR BOOTSTRAP_JS", file=sys.stderr)
        return 2

    webui_dir = Path(sys.argv[1]).resolve()
    bootstrap_source = Path(sys.argv[2]).resolve()
    index_path = webui_dir / "index.html"
    if not index_path.is_file():
        raise FileNotFoundError(f"LightRAG WebUI index not found: {index_path}")
    if not bootstrap_source.is_file():
        raise FileNotFoundError(f"Bootstrap script not found: {bootstrap_source}")

    shutil.copyfile(bootstrap_source, webui_dir / SCRIPT_NAME)
    html = index_path.read_text(encoding="utf-8")
    marker = f'<script src="./{SCRIPT_NAME}"></script>'
    if marker not in html:
        html = html.replace("</head>", f"    {marker}\n  </head>")
        index_path.write_text(html, encoding="utf-8")
    print(f"Patched LightRAG WebUI: {webui_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
