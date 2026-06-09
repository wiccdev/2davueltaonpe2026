#!/usr/bin/env python3
"""
forecast.py — Pronóstico probabilístico Monte Carlo del resultado final,
CALIBRADO con la primera vuelta (r1_data.json).

Resultado final = brecha_actual + aporte_neto_del_reservorio_pendiente.
Para cada unidad pendiente, la proporción Keiko proyectada es una MEZCLA
PONDERADA POR PRECISIÓN entre:
  (a) su dato local R2 contado          — preciso si hay muchas actas contadas
  (b) la predicción desde 1ra vuelta    — regresión k2 = a + b·(Keiko head-to-head R1)
La 1ra vuelta (validada: r=0.96, -49% RMSE) domina donde el dato R2 es escaso
(exterior parcialmente contado, actas observadas, distritos con 0 actas).

Incertidumbres propagadas:
  - estimación por unidad (local + R1, combinadas por precisión)
  - swing sistemático global correlacionado (sigma_sys)
  - actas OBSERVADAS (JEE): swing extra + fracción que se computa (m ~ Beta)
"""
import json, os
import numpy as np

def _tot(n):  return n.get("totales", {}) or {}
def _gi(t, k): return int(t.get(k, 0) or 0)
def _gf(t, k): return float(t.get(k, 0) or 0)


def _extract_pct(extract_fn, node, c_local):
    k, s = extract_fn(node.get("candidatos"))
    if k["pct"] is None or s["pct"] is None or c_local <= 0:
        return None, None
    return float(k["pct"]) / 100.0, float(s["pct"]) / 100.0


def load_r1(here):
    # Busca r1_data.json en varios lugares (robustez ante el bundling de Vercel)
    candidates = [
        here,
        os.path.dirname(os.path.abspath(__file__)),
        os.getcwd(),
        os.path.join(os.getcwd(), "api"),
        "/var/task", os.path.join("/var/task", "api"),
    ]
    seen = set()
    for d in candidates:
        if not d or d in seen:
            continue
        seen.add(d)
        path = os.path.join(d, "r1_data.json")
        if os.path.exists(path):
            try:
                return json.load(open(path, encoding="utf-8"))
            except Exception:
                continue
    return None


def _r1_2w(R1, ub):
    """Keiko head-to-head vs Sánchez en 1ra vuelta: K1/(K1+S1). None si no hay dato."""
    if not R1:
        return None
    r = R1.get(ub)
    if not r:
        return None
    k, s = r.get("k"), r.get("s")
    if k is None or s is None or (k + s) <= 0:
        return None
    return k / (k + s)


def fit_r1_regression(data, R1, extract_fn):
    """
    Ajusta k2 = a + b·k1_2w sobre hojas R2 BIEN contadas (>=90% y >=8 actas),
    ponderada por votos. Devuelve (a, b, resid_std, n, corr).
    """
    xs, ys, ws, ambs = [], [], [], []
    for amb, tops in [("PE", data["departamentos"]), ("EX", data["continentes"])]:
        for top in tops:
            for p in top.get("provincias", []):
                leaves = p.get("distritos", []) or [p]
                for leaf in leaves:
                    t = _tot(leaf); c = _gi(t, "contabilizadas")
                    if c < 8 or _gf(t, "actasContabilizadas") < 90:
                        continue
                    k2c, _s = _extract_pct(extract_fn, leaf, c)
                    if k2c is None:
                        continue
                    x = _r1_2w(R1, leaf["id"])
                    if x is None:
                        continue
                    xs.append(x); ys.append(k2c); ws.append(_gi(t, "totalVotosValidos")); ambs.append(amb)
    if len(xs) < 30:
        return None
    x = np.array(xs); y = np.array(ys); w = np.array(ws, dtype=float); amb = np.array(ambs)
    mx, my = np.average(x, weights=w), np.average(y, weights=w)
    vx = np.average((x - mx) ** 2, weights=w)
    b = np.average((x - mx) * (y - my), weights=w) / vx
    a = my - b * mx
    resid = y - (a + b * x)
    resid_std = float(np.sqrt(np.average(resid ** 2, weights=w)))
    corr = float(np.average((x - mx) * (y - my), weights=w) /
                 np.sqrt(vx * np.average((y - my) ** 2, weights=w)))
    # Residual por dominio: el exterior es más difícil de predecir → más incertidumbre
    def dom_std(mask, fallback):
        if mask.sum() < 8:
            return fallback
        return float(np.sqrt(np.average(resid[mask] ** 2, weights=w[mask])))
    rstd_pe = dom_std(amb == "PE", resid_std)
    rstd_ex = dom_std(amb == "EX", max(resid_std * 2.5, 0.12))   # piso prudente si pocos datos
    return {"a": float(a), "b": float(b), "resid_std": resid_std,
            "resid_std_pe": rstd_pe, "resid_std_ex": rstd_ex,
            "n": len(xs), "n_ex": int((amb == "EX").sum()), "corr": corr}


