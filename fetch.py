#!/usr/bin/env python3
"""
fetch.py — Resultados ONPE 2026 segunda vuelta.
Uso:  python fetch.py
      python fetch.py --loop
      python fetch.py --loop --interval 60
      python fetch.py --no-districts   (solo dept + provincia, más rápido)
"""
import csv, json, sys, os, subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))

# Motor de pronóstico Monte Carlo (opcional — requiere numpy)
try:
    import forecast as _forecast_mod
    _HAS_FORECAST = True
except Exception:
    _HAS_FORECAST = False

try:
    from curl_cffi import requests as _req_lib
    _SESSION = _req_lib.Session(impersonate="chrome")
    _USE_CFFI = True
except ImportError:
    print("curl_cffi no encontrado. Instalando…")
    try:
        subprocess.run([sys.executable, "-m", "pip", "install", "curl-cffi", "-q"], check=True)
        from curl_cffi import requests as _req_lib
        _SESSION = _req_lib.Session(impersonate="chrome")
        _USE_CFFI = True
        print("curl_cffi instalado OK.")
    except Exception:
        try:
            import requests as _req_lib
        except ImportError:
            sys.exit("pip install requests")
        _SESSION = _req_lib.Session()
        try:
            _SESSION.get("https://resultadosegundavuelta.onpe.gob.pe/main/resumen", timeout=15)
        except Exception:
            pass
        _USE_CFFI = False
        print("Usando requests (sin impersonación Chrome — participantes puede fallar)")

# ── Constantes ─────────────────────────────────────────────────────────────────

BASE     = "https://resultadosegundavuelta.onpe.gob.pe/presentacion-backend/resumen-general/"
BASE_UBI = "https://resultadosegundavuelta.onpe.gob.pe/presentacion-backend/ubigeos/"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://resultadosegundavuelta.onpe.gob.pe/main/resumen",
    "Origin": "https://resultadosegundavuelta.onpe.gob.pe",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Cache-Control": "no-cache",
}

DEPARTAMENTOS = [
    ("010000","Amazonas"), ("020000","Áncash"),    ("030000","Apurímac"),
    ("040000","Arequipa"), ("050000","Ayacucho"),  ("060000","Cajamarca"),
    ("240000","Callao"),   ("070000","Cusco"),      ("080000","Huancavelica"),
    ("090000","Huánuco"),  ("100000","Ica"),         ("110000","Junín"),
    ("120000","La Libertad"), ("130000","Lambayeque"), ("140000","Lima"),
    ("150000","Loreto"),   ("160000","Madre de Dios"), ("170000","Moquegua"),
    ("180000","Pasco"),    ("190000","Piura"),       ("200000","Puno"),
    ("210000","San Martín"), ("220000","Tacna"),    ("230000","Tumbes"),
    ("250000","Ucayali"),
]

CONTINENTES = [
    ("910000","África"), ("920000","América (exterior)"), ("930000","Asia"),
    ("940000","Europa"), ("950000","Oceanía"),
]

CAND_EP = "participantes"

# ── HTTP ───────────────────────────────────────────────────────────────────────

def get(endpoint, params, base=None):
    if base is None:
        base = BASE
    try:
        r = _SESSION.get(base + endpoint, params=params, headers=HEADERS, timeout=20)
        if not r.ok:
            return None
        text = r.text.strip()
        if not text or text.startswith("<"):
            return None
        body = r.json()
        if body.get("success"):
            return body["data"]
    except Exception:
        pass
    return None

def get_catalog(endpoint, params):
    """Devuelve lista de (ubigeo, nombre) del catálogo ubigeos/."""
    data = get(endpoint, params, base=BASE_UBI)
    if isinstance(data, list):
        return [(x["ubigeo"], x["nombre"].title()) for x in data]
    return []

# ── Fetch ──────────────────────────────────────────────────────────────────────

def _geo_params(tipo, ambito, dept_id, prov_id=None, dist_id=None):
    p = {"idEleccion": "10", "tipoFiltro": tipo}
    if ambito is not None:
        p["idAmbitoGeografico"] = ambito
    if dept_id:
        p["idUbigeoDepartamento"] = dept_id
    if prov_id:
        p["idUbigeoProvincia"] = prov_id
    if dist_id:
        p["idUbigeoDistrito"] = dist_id
    return p

def _fetch_data(tipo, ambito, dept_id, prov_id=None, dist_id=None):
    p = _geo_params(tipo, ambito, dept_id, prov_id, dist_id)
    return {
        "totales":    get("totales",  p) or {},
        "candidatos": get(CAND_EP,    p),
    }

