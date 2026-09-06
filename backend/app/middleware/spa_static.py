from pathlib import Path

from fastapi.responses import FileResponse

# no-store on both branches: the JS/CSS under /assets/ (mounted separately in
# main.py) are content-hashed per build and fine to cache hard, but everything
# served through here — index.html, LICENSE, robots.txt — isn't, and
# Starlette's FileResponse sets no Cache-Control by default, leaving
# browsers to apply their own heuristic caching. That's exactly what
# made the mta-sts hostname fix look "flaky" live: a browser that had
# cached the SPA shell from before restrict_mta_sts_hostname existed
# kept serving it back across refreshes, even though the server was
# already answering consistently.
NO_STORE = {"Cache-Control": "no-store"}


def make_serve_spa(static_dir: Path):
    async def serve_spa(full_path: str) -> FileResponse:
        # Contain to the static root: resolve() collapses any `..`/encoded
        # traversal and symlinks, and is_relative_to() rejects anything that
        # escaped the directory (e.g. /%2e%2e/app/config.py, //etc/passwd).
        # Without this, `STATIC_DIR / full_path` served arbitrary files —
        # `FileResponse` will happily read /etc/passwd or the app source.
        root = static_dir.resolve()
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate, headers=NO_STORE)
        return FileResponse(root / "index.html", headers=NO_STORE)

    return serve_spa