def pending_units(data, extract_fn, R1=None):
    """
    Recorre la jerarquía hasta la hoja más granular con actas pendientes.
    Por unidad: pend_clean, pend_jee, vpa, k (local R2 con respaldo),
                c_base (actas contadas que sustentan k),
                k1_2w (Keiko head-to-head R1, con respaldo hoja->prov->dept->nac).
    """
    ft = _tot(data["full_total"])
    fk, fs = extract_fn(data["full_total"].get("candidatos"))
    k_nat = float(fk["pct"]) / 100.0
    c_nat = _gi(ft, "contabilizadas")
    vpa_nat = _gi(ft, "totalVotosValidos") / max(c_nat, 1)
    r1_nat = _r1_2w(R1, "__full__")

    units = []
    tops = [(d, "PE") for d in data["departamentos"]] + \
           [(c, "EX") for c in data["continentes"]]

    for top, _amb in tops:
        t_top = _tot(top); c_top = _gi(t_top, "contabilizadas")
        k_top, _s = _extract_pct(extract_fn, top, c_top)
        cb_top = c_top
        if k_top is None: k_top, cb_top = k_nat, c_nat
        vpa_top = (_gi(t_top, "totalVotosValidos") / c_top) if c_top > 0 else vpa_nat
        r1_top = _r1_2w(R1, top["id"]) or r1_nat
        label = top["nombre"]

        for sub1 in top.get("provincias", []):
            t_p = _tot(sub1); c_p = _gi(t_p, "contabilizadas")
            k_p, _s = _extract_pct(extract_fn, sub1, c_p)
            cb_p = c_p
            if k_p is None: k_p, cb_p = k_top, cb_top
            vpa_p = (_gi(t_p, "totalVotosValidos") / c_p) if c_p > 0 else vpa_top
            r1_p = _r1_2w(R1, sub1["id"]) or r1_top

            leaves = sub1.get("distritos", [])
            if leaves:
                for leaf in leaves:
                    t_l = _tot(leaf); c_l = _gi(t_l, "contabilizadas")
                    pc = _gi(t_l, "pendientesJee"); pj = _gi(t_l, "enviadasJee")
                    if pc + pj == 0: continue
                    k_l, _s = _extract_pct(extract_fn, leaf, c_l)
                    cb_l = c_l
                    if k_l is None: k_l, cb_l = k_p, cb_p
                    vpa_l = (_gi(t_l, "totalVotosValidos") / c_l) if c_l > 0 else vpa_p
                    r1_l = _r1_2w(R1, leaf["id"]) or r1_p
                    units.append({"pend_clean": pc, "pend_jee": pj,
                                  "vpa": min(vpa_l, 300.0), "k": k_l,
                                  "c_base": cb_l, "k1_2w": r1_l, "amb": _amb, "top": label})
            else:
                pc = _gi(t_p, "pendientesJee"); pj = _gi(t_p, "enviadasJee")
                if pc + pj == 0: continue
                units.append({"pend_clean": pc, "pend_jee": pj,
                              "vpa": min(vpa_p, 300.0), "k": k_p,
                              "c_base": cb_p, "k1_2w": r1_p, "amb": _amb, "top": label})
    return units


def _beta_params(mean, sd):
    mean = min(max(mean, 1e-3), 1 - 1e-3)
    max_var = mean * (1 - mean)
    var = min(sd * sd, max_var * 0.98)
    k = mean * (1 - mean) / var - 1
    return mean * k, (1 - mean) * k


