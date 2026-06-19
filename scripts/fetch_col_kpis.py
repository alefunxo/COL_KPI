"""
fetch_colombia_kpis.py

Independently fetches the indicators behind the "Colombia en cifras" dashboard
directly from the World Bank's open Indicators API (no key required, no
scraping, no LLM-recalled numbers) and aggregates them into the same nine
four-year periods the dashboard uses (Uribe and Santos are split into their
two terms each, so no period gets double the time window of the others).
For the Latin America & Caribbean region, the SAME nine periods are used,
so the regional comparison is genuinely period-matched rather than one
flat 36-year benchmark.

WHY THIS EXISTS
----------------
The dashboard's PRESIDENTS object was built mostly from my own knowledge plus
a handful of web searches, NOT from a systematic pull of primary series. That
is a legitimate reason not to trust it at face value. This script lets you
check it yourself: every number it prints traces back to one HTTP call you
can re-run, inspect, or paste into a browser.

WHAT THIS SCRIPT CAN AND CANNOT GET YOU
-----------------------------------------
The World Bank Indicators API aggregates national-statistics-office data, so
it covers GDP growth, inflation, the (ILO-modeled) unemployment rate, Gini,
national poverty headcount, international extreme-poverty headcount, and the
UNODC-sourced homicide rate -- for Colombia AND for the Latin America &
Caribbean region aggregate (code LCN), using the exact same indicator series
for both, which is what makes the comparison fair.

It does NOT cleanly cover (these need a different, manual source -- see the
NOT_AVAILABLE_VIA_API notes printed at the end):
  - DANE's own monetary/extreme poverty series under the MESEP/2019 method-
    ology (the WB's SI.POV.NAHC and SI.POV.DDAY use different definitions
    and will NOT match DANE's published national figures exactly)
  - DANE multidimensional poverty (no standardized WB series)
  - DANE GEIH labor informality
  - Colombia's central government fiscal deficit / public debt with full
    historical depth (the WB series exist but have large gaps for Colombia)
  - Instituto Nacional de Medicina Legal's own homicide count (the WB/UNODC
    series is a reasonable proxy but is not identical to INMLCF's count)

Run:
    pip install requests --break-system-packages   # if requests isn't installed
    python3 fetch_colombia_kpis.py

Output:
    - A console report per indicator, per president, with the exact years
      used and the underlying values (so you can audit the aggregation)
    - colombia_kpis_fetched.json, structured like the dashboard's PRESIDENTS
      object, for direct comparison against what's currently in the HTML file
"""

import json
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

BASE_URL = "https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
HEADERS = {"User-Agent": "research-script/1.0 (academic use)"}

COUNTRY = "COL"
REGION = "LCN"  # World Bank aggregate code for Latin America & Caribbean

# Indicator codes. Left-hand keys match the dashboard's field names so the
# output JSON can be diffed directly against the PRESIDENTS object in the
# HTML file.
INDICATORS = {
    "gdpGrowth":        "NY.GDP.MKTP.KD.ZG",   # GDP growth, annual %
    "inflation":        "FP.CPI.TOTL.ZG",      # CPI inflation, annual %
    "fiscalBalance":    "GC.BAL.CASH.GD.ZS",   # cash surplus(+)/deficit(-), % GDP -- patchy for COL
    "govDebt":          "GC.DOD.TOTL.GD.ZS",   # central government debt, % GDP -- patchy for COL
    "unemployment":     "SL.UEM.TOTL.ZS",      # unemployment, modeled ILO estimate, % labor force
    "gini":             "SI.POV.GINI",         # Gini index, 0-100 scale (dashboard uses 0-1)
    "povertyNational":  "SI.POV.NAHC",         # poverty headcount, national poverty line, %
    "extremePovertyIntl": "SI.POV.DDAY",       # poverty headcount, $2.15/day 2017PPP, % -- NOT DANE's extreme line
    "homicideRate":     "VC.IHR.PSRC.P5",      # intentional homicides, per 100,000 (source: UNODC)
}