def fetch_all(fetch_districts=True):
    """Descarga toda la jerarquía:
       Perú:     Departamento → Provincia → Distrito
       Exterior: Continente  → País      → Ciudad
    """
    # top_all: (ambito, id, nombre, grupo_label, sub1_label, sub2_label)
    top_all = (
        [("1", d, n, "Perú",       "Provincia", "Distrito") for d, n in DEPARTAMENTOS] +
        [("2", d, n, "Extranjero", "País",      "Ciudad")   for d, n in CONTINENTES]
    )
    ambito_of  = {tid: amb                 for amb, tid, *_ in top_all}
    sub1lbl_of = {tid: sub1lbl             for amb, tid, nom, grp, sub1lbl, sub2lbl in top_all}
    sub2lbl_of = {tid: sub2lbl             for amb, tid, nom, grp, sub1lbl, sub2lbl in top_all}

    # ── Stage 1: datos de cada top-level ───────────────────────────────────
    base_results = [None] * len(top_all)
    with ThreadPoolExecutor(max_workers=12) as ex:
        fmap = {
            ex.submit(_fetch_data, "ubigeo_nivel_01", amb, tid): i
            for i, (amb, tid, *_) in enumerate(top_all)
        }
        done = 0
        for fut in as_completed(fmap):
            i = fmap[fut]
            amb, tid, nom, grp, *_ = top_all[i]
            base_results[i] = {"id": tid, "nombre": nom, "grupo": grp, **fut.result(), "provincias": []}
            done += 1
            print(f"  {done}/{len(top_all)}", end="\r")
    print()

    depts_base = base_results[:len(DEPARTAMENTOS)]
    conts_base = base_results[len(DEPARTAMENTOS):]

    # ── Stage 2: catálogo de sub-nivel 1 (provincias / países) ────────────
    sub1_catalog = {}   # top_id -> [(sub1_id, sub1_nom)]
    with ThreadPoolExecutor(max_workers=35) as ex:
        fmap2 = {
            ex.submit(get_catalog, "provincias",
                      {"idEleccion":"10","idAmbitoGeografico":ambito_of[tid],
                       "idUbigeoDepartamento":tid}): tid
            for amb, tid, *_ in top_all
        }
        for fut in as_completed(fmap2):
            sub1_catalog[fmap2[fut]] = fut.result() or []

    # ── Stage 3: datos de sub-nivel 1 ─────────────────────────────────────
    sub1_tasks = [
        (ambito_of[tid], tid, sid, snom)
        for amb, tid, *_ in top_all
        for sid, snom in sub1_catalog.get(tid, [])
    ]
    sub1_data = {}   # (top_id, sub1_id) -> data
    with ThreadPoolExecutor(max_workers=35) as ex:
        fmap3 = {
            ex.submit(_fetch_data, "ubigeo_nivel_02", amb, tid, sid): (tid, sid)
            for amb, tid, sid, _ in sub1_tasks
        }
        done = 0
        for fut in as_completed(fmap3):
            key = fmap3[fut]
            sub1_data[key] = fut.result()
            done += 1
            print(f"  prov/país {done}/{len(sub1_tasks)}", end="\r")
    print()

    # ── Stage 4 (opcional): catálogo de sub-nivel 2 ────────────────────────
    sub2_catalog = {}   # sub1_id -> [(sub2_id, sub2_nom)]
    if fetch_districts:
        with ThreadPoolExecutor(max_workers=40) as ex:
            fmap4 = {
                ex.submit(get_catalog, "distritos",
                          {"idEleccion":"10","idAmbitoGeografico":amb,
                           "idUbigeoProvincia":sid}): sid
                for amb, tid, sid, _ in sub1_tasks
            }
            for fut in as_completed(fmap4):
                sub2_catalog[fmap4[fut]] = fut.result() or []

    # ── Stage 5 (opcional): datos de sub-nivel 2 ──────────────────────────
    sub2_data = {}   # (top_id, sub1_id, sub2_id) -> data
    if fetch_districts:
        sub2_tasks = [
            (amb, tid, sid, did, dnom)
            for amb, tid, sid, _ in sub1_tasks
            for did, dnom in sub2_catalog.get(sid, [])
        ]
        with ThreadPoolExecutor(max_workers=40) as ex:
            fmap5 = {
                ex.submit(_fetch_data, "ubigeo_nivel_03", amb, tid, sid, did): (tid, sid, did)
                for amb, tid, sid, did, _ in sub2_tasks
            }
            done = 0
            for fut in as_completed(fmap5):
                key = fmap5[fut]
                sub2_data[key] = fut.result()
                done += 1
                print(f"  dist/ciudad {done}/{len(sub2_tasks)}", end="\r")
        print()

    # ── Stage 6: ensamblaje jerárquico ─────────────────────────────────────
    def assemble(top_obj):
        tid = top_obj["id"]
        sub1s = []
        for sid, snom in sub1_catalog.get(tid, []):
            sd = sub1_data.get((tid, sid), {"totales": {}, "candidatos": None})
            sub2s = []
            if fetch_districts:
                for did, dnom in sub2_catalog.get(sid, []):
                    dd = sub2_data.get((tid, sid, did), {"totales": {}, "candidatos": None})
                    sub2s.append({"id": did, "nombre": dnom,
                                  "grupo": sub2lbl_of[tid], **dd})
            sub1s.append({"id": sid, "nombre": snom,
                           "grupo": sub1lbl_of[tid], **sd, "distritos": sub2s})
        top_obj["provincias"] = sub1s
        return top_obj

    depts_out = [assemble(d) for d in depts_base]
    conts_out  = [assemble(c) for c in conts_base]

    # Resúmenes
    peru_tot = _fetch_data("ambito_geografico", "1",  None)
    ext_tot  = _fetch_data("ambito_geografico", "2",  None)
    full_tot = _fetch_data("eleccion",           None, None)
    for r, label, id_, g in [
        (peru_tot, "Total Perú",       "peru_total",  "Total Perú"),
        (ext_tot,  "Total Extranjero", "ext_total",   "Extranjero Total"),
        (full_tot, "Total Nacional",   "full_total",  "Total Nacional"),
    ]:
        r.update({"nombre": label, "id": id_, "grupo": g})

    return {
        "departamentos": depts_out,
        "continentes":   conts_out,
        "peru_total":    peru_tot,
        "extranjero_total": ext_tot,
        "full_total":    full_tot,
    }

# ── Helpers ────────────────────────────────────────────────────────────────────

def extract(c):
    k = {"votos": None, "pct": None}
    s = {"votos": None, "pct": None}
    if not c:
        return k, s
    items = c if isinstance(c, list) else [c]
    unmatched = []
    for x in items:
        name  = str(x.get("candidato", x.get("nombreCandidato",
                    x.get("nombre", x.get("nombreOrganizacionPolitica",""))))).upper()
        votos = x.get("totalVotos", x.get("votos", x.get("cantVotos", x.get("totalVotosValidos"))))
        pct   = x.get("porcentaje", x.get("porcentajeVotos", x.get("pct", x.get("porcentajeVotosValidos"))))
        if "FUJIMORI" in name or "KEIKO" in name or "FUERZA POPULAR" in name:
            k = {"votos": votos, "pct": pct}
        elif any(w in name for w in ("SANCHEZ","SÁNCHEZ","JUNTOS POR","ROBERTO")):
            s = {"votos": votos, "pct": pct}
        else:
            unmatched.append({"votos": votos, "pct": pct})
    if k["votos"] is None and len(unmatched) >= 1: k = unmatched[0]
    if s["votos"] is None and len(unmatched) >= 2: s = unmatched[1]
    return k, s

def fmt_ts(ts):
    try:
        return datetime.fromtimestamp(ts / 1000).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""

# ── CSV ────────────────────────────────────────────────────────────────────────

def build_csv(data):
    """Genera el CSV completo como string (sin escribir a disco)."""
    import io
    cols = ["Nivel","Grupo","Nombre","ID",
            "% Actas","Actas","Total Actas","Votos Emitidos","Votos Válidos",
            "Keiko Votos","Keiko %","Sánchez Votos","Sánchez %",
            "Líder","Diferencia pp","Actualizado"]

    def make_row(nivel, r):
        t = r.get("totales", {})
        k, s = extract(r.get("candidatos"))
        lider = diff = ""
        if k["pct"] is not None and s["pct"] is not None:
            d = float(k["pct"]) - float(s["pct"])
            lider = "Keiko" if d > 0 else "Sánchez"
            diff  = f"{abs(d):.3f}"
        return {
            "Nivel": nivel,
            "Grupo": r.get("grupo",""), "Nombre": r["nombre"], "ID": r["id"],
            "% Actas": t.get("actasContabilizadas",""), "Actas": t.get("contabilizadas",""),
            "Total Actas": t.get("totalActas",""),
            "Votos Emitidos": t.get("totalVotosEmitidos",""),
            "Votos Válidos":  t.get("totalVotosValidos",""),
            "Keiko Votos":  k["votos"] if k["votos"] is not None else "",
            "Keiko %":      k["pct"]   if k["pct"]   is not None else "",
            "Sánchez Votos":s["votos"] if s["votos"] is not None else "",
            "Sánchez %":    s["pct"]   if s["pct"]   is not None else "",
            "Líder": lider, "Diferencia pp": diff,
            "Actualizado": fmt_ts(t["fechaActualizacion"]) if t.get("fechaActualizacion") else "",
        }

    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    w.writerow(make_row("Total", data["full_total"]))
    w.writerow(make_row("Total", data["peru_total"]))
    for dept in data["departamentos"]:
        w.writerow(make_row("Departamento", dept))
        for prov in dept.get("provincias", []):
            w.writerow(make_row("Provincia", prov))
            for dist in prov.get("distritos", []):
                w.writerow(make_row("Distrito", dist))
    w.writerow(make_row("Total", data["extranjero_total"]))
    for cont in data["continentes"]:
        w.writerow(make_row("Continente", cont))
        for pais in cont.get("provincias", []):
            w.writerow(make_row("País", pais))
            for ciudad in pais.get("distritos", []):
                w.writerow(make_row("Ciudad", ciudad))
    return buf.getvalue()

