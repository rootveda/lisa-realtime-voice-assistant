#!/usr/bin/env python3
"""One-off Ollama vision round-trip test in the browser. Does not register routes on the main Pipecat app.

Serves a tiny page + ``POST /api/vision/preview`` (same ``caption_jpeg`` as the stack). Run on a separate port.

  cd offline_setup/app
  uv run python scripts/ollama_vision_latency_serve.py
  # open http://127.0.0.1:8765/

  # listen on all interfaces (phone / other machine on LAN):
  # uv run python scripts/ollama_vision_latency_serve.py --host 0.0.0.0
"""

import argparse
import json
import os
import sys
from pathlib import Path

_APP_DIR = Path(__file__).resolve().parent.parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

_HTML = Path(__file__).resolve().parent / "ollama_vision_latency_test.html"


def _effective_vision_model() -> str:
    from pipecat_bots.vision_caption import effective_vision_ollama_model

    return effective_vision_ollama_model()


def _ollama_installed_names() -> tuple[list[str], str]:
    """Return (model names, ollama root URL used). Empty list if unreachable."""
    import httpx
    from pipecat_bots.vision_caption import _ollama_root_no_v1

    root = _ollama_root_no_v1()
    try:
        r = httpx.get(f"{root}/api/tags", timeout=5.0)
        r.raise_for_status()
        data = r.json()
        models = data.get("models") if isinstance(data, dict) else None
        if not isinstance(models, list):
            return [], root
        names: list[str] = []
        for m in models:
            if isinstance(m, dict) and isinstance(m.get("name"), str):
                names.append(m["name"])
        return names, root
    except Exception:
        return [], root


def _verify_model_or_warn() -> None:
    model = _effective_vision_model()
    names, root = _ollama_installed_names()
    print(f"VISION_OLLAMA_MODEL effective: {model!r}  (Ollama: {root})")
    if not names:
        print(
            "WARNING: Could not list models from Ollama (/api/tags). Is `ollama serve` running and reachable?",
            file=sys.stderr,
        )
        return
    if model not in names:
        sample = ", ".join(names[:12])
        print(
            f"WARNING: Model {model!r} is NOT installed — Ollama returns 'invalid model name'.\n"
            f"  Run: ollama list\n"
            f"  Then: export VISION_OLLAMA_MODEL=<one of your vision tags>\n"
            f"  Installed tags (sample): {sample}",
            file=sys.stderr,
        )
    else:
        print(f"OK: {model!r} is present in ollama list.")


def _build_app():
    from fastapi import FastAPI, File, UploadFile
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse, JSONResponse, Response
    from loguru import logger

    app = FastAPI(title="Ollama vision latency test (standalone)", version="0.1")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/")
    async def index():
        if _HTML.is_file():
            return FileResponse(_HTML, media_type="text/html")
        return Response(
            "Missing ollama_vision_latency_test.html next to ollama_vision_latency_serve.py",
            status_code=500,
            media_type="text/plain",
        )

    @app.get("/api/vision/config")
    async def vision_config():
        """Which model tag caption_jpeg uses + what Ollama reports as installed."""
        from pipecat_bots.vision_caption import _ollama_root_no_v1

        model = _effective_vision_model()
        names, root = _ollama_installed_names()
        return JSONResponse(
            {
                "vision_ollama_model": model,
                "model_installed": model in names if names else None,
                "ollama_root": root,
                "installed_models": names,
                "hint": None
                if (not names or model in names)
                else "Set VISION_OLLAMA_MODEL to a tag from installed_models, then restart this script.",
            }
        )

    # Multipart `image` via UploadFile (using `Request.form()` + `request: Request` led to 422: required query param "request").
    @app.post("/api/vision/preview")
    async def vision_preview(image: UploadFile = File(..., description="JPEG frame")):
        from pipecat_bots.vision_caption import caption_jpeg

        try:
            raw = await image.read()
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        if not raw:
            return JSONResponse({"ok": False, "error": "empty upload"}, status_code=400)
        try:
            cap = await caption_jpeg(raw)
        except Exception as e:
            err = str(e)
            if "invalid model name" in err.lower():
                logger.warning(f"[ollama_vision_latency_serve] {err}")
            else:
                logger.exception("[ollama_vision_latency_serve] caption failed")
            hint = None
            if "invalid model name" in err.lower():
                hint = (
                    "Ollama does not recognize VISION_OLLAMA_MODEL. Run `ollama list`, "
                    "then `export VISION_OLLAMA_MODEL=<exact tag>` (vision model). "
                    "Open GET /api/vision/config in this server for installed tags."
                )
            body: dict = {"ok": False, "error": err}
            if hint:
                body["hint"] = hint
            return JSONResponse(body, status_code=502)
        return Response(
            content=json.dumps({"ok": True, "caption": cap}).encode(),
            media_type="application/json",
        )

    return app


def main() -> None:
    p = argparse.ArgumentParser(description="Standalone Ollama vision latency test (not the main bot).")
    p.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 for LAN)")
    p.add_argument("--port", type=int, default=8765, help="default 8765 avoids Pipecat ports")
    args = p.parse_args()

    try:
        import uvicorn
    except ImportError as e:
        print("Install uvicorn: pip install uvicorn", file=sys.stderr)
        raise SystemExit(2) from e

    app = _build_app()
    print(f"Ollama vision test server — http://{args.host}:{args.port}/")
    print("Stop with Ctrl+C. Does not change the main assistant process.")
    _verify_model_or_warn()
    print(f"Config JSON: http://{args.host}:{args.port}/api/vision/config")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
