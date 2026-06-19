"""
build_dashboard.py

Takes colombia_kpis_fetched.json (produced by fetch_colombia_kpis.py) and an
existing copy of the dashboard HTML, and rebuilds the data block inside that
HTML -- the `PRESIDENTS` and `REGION_BY_PERIOD` JS objects, plus the
per-chart "Fuente:" labels -- directly from the fetched numbers. The rest
of the page (CSS, layout, chart-rendering JS, ribbon/tabs logic) is left
untouched; only the data and its source attribution change.

This closes the loop: fetch_colombia_kpis.py pulls real numbers from a real
API -> this script writes those numbers, and ONLY those numbers, into the
page. Anything the API doesn't cover (informality, DANE's own poverty/MPI
methodology, full fiscal/debt history, INMLCF's homicide count, presidential
approval) is left as null in the output -- the chart will show "s/d" for
it -- rather than silently falling back to an estimate. If you want those
filled in, that has to come from a manual, primary-source step (see
--overrides below, and the dashboard's own methodology section for which
institution to query).

CHANGELOG (this revision)
-------------------------------------------------------
The shape of the overrides file changed since this script was first written.
The old shape was a bare scalar per field:
    {"country": {"samper": {"economy": {"fiscalDeficit": 3.78}}}}
The current overrides.json instead carries full per-value provenance:
    {"country": {"samper": {"economy": {"fiscalDeficit":
        {"value": 3.78, "verified_this_session": false,
         "source": "...", "url": null}}}}}
Three concrete things broke as a result, all fixed here:

  1. apply_overrides() was assigning the raw override object straight into
     the dashboard's data field. The dashboard's rendering JS looks for a
     SPECIFIC runtime shape -- {"value": X, "flag": true, "note": "..."} --
     to draw the *** marker and hatched-bar styling. The richer overrides
     shape doesn't have "flag"/"note" keys at all, so every manually-sourced
     value would have silently rendered as if it were an unflagged,
     ordinary API value -- exactly the kind of silent mislabeling this
     project has been trying to avoid. normalize_override_value() now
     converts the rich shape into that runtime shape, folding source/url/
     caveat/alt_value/alt_source/alt_url into a single human-readable note.
     A bare scalar override (the old shape) is still accepted as-is, for
     backward compatibility.

  2. apply_overrides() crashed outright on the current overrides.json's
     "region": {"_note": "..."} block: it tried to call .items() on a
     string. Any key starting with "_" (a comment/metadata key, by this
     project's convention -- see overrides.json's own "_readme") is now
     skipped at every level (period, category, and field), and non-dict
     values are skipped defensively instead of raising.

  3. build_presidents() never emitted an "approval" category at all --
     there is no World Bank series for presidential approval, so it wasn't
     in country_to_dashboard_fields()'s output, and STATIC_META doesn't
     carry it either. The live template's "Popularidad" tab depends
     entirely on overrides for this field. Without an explicit
     {"start": None, "end": None} baseline, this wasn't strictly a crash,
     but it meant the approval tab would only be populated for periods
     that happen to appear as a key in the overrides file in exactly the
     right shape -- fragile, and silently wrong for anything that doesn't.
     build_presidents() now seeds every period with an explicit
     approval: {"start": None, "end": None}, the same way every other
     not-covered-by-the-API field is seeded with None rather than omitted.

  4. patch_footer_note() did an exact-string match against the footer's
     first <span>. That literal string had already drifted from what's in
     the current template (the footer text was hand-edited since this
     script was last run), so the function would raise. It now matches
     structurally -- the first <span> inside <footer> -- so future wording
     changes to that span don't break regeneration.

  5. (Enhancement, not a strict bug fix.) patch_sources() now pulls the
     actual URL out of the override data itself, when one exists, and
     builds the "Fuente:" caption with a real clickable link -- instead of
     a generic "+ carga manual, ver overrides.json" label. This means
     re-running this script doesn't regress the source-citation work done
     directly in the HTML in an earlier pass; overrides.json stays the
     single source of truth for the URL instead of it being hardcoded a
     second time in this script.

WHAT IS *NOT* COMING FROM THE API, AND WHY THAT'S OK
-------------------------------------------------------
Names, party labels, narrative notes ("Recesión de 1999...", etc.), the
ongoing/termNumber flags, and the exact start/end dates are presentation
metadata, not KPI values -- there's no API for "what a government called
itself." Those stay hardcoded in STATIC_META below. Every NUMBER in the
output, by contrast, traces back to a field in the fetched JSON, to an
entry in --overrides, or is explicitly null.

DEFINITIONAL MISMATCHES TO BE AWARE OF (the script labels these honestly
in the regenerated HTML, but you should still know about them):
  - "unemployment" is the World Bank's ILO-modeled estimate, not DANE's GEIH
    series -- these are constructed differently and will not match exactly.
  - "extremeStart"/"extremeEnd" become the World Bank's international
    $2.15/day (2017 PPP) extreme-poverty line, NOT DANE's national extreme-
    poverty line. For a middle-income country like Colombia these can
    differ substantially. Read that chart as "international line", not as
    a drop-in replacement for the DANE figure that was there before.
  - "fiscalDeficit" is -1 * the World Bank's cash surplus/deficit series
    (which reports surpluses as positive), so the sign is flipped to match
    the dashboard's convention of deficit-as-positive.
  - "gini" is divided by 100 to convert the World Bank's 0-100 scale to the
    dashboard's 0-1 scale.
  - "informality", "mpiEnd", and "approval" are always null straight out of
    the API: no World Bank series exists for any of the three.
  - homicideAvg is read from the API's homicideRate.startValue, not its
    termAverage, despite the dashboard label saying "promedio del periodo."
    This mirrors how the original fetch/build pipeline was wired and is
    left as-is here -- changing it would silently shift every homicide bar
    in the chart and is out of scope for an override-compatibility fix.

OVERRIDES
-------------------------------------------------------
The World Bank API has no series at all for three fields this project
cares about (informality, mpiEnd, approval), partial coverage for two more
(fiscalDeficit, debtEnd for Colombia), and the regional comparators need
judgment calls the API can't make on its own. Re-running this script with
only --fetched would silently wipe out any manually-researched numbers for
those fields and reset them to null/raw-API values.

--overrides lets you supply a JSON file of manually-sourced values layered
on TOP of the API-derived base, after transforms (sign flip, /100 scaling)
are already applied -- so values in this file should be in the SAME units
the dashboard displays, not the World Bank's raw units. See overrides.json
(shipped alongside this script) for the exact shape and for the actual
verified values found via primary-source research in this project so far.

Usage (run from repo root):
    python scripts/build_dashboard.py --overrides overrides.json
    # reads data/colombia_kpis_fetched.json, writes index.html in place

    # To preview without overwriting index.html:
    python scripts/build_dashboard.py --overrides overrides.json --output preview.html
"""