def save_csv(data, path="resultados.csv"):
    """Genera y escribe el CSV a disco (uso local / loop)."""
    csv_text = build_csv(data)
    with open(os.path.join(HERE, path), "w", newline="", encoding="utf-8-sig") as f:
        f.write(csv_text)
    print(f"CSV: {os.path.join(HERE, path)}")
    return csv_text

# ── HTML helpers ───────────────────────────────────────────────────────────────

ND  = '<span style="color:#484f58;font-style:italic">—</span>'
TH  = "background:#161b22;padding:8px 11px;text-align:left;color:#8b949e;font-weight:600;white-space:nowrap;border-bottom:1px solid #30363d;position:sticky;top:0"

def mini_bar(pct, color):
    w = min(100, max(0, float(pct or 0)))
    return (f'<div style="background:#21262d;border-radius:3px;height:5px;overflow:hidden;margin-top:2px">'
            f'<div style="height:100%;width:{w}%;background:{color}"></div></div>')

def build_row(r, idx, bg, avg_national=201, level=0, toggle_id=None, parent_id=None, start_hidden=True):
    t = r.get("totales", {})
    k, s = extract(r.get("candidatos"))
    ap = t.get("actasContabilizadas")

    if ap is not None:
        w = min(100, max(0, float(ap)))
        ac = (f'<div style="display:flex;align-items:center;gap:6px">'
              f'<div style="background:#21262d;border-radius:3px;height:5px;width:60px;overflow:hidden;flex-shrink:0">'
              f'<div style="height:100%;width:{w}%;background:#388bfd"></div></div>'
              f'<span>{float(ap):.1f}%</span></div>'
              f'<div style="font-size:.7rem;color:#8b949e">'
              f'{int(t.get("contabilizadas") or 0):,} / {int(t.get("totalActas") or 0):,}</div>')
    else:
        ac = ND

    def cand_cells(c, color):
        if c["votos"] is None:
            return ND, ND
        v = f'<div>{int(c["votos"]):,}</div>' + mini_bar(c["pct"], color)
        p = f'<b>{float(c["pct"]):.2f}%</b>'
        return v, p

    kv, kp = cand_cells(k, "#1a6fd4")
    sv, sp = cand_cells(s, "#2ea043")

    lider = dif = ND
    if k["pct"] is not None and s["pct"] is not None:
        d = float(k["pct"]) - float(s["pct"])
        kv_i = int(k["votos"]) if k["votos"] is not None else 0
        sv_i = int(s["votos"]) if s["votos"] is not None else 0
        if kv_i == 0 and sv_i == 0:
            lider = '<span style="color:#484f58;font-size:.69rem">sin datos</span>'
            dif = ND
        else:
            badge_style = ("background:#1a6fd422;color:#79c0ff;border:1px solid #1a6fd466" if d > 0
                           else "background:#2ea04322;color:#56d364;border:1px solid #2ea04366")
            label = "Keiko" if d > 0 else "Sánchez"
            lider = f'<span style="{badge_style};padding:2px 6px;border-radius:20px;font-size:.69rem;font-weight:600">{label}</span>'
            dif = f'{abs(d):.2f} pp'

    vv    = t.get("totalVotosValidos")
    vv_s  = f'{int(vv):,}' if vv is not None else ND
    fecha = fmt_ts(t["fechaActualizacion"]) if t.get("fechaActualizacion") else "—"

    tot_c_r = int(t.get("contabilizadas", 0) or 0)
    jee_r   = int(t.get("enviadasJee",    0) or 0)
    pend_r  = int(t.get("pendientesJee",  0) or 0)
    total_r = jee_r + pend_r
    if total_r == 0:
        rest_cell = '<span style="color:#3fb950;font-size:.85rem">✓</span>'
    else:
        pc   = "#e3b341" if total_r < 50 else "#f0883e" if total_r < 300 else "#f85149"
        vv_i = int(vv or 0)
        vest = int(total_r * vv_i / tot_c_r) if tot_c_r > 0 else int(total_r * avg_national)
        vest_max = total_r * 300
        rest_cell = (
            f'<div style="font-weight:700;color:{pc};font-size:.85rem">~{vest:,} v.</div>'
            f'<div style="font-size:.68rem;color:#8b949e">{total_r:,} actas · máx {vest_max:,}</div>'
        )
        sub = []
        if jee_r:  sub.append(f'JEE:{jee_r}')
        if pend_r: sub.append(f'P:{pend_r}')
        if sub:
            rest_cell += f'<div style="font-size:.62rem;color:#8b949e">{" ".join(sub)}</div>'

    # Nombre con indentación y flecha si tiene hijos
    indent_px = level * 14
    arrow = ""
    if toggle_id:
        arrow = f'<span class="arr" id="arr-{toggle_id}" style="font-size:.7rem;margin-right:4px;color:#8b949e;display:inline-block;width:10px">▶</span>'
    num_cell = f'<span style="color:#8b949e;font-size:.75rem">{idx if idx is not None else ""}</span>'
    nombre_cell = (
        f'<div style="padding-left:{indent_px}px;display:flex;align-items:center;gap:2px">'
        f'{arrow}<span style="font-size:{".85rem" if level >= 2 else ".9rem"}">{r["nombre"]}</span>'
        f'</div>'
    )

    tds = "".join(f"<td>{v}</td>" for v in [
        num_cell, nombre_cell, ac, kv, kp, sv, sp, vv_s, lider,
        f'<span style="white-space:nowrap">{dif}</span>',
        rest_cell,
        f'<span style="color:#8b949e;font-size:.68rem;white-space:nowrap">{fecha}</span>',
    ])

    # Atributos de la fila
    extra_style = "display:none;" if (parent_id and start_hidden) else ""
    cursor_style = "cursor:pointer;" if toggle_id else ""
    row_style = f"background:{bg};{extra_style}{cursor_style}"

    classes = []
    if parent_id:
        classes.append(f"ch-{parent_id}")
    class_attr = f' class="{" ".join(classes)}"' if classes else ""
    data_attr  = f' data-id="{toggle_id}"' if toggle_id else ""
    onclick    = f' onclick="tog(\'{toggle_id}\')"' if toggle_id else ""

    return f'<tr style="{row_style}"{class_attr}{data_attr}{onclick}>{tds}</tr>'

def section_div(label):
    return (f'<tr><td colspan="12" style="background:#1c2128;color:#8b949e;font-weight:700;'
            f'font-size:.72rem;text-transform:uppercase;letter-spacing:.5px;'
            f'padding:6px 11px;border-top:2px solid #30363d;border-bottom:1px solid #30363d">{label}</td></tr>')

