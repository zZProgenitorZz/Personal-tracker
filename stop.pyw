"""Stops Progen cleanly (for the "Stop Progen" shortcut; the tray icon has the
same button). The server finishes running requests and closes the
database; that is cleaner than killing the process.
"""
from app.desktop import make_dpi_aware, request_stop, show_message


def main() -> None:
    make_dpi_aware()  # a sharp message at display scaling > 100%
    problem = request_stop()
    if problem:
        show_message(problem, error=False)


if __name__ == "__main__":
    main()