import argparse
import json
import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Presentation metadata that has no API equivalent. Numbers are intentionally
# absent from this dict -- they all come from the fetched JSON instead.
# ---------------------------------------------------------------------------
STATIC_META = [
    {"id": "gaviria",  "name": "César Gaviria", "party": "Partido Liberal",
     "start": "1990-08-07", "end": "1994-08-07",
     "note": "Apertura económica y nueva Constitución (1991)."},
    {"id": "samper",   "name": "Ernesto Samper", "party": "Partido Liberal",
     "start": "1994-08-07", "end": "1998-08-07",
     "note": "Crisis de legitimidad (Proceso 8000) y deterioro fiscal hacia el final del mandato."},
    {"id": "pastrana", "name": "Andrés Pastrana", "party": "Gran Alianza por el Cambio",
     "start": "1998-08-07", "end": "2002-08-07",
     "note": "Recesión de 1999, acuerdo con el FMI, ruptura del proceso de paz con las FARC."},
    {"id": "uribe1",   "name": "Álvaro Uribe", "party": "Independiente / Primero Colombia",
     "start": "2002-08-07", "end": "2006-08-07", "termNumber": 1,
     "note": "Desmovilización de las AUC; comienza el auge de precios de materias primas."},
    {"id": "uribe2",   "name": "Álvaro Uribe", "party": "Independiente / Primero Colombia",
     "start": "2006-08-07", "end": "2010-08-07", "termNumber": 2,
     "note": "Reelección; la crisis financiera global de 2008-09 modera el crecimiento al final.",
     "povertyEndNote": "primer año con cifra nacional comparable en la serie (2012), no el año real de fin de mandato (2010)."},
    {"id": "santos1",  "name": "Juan Manuel Santos", "party": "Partido de la U",
     "start": "2010-08-07", "end": "2014-08-07", "termNumber": 1,
     "note": "Auge minero-energético y regla fiscal (2011); inician negociaciones de paz en La Habana."},
    {"id": "santos2",  "name": "Juan Manuel Santos", "party": "Partido de la U",
     "start": "2014-08-07", "end": "2018-08-07", "termNumber": 2,
     "note": "Caída de precios del petróleo (2014-16); firma del acuerdo de paz con las FARC (2016)."},
    {"id": "duque",    "name": "Iván Duque", "party": "Centro Democrático",
     "start": "2018-08-07", "end": "2022-08-07",
     "note": "Pandemia de COVID-19: PIB -7.2% (2020) seguido de +10.8% (2021)."},
    {"id": "petro",    "name": "Gustavo Petro", "party": "Pacto Histórico",
     "start": "2022-08-07", "end": "2026-08-07", "ongoing": True,
     "note": "Primer gobierno de izquierda; inflación post-pandemia alta cede gradualmente."},
]