def section_toggle(label, tid, expanded=True):
    """Encabezado de sección colapsable (clic muestra/oculta todo el grupo ch-{tid})."""
    arrow = "▼" if expanded else "▶"
    return (f'<tr style="cursor:pointer" data-id="{tid}" onclick="tog(\'{tid}\')">'
            f'<td colspan="12" style="background:#1c2128;color:#8b949e;font-weight:700;'
            f'font-size:.72rem;text-transform:uppercase;letter-spacing:.5px;'
            f'padding:6px 11px;border-top:2px solid #30363d;border-bottom:1px solid #30363d">'
            f'<span class="arr" id="arr-{tid}" style="display:inline-block;width:12px;color:#8b949e">{arrow}</span>'
            f'{label}</td></tr>')

def _sc_row(label, kp, sp, ganador, gap_v):
    gc = "#58a6ff" if ganador == "Keiko" else "#3fb950"
    return (
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:4px 0;border-top:1px solid #21262d;font-size:.77rem;gap:8px;flex-wrap:wrap">'
        f'<span style="color:#8b949e;flex:1;min-width:180px">{label}</span>'
        f'<span>K: <b>{kp:.3f}%</b> · S: <b>{sp:.3f}%</b></span>'
        f'<span style="font-weight:700;color:{gc};white-space:nowrap">'
        f'→ {ganador} +{int(gap_v):,} vts</span>'
        f'</div>'
    )

_HIST_COLS = ["ts", "pct_c", "p_keiko", "margin_med", "k_final", "s_final", "gap_v"]

def _data_dir():
    """Directorio escribible. En Vercel el FS es read-only salvo /tmp."""
    if os.environ.get("VERCEL") or os.environ.get("AWS_LAMBDA_FUNCTION_NAME"):
        return "/tmp"
    return HERE

def log_history(fc, pct_c, gap_v, path="forecast_history.csv"):
    """Persiste un punto del pronóstico. Tolerante a FS read-only (no-op si falla)."""
    if not fc or not fc.get("ok"):
        return
    fp = os.path.join(_data_dir(), path)
    new = {
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pct_c": f"{pct_c:.3f}", "p_keiko": f"{fc['p_keiko']*100:.2f}",
        "margin_med": f"{fc['margin_med']:.0f}",
        "k_final": f"{fc['k_final_pct']:.3f}", "s_final": f"{fc['s_final_pct']:.3f}",
        "gap_v": str(int(gap_v)),
    }
    try:
        exists = os.path.exists(fp)
        last = None
        if exists:
            with open(fp, encoding="utf-8") as f:
                lines = f.read().strip().splitlines()
            if len(lines) >= 2:
                last = dict(zip(_HIST_COLS, lines[-1].split(",")))
        # Dedup: no registrar si el dato subyacente no cambió (mismo % actas y misma brecha)
        if last and last.get("pct_c") == new["pct_c"] and last.get("gap_v") == new["gap_v"]:
            return
        with open(fp, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_HIST_COLS)
            if not exists:
                w.writeheader()
            w.writerow(new)
    except OSError:
        pass   # FS read-only (serverless sin store) → histórico best-effort

def read_history(path="forecast_history.csv", limit=180):
    fp = os.path.join(_data_dir(), path)
    if not os.path.exists(fp):
        return []
    try:
        with open(fp, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return rows[-limit:]
    except Exception:
        return []

def _history_chart(history):
    """Mini gráfico SVG de la evolución de P(Keiko) entre refrescos."""
    pts = [float(r["p_keiko"]) for r in (history or []) if r.get("p_keiko")]
    if len(pts) < 2:
        return ('<div style="margin-top:10px;border-top:1px solid #30363d;padding-top:8px;'
                'font-size:.66rem;color:#8b949e">📈 Histórico P(Keiko): recopilando entre refrescos…</div>')
    W, H, pad = 320, 78, 5
    ymin = max(0.0, min(pts) - 2); ymax = min(100.0, max(pts) + 2)
    if ymax - ymin < 4:
        ymin = max(0.0, ymin - 2); ymax = min(100.0, ymax + 2)
    def X(i): return pad + i * (W - 2 * pad) / (len(pts) - 1)
    def Y(v): return H - pad - (v - ymin) / (ymax - ymin) * (H - 2 * pad)
    poly = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(pts))
    area = f"{X(0):.1f},{H-pad:.1f} " + poly + f" {X(len(pts)-1):.1f},{H-pad:.1f}"
    last, first = pts[-1], pts[0]
    delta = last - first
    arr  = "▲" if delta > 0.05 else "▼" if delta < -0.05 else "▬"
    acol = "#3fb950" if delta > 0.05 else "#f85149" if delta < -0.05 else "#8b949e"
    line50 = ""
    if ymin <= 50 <= ymax:
        y50 = Y(50)
        line50 = (f'<line x1="{pad}" y1="{y50:.1f}" x2="{W-pad}" y2="{y50:.1f}" '
                  f'stroke="#f85149" stroke-width="0.6" stroke-dasharray="3,3" opacity="0.6"/>')
    svg = (
        f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" style="width:100%;height:78px;display:block">'
        f'<polygon points="{area}" fill="#1a6fd433"/>{line50}'
        f'<polyline points="{poly}" fill="none" stroke="#58a6ff" stroke-width="1.5"/>'
        f'<circle cx="{X(len(pts)-1):.1f}" cy="{Y(last):.1f}" r="2.6" fill="#58a6ff"/>'
        f'</svg>'
    )
    t0 = history[0]["ts"][11:16]; t1 = history[-1]["ts"][11:16]
    return (
        f'<div style="margin-top:10px;border-top:1px solid #30363d;padding-top:8px">'
        f'<div style="display:flex;justify-content:space-between;font-size:.67rem;color:#8b949e;'
        f'text-transform:uppercase;letter-spacing:.4px;margin-bottom:3px">'
        f'<span>Histórico P(Keiko) · {len(pts)} ptos</span>'
        f'<span style="color:{acol};font-weight:700">{arr} {delta:+.1f} pp desde inicio</span></div>'
        f'<div style="position:relative">{svg}'
        f'<span style="position:absolute;left:1px;top:-3px;font-size:.6rem;color:#6e7681">{ymax:.0f}%</span>'
        f'<span style="position:absolute;left:1px;bottom:-2px;font-size:.6rem;color:#6e7681">{ymin:.0f}%</span></div>'
        f'<div style="display:flex;justify-content:space-between;font-size:.6rem;color:#6e7681;margin-top:1px">'
        f'<span>{t0}</span><span>ahora {t1} · <b style="color:#58a6ff">{last:.1f}%</b></span></div>'
        f'</div>'
    )