# Indicators the dashboard uses that have no reliable WB series at all.
NOT_AVAILABLE_VIA_API = {
    "employment.informality": "DANE GEIH, published as Excel bulletins: "
        "https://www.dane.gov.co/index.php/estadisticas-por-tema/mercado-laboral/empleo-informal-y-seguridad-social",
    "social.mpiEnd (pobreza multidimensional)": "DANE, no standardized WB series: "
        "https://www.dane.gov.co/index.php/estadisticas-por-tema/pobreza-y-condiciones-de-vida/pobreza-multidimensional",
    "social.povertyStart/End (cifra oficial DANE/MESEP)": "WB's SI.POV.NAHC is a different "
        "methodology than DANE's official monetary-poverty series; cross-check against "
        "https://www.dane.gov.co/index.php/estadisticas-por-tema/pobreza-y-condiciones-de-vida/pobreza-monetaria",
    "security.homicide (cifra oficial INMLCF)": "Instituto Nacional de Medicina Legal y "
        "Ciencias Forenses, annual 'Forensis' report (PDF, no API): "
        "https://www.medicinalegal.gov.co/cifras-estadisticas/forensis",
    "economy.fiscalDeficit / debtEnd (cifra oficial MinHacienda)": "Ministerio de Hacienda y "
        "Crédito Público, Marco Fiscal de Mediano Plazo (PDF/Excel, no API): "
        "https://www.minhacienda.gov.co",
}

# Periods: (id, calendar year of inauguration, calendar year of handover).
# Uribe and Santos are split into their two terms so the comparison isn't
# distorted by giving them double the time window of every other period.
PRESIDENTS_META = [
    {"id": "gaviria",  "name": "César Gaviria",            "start": 1990, "end": 1994},
    {"id": "samper",   "name": "Ernesto Samper",           "start": 1994, "end": 1998},
    {"id": "pastrana", "name": "Andrés Pastrana",          "start": 1998, "end": 2002},
    {"id": "uribe1",   "name": "Álvaro Uribe (1er mandato)", "start": 2002, "end": 2006},
    {"id": "uribe2",   "name": "Álvaro Uribe (2do mandato)", "start": 2006, "end": 2010},
    {"id": "santos1",  "name": "Juan Manuel Santos (1er mandato)", "start": 2010, "end": 2014},
    {"id": "santos2",  "name": "Juan Manuel Santos (2do mandato)", "start": 2014, "end": 2018},
    {"id": "duque",    "name": "Iván Duque",               "start": 2018, "end": 2022},
    {"id": "petro",    "name": "Gustavo Petro",            "start": 2022, "end": 2026},
]


def fetch_series(country_code, indicator_code, start_year=1989, end_year=2026):
    """
    Hits the real World Bank endpoint and returns {year: value} for one
    country/indicator. Returns an empty dict (with a printed warning) on
    any failure, rather than silently inventing numbers.
    """
    url = BASE_URL.format(country=country_code, indicator=indicator_code)
    url += f"?format=json&per_page=1000&date={start_year}:{end_year}"
    try:
        req = Request(url, headers=HEADERS)
        with urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError) as e:
        print(f"  [WARN] request failed for {country_code}/{indicator_code}: {e}")
        return {}
    except json.JSONDecodeError:
        print(f"  [WARN] non-JSON response for {country_code}/{indicator_code} -- check the URL manually: {url}")
        return {}

    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        print(f"  [WARN] no data returned for {country_code}/{indicator_code} (URL: {url})")
        return {}

    series = {}
    for row in payload[1]:
        year = int(row["date"])
        val = row["value"]
        if val is not None:
            series[year] = float(val)
    return series


def term_years(start, end):
    """Full calendar years strictly inside an Aug-to-Aug presidential term.
    e.g. Gaviria 1990-1994 -> [1991, 1992, 1993, 1994]. This is a documented
    convention, not the only valid one -- see the dashboard's own methodology
    note about calendar-year vs. term-year mismatch."""
    return list(range(start + 1, end + 1))


def term_average(series, start, end):
    years = term_years(start, end)
    vals = [(y, series[y]) for y in years if y in series]
    if not vals:
        return None, []
    avg = sum(v for _, v in vals) / len(vals)
    return round(avg, 2), vals