# Honest, specific source strings -- these REPLACE the dashboard's original
# "Fuente:" labels for every chart whose key is listed below, since this
# script's whole point is that the numbers no longer come from wherever the
# template said before -- they come from the API (or from --overrides).
SOURCES = {
    "gdpGrowth":      "Fuente: Banco Mundial, API (NY.GDP.MKTP.KD.ZG)",
    "inflation":      "Fuente: Banco Mundial, API (FP.CPI.TOTL.ZG)",
    "fiscalDeficit":  "Fuente: Banco Mundial, API (GC.BAL.CASH.GD.ZS) — sin cobertura para Colombia",
    "debtEnd":        "Fuente: Banco Mundial, API (GC.DOD.TOTL.GD.ZS) — cobertura incompleta para Colombia",
    "unemployment":   "Fuente: Banco Mundial, API (SL.UEM.TOTL.ZS, estimación modelada OIT — no es la serie GEIH del DANE)",
    "informality":    "Fuente: No disponible vía API — pendiente de fuente manual (DANE GEIH)",
    "povertyStart":   "Fuente: Banco Mundial, API (SI.POV.NAHC) — puede no coincidir con la serie oficial DANE/MESEP",
    "extremeStart":   "Fuente: Banco Mundial, API (SI.POV.DDAY, línea internacional de USD 2.15/día) — no es la línea nacional del DANE",
    "gini":           "Fuente: Banco Mundial, API (SI.POV.GINI)",
    "mpiEnd":         "Fuente: No disponible vía API — pendiente de fuente manual (DANE)",
    "homicideAvg":    "Fuente: Banco Mundial, API (VC.IHR.PSRC.P5, fuente original UNODC) — puede diferir del conteo del INMLCF",
}


def r1(v, nd=1):
    return None if v is None else round(v, nd)