def _forecast_block(fc, history=None):
    """Bloque visual del pronóstico Monte Carlo (P de victoria + IC + histórico + sensibilidad)."""
    if not fc or not fc.get("ok"):
        return ""
    p   = fc["p_keiko"] * 100
    fav = "Keiko" if p >= 50 else "Sánchez"
    favcol = "#58a6ff" if p >= 50 else "#3fb950"
    pfav = p if p >= 50 else 100 - p
    if   pfav >= 95: fuerza = "casi seguro"
    elif pfav >= 85: fuerza = "muy probable"
    elif pfav >= 70: fuerza = "probable"
    elif pfav >= 58: fuerza = "favorito leve"
    else:            fuerza = "moneda al aire"

    med = fc["margin_med"]; p05 = fc["margin_p05"]; p95 = fc["margin_p95"]
    gan_med = fc["ganador_med"]; gcol = "#58a6ff" if gan_med == "Keiko" else "#3fb950"

    # Barra de probabilidad (Keiko azul vs Sánchez verde)
    bar = (
        f'<div style="background:#21262d;border-radius:5px;height:22px;position:relative;overflow:hidden;margin:6px 0 3px">'
        f'<div style="position:absolute;left:0;top:0;height:100%;width:{p:.1f}%;background:#1a6fd4;'
        f'display:flex;align-items:center;padding-left:7px;font-size:.7rem;font-weight:700;color:#fff;white-space:nowrap">Keiko {p:.1f}%</div>'
        f'<div style="position:absolute;right:0;top:0;height:100%;width:{100-p:.1f}%;background:#2ea043;'
        f'display:flex;align-items:center;justify-content:flex-end;padding-right:7px;font-size:.7rem;font-weight:700;color:#fff;white-space:nowrap">{100-p:.1f}% S.</div>'
        f'</div>'
    )

    # Sensibilidad a m (actas observadas que se computan)
    cols = ""
    for mv, pp, _med in fc["sens"]:
        hpx = max(3, pp * 100 * 0.46)
        bcol = "#1a6fd4" if pp >= 50 else "#2ea043"
        cols += (
            f'<div style="flex:1;text-align:center;min-width:40px">'
            f'<div style="font-size:.72rem;font-weight:700;color:#c9d1d9">{pp*100:.0f}%</div>'
            f'<div style="height:50px;display:flex;align-items:flex-end;justify-content:center;margin:2px 0">'
            f'<div style="width:58%;height:{hpx:.0f}px;background:{bcol};border-radius:2px 2px 0 0"></div></div>'
            f'<div style="font-size:.66rem;color:#8b949e;border-top:1px solid #30363d;padding-top:3px">m={mv*100:.0f}%</div>'
            f'</div>'
        )

    pr = fc["params"]
    r1 = fc.get("r1", {})
    r1_badge = ""
    if r1.get("used"):
        r1_badge = (
            f'<span style="display:inline-block;margin-left:8px;padding:1px 8px;border-radius:20px;'
            f'background:#23863622;border:1px solid #2ea04355;color:#56d364;font-size:.62rem;font-weight:700;'
            f'vertical-align:middle">✓ calibrado con 1ª vuelta · r={r1["corr"]:.2f}</span>'
        )
    return (
        f'<div style="background:linear-gradient(180deg,#161b22,#11161d);border:1px solid {favcol}66;'
        f'border-radius:8px;padding:13px 16px;margin-bottom:10px">'
        f'<div style="font-size:.66rem;color:#8b949e;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px">'
        f'Pronóstico final · simulación Monte Carlo ({pr["n_sims"]:,} universos){r1_badge}</div>'
        f'<div style="display:flex;align-items:baseline;gap:10px;flex-wrap:wrap">'
        f'<span style="font-size:2.1rem;font-weight:800;color:{favcol};line-height:1">{pfav:.1f}%</span>'
        f'<span style="font-size:.82rem;color:#c9d1d9">probabilidad de que gane <b style="color:{favcol}">{fav}</b> '
        f'<span style="color:#8b949e">· {fuerza}</span></span>'
        f'</div>'
        f'{bar}'
        f'<div style="display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:.78rem;align-items:baseline;margin-top:8px">'
        f'<span style="color:#8b949e">Resultado proyectado</span>'
        f'<span><b style="color:#58a6ff">Keiko {fc["k_final_pct"]:.3f}%</b> &nbsp;—&nbsp; <b style="color:#3fb950">Sánchez {fc["s_final_pct"]:.3f}%</b></span>'
        f'<span style="color:#8b949e">Margen final (mediana)</span>'
        f'<span style="font-weight:700;color:{gcol}">{gan_med} +{abs(med):,.0f} votos</span>'
        f'<span style="color:#8b949e">Rango probable (90%)</span>'
        f'<span>{"Keiko" if p05>0 else "Sánchez"} +{abs(p05):,.0f} &nbsp;a&nbsp; {"Keiko" if p95>0 else "Sánchez"} +{abs(p95):,.0f}</span>'
        f'</div>'
        f'{_history_chart(history)}'
        f'<div style="margin-top:10px;border-top:1px solid #30363d;padding-top:8px">'
        f'<div style="font-size:.67rem;color:#8b949e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:2px">'
        f'P(Keiko) según cuántas actas OBSERVADAS (en disputa JEE) se computen</div>'
        f'<div style="font-size:.66rem;color:#8b949e;margin-bottom:4px">'
        f'el {fc["net_jee"]/(fc["net_clean"]+fc["net_jee"])*100:.0f}% de la vuelta depende de ellas → este es el riesgo clave</div>'
        f'<div style="display:flex;gap:4px;align-items:flex-end">{cols}</div>'
        f'</div>'
        f'<div style="margin-top:8px;font-size:.63rem;color:#6e7681;line-height:1.4">'
        + (f'Calibrado con 1ª vuelta: k2 = {r1["a"]:.2f} + {r1["b"]:.2f}·(Keiko head-to-head R1), '
           f'r={r1["corr"]:.3f}, error ±{r1["resid_std"]*100:.1f}pp. ' if r1.get("used") else "")
        + f'Cada unidad pendiente mezcla por precisión su dato local R2 con la predicción R1 '
        f'(domina R1 donde hay poco conteo: exterior, observadas). Incertidumbre: swing sistémico '
        f'σ={pr["sigma_sys"]*100:.1f}pp + materialización de observadas m~Beta({pr["m_mean"]*100:.0f}%). '
        f'Parámetros ajustables.</div>'
        f'</div>'
    )

# ── JavaScript para expand/collapse ───────────────────────────────────────────