def nearest_value(series, target_year, max_lookback=2):
    """Picks the value at target_year, or the closest available year within
    max_lookback years, and reports which year was actually used."""
    if target_year in series:
        return target_year, series[target_year]
    for delta in range(1, max_lookback + 1):
        for candidate in (target_year - delta, target_year + delta):
            if candidate in series:
                return candidate, series[candidate]
    return None, None


def build_report():
    print("=" * 78)
    print("Fetching from the World Bank Indicators API (api.worldbank.org/v2)")
    print("No authentication, no scraping -- every value below traces to one URL.")
    print("=" * 78)

    all_series_col = {}
    all_series_reg = {}

    for field, code in INDICATORS.items():
        print(f"\n--- {field}  (WB code: {code}) ---")
        print(f"  Colombia URL: {BASE_URL.format(country=COUNTRY, indicator=code)}?format=json&per_page=1000&date=1989:2026")
        col_series = fetch_series(COUNTRY, code)
        print(f"  -> {len(col_series)} years with data for Colombia")
        time.sleep(0.3)  # be polite to the API

        print(f"  LAC region URL: {BASE_URL.format(country=REGION, indicator=code)}?format=json&per_page=1000&date=1989:2026")
        reg_series = fetch_series(REGION, code)
        print(f"  -> {len(reg_series)} years with data for LAC region")
        time.sleep(0.3)

        all_series_col[field] = col_series
        all_series_reg[field] = reg_series

    # ---- Aggregate per presidential term ----
    results = {"country": {}, "region_matched": {}}
    for p in PRESIDENTS_META:
        pid, start, end = p["id"], p["start"], p["end"]
        results["country"][pid] = {"name": p["name"], "start": start, "end": end}
        print(f"\n{'='*78}\n{p['name']}  ({start}-{end})\n{'='*78}")

        for field in INDICATORS:
            series = all_series_col[field]
            avg, used = term_average(series, start, end)
            sy, sval = nearest_value(series, start)
            ey, eval_ = nearest_value(series, end)
            unit = "(0-100 scale, WB)" if field == "gini" else ""
            print(f"  {field:22s} avg={avg!s:>8} {unit:18s} "
                  f"start[{sy}]={sval!r:>10}  end[{ey}]={eval_!r:>10}  "
                  f"(years used for avg: {[y for y,_ in used]})")
            results["country"][pid][field] = {
                "termAverage": avg,
                "startYear": sy, "startValue": sval,
                "endYear": ey, "endValue": eval_,
            }

    # ---- Region: matched to the SAME nine periods as Colombia, using the
    #      same term_average() logic, so the comparison is genuinely
    #      apples-to-apples rather than one flat 36-year benchmark ----
    print(f"\n{'='*78}\nLAC region, matched to each of the same nine periods\n{'='*78}")
    results["region_matched"] = {}
    for p in PRESIDENTS_META:
        pid, start, end = p["id"], p["start"], p["end"]
        results["region_matched"][pid] = {}
        print(f"\n  {p['name']}  ({start}-{end})")
        for field in INDICATORS:
            series = all_series_reg[field]
            avg, used = term_average(series, start, end)
            sy, sval = nearest_value(series, start)
            ey, eval_ = nearest_value(series, end)
            print(f"    {field:22s} avg={avg!s:>8}  start[{sy}]={sval!r:>10}  end[{ey}]={eval_!r:>10}")
            results["region_matched"][pid][field] = {
                "termAverage": avg, "startYear": sy, "startValue": sval,
                "endYear": ey, "endValue": eval_,
            }

    return results


def print_not_available():
    print(f"\n{'='*78}\nNOT available via this API -- needs a manual/primary source\n{'='*78}")
    for k, v in NOT_AVAILABLE_VIA_API.items():
        print(f"  - {k}\n      {v}\n")


if __name__ == "__main__":
    report = build_report()
    print_not_available()

    out_path = "data/colombia_kpis_fetched.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\nSaved full structured results to {out_path}")
    print("Compare this against the PRESIDENTS object in the dashboard's HTML --"
          " any field that disagrees should be treated as my estimate being wrong,"
          " not the other way around.")