def country_to_dashboard_fields(entry):
    """entry = report['country'][pid] or report['region_matched'][pid]
    from the fetched JSON."""
    def g(field, key):
        d = entry.get(field) or {}
        return d.get(key)

    fiscal_bal = g("fiscalBalance", "termAverage")
    gini_raw = g("gini", "termAverage")

    return {
        "economy": {
            "gdpGrowth":     r1(g("gdpGrowth", "termAverage")),
            "inflation":     r1(g("inflation", "termAverage")),
            "fiscalDeficit": r1(-fiscal_bal) if fiscal_bal is not None else None,
            "debtEnd":       r1(g("govDebt", "endValue"), 0),
        },
        "employment": {
            "unemployment": r1(g("unemployment", "termAverage")),
            "informality":  None,  # no API series
        },
        "social": {
            "povertyStart": r1(g("povertyNational", "startValue")),
            "povertyEnd":   r1(g("povertyNational", "endValue")),
            "extremeStart": r1(g("extremePovertyIntl", "startValue")),
            "extremeEnd":   r1(g("extremePovertyIntl", "endValue")),
            "gini":         r1(gini_raw / 100, 3) if gini_raw is not None else None,
            "mpiEnd":       None,  # no API series
        },
        "security": {
            "homicideAvg": r1(g("homicideRate", "startValue"), 0),
            "homicideEnd": r1(g("homicideRate", "endValue"), 0),
        },
    }


def build_presidents(report):
    out = []
    for meta in STATIC_META:
        pid = meta["id"]
        if pid not in report.get("country", {}):
            raise KeyError(f"'{pid}' not found in fetched JSON's 'country' section -- "
                            f"did you run the updated fetch_colombia_kpis.py?")
        fields = country_to_dashboard_fields(report["country"][pid])
        entry = {"id": pid, "name": meta["name"], "party": meta["party"],
                 "start": meta["start"], "end": meta["end"]}
        if "termNumber" in meta:
            entry["termNumber"] = meta["termNumber"]
        entry.update(fields)
        # No World Bank series exists for presidential approval at all -- this
        # is seeded explicitly (not just omitted) so every period has the key,
        # and --overrides is the ONLY way any value here is ever non-null.
        entry["approval"] = {"start": None, "end": None}
        entry["note"] = meta["note"]
        if "povertyEndNote" in meta:
            entry["povertyEndNote"] = meta["povertyEndNote"]
        if meta.get("ongoing"):
            entry["ongoing"] = True
        out.append(entry)
    return out


def build_region(report):
    out = {}
    for meta in STATIC_META:
        pid = meta["id"]
        if pid not in report.get("region_matched", {}):
            raise KeyError(f"'{pid}' not found in fetched JSON's 'region_matched' section.")
        out[pid] = country_to_dashboard_fields(report["region_matched"][pid])
        # Deliberately no "approval" key for region: there's no meaningful
        # concept of a regional approval rating, unlike every other field
        # here which at least has a hypothetical regional aggregate.
    return out


def normalize_override_value(raw):
    """Convert one entry from the overrides file into the dashboard's
    runtime data shape.

    The dashboard's rendering JS detects manually-sourced values by looking
    for {"value": X, "flag": true, "note": "..."} (see isFlagged()/flagNote()
    in the template) -- that shape is what draws the *** marker, the hatched
    bar, and the tooltip text. The current overrides.json instead carries
    rich per-value provenance ("source", "url", "verified_this_session",
    "caveat", "_superseded", "alt_value", "alt_source", "alt_url"). This
    function folds all of that into a single human-readable note string and
    returns the shape the renderer actually understands.

    Backward compatibility: if `raw` is a bare scalar (the older, simpler
    override shape this script originally shipped with), it's returned
    unchanged -- a bare number was never flagged/hatched by the template
    even before this revision, and that behaviour is preserved rather than
    silently changed for old override files.

    The "partial" key (used specifically by the informality chart's
    GEIH-2021-break styling) is passed through if present.
    """
    if not isinstance(raw, dict):
        return raw

    value = raw.get("value")
    parts = []
    if raw.get("source"):
        parts.append(str(raw["source"]).strip())
    if raw.get("_superseded"):
        parts.append("Corrección respecto a una versión anterior: " + str(raw["_superseded"]).strip())
    if raw.get("caveat"):
        parts.append(str(raw["caveat"]).strip())
    if raw.get("alt_value") is not None:
        alt = f"Cifra alternativa: {raw['alt_value']}"
        if raw.get("alt_source"):
            alt += f" ({str(raw['alt_source']).strip()})"
        parts.append(alt)
    urls = [u for u in (raw.get("url"), raw.get("alt_url")) if u]
    if urls:
        parts.append("Fuente: " + " · ".join(urls))

    out = {"value": value, "flag": True, "note": " ".join(parts) if parts else None}
    if raw.get("partial"):
        out["partial"] = True
    return out