TOGGLE_JS = """
<script>
function tog(id) {
  var rows = document.querySelectorAll('.ch-' + id);
  if (!rows.length) return;
  var show = rows[0].style.display === 'none';
  rows.forEach(function(el) {
    el.style.display = show ? '' : 'none';
    if (!show && el.dataset && el.dataset.id) collapseAll(el.dataset.id);
  });
  var arr = document.getElementById('arr-' + id);
  if (arr) arr.textContent = show ? '▼' : '▶';
  saveState();
}
function collapseAll(id) {
  document.querySelectorAll('.ch-' + id).forEach(function(el) {
    el.style.display = 'none';
    if (el.dataset && el.dataset.id) collapseAll(el.dataset.id);
  });
  var arr = document.getElementById('arr-' + id);
  if (arr) arr.textContent = '▶';
}
// Pone un toggle en estado abierto/cerrado (idempotente). No actúa si su fila está oculta.
function setOpen(id, open) {
  var arr = document.getElementById('arr-' + id);
  if (arr) { var row = arr.closest('tr'); if (row && row.style.display === 'none') return; }
  var ch = document.querySelectorAll('.ch-' + id);
  if (!ch.length) return;
  var isOpen = ch[0].style.display !== 'none';
  if (open !== isOpen) tog(id);
}
// Persistencia del estado de colapso (sobrevive al auto-refresh de 60s)
function saveState() {
  var open = [];
  document.querySelectorAll('[id^="arr-"]').forEach(function(a) {
    if (a.textContent.indexOf('\\u25BC') >= 0) open.push(a.id.slice(4));
  });
  try { localStorage.setItem('onpe_open', JSON.stringify(open)); } catch (e) {}
}
function restoreState() {
  var open = null;
  try { var s = localStorage.getItem('onpe_open'); if (s !== null) open = JSON.parse(s); } catch (e) {}
  if (open === null) return;               // primera visita: deja los defaults renderizados
  var set = {}; open.forEach(function(id){ set[id] = 1; });
  // Reconciliar de arriba hacia abajo: secciones, luego depts/continentes, luego provincias/países
  ['peru','ext'].forEach(function(id){ setOpen(id, !!set[id]); });
  document.querySelectorAll('[id^="arr-d-"]').forEach(function(a){ setOpen(a.id.slice(4), !!set[a.id.slice(4)]); });
  document.querySelectorAll('[id^="arr-p-"]').forEach(function(a){ setOpen(a.id.slice(4), !!set[a.id.slice(4)]); });
}
// Botones globales
function expandAllTop() {
  ['peru','ext'].forEach(function(id){ setOpen(id, true); });
  document.querySelectorAll('[id^="arr-d-"]').forEach(function(a){ setOpen(a.id.slice(4), true); });
}
function collapseAllTop() {
  ['peru','ext'].forEach(function(id){ setOpen(id, false); });
}
document.addEventListener('DOMContentLoaded', restoreState);
</script>
"""

# ── save_html ─────────────────────────────────────────────────────────────────

