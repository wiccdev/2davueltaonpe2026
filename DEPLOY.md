# Deploy a Vercel

Dashboard ONPE como función serverless **bajo demanda** + caché de 60s en el edge.
No usa cron ni loop: cada visita genera el HTML en vivo, y el `<meta refresh>` de
60s lo actualiza solo. La caché (`s-maxage=60`) hace que ONPE se golpee como máximo
**1 vez por minuto** aunque haya muchos visitantes.

## Archivos del deploy

| Archivo | Rol |
|---|---|
| `api/index.py` | Función serverless: fetch en vivo → HTML/CSV con cabecera de caché |
| `fetch.py` | Lógica de fetch + render (compartida con el uso local) |
| `forecast.py` | Motor Monte Carlo calibrado con 1ª vuelta |
| `r1_data.json` | Resultados 1ª vuelta (estáticos, se empaquetan) |
| `vercel.json` | Rutas, runtime python3.12, maxDuration 60s, includeFiles |
| `requirements.txt` | `curl_cffi`, `numpy` |
| `.vercelignore` | Excluye archivos generados/locales |

> Lo NO incluido (loop, `--loop`, `analisis.py`, `fetch_r1.py`, histórico CSV)
> sigue sirviendo para correr local; no se sube.

## Opción A — Vercel CLI (lo más rápido)

```bash
npm i -g vercel
cd C:\Users\kquispe\Desktop\tmp\on
vercel            # primer deploy (preview); login la primera vez
vercel --prod     # a producción
```

## Opción B — GitHub + import

1. `git init && git add . && git commit -m "dashboard onpe"`
2. Subir a un repo de GitHub.
3. En vercel.com → **Add New Project** → importar el repo → Deploy.

## Variables de entorno (opcionales, en Vercel → Settings → Environment Variables)

| Variable | Efecto |
|---|---|
| `ONPE_FETCH_DEPTH=province` | Fetch más liviano (~300 reqs, ~3s) sin distritos/ciudades. Usar si hay timeouts o bloqueos. Default: `district` (completo, ~11s). |

## ⚠️ Los dos riesgos (solo se confirman deployando)

1. **`curl_cffi` en el runtime de Vercel.** Es una extensión C con binario de
   impersonación. Hay wheels manylinux, así que *suele* funcionar, pero podría fallar
   al importar. → Si en los logs ves `ImportError` de curl_cffi, no compiló.
2. **¿ONPE/CloudFront sirve a IPs de AWS?** El más serio. CloudFront a veces bloquea
   rangos de datacenter. → Si la página carga pero sin datos, mirá los logs: si las
   respuestas de ONPE llegan como HTML en vez de JSON, la IP de Vercel está bloqueada.
   **Esto no se arregla con código** — ahí necesitás el host always-on.

**Cómo verificar tras el deploy:** abrí la URL. Si ves el dashboard con datos → todo
OK. Si ves la pantalla de error (que muestra el traceback) o el dashboard sin números
→ revisá *Vercel → tu proyecto → Logs* y mirá cuál de los dos casos es.

## Limitaciones conocidas

- **Histórico P(Keiko):** en serverless el disco no persiste entre invocaciones, así
  que la curva es *best-effort* (puede resetear en cold starts). Para histórico real,
  agregar **Vercel KV / Upstash Redis** y guardar los puntos ahí (mejora futura).
- **Duración:** el fetch completo tarda ~11s; el límite de Vercel es 60s (Hobby).
  Si el cold start lo acerca al límite, usar `ONPE_FETCH_DEPTH=province`.

## Costo

Funciona en plan **Hobby (gratis)**: no necesita cron (que sí requeriría Pro). La caché
de 60s mantiene las invocaciones bajas aunque haya tráfico.

## Cuenta free (Hobby) — qué tener en cuenta

| Límite Hobby | Este proyecto |
|---|---|
| **Sin cron < 1/día** | No usamos cron. ✓ |
| **maxDuration ≤ 60s** | Fetch distrito ~11s (entra). Por seguridad ante cold starts, recomendado **`ONPE_FETCH_DEPTH=province`** (~3s). |
| **100 GB ancho de banda/mes** | Página distrito ~3.5 MB (~28k cargas/mes); página provincia ~0.5 MB (~200k cargas). |
| **Uso personal / no comercial** | Un dashboard informativo personal está OK; tráfico masivo/comercial puede chocar con la ToS de Hobby. |

**Recomendado para free:** poné `ONPE_FETCH_DEPTH=province` en las env vars. Queda
rápido, liviano y bien lejos del límite de 60s. (Perdés el drill-down a distrito/ciudad,
pero el pronóstico calibrado con 1ª vuelta sigue igual — a esta altura del conteo la
diferencia distrito-vs-provincia es ~2.400 votos.)

> ⚠️ **Pagar Pro NO resuelve los dos riesgos de arriba** (curl_cffi, bloqueo de IP de
> ONPE a AWS). Esos son del entorno, no del plan. Si ONPE bloquea la IP de Vercel,
> falla igual en free que en Pro → ahí sí o sí el host always-on.