def apply_overrides(by_id_dict, overrides_section, scope_name):
    """overrides_section: {period_id: {category: {field: value}}}, where
    `value` may be a bare scalar (old shape) or a rich provenance dict (see
    normalize_override_value). Mutates entries in by_id_dict in place.

    Keys starting with "_" are treated as comments/metadata at every level
    (period, category, field) and skipped -- this project's overrides.json
    uses that convention for things like "_readme" and "region._note".
    Non-dict values where a dict is expected are skipped defensively rather
    than raising, since a malformed entry shouldn't take down the whole
    regeneration.

    Returns (touched, examples_by_field):
      touched: set of (category, field) tuples touched by at least one
        override, for the existing per-field source-label logic.
      examples_by_field: {field: raw_override_dict} for the first override
        of that field that carried a real "url", so patch_sources() can
        build a caption with an actual clickable link instead of a generic
        label. (Only meaningful for the rich/dict override shape.)
    """
    touched = set()
    examples_by_field = {}
    if not overrides_section:
        return touched, examples_by_field
    for pid, cats in overrides_section.items():
        if not isinstance(pid, str) or pid.startswith("_"):
            continue
        if pid not in by_id_dict:
            print(f"WARNING ({scope_name}): override for unknown period '{pid}', skipping")
            continue
        if not isinstance(cats, dict):
            print(f"WARNING ({scope_name}): override block for '{pid}' is not an object, skipping")
            continue
        target = by_id_dict[pid]
        for cat, fields in cats.items():
            if not isinstance(cat, str) or cat.startswith("_"):
                continue
            if not isinstance(fields, dict):
                print(f"WARNING ({scope_name}): override category '{pid}.{cat}' is not an object, skipping")
                continue
            if cat not in target:
                target[cat] = {}
            for field, raw_value in fields.items():
                if not isinstance(field, str) or field.startswith("_"):
                    continue
                target[cat][field] = normalize_override_value(raw_value)
                touched.add((cat, field))
                if (field not in examples_by_field
                        and isinstance(raw_value, dict)
                        and raw_value.get("url")):
                    examples_by_field[field] = raw_value
    return touched, examples_by_field


def index_by_id(presidents_list):
    return {p["id"]: p for p in presidents_list}


def manual_source_label(base_label, field_label):
    return f"Fuente: {base_label} + carga manual ({field_label}) — ver overrides.json"


def link(url, text=None):
    text = text or url
    return f'<a href="{url}" target="_blank">{text}</a>'


def patch_data_block(html, marker_start, marker_end_re, replacement):
    pattern = re.compile(re.escape(marker_start) + r".*?" + marker_end_re, re.DOTALL)
    new_html, n = pattern.subn(replacement, html, count=1)
    if n != 1:
        raise RuntimeError(f"Expected exactly one match for block starting with "
                            f"{marker_start!r}, found {n}. The template may have "
                            f"changed shape since this script was written.")
    return new_html