def montecarlo_forecast(data, extract_fn, *,
                        here=None,
                        n_sims=20000,
                        s_acta=0.10,
                        bias_floor=0.020,     # sesgo irreducible pendiente-vs-contado por unidad
                        sigma_sys=0.012,      # swing sistemático global correlacionado
                        sigma_jee=0.025,      # swing extra de actas OBSERVADAS
                        s_jee_unit=0.03,      # idiosincrático por unidad observada
                        m_mean=0.85, m_sd=0.15,  # materialización actas observadas
                        seed=12345):
    if here is None:
        here = os.path.dirname(os.path.abspath(__file__))
    ft = _tot(data["full_total"])
    fk, fs = extract_fn(data["full_total"].get("candidatos"))
    tot_k = int(fk["votos"]); tot_s = int(fs["votos"])
    tot_v = _gi(ft, "totalVotosValidos")
    base_gap = tot_k - tot_s

    R1 = load_r1(here)
    reg = fit_r1_regression(data, R1, extract_fn) if R1 else None

    units = pending_units(data, extract_fn, R1)
    if not units:
        return {"ok": False}

    pend_clean = np.array([u["pend_clean"] for u in units], dtype=float)
    pend_jee   = np.array([u["pend_jee"]   for u in units], dtype=float)
    vpa        = np.array([u["vpa"]        for u in units], dtype=float)
    kloc       = np.array([u["k"]          for u in units], dtype=float)
    c_base     = np.array([u["c_base"]     for u in units], dtype=float)

    vp_clean = pend_clean * vpa
    vp_jee   = pend_jee   * vpa
    v_rest_total = float((vp_clean + vp_jee).sum())

    # ── Centro y sigma por unidad: mezcla por precisión (local R2 + R1) ──────
    n_units = len(units)
    k_center = np.empty(n_units)
    sig_unit = np.empty(n_units)
    n_r1_used = 0
    for i, u in enumerate(units):
        # precisión local: error muestral + piso de sesgo de no-respuesta
        sig_loc = np.sqrt((s_acta / np.sqrt(max(c_base[i], 1.0))) ** 2 + bias_floor ** 2)
        prec_loc = 1.0 / sig_loc ** 2 if c_base[i] > 0 else 0.0
        k_loc = kloc[i]
        # precisión R1 (si hay regresión y dato R1 para la unidad)
        if reg and u["k1_2w"] is not None:
            k_r1 = min(max(reg["a"] + reg["b"] * u["k1_2w"], 0.0), 1.0)
            rstd = reg["resid_std_ex"] if u.get("amb") == "EX" else reg["resid_std_pe"]
            prec_r1 = 1.0 / rstd ** 2
            n_r1_used += 1
        else:
            k_r1, prec_r1 = 0.0, 0.0
        prec = prec_loc + prec_r1
        if prec > 0:
            k_center[i] = (prec_loc * k_loc + prec_r1 * k_r1) / prec
            sig_unit[i] = 1.0 / np.sqrt(prec)
        else:
            k_center[i] = k_loc
            sig_unit[i] = 0.08

    rng = np.random.default_rng(seed)

    def simulate(n, m_draw):
        delta     = rng.normal(0.0, sigma_sys, n)
        delta_jee = rng.normal(0.0, sigma_jee, n)
        if m_draw is None:
            a, b = _beta_params(m_mean, m_sd)
            m = rng.beta(a, b, n)
        else:
            m = np.full(n, float(m_draw))
        M = np.full(n, float(base_gap))
        for i in range(n_units):
            eps = rng.normal(0.0, sig_unit[i], n)
            k_cl = np.clip(k_center[i] + delta + eps, 0.0, 1.0)
            M += vp_clean[i] * (2.0 * k_cl - 1.0)
            if vp_jee[i] > 0:
                eps_j = rng.normal(0.0, s_jee_unit, n)
                k_jj = np.clip(k_center[i] + delta + delta_jee + eps + eps_j, 0.0, 1.0)
                M += vp_jee[i] * (2.0 * k_jj - 1.0) * m
        return M

    M = simulate(n_sims, None)
    p_keiko = float((M > 0).mean())
    margin_med = float(np.median(M))
    margin_p05 = float(np.percentile(M, 5))
    margin_p95 = float(np.percentile(M, 95))
    margin_p25 = float(np.percentile(M, 25))
    margin_p75 = float(np.percentile(M, 75))

    sens = []
    for mv in (0.0, 0.5, 0.75, 0.9, 1.0):
        Ms = simulate(6000, mv)
        sens.append((mv, float((Ms > 0).mean()), float(np.median(Ms))))

    # Netos deterministas usando el centro (k_center) — ya calibrado con R1
    swing_c = 2.0 * k_center - 1.0
    net_clean = float((vp_clean * swing_c).sum())
    net_jee   = float((vp_jee   * swing_c).sum())

    tot_final_v = tot_v + v_rest_total
    k_final = (tot_final_v + margin_med) / 2.0
    s_final = (tot_final_v - margin_med) / 2.0

    return {
        "ok": True,
        "p_keiko": p_keiko,
        "base_gap": base_gap,
        "lider_actual": "Keiko" if base_gap > 0 else "Sánchez",
        "margin_med": margin_med, "margin_p05": margin_p05, "margin_p95": margin_p95,
        "margin_p25": margin_p25, "margin_p75": margin_p75,
        "ganador_med": "Keiko" if margin_med > 0 else "Sánchez",
        "v_rest_total": v_rest_total,
        "net_clean": net_clean, "net_jee": net_jee,
        "k_final": k_final, "s_final": s_final,
        "k_final_pct": k_final / tot_final_v * 100,
        "s_final_pct": s_final / tot_final_v * 100,
        "sens": sens,
        "n_units": n_units,
        "r1": {"used": reg is not None, "n_units_r1": n_r1_used,
               "corr": reg["corr"] if reg else None,
               "resid_std": reg["resid_std"] if reg else None,
               "a": reg["a"] if reg else None, "b": reg["b"] if reg else None} ,
        "params": {"n_sims": n_sims, "s_acta": s_acta, "sigma_sys": sigma_sys,
                   "m_mean": m_mean, "m_sd": m_sd, "bias_floor": bias_floor},
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    import fetch
    from fetch import extract
    data = json.load(open(os.path.join(fetch.HERE, "raw_data.json"), encoding="utf-8"))
    f = montecarlo_forecast(data, extract, here=fetch.HERE)
    print("=" * 64)
    print("PRONÓSTICO MONTE CARLO (calibrado con 1ra vuelta)")
    print("=" * 64)
    r1 = f["r1"]
    if r1["used"]:
        print(f"  Calibración R1: r={r1['corr']:.3f}, RMSE residual={r1['resid_std']*100:.2f}pp, "
              f"k2 = {r1['a']:.3f} + {r1['b']:.3f}·k1_2w  ({r1['n_units_r1']}/{f['n_units']} unidades con R1)")
    else:
        print("  Calibración R1: NO disponible (falta r1_data.json) — usa solo método R2")
    print(f"  Brecha actual (K-S)           : {f['base_gap']:+,} ({f['lider_actual']} adelante)")
    print(f"  Votos restantes estimados     : {f['v_rest_total']:,.0f}")
    print(f"  Neto Keiko limpias / observadas: {f['net_clean']:+,.0f} / {f['net_jee']:+,.0f}")
    print()
    print(f"  >>> P(Keiko gana) = {f['p_keiko']*100:.1f}%")
    print(f"  Margen final (K-S): mediana {f['margin_med']:+,.0f}")
    print(f"      IC 90%: [{f['margin_p05']:+,.0f}, {f['margin_p95']:+,.0f}]")
    print(f"  Resultado proyectado: Keiko {f['k_final_pct']:.3f}% - Sánchez {f['s_final_pct']:.3f}%")
    print()
    print("  SENSIBILIDAD a m (actas observadas que se computan):")
    for mv, p, med in f["sens"]:
        print(f"    m={mv:>4.0%}  ->  P(Keiko)={p*100:5.1f}%   margen mediano {med:+,.0f}")