def build_html(data, auto_refresh=0):
    """Genera el HTML completo del dashboard como string (sin escribir a disco)."""
    depts    = data["departamentos"]
    conts    = data["continentes"]
    ext      = data["extranjero_total"]
    peru_tot = data["peru_total"]
    full_tot = data["full_total"]

    ft   = full_tot.get("totales", {})
    fk, fs = extract(full_tot.get("candidatos"))
    pct_k = fk["pct"]
    pct_s = fs["pct"]
    tot_k = int(fk["votos"]) if fk["votos"] is not None else 0
    tot_s = int(fs["votos"]) if fs["votos"] is not None else 0
    tot_v = int(ft.get("totalVotosValidos",  0) or 0)
    tot_e = int(ft.get("totalVotosEmitidos", 0) or 0)
    tot_a = int(ft.get("totalActas",         0) or 0)
    tot_c = int(ft.get("contabilizadas",     0) or 0)
    pct_c = float(ft.get("actasContabilizadas", 0) or 0)
    tot_jee  = int(ft.get("enviadasJee",   0) or 0)
    tot_pend = int(ft.get("pendientesJee", 0) or 0)
    pct_jee  = float(ft.get("actasEnviadasJee",   0) or 0)
    pct_pend = float(ft.get("actasPendientesJee", 0) or 0)
    gen_ts   = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    avg_vpa    = tot_v / max(tot_c, 1)
    actas_falt = tot_jee + tot_pend
    votos_rest = int(actas_falt * avg_vpa)
    gap_v      = abs(tot_k - tot_s)
    lider_n    = "Keiko" if tot_k > tot_s else "Sánchez"
    rezag_n    = "Sánchez" if tot_k > tot_s else "Keiko"
    gap_pct_f  = abs(float(pct_k or 0) - float(pct_s or 0))
    rezag_pct_f = float(pct_k or 0) if lider_n == "Sánchez" else float(pct_s or 0)
    pct_nec    = 50.0 + (gap_v / 2.0 / max(votos_rest, 1) * 100) if votos_rest > 0 else None
    brecha_r   = (pct_nec - rezag_pct_f) if pct_nec is not None else None

    # Proyecciones
    k_pct_nat = float(pct_k or 0) / 100
    s_pct_nat = float(pct_s or 0) / 100
    k_sc_est = float(tot_k); s_sc_est = float(tot_s)
    for reg in depts + conts:
        t_r  = reg.get("totales", {})
        c_r  = int(t_r.get("contabilizadas", 0) or 0)
        tp_r = int(t_r.get("enviadasJee", 0) or 0) + int(t_r.get("pendientesJee", 0) or 0)
        if tp_r == 0:
            continue
        vv_r = int(t_r.get("totalVotosValidos", 0) or 0)
        ar_r = vv_r / c_r if c_r > 0 else avg_vpa
        vp_r = tp_r * ar_r
        kr_, sr_ = extract(reg.get("candidatos"))
        kp_r = float(kr_["pct"]) / 100 if (kr_["pct"] is not None and c_r > 0) else k_pct_nat
        sp_r = float(sr_["pct"]) / 100 if (sr_["pct"] is not None and c_r > 0) else s_pct_nat
        k_sc_est += vp_r * kp_r
        s_sc_est += vp_r * sp_r

    tot_sc_est = k_sc_est + s_sc_est
    if tot_sc_est > 0:
        k_pct_sc_est = k_sc_est / tot_sc_est * 100
        s_pct_sc_est = s_sc_est / tot_sc_est * 100
    else:
        k_pct_sc_est = s_pct_sc_est = 0.0
    gan_est  = "Keiko" if k_sc_est > s_sc_est else "Sánchez"
    gap_est  = abs(k_sc_est - s_sc_est)

    k_sc_max   = float(tot_k) + votos_rest
    s_sc_max   = float(tot_s)
    tot_sc_max = k_sc_max + s_sc_max
    if tot_sc_max > 0:
        k_pct_sc_max = k_sc_max / tot_sc_max * 100
        s_pct_sc_max = s_sc_max / tot_sc_max * 100
    else:
        k_pct_sc_max = s_pct_sc_max = 0.0
    gan_max = "Keiko" if k_sc_max > s_sc_max else "Sánchez"
    gap_max = abs(k_sc_max - s_sc_max)

    # ── Cards y barra nacional ─────────────────────────────────────────────
    if pct_k is not None:
        nac = (
            f'<div style="margin-bottom:16px">'
            f'<div style="font-size:.7rem;color:#8b949e;margin-bottom:2px">RESULTADO NACIONAL + EXTERIOR</div>'
            f'<div style="background:#21262d;border-radius:5px;height:24px;position:relative;overflow:hidden;margin:6px 0 4px">'
            f'<div style="position:absolute;left:0;top:0;height:100%;width:{pct_k:.3f}%;background:#1a6fd4;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;color:#fff;overflow:hidden;padding:0 7px">{pct_k:.3f}% Keiko</div>'
            f'<div style="position:absolute;right:0;top:0;height:100%;width:{pct_s:.3f}%;background:#2ea043;display:flex;align-items:center;justify-content:center;font-size:.75rem;font-weight:700;color:#fff;overflow:hidden;padding:0 7px">Sánchez {pct_s:.3f}%</div>'
            f'</div>'
            f'<div style="display:flex;justify-content:space-between;font-size:.71rem;color:#8b949e">'
            f'<span>{tot_k:,} votos</span><span>{tot_s:,} votos</span></div></div>'
        )
        cand_cards = (
            f'<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:11px 16px;flex:1;min-width:150px">'
            f'<div style="font-size:.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:3px">Keiko — Fuerza Popular</div>'
            f'<div style="font-size:1.45rem;font-weight:700;color:#58a6ff">{pct_k:.3f}%</div>'
            f'<div style="font-size:.73rem;color:#8b949e;margin-top:2px">{tot_k:,} votos</div></div>'
            f'<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:11px 16px;flex:1;min-width:150px">'
            f'<div style="font-size:.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:3px">Sánchez — Juntos por el Perú</div>'
            f'<div style="font-size:1.45rem;font-weight:700;color:#3fb950">{pct_s:.3f}%</div>'
            f'<div style="font-size:.73rem;color:#8b949e;margin-top:2px">{tot_s:,} votos</div></div>'
        )
        # Pronóstico Monte Carlo (encabezado de la tarjeta)
        fc = None
        if _HAS_FORECAST:
            try:
                fc = _forecast_mod.montecarlo_forecast(data, extract, here=HERE)
            except Exception as _e:
                fc = None
        log_history(fc, pct_c, gap_v)
        forecast_html = _forecast_block(fc, read_history())

        if brecha_r is not None:
            v_color = "#3fb950" if brecha_r <= 0 else "#e3b341" if brecha_r <= 3 else "#f0883e" if brecha_r <= 6 else "#f85149"
            v_label = "Ya cubierta" if brecha_r <= 0 else "Matemáticamente posible" if brecha_r <= 3 else "Difícil" if brecha_r <= 6 else "Muy difícil"
            vuelta_card = (
                f'<div style="background:#161b22;border:1px solid {v_color}55;border-radius:8px;padding:11px 16px;flex:2;min-width:280px">'
                + forecast_html
                + f'<div style="font-size:.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:7px">Mecánica — ¿puede <b style="color:{v_color}">{rezag_n}</b> dar la vuelta?</div>'
                f'<div style="display:grid;grid-template-columns:auto 1fr;gap:3px 12px;font-size:.78rem;align-items:baseline">'
                f'<span style="color:#8b949e">Brecha actual</span>'
                f'<span style="font-weight:700">{gap_v:,} votos · {gap_pct_f:.3f} pp</span>'
                f'<span style="color:#8b949e">Actas sin contar</span>'
                f'<span>{actas_falt:,} (<span style="color:#58a6ff">JEE:{tot_jee:,}</span> + <span style="color:#e3b341">Pend:{tot_pend:,}</span>)</span>'
                f'<span style="color:#8b949e">Votos restantes</span>'
                f'<span>~{votos_rest:,} est. · máx {actas_falt*300:,} <span style="color:#8b949e;font-size:.7rem">({avg_vpa:.0f} vts/acta · máx 300)</span></span>'
                f'<span style="color:#8b949e">{rezag_n} necesita</span>'
                f'<span style="font-weight:700;color:{v_color}">{pct_nec:.2f}% de los restantes (solo con actas limpias)</span>'
                f'<span style="color:#8b949e">Tiene actualmente</span>'
                f'<span>{rezag_pct_f:.3f}% → debe subir <b style="color:{v_color}">+{brecha_r:.2f} pp</b></span>'
                f'</div>'
                f'<div style="margin-top:10px;border-top:1px solid #30363d;padding-top:8px">'
                f'<div style="font-size:.67rem;color:#8b949e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:6px">Anclas deterministas (sin incertidumbre)</div>'
                + _sc_row("Estable — proporción actual de cada distrito", k_pct_sc_est, s_pct_sc_est, gan_est, gap_est)
                + _sc_row(f"Máx. Keiko (~{votos_rest:,} restantes todos a Keiko)", k_pct_sc_max, s_pct_sc_max, gan_max, gap_max)
                + f'</div>'
                f'</div>'
            )
        elif forecast_html:
            vuelta_card = (
                f'<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:11px 16px;flex:2;min-width:280px">'
                + forecast_html + '</div>'
            )
        else:
            vuelta_card = ""
    else:
        nac = '<div style="color:#8b949e;font-size:.8rem;padding:10px">Datos no disponibles.</div>'
        cand_cards = vuelta_card = ""

    actas_bar = (
        f'<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:10px 16px;margin-bottom:11px;display:flex;gap:20px;flex-wrap:wrap;align-items:center">'
        f'<div style="font-size:.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:.4px;font-weight:600">Actas ({tot_a:,} total)</div>'
        f'<div style="display:flex;align-items:center;gap:6px;font-size:.8rem"><span style="width:10px;height:10px;border-radius:50%;background:#1a6fd4;display:inline-block"></span><b>Contabilizadas</b>&nbsp;{tot_c:,}&nbsp;<span style="color:#8b949e">({pct_c:.2f}%)</span></div>'
        f'<div style="display:flex;align-items:center;gap:6px;font-size:.8rem"><span style="width:10px;height:10px;border-radius:50%;background:#58a6ff;display:inline-block"></span><b>Para JEE</b>&nbsp;{tot_jee:,}&nbsp;<span style="color:#8b949e">({pct_jee:.2f}%)</span></div>'
        f'<div style="display:flex;align-items:center;gap:6px;font-size:.8rem"><span style="width:10px;height:10px;border-radius:50%;border:2px solid #8b949e;display:inline-block"></span><b>Pendientes</b>&nbsp;{tot_pend:,}&nbsp;<span style="color:#8b949e">({pct_pend:.2f}%)</span></div>'
        f'</div>'
    )
    cards = (
        f'<div style="display:flex;gap:11px;margin-bottom:11px;flex-wrap:wrap">'
        f'<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:11px 16px;flex:1;min-width:150px">'
        f'<div style="font-size:.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:3px">Actas contabilizadas</div>'
        f'<div style="font-size:1.45rem;font-weight:700">{pct_c:.2f}%</div>'
        f'<div style="font-size:.73rem;color:#8b949e;margin-top:2px">{tot_c:,} / {tot_a:,}</div></div>'
        f'{cand_cards}'
        f'<div style="background:#161b22;border:1px solid #30363d;border-radius:8px;padding:11px 16px;flex:1;min-width:150px">'
        f'<div style="font-size:.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:.4px;margin-bottom:3px">Votos válidos</div>'
        f'<div style="font-size:1.45rem;font-weight:700">{tot_v:,}</div>'
        f'<div style="font-size:.73rem;color:#8b949e;margin-top:2px">Emitidos: {tot_e:,}</div></div>'
        f'</div>'
        + (f'<div style="margin-bottom:11px">{vuelta_card}</div>' if vuelta_card else "")
        + actas_bar
    )

    # ── Botones de control ─────────────────────────────────────────────────
    has_sub2 = (
        any(bool(prov.get("distritos")) for dept in depts for prov in dept.get("provincias", []))
        or any(bool(p.get("distritos")) for cont in conts for p in cont.get("provincias", []))
    )
    ctrl_hint = (
        "Clic en Perú / Exterior para plegar la sección. Dentro: dept → prov → dist."
        if has_sub2 else
        "Clic en Perú / Exterior, o en departamento/continente, para plegar."
    )
    btn = "background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:5px;padding:3px 10px;font-size:.72rem;cursor:pointer"
    ctrl_bar = (
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap">'
        f'<span style="font-size:.72rem;color:#8b949e">{ctrl_hint}</span>'
        f'<button onclick="expandAllTop()" style="{btn}">Expandir</button>'
        f'<button onclick="collapseAllTop()" style="{btn}">Colapsar todo</button>'
        f'<span style="font-size:.66rem;color:#6e7681">el estado se recuerda entre refrescos</span>'
        f'</div>'
    )

    # ── Filas de la tabla ──────────────────────────────────────────────────
    BG1, BG2, BG_TOT = "#0d1117", "rgba(22,27,34,.55)", "#161b22"
    rows = [
        section_div("🌐 Total Nacional (Perú + Exterior)"),
        build_row(full_tot, None, BG_TOT, avg_vpa),
        section_toggle("🇵🇪 Perú", "peru", expanded=False),
        build_row(peru_tot, None, BG_TOT, avg_vpa, parent_id="peru", start_hidden=True),
    ]
    for i, dept in enumerate(depts):
        d_id = f"d-{dept['id']}"
        rows.append(build_row(dept, i+1, BG1 if i%2==0 else BG2, avg_vpa,
                               level=1, toggle_id=d_id, parent_id="peru", start_hidden=True))
        for prov in dept.get("provincias", []):
            p_id = f"p-{prov['id']}"
            has_children = bool(prov.get("distritos"))
            rows.append(build_row(prov, None, BG2, avg_vpa,
                                   level=2,
                                   toggle_id=p_id if has_children else None,
                                   parent_id=d_id))
            for dist in prov.get("distritos", []):
                rows.append(build_row(dist, None, BG1, avg_vpa,
                                       level=3, parent_id=p_id))

    rows.append(section_toggle("🌍 Exterior", "ext", expanded=False))
    rows.append(build_row(ext, None, BG_TOT, avg_vpa, parent_id="ext", start_hidden=True))
    for i, cont in enumerate(conts):
        c_id = f"d-{cont['id']}"
        rows.append(build_row(cont, None, BG1 if i%2==0 else BG2, avg_vpa,
                               level=1, toggle_id=c_id, parent_id="ext", start_hidden=True))
        for pais in cont.get("provincias", []):
            p_id = f"p-{pais['id']}"
            has_cities = bool(pais.get("distritos"))
            rows.append(build_row(pais, None, BG2, avg_vpa,
                                   level=2,
                                   toggle_id=p_id if has_cities else None,
                                   parent_id=c_id))
            for ciudad in pais.get("distritos", []):
                rows.append(build_row(ciudad, None, BG1, avg_vpa,
                                       level=3, parent_id=p_id))

    ths = "".join(f'<th style="{TH}">{h}</th>'
                  for h in ["#","Región","Actas","Keiko votos","Keiko %",
                             "Sánchez votos","Sánchez %","Votos válidos",
                             "Líder","Diferencia","Pend. / est.","Actualizado"])

    refresh_tag = f"<meta http-equiv='refresh' content='{auto_refresh if auto_refresh else 60}'>\n"
    html = (
        "<!DOCTYPE html>\n<html lang='es'>\n<head>\n"
        "<meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>\n"
        + refresh_tag +
        "<title>Resultados ONPE 2026 — Segunda Vuelta</title>\n"
        "<style>\n"
        "*{box-sizing:border-box;margin:0;padding:0}\n"
        "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh}\n"
        "header{background:#161b22;border-bottom:1px solid #30363d;padding:13px 22px;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px}\n"
        "h1{font-size:1rem;font-weight:700;color:#58a6ff}\n"
        ".sub{font-size:.75rem;color:#8b949e;margin-top:2px}\n"
        "a.btn{padding:4px 13px;background:#21262d;color:#58a6ff;border:1px solid #30363d;border-radius:6px;text-decoration:none;font-size:.78rem}\n"
        "a.btn:hover{background:#30363d}\n"
        ".c{padding:18px 22px}\n"
        ".tw{overflow-x:auto;border-radius:8px;border:1px solid #30363d}\n"
        "table{width:100%;border-collapse:collapse;font-size:.8rem}\n"
        "td{padding:5px 10px;border-bottom:1px solid #21262d;vertical-align:middle}\n"
        "tr:hover td{background:#1c2128!important}\n"
        "tr:last-child td{border-bottom:none}\n"
        "</style>\n"
        + TOGGLE_JS +
        "</head>\n<body>\n"
        "<header>\n"
        f"  <div><h1>Elección Presidencial Perú 2026 — Segunda Vuelta</h1>\n"
        f"  <div class='sub'>Fuente: ONPE &nbsp;·&nbsp; Generado: {gen_ts}</div></div>\n"
        "  <a href='resultados.csv' class='btn' download>⬇ CSV</a>\n"
        "</header>\n"
        "<div class='c'>\n"
        + nac + cards + ctrl_bar
        + "<div class='tw'><table>\n"
        + f"<thead><tr>{ths}</tr></thead>\n"
        + "<tbody>\n"
        + "\n".join(rows)
        + "\n</tbody></table></div>\n"
        + "</div>\n</body>\n</html>"
    )
    return html

