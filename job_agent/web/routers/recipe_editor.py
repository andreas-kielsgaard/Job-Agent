from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_agent.services.recipe_editor_service import RecipeEditorService
from job_agent.web.dependencies import current_root, templates
from job_agent.web.form_options import recipe_options

router = APIRouter()


@router.get("/recipe-editor", response_class=HTMLResponse)
def recipe_editor(
    request: Request,
    recipe_path: str = Query(""),
    artifact_dir: str = Query(""),
    saved: bool = Query(False),
) -> HTMLResponse:
    root = current_root()
    service = RecipeEditorService(root)
    state = service.load(recipe_path=recipe_path, artifact_dir=artifact_dir)
    return templates.TemplateResponse(
        request,
        "recipe_editor.html",
        {
            "request": request,
            "state": state,
            "recipe_options": recipe_options(root),
            "saved": saved,
        },
    )


@router.post("/recipe-editor/save")
async def save_recipe_editor(request: Request) -> RedirectResponse:
    form = await request.form()
    recipe_path = str(form.get("recipe_path") or "")
    artifact_dir = str(form.get("artifact_dir") or "")
    values = {
        key.removeprefix("selector__").replace("__", "."): str(value)
        for key, value in form.items()
        if key.startswith("selector__")
    }
    try:
        RecipeEditorService(current_root()).save_selectors(recipe_path, values)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(
        f"/recipe-editor?recipe_path={quote(recipe_path)}&artifact_dir={quote(artifact_dir)}&saved=true",
        status_code=303,
    )


@router.get("/recipe-editor/snapshot", response_class=HTMLResponse)
def recipe_snapshot(artifact_dir: str = Query("")) -> HTMLResponse:
    try:
        page = RecipeEditorService(current_root()).resolve_artifact_page(artifact_dir)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    html = page.read_text(encoding="utf-8")
    return HTMLResponse(_instrument_html(html))


def _instrument_html(html: str) -> str:
    script = """
<script>
(() => {
  const highlightClass = "job-agent-recipe-highlight";
  const style = document.createElement("style");
  style.textContent = `
    .${highlightClass} {
      outline: 3px solid #0f766e !important;
      outline-offset: 2px !important;
      background: rgba(15, 118, 110, 0.12) !important;
    }
  `;
  document.head.appendChild(style);

  function escapePart(value) {
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return value.replace(/[^a-zA-Z0-9_-]/g, "\\\\$&");
  }

  function selectorFor(element) {
    if (!element || element.nodeType !== 1) return "";
    if (element.id) return `#${escapePart(element.id)}`;
    const parts = [];
    let current = element;
    while (current && current.nodeType === 1 && current.tagName.toLowerCase() !== "html") {
      const tag = current.tagName.toLowerCase();
      const classes = [...current.classList].slice(0, 3).map((name) => `.${escapePart(name)}`).join("");
      let part = `${tag}${classes}`;
      const parent = current.parentElement;
      if (parent && !classes) {
        const siblings = [...parent.children].filter((child) => child.tagName === current.tagName);
        if (siblings.length > 1) part += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      }
      parts.unshift(part);
      if (classes && document.querySelectorAll(parts.join(" > ")).length === 1) break;
      current = parent;
      if (parts.length >= 5) break;
    }
    return parts.join(" > ");
  }

  function highlight(selector) {
    document.querySelectorAll(`.${highlightClass}`).forEach((node) => node.classList.remove(highlightClass));
    if (!selector) return;
    let matches = [];
    try {
      matches = [...document.querySelectorAll(selector)];
    } catch (_error) {
      return;
    }
    matches.forEach((node) => node.classList.add(highlightClass));
    if (matches[0]) matches[0].scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }

  document.addEventListener("click", (event) => {
    const target = event.target;
    event.preventDefault();
    event.stopPropagation();
    const selector = selectorFor(target);
    highlight(selector);
    window.parent.postMessage({
      type: "recipe-editor-selection",
      selector,
      text: (target.innerText || target.textContent || "").trim().slice(0, 500)
    }, "*");
  }, true);

  window.addEventListener("message", (event) => {
    if (!event.data || event.data.type !== "recipe-editor-highlight") return;
    highlight(event.data.selector || "");
  });
})();
</script>
"""
    lower = html.lower()
    body_index = lower.rfind("</body>")
    if body_index != -1:
        return html[:body_index] + script + html[body_index:]
    return html + script
