"""Desktop launcher for packaged Patent Agent builds."""

from __future__ import annotations

import os
import threading
import time
import webbrowser

from app import app, find_available_port


def _run_flask(host: str, port: int) -> None:
    app.run(host=host, port=port, debug=False, use_reloader=False)


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
    host = os.getenv("WEB_HOST", "127.0.0.1")
    preferred_port = int(os.getenv("WEB_PORT", "5000"))
    port = find_available_port(host=host, preferred=preferred_port)
    url = f"http://{host}:{port}"

    server = threading.Thread(target=_run_flask, args=(host, port), daemon=True)
    server.start()
    time.sleep(0.8)
    _open_native_window(url)


if __name__ == "__main__":
    main()