def save_html(data, path="resultados.html", auto_refresh=0):
    """Genera y escribe el HTML a disco (uso local / loop)."""
    html = build_html(data, auto_refresh=auto_refresh)
    with open(os.path.join(HERE, path), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML: {os.path.join(HERE, path)}")
    return html

# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import time, argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop",         action="store_true", help="Repetir automáticamente")
    ap.add_argument("--interval",     type=int, default=60,  help="Segundos entre corridas (default 60)")
    ap.add_argument("--no-districts", action="store_true",   help="Solo departamentos + provincias (más rápido)")
    args = ap.parse_args()

    fetch_dists = not args.no_districts
    lib = 'curl_cffi' if _USE_CFFI else 'requests'

    while True:
        t0 = datetime.now()
        niveles = "dept+prov" if not fetch_dists else "dept+prov+dist"
        print(f"\n[{t0.strftime('%H:%M:%S')}] Descargando {niveles} ({lib})...")
        data = fetch_all(fetch_districts=fetch_dists)
        save_csv(data)
        save_html(data, auto_refresh=args.interval if args.loop else 0)
        elapsed = (datetime.now() - t0).total_seconds()
        print(f"Listo en {elapsed:.1f}s")
        if not args.loop:
            break
        next_run = datetime.fromtimestamp(t0.timestamp() + args.interval)
        print(f"Próxima actualización: {next_run.strftime('%H:%M:%S')} (Ctrl+C para detener)")
        time.sleep(args.interval)