def patch_sources(html, country_touched=None, examples_by_field=None):
    country_touched = country_touched or set()
    examples_by_field = examples_by_field or {}

    def touched(field):
        return any(f == field for c, f in country_touched)

    def with_link_or_fallback(field, primary_label, fallback_builder):
        """If an override for `field` carried a real URL, build a caption
        around that link. Otherwise fall back to the old generic
        "+ carga manual, ver overrides.json" label."""
        if not touched(field):
            return SOURCES[field]
        ex = examples_by_field.get(field)
        if ex and ex.get("url"):
            return fallback_builder(ex)
        return manual_source_label(primary_label, field)

    informality_src = with_link_or_fallback(
        "informality", "DANE — GEIH",
        lambda ex: f"Fuente: DANE GEIH, dominio Total Nacional — {link(ex['url'], 'ver anexo')}. "
                   f"+ carga manual — ver overrides.json")

    mpi_src = with_link_or_fallback(
        "mpiEnd", "DANE, boletines de Pobreza Multidimensional",
        lambda ex: f"Fuente: DANE, Pobreza Multidimensional — {link(ex['url'], 'ver anexo')}. "
                   f"+ carga manual — ver overrides.json")

    fiscal_src = with_link_or_fallback(
        "fiscalDeficit", SOURCES["fiscalDeficit"],
        lambda ex: f"{SOURCES['fiscalDeficit']}. *** {link(ex['url'], 'Ministerio de Hacienda')} "
                   f"para el/los periodo(s) con cifra verificada. Demás periodos: "
                   f"carga manual no reverificada — ver overrides.json")

    debt_src = with_link_or_fallback(
        "debtEnd", SOURCES["debtEnd"],
        lambda ex: f"{SOURCES['debtEnd']}. + carga manual — {link(ex['url'])}")

    # The homicide chart has one shared "Fuente:" line covering both
    # homicideAvg (keyStart) and homicideEnd (keyEnd). homicideEnd isn't its
    # own matchable chart key, so its override (if any) is folded into the
    # homicideAvg caption here rather than matched independently below.
    homicide_src = SOURCES["homicideAvg"]
    if touched("homicideEnd"):
        ex = examples_by_field.get("homicideEnd")
        extra = " *** Fin de periodo (periodo más reciente): fuente primaria distinta a UNODC/Banco Mundial"
        if ex and ex.get("alt_url"):
            extra += f"; cifra alternativa de {link(ex['alt_url'], 'fuente secundaria')}"
        elif ex and ex.get("url"):
            extra += f" — {link(ex['url'])}"
        extra += " — ver overrides.json para el detalle."
        homicide_src = SOURCES["homicideAvg"] + extra

    # key -> new source text. Matched by the stable `key:'...'`/`keyStart:'...'`
    # attribute via regex rather than the exact existing source string, so this
    # survives manual rewording of the "Fuente:" labels in the template.
    key_to_source = {
        "economy.gdpGrowth":       SOURCES["gdpGrowth"],
        "economy.inflation":       SOURCES["inflation"],
        "economy.fiscalDeficit":   fiscal_src,
        "economy.debtEnd":         debt_src,
        "employment.unemployment": SOURCES["unemployment"],
        "employment.informality":  informality_src,
        "social.povertyStart":     SOURCES["povertyStart"],
        "social.extremeStart":     SOURCES["extremeStart"],
        "social.gini":             SOURCES["gini"],
        "social.mpiEnd":           mpi_src,
        "security.homicideAvg":    homicide_src,
    }

    for key, new_source in key_to_source.items():
        if "'" in new_source:
            raise RuntimeError(
                f"Generated source text for {key!r} contains a literal single quote, "
                f"which would break the JS string literal it gets inserted into: "
                f"{new_source!r}. Fix the override's source/url/caveat text to avoid "
                f"apostrophes, or escape it before calling patch_sources().")
        attr = "keyStart" if key in ("social.povertyStart", "social.extremeStart", "security.homicideAvg") else "key"
        pattern = re.compile(r"source:'[^']*'(,\s*" + attr + r":'" + re.escape(key) + r"')")
        new_html, n = pattern.subn(lambda m: f"source:'{new_source}'{m.group(1)}", html, count=1)
        if n != 1:
            raise RuntimeError(f"Expected exactly one chart with {attr}:'{key}' and found {n} -- "
                                f"the template may have changed shape since this script was written.")
        html = new_html
    return html


