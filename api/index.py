"""
Vercel serverless function — Dashboard ONPE bajo demanda.

Cada request hace el fetch en vivo a ONPE + Monte Carlo y devuelve el HTML.
La cabecera Cache-Control (s-maxage=60) hace que ONPE se golpee como máximo
1 vez por minuto sin importar cuántos visitantes haya (caché en el edge de Vercel).
El <meta refresh> de 60s en la página la actualiza sola → mismo efecto que el loop.
"""
import os, sys, traceback
from http.server import BaseHTTPRequestHandler

# Permitir importar fetch.py / forecast.py de la raíz del proyecto
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import fetch  # noqa: E402

# Caché en el edge: 1 generación por minuto máx., sirve cacheado el resto.
CACHE = "public, max-age=0, s-maxage=60, stale-while-revalidate=120"
# max-age=0: browser no cachea el HTML (siempre pide al edge)
# s-maxage=60: el edge de Vercel cachea 60s (máx 1 fetch a ONPE por minuto)
# stale-while-revalidate=120: edge sirve stale mientras regenera en background

def _fetch_districts():
    # ONPE_FETCH_DEPTH=province  → más liviano (~300 reqs) y rápido si hay timeouts/bloqueos
    return os.environ.get("ONPE_FETCH_DEPTH", "district").lower() != "province"

def generate(path=""):
    """Núcleo testeable: devuelve (status, content_type, body_bytes)."""
    p = (path or "").split("?")[0].lower()
    if "csv" in p:
        data = fetch.fetch_all(fetch_districts=_fetch_districts())
        return 200, "text/csv; charset=utf-8", fetch.build_csv(data).encode("utf-8-sig")
    if p in ("", "/", "/api/index", "/index"):
        data = fetch.fetch_all(fetch_districts=_fetch_districts())
        return 200, "text/html; charset=utf-8", fetch.build_html(data, auto_refresh=60).encode("utf-8")
    return 204, "text/plain; charset=utf-8", b""   # favicon u otros assets: no disparar fetch

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            status, ctype, body = generate(self.path)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", CACHE)
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            msg = ("<!DOCTYPE html><meta charset='utf-8'>"
                   "<body style='font-family:monospace;background:#0d1117;color:#e6edf3;padding:20px'>"
                   "<h2>Error generando el dashboard</h2><pre>" + traceback.format_exc() +
                   "</pre><p>Revisá los logs de la función en Vercel. Causas típicas: "
                   "curl_cffi no compiló, o ONPE bloqueó la IP de Vercel.</p></body>")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(msg.encode("utf-8"))
