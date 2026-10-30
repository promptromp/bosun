"""Builds index.html from src/: the shell with every src/[0-9]-*.js file inlined, in order.

Run it after editing anything under src/: python3 build.py. A small error collector goes first, so a
headless check can read the page's errors from <html data-errors="...">.
"""

from pathlib import Path


HERE = Path(__file__).resolve().parent
GUARD = (
    "window.__errors = []; window.addEventListener('error', e => { window.__errors.push(String(e.message)); "
    "document.documentElement.dataset.errors = window.__errors.join(' | '); });\n"
)


def main() -> None:
    js = "\n".join(path.read_text() for path in sorted((HERE / "src").glob("[0-9]-*.js")))
    shell = (HERE / "src" / "shell.html").read_text()
    (HERE / "index.html").write_text(shell.replace("{{SCRIPT}}", GUARD + js))


if __name__ == "__main__":
    main()
