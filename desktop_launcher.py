"""Desktop launcher for packaged Patent Agent builds."""

from __future__ import annotations

import sys
import webbrowser

from backend_runtime import run_backend_forever, start_backend_process


def _open_native_window(url: str) -> None:
    try:
        import webview  # type: ignore[import-not-found]
    except ImportError:
        webbrowser.open(url)
        return

    window = webview.create_window(
        "专利策源台",
        url,
        width=1440,
        height=920,
        min_size=(1120, 720),
        confirm_close=True,
    )
    webview.start(debug=False)


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "--backend":
        run_backend_forever()
        return
    if args and args[0] == "--mcp":
        from patent_agent_mcp import main as mcp_main

        mcp_main()
        return
    if args and args[0] == "--cli":
        from patent_agent_cli import main as cli_main

        raise SystemExit(cli_main(args[1:]))

    endpoint = start_backend_process()
    _open_native_window(endpoint.url)


if __name__ == "__main__":
    main()
