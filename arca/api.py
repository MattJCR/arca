from __future__ import annotations

import html
from pathlib import Path
from string import Template

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from arca import __version__
from arca.config import ArcaConfig, RootScan, scan_root
from arca.diagnostics import CheckResult, render, run_checks
from arca.i18n import Catalog, load_catalog, normalize_language

PACKAGE_DIR = Path(__file__).parent


def create_app(root: Path, config: ArcaConfig) -> FastAPI:
    app = FastAPI(
        title="ARCA",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/api/diagnostics")
    def api_diagnostics(lang: str | None = None) -> JSONResponse:
        catalog = _catalog_for(config, lang)
        results = run_checks(root, config)
        payload = {
            "language": catalog.language,
            "checks": [render(result, catalog) for result in results],
        }
        return JSONResponse(payload)

    @app.get("/", response_class=HTMLResponse)
    def home(lang: str | None = None) -> HTMLResponse:
        catalog = _catalog_for(config, lang)
        scan = scan_root(root)
        results = run_checks(root, config)
        return HTMLResponse(_render_home(catalog, root, scan, results))

    @app.get("/admin", response_class=HTMLResponse)
    def admin(lang: str | None = None) -> HTMLResponse:
        catalog = _catalog_for(config, lang)
        template = Template((PACKAGE_DIR / "templates" / "admin.html").read_text("utf-8"))
        page = template.substitute(
            language=catalog.language,
            title=html.escape(catalog.text("admin.title")),
            placeholder=html.escape(catalog.text("admin.placeholder")),
            lang_links=_language_links(catalog, "/admin"),
        )
        return HTMLResponse(page)

    return app


def _catalog_for(config: ArcaConfig, lang: str | None) -> Catalog:
    return load_catalog(normalize_language(lang) if lang else config.language)


def _language_links(catalog: Catalog, base_path: str) -> str:
    links = []
    for language in ("es", "en"):
        label = html.escape(catalog.text(f"ui.language.{language}"))
        links.append(f'<a href="{base_path}?lang={language}">{label}</a>')
    return "\n".join(links)


def _render_home(catalog: Catalog, root: Path, scan: RootScan, results: list[CheckResult]) -> str:
    template = Template((PACKAGE_DIR / "templates" / "index.html").read_text("utf-8"))
    status_items = [
        catalog.text("status.root", root=str(root)),
        catalog.text("status.zim_count", count=scan.zim_count),
        catalog.text("status.chat_models", count=scan.chat_model_count),
        catalog.text("status.embed_models", count=scan.embed_model_count),
    ]
    if not scan.database_present:
        status_items.append(catalog.text("status.db_missing"))
    invite_block = ""
    if scan.zim_count == 0:
        invite = html.escape(catalog.text("status.empty.invite"))
        invite_block = f'<p class="invite">{invite}</p>'
    return template.substitute(
        language=catalog.language,
        title=html.escape(catalog.text("app.title")),
        tagline=html.escape(catalog.text("app.tagline")),
        lang_links=_language_links(catalog, "/"),
        status_heading=html.escape(catalog.text("status.heading")),
        status_items="\n".join(f"<li>{html.escape(item)}</li>" for item in status_items),
        invite_block=invite_block,
        diag_heading=html.escape(catalog.text("diag.heading")),
        diag_items="\n".join(_diagnostic_item(result, catalog) for result in results),
    )


def _diagnostic_item(result: CheckResult, catalog: Catalog) -> str:
    rendered = render(result, catalog)
    state = rendered["state"]
    label = html.escape(rendered["state_label"])
    summary = html.escape(rendered["summary"])
    parts = [f'<li><span class="state state-{state}">{label}</span> {summary}']
    for aspect in ("impact", "action"):
        if aspect in rendered:
            parts.append(f'<p class="detail">{html.escape(rendered[aspect])}</p>')
    parts.append("</li>")
    return "".join(parts)