def patch_footer_note(html, fetched_path):
    """Replace the first <span> inside <footer> with a regeneration stamp.

    Matches structurally (first <span> after <footer>) rather than against
    an exact literal string -- the previous version required an exact match
    against specific wording, which broke the moment that wording was
    hand-edited elsewhere in the project. The structure (a <footer> with at
    least one <span> inside) is far more stable than any specific sentence.
    """
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    new_span = (f"<span>Datos regenerados desde {fetched_path} el {stamp} por build_dashboard.py "
                f"· Petro: periodo en curso, datos parciales</span>")
    pattern = re.compile(r"(<footer>\s*)<span>.*?</span>", re.DOTALL)
    new_html, n = pattern.subn(lambda m: m.group(1) + new_span, html, count=1)
    if n != 1:
        raise RuntimeError("Footer marker not found -- expected a <footer> element with at "
                            "least one <span> inside. The template may have changed shape.")
    return new_html


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetched", default="data/colombia_kpis_fetched.json",
                     help="JSON produced by scripts/fetch_col_kpis.py")
    ap.add_argument("--overrides", default=None,
                     help="Optional JSON file of manually-sourced values to layer on top "
                          "(see overrides.json for the shape)")
    ap.add_argument("--template", default="index.html",
                     help="Existing dashboard HTML to use as the base/template")
    ap.add_argument("--output", default="index.html",
                     help="Where to write the regenerated HTML (default: overwrites index.html in place)")
    args = ap.parse_args()

    with open(args.fetched, "r", encoding="utf-8") as f:
        report = json.load(f)

    presidents = build_presidents(report)
    region = build_region(report)

    country_touched, country_examples = set(), {}
    if args.overrides:
        with open(args.overrides, "r", encoding="utf-8") as f:
            overrides = json.load(f)
        presidents_by_id = index_by_id(presidents)
        country_touched, country_examples = apply_overrides(presidents_by_id, overrides.get("country"), "country")
        region_touched, _ = apply_overrides(region, overrides.get("region"), "region")
        print(f"Applied overrides from {args.overrides}:")
        print(f"  country fields touched: {sorted(country_touched)}")
        print(f"  region fields touched:  {sorted(region_touched)}")
        with_url = sorted(f for f in country_examples)
        print(f"  country fields with a real URL (will get a linked caption): {with_url}")

    with open(args.template, "r", encoding="utf-8") as f:
        html = f.read()

    presidents_js = "const PRESIDENTS = " + json.dumps(presidents, indent=2, ensure_ascii=False) + ";"
    region_js = "const REGION_BY_PERIOD = " + json.dumps(region, indent=2, ensure_ascii=False) + ";"

    html = patch_data_block(html, "const PRESIDENTS = [", r"\n\];", presidents_js)
    html = patch_data_block(html, "const REGION_BY_PERIOD = {", r"\n\};", region_js)
    html = patch_sources(html, country_touched, country_examples)
    html = patch_footer_note(html, args.fetched)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\nWrote {args.output}")
    print("Every numeric KPI now traces to either the World Bank API fetch or to an")
    print("explicit manual override -- check the printed 'fields touched' lists above")
    print("against what you expected to override. Presidential approval is 100% override-")
    print("driven (no API series exists); if 'approval' doesn't appear in the touched")
    print("list above, every approval bar in the output will show 's/d'.")


if __name__ == "__main__":
    main()