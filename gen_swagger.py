#!/usr/bin/env python3
"""Generate swagger-ui.html for the Tacticus API from the saved OpenAPI spec.

- Embeds the spec inline (works over file:// and http://)
- Rewrites the spec's relative server url to the local proxy origin
  (api.tacticusgame.com has no CORS support, so the browser cannot call it
   directly from a page; tacticus_proxy.py forwards and adds the headers)
- Embeds the API key from .tacticus_api_key as the X-API-KEY example
  (prefills the try-it-out field) and injects it in requestInterceptor
"""
import json
import pathlib

BASE = pathlib.Path(__file__).resolve().parent
# The browser cannot call api.tacticusgame.com directly (no CORS headers, and
# its OPTIONS preflight 403s). Base every request on the local proxy instead;
# tacticus_proxy.py forwards to the real origin and adds Access-Control-*.
API_ORIGIN = "http://127.0.0.1:8124"
API_KEY = (BASE / ".tacticus_api_key").read_text().strip()

spec = json.loads((BASE / "tacticus-openapi.json").read_text())

# 1. Absolute server URL so every built request targets the proxy
spec["servers"] = [{"url": API_ORIGIN}]

# 2. Default value for the X-API-KEY header on every operation
for path_item in spec["paths"].values():
    for op in path_item.values():
        if not isinstance(op, dict):
            continue
        for param in op.get("parameters", []):
            if param.get("name") == "X-API-KEY" and param.get("in") == "header":
                param["example"] = API_KEY
                param.setdefault("schema", {})["example"] = API_KEY

spec_js = json.dumps(spec, ensure_ascii=False).replace("</", "<\\/")

html = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Tacticus API — Swagger UI</title>
  <link rel="stylesheet" href="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui.css" />
  <style>
    body { margin: 0; background: #fafafa; }
    .swagger-ui .topbar { display: none; }
    .banner {
      background: #1b1b1b; color: #eee; padding: 10px 20px;
      font: 13px/1.5 system-ui, sans-serif; display: flex; gap: 16px; align-items: center; flex-wrap: wrap;
    }
    .banner code { background: #333; padding: 2px 6px; border-radius: 4px; color: #9fef00; }
    .banner .hint { color: #aaa; }
  </style>
</head>
<body>
  <div class="banner">
    <strong>Tacticus API</strong>
    <span class="hint">Base URL <code>__API_ORIGIN__</code> (local proxy → <code>api.tacticusgame.com</code>, needed for CORS) — API key pre-filled on <code>X-API-KEY</code>.</span>
  </div>
  <div id="swagger-ui"></div>

  <script src="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-bundle.js" crossorigin></script>
  <script src="https://unpkg.com/swagger-ui-dist@5.11.0/swagger-ui-standalone-preset.js" crossorigin></script>
  <script>
    const API_ORIGIN = '__API_ORIGIN__';
    const API_KEY = '__API_KEY__';
    // Spec embedded inline so the page needs no network fetch for the definition.
    const SPEC = __SPEC_JSON__;

    window.onload = () => {
      window.ui = SwaggerUIBundle({
        spec: SPEC,
        dom_id: '#swagger-ui',
        presets: [SwaggerUIBundle.presets.apis, SwaggerUIStandalonePreset],
        layout: 'StandaloneLayout',
        deepLinking: true,
        docExpansion: 'list',
        defaultModelsExpandDepth: 1,
        tryItOutEnabled: false,
        validatorUrl: null,          // offline: skip validator.swagger.io
        onComplete: () => prefillKey(),
        requestInterceptor: (req) => {
          // Safety net: force every request onto the local proxy, which is the
          // only origin the browser is allowed to call (api.tacticusgame.com
          // sends no CORS headers). The proxy forwards upstream unchanged.
          try {
            const u = new URL(req.url, API_ORIGIN);
            const target = new URL(API_ORIGIN);
            if (u.origin !== target.origin) {
              u.protocol = target.protocol;
              u.host = target.host;
            }
            req.url = u.toString();
          } catch (e) {
            if (req.url.startsWith('/')) req.url = API_ORIGIN + req.url;
          }
          // Inject the key whenever the caller did not supply one.
          req.headers = req.headers || {};
          const given = req.headers['X-API-KEY'] || req.headers['x-api-key'];
          if (!given) req.headers['X-API-KEY'] = API_KEY;
          return req;
        }
      });
    };

    // Cosmetic: put the key into any visible X-API-KEY field the UI renders.
    function prefillKey() {
      document
        .querySelectorAll('input[placeholder="X-API-KEY"], input[name="X-API-KEY"]')
        .forEach((el) => { if (!el.value) el.value = API_KEY; });
    }
    new MutationObserver(prefillKey).observe(document.body, { childList: true, subtree: true });
    setInterval(prefillKey, 2000);
  </script>
</body>
</html>
"""

html = (
    html.replace("__SPEC_JSON__", spec_js)
    .replace("__API_ORIGIN__", API_ORIGIN)
    .replace("__API_KEY__", API_KEY)
)
out = BASE / "swagger-ui.html"
out.write_text(html)
print(f"wrote {out} ({out.stat().st_size} bytes)")
