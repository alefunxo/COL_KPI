# Colombia en cifras, gobierno por gobierno

Panel comparativo de indicadores económicos, sociales y de seguridad para los nueve periodos presidenciales de Colombia desde 1990 (Gaviria → Petro). Los mandatos de Uribe y Santos se muestran por separado para que ningún periodo tenga el doble de ventana de tiempo que los demás.

La línea punteada roja en cada gráfico es el promedio de América Latina y el Caribe **para ese mismo periodo de cuatro años** — no un promedio genérico de 36 años.

---

## Indicadores

| Categoría | Indicadores |
|-----------|-------------|
| Economía | PIB (crecimiento anual), inflación, déficit fiscal, deuda pública |
| Empleo | Desempleo (estimación ILO), informalidad laboral (DANE GEIH) |
| Social | Pobreza monetaria, pobreza extrema, Gini, pobreza multidimensional (IPM) |
| Seguridad | Tasa de homicidios |
| Popularidad | Aprobación presidencial (Invamer) |

## Fuentes

| Campo | Fuente | Cobertura |
|-------|--------|-----------|
| PIB, inflación, desempleo, pobreza, Gini, homicidios (serie) | Banco Mundial API | Colombia + región ALC |
| Informalidad laboral | DANE GEIH (`data/anex-GEIHEISS-oct-dic2025.xlsx`) | Solo desde 2021 (GEIH marco 2018) |
| Pobreza multidimensional (IPM) | DANE (`data/anex-PMultidimensional-2025.xlsx`) | Desde 2010 |
| Déficit fiscal, Petro | Ministerio de Hacienda, Balance GNC | 2023–2025 |
| Aprobación presidencial | Invamer (citada en prensa) | Parcial por periodo |

Los detalles exactos — con URL y caveats por campo — están en [`overrides.json`](overrides.json).

Todo campo marcado `s/d` está genuinamente sin dato verificado. Los campos `***` tienen fuente manual fuera del pipeline del Banco Mundial.

## Estructura del repositorio

```
/
├── index.html                    # Panel publicado (también la plantilla del pipeline)
├── overrides.json                # Valores con fuente manual; enlazado desde index.html
├── requirements.txt
├── scripts/
│   ├── fetch_col_kpis.py         # Consulta la API del Banco Mundial
│   ├── build_dashboard.py        # Inyecta los datos en index.html
└── data/
    ├── colombia_kpis_fetched.json           # Última consulta API (comprometida para auditoría)
    ├── anex-GEIHEISS-oct-dic2025.xlsx       # DANE GEIH — informalidad
    ├── anex-PMultidimensional-2025.xlsx     # DANE — Pobreza Multidimensional (IPM)
    └── anexo_geih_informalidad_oct20_dic20.xls  # DANE GEIH — boletín oct-dic 2020
```

## Cómo reproducir / actualizar

Los scripts se ejecutan desde la **raíz del repositorio** (no desde dentro de `scripts/`).

### Requisitos

```bash
pip install requests   # solo para fetch_dane_opendata.py y diag.py; el resto usa stdlib
```

### 1. Actualizar los datos del Banco Mundial

```bash
python scripts/fetch_col_kpis.py
```

Escribe `data/colombia_kpis_fetched.json`. No requiere clave de API.

### 2. Regenerar el panel

```bash
python scripts/build_dashboard.py --overrides overrides.json
```

Lee `index.html` como plantilla, aplica los datos de la API y los valores manuales de `overrides.json`, y escribe el resultado directamente sobre `index.html`.

Para previsualizar sin sobreescribir `index.html`:

```bash
python scripts/build_dashboard.py --overrides overrides.json --output preview.html
```

### 3. Revisar y comprometer

```bash
git diff index.html
git add index.html data/colombia_kpis_fetched.json
git commit -m "Actualizar datos — $(date +%Y-%m-%d)"
git push
```

GitHub Pages publica `index.html` automáticamente.

## Actualizar un override manualmente

Edita `overrides.json` siguiendo la estructura existente y luego re-ejecuta el paso 2. El formato esperado por campo:

```json
{
  "country": {
    "petro": {
      "approval": {
        "end": {
          "value": 37,
          "verified_this_session": true,
          "source": "Invamer, agosto de 2025 (vía El Colombiano/Infobae)",
          "url": null
        }
      }
    }
  }
}
```

## Notas metodológicas

- **Periodos**: de agosto a agosto; los promedios usan años calendario completos dentro de cada periodo (p.ej. para 2002–2006 se promedian 2003–2006).
- **Ruptura GEIH 2021**: la informalidad solo es comparable a nivel Total Nacional desde 2021 (rediseño metodológico GEIH marco 2018). Los periodos anteriores a Duque aparecen vacíos.
- **Desempleo**: la serie del Banco Mundial (estimación modelada ILO) no es idéntica a la serie GEIH del DANE.
- **Pobreza extrema**: el Banco Mundial usa la línea internacional de USD 2.15/día (2017 PPP), que no coincide con la línea nacional extrema del DANE.
- **Homicidios**: la serie del Banco Mundial proviene de UNODC; puede diferir del conteo del Instituto Nacional de Medicina Legal y Ciencias Forenses.

## Credenciales (datos.gov.co)

`fetch_dane_opendata.py` y `diag.py` usan un token de la API de Socrata. Copia `.env.example` a `.env` y rellena tus credenciales — `.env` está en `.gitignore` y nunca se compromete al repositorio.

## Licencia

Los datos provienen de fuentes públicas (Banco Mundial, DANE, Ministerio de Hacienda). El código de este repositorio está bajo licencia [MIT](LICENSE).
