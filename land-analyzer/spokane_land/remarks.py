"""Extract utility / access facts from listing text and structured MLS fields.

Each rule is (status, confidence, regex). Within a category, rules are tried in
order and every match is reported, so put the more specific / negative phrases
first (e.g. "no power" before "power").
"""

import re

from .models import HIGH, MEDIUM, LOW, Evidence

_W = r"[\s\-]*"
# "well" the noun, not the adverb: skip "well-drained", "well-kept" and "as well (in)".
WELL = r"(?<!\bas\s)well(?!-\w)"

RULES = {
    "water": [
        ("none", MEDIUM, r"\b(no|without)\s+(a\s+)?(well|water)\b"),
        ("none", MEDIUM, r"\b(well|water)\s+(will\s+be\s+|is\s+)?(needed|required)\b"),
        ("none", MEDIUM, r"\b(buyer|purchaser)s?\s+(to|will|would)\s+(need\s+to\s+)?(drill|install|put\s+in)\s+(a\s+)?well\b"),
        ("none", MEDIUM, r"\bneeds?\s+(a\s+)?well\b"),
        ("shared_well", HIGH, r"\bshared" + _W + WELL + r"\b|\b" + WELL + r"\s+share\b|\bgroup\s+(a|b)\s+water\b"),
        ("well", HIGH, r"\b(existing|drilled|private|installed|producing|new|good|deep)\s+" + WELL + r"\b"),
        ("well", HIGH, r"\b" + WELL + r"\s+(is\s+)?(in\b(?!\s+(the\s+)?(area|neighborhood|vicinity))|drilled|installed|on\s+(the\s+)?(property|site|lot)|producing|log|report)\b"),
        ("well", MEDIUM, r"\b\d{1,3}(\.\d+)?\s*(gpm|gal(lons)?\s*(per|/)\s*min)\b"),
        ("public", HIGH, r"\b(public|city|municipal|community|district)\s+water\b"),
        ("public", HIGH, r"\bwater\s+(district|purveyor|hook" + _W + r"up|meter|tap|connection|line|main|service)\b"),
        ("public", MEDIUM, r"\bwater\s+(is\s+)?(available|at\s+(the\s+)?(road|street|lot\s+line|property\s+line))\b"),
        ("well_possible", LOW, r"\bwells?\s+in\s+(the\s+)?(area|neighborhood)\b|\bneighboring\s+wells?\b"),
        ("water_rights", MEDIUM, r"\bwater\s+rights?\b|\birrigation\s+rights?\b"),
    ],
    "electric": [
        ("none", MEDIUM, r"\boff" + _W + r"grid\b"),
        ("none", MEDIUM, r"\bno\s+(power|electric(ity)?|utilities)\b"),
        ("none", MEDIUM, r"\b(power|electric(ity)?)\s+(is\s+)?not\s+(available|in|on|to)\b"),
        ("on_site", HIGH, r"\b(power|electric(ity)?)\s+(is\s+)?(on|to)\s+(the\s+)?(property|site|lot|parcel|land|building\s+site)\b"),
        ("on_site", HIGH, r"\b(power|electric(ity)?)\s+(is\s+)?(already\s+)?(in\b(?!\s+(the\s+)?(area|street|road|neighborhood|vicinity|subdivision|development))|installed|run\b(?!\s+(along|down|on|to)\s+(the\s+)?(road|street)))"),
        ("on_site", HIGH, r"\b(power|meter)\s+(pole|base|box|panel)\b|\b(transformer|meter)\s+on\s+(the\s+)?(property|site|lot)\b"),
        ("on_site", MEDIUM, r"\b(utilities|all\s+utilities)\s+(are\s+)?(in|installed|on\s+site|to\s+(the\s+)?property)\b"),
        ("at_road", HIGH, r"\b(power|electric(ity)?|utilities)\s+(is\s+|are\s+)?(at|along|on|in|to)\s+(the\s+)?(road|street|lot\s+line|property\s+line|edge|easement|corner)\b"),
        ("at_road", MEDIUM, r"\b(power|electric(ity)?|utilities)\s+(is\s+|are\s+)?(nearby|close|available|adjacent|close\s+by)\b"),
        ("at_road", MEDIUM, r"\b(power|electric(ity)?)\s+within\s+\d+"),
        ("at_road", MEDIUM, r"\b(power|electric(ity)?|utilities)\s+(is\s+|are\s+)?in\s+(the\s+)?(area|street|road|subdivision)\b"),
        ("at_road", MEDIUM, r"\b(avista|inland\s+power|modern\s+electric|vera\s+(water\s+(and|&)\s+)?power|kootenai\s+electric)\b"),
        ("solar", LOW, r"\bsolar\b"),
    ],
    "septic": [
        ("failed", HIGH, r"\b(failed|did\s+not\s+pass)\s+(a\s+|the\s+)?(perc|percolation|soil\s+log)s?\b|\b(perc|percolation)\s+(test\s+)?failed\b|\b(will|does|did)\s+not\s+perc\b"),
        ("sewer", HIGH, r"\b(public|city|municipal|county)\s+sewer\b|\bsewer\s+(hook" + _W + r"up|connection|available|at|in|line|main|stub|district)\b"),
        ("installed", HIGH, r"\bseptic\s+(system\s+)?(is\s+)?(installed|in\s+place|existing|already\s+in)\b|\bexisting\s+septic\b"),
        ("approved", HIGH, r"\bseptic\s+(design(ed)?|permit(ted)?|approv(al|ed)|site\s+(evaluation|approved))\b|\bapproved\s+(septic|drainfield|drain\s+field)\b"),
        ("approved", HIGH, r"\b(perc|percolation)\s+(test\s+)?(passed|approved|done|completed|on\s+file|in\s+hand)\b|\b(perc|percolation)\s+tested\b|\bhas\s+(a\s+)?perc\b|\bperc(ed|'d)\b"),
        ("approved", HIGH, r"\b(perc|percolation|soil)\s+(tests?|logs?)\b[^.]{0,40}?\b(has|have)\s+been\s+(successfully\s+)?(done|completed|approved|passed)\b"),
        ("approved", HIGH, r"\bsoil\s+(log|logs|test|tests|evaluation)s?\s+(done|completed|on\s+file|approved|passed|available)\b|\bsoil\s+logs?\s+(have\s+been\s+)?(done|completed)\b"),
        ("needed", MEDIUM, r"\b(no|without)\s+(a\s+)?(perc|percolation|soil\s+logs?)(\s+tests?)?\b|\b(perc|soil\s+logs?|septic)\s+(test\s+)?(needed|required|not\s+(done|completed|yet))\b|\bbuyer\s+to\s+(do|obtain|complete|verify)\s+(perc|septic|soil)"),
        ("mentioned", LOW, r"\b(septic|drain\s*field|perc)\b"),
    ],
    "access": [
        ("landlocked", HIGH, r"\bland" + _W + r"locked\b|\bno\s+(legal\s+|deeded\s+|recorded\s+)?access\b|\baccess\s+(is\s+)?not\s+(guaranteed|recorded|deeded)\b"),
        ("easement", HIGH, r"\b(deeded|recorded|legal|access|ingress|egress|private)\s+(access\s+)?easement\b|\beasement\s+(access|road|in\s+place|recorded)\b|\baccess\s+(is\s+)?(via|by|through)\s+(an?\s+)?(recorded\s+|deeded\s+)?easement\b"),
        ("private_road", MEDIUM, r"\bprivate\s+(road|drive|lane)\b|\broad\s+maintenance\s+agreement\b|\bshared\s+(road|driveway)\b"),
        ("seasonal", MEDIUM, r"\bseasonal\s+(access|road)\b|\bnot\s+(plowed|maintained)\b|\b(4x4|4wd|atv)\s+(only\s+)?access\b"),
        ("public_road", HIGH, r"\b(county|paved|state|public|city)\s+(maintained\s+)?(road|street|rd|hwy|highway)\b|\b(road|street)\s+frontage\b|\bfrontage\s+on\b|\bfronts\s+(on\s+)?(a\s+)?[\w.]+\s+(road|rd|street|st|hwy|highway|ave|avenue|ln|lane)\b"),
        ("public_road", LOW, r"\b(gravel|paved)\s+(road\s+)?access\b|\byear" + _W + r"round\s+access\b|\beasy\s+access\b"),
    ],
}

_NEGATED_PERC = re.compile(r"\b(no|without|not\s+(yet\s+)?(been\s+)?)\s*(a\s+)?(perc|percolation|soil\s+logs?|septic\s+(design|permit|approval))", re.I)
_COMPILED = {cat: [(s, c, re.compile(rx, re.I)) for s, c, rx in rules] for cat, rules in RULES.items()}


def scan_text(text, source="listing remarks"):
    """Return {category: [Evidence, ...]} for all rules matching ``text``."""
    out = {cat: [] for cat in RULES}
    if not text:
        return out
    for cat, rules in _COMPILED.items():
        seen = set()
        for status, conf, rx in rules:
            m = rx.search(text)
            if not m or status in seen:
                continue
            seen.add(status)
            snippet = _snippet(text, m.start(), m.end())
            out[cat].append(Evidence(status, conf, f'"{snippet}"', source))
        # A generic match adds nothing once a more specific one hit.
        # "No perc test done yet" must not also count as an approved perc.
        if cat == "septic" and _NEGATED_PERC.search(text):
            out[cat] = [e for e in out[cat] if e.status != "approved"]
        if cat == "septic" and len(out[cat]) > 1:
            out[cat] = [e for e in out[cat] if e.status != "mentioned"]
        if cat == "water" and "shared_well" in seen:
            out[cat] = [e for e in out[cat] if e.status != "well"]
    return out


def _snippet(text, start, end, pad=45):
    a = max(0, start - pad)
    b = min(len(text), end + pad)
    s = " ".join(text[a:b].split())
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


# --- Red flags in the listing text ---------------------------------------------

# (key, level, label, regex). level "bad" = can stop a purchase, "warn" = read the fine print.
RED_FLAGS = [
    ("unbuildable", "bad", "Listing says it may not be buildable",
     r"\b(not|non)" + _W + r"buildable\b|\bunbuildable\b|\bno\s+building\s+(allowed|permitted|rights)\b|\bnot\s+a\s+building\s+(lot|site)\b"),
    ("conservation", "bad", "Conservation easement limits building",
     r"\bconservation\s+easement\b"),
    ("hoa", "warn", "HOA / association dues",
     r"\bHOA\b|\bhome\s*owners?'?\s+association\b|\bassociation\s+(dues|fees?)\b|\bannual\s+dues\b"),
    ("ccrs", "warn", "Covenants or deed restrictions",
     r"\bCC\s*&\s*R'?s?\b|\bCCRs?\b|\bcovenants\b|\bdeed\s+restrict|\brestrictive\s+covenant"),
    ("no_mobile", "warn", "No manufactured / mobile homes",
     r"\bno\s+(single" + _W + r"wide\s+|double" + _W + r"wide\s+)?(mobile|manufactured)(\s+homes?)?\b|\b(mobile|manufactured)\s+homes?\s+(are\s+)?not\s+(allowed|permitted)\b|\bstick" + _W + r"built\s+only\b"),
    ("min_size", "warn", "Minimum house size required",
     r"\bminimum\s+(of\s+)?[\d,]{3,6}\s*(sq\.?\s*f(ee)?t|square\s+f(ee|oo)t|sf)\b"),
    ("line_easement", "warn", "Power-line or pipeline easement across the land",
     r"\b(BPA|transmission|high" + _W + r"voltage|power" + _W + r"line|pipe" + _W + r"line|gas\s+line)\s+(easement|corridor|right" + _W + r"of" + _W + r"way)\b"),
    ("road_agreement", "warn", "Shared road maintenance agreement",
     r"\broad\s+maintenance\s+(agreement|association|fee)"),
]
_RED = [(k, lvl, lab, re.compile(rx, re.I)) for k, lvl, lab, rx in RED_FLAGS]


def red_flags(text):
    """[{key, level, label, detail}] for restrictions mentioned in the listing text."""
    out = []
    for key, level, label, rx in _RED:
        m = rx.search(text or "")
        if m:
            out.append({"key": key, "level": level, "label": label,
                        "detail": f'"{_snippet(text, m.start(), m.end())}"', "source": "listing remarks"})
    return out


# --- Structured MLS (RESO Data Dictionary) values -----------------------------

def from_structured(listing):
    """Evidence from RESO-style structured fields (WaterSource, Electric, Sewer,
    RoadFrontageType...). These come straight from the listing agent, so they
    rank above free-text matches."""
    out = {"water": [], "electric": [], "septic": [], "access": []}
    src = f"{listing.source} MLS field"

    w = listing.water_source.lower()
    neg = re.compile(r"\b(no|none|needed|needs|required|not|to\s+be\s+drilled)\b")
    if w:
        if "none" in w or "no water" in w or ("well" in w and neg.search(w)):
            out["water"].append(Evidence("none", HIGH, f"WaterSource = {listing.water_source}", src))
        elif "shared" in w:
            out["water"].append(Evidence("shared_well", HIGH, f"WaterSource = {listing.water_source}", src))
        elif "well" in w or "private" in w:
            out["water"].append(Evidence("well", HIGH, f"WaterSource = {listing.water_source}", src))
        elif any(k in w for k in ("public", "municipal", "district", "community", "city")):
            out["water"].append(Evidence("public", HIGH, f"WaterSource = {listing.water_source}", src))
        else:
            out["water"].extend(scan_text(listing.water_source, src)["water"])

    e = listing.electric.lower()
    if e:
        if "none" in e or "not available" in e or "off grid" in e or re.search(r"\bno\s+(power|electric)", e):
            out["electric"].append(Evidence("none", HIGH, f"Electric = {listing.electric}", src))
        elif any(k in e for k in ("at road", "at street", "nearby", "available", "lot line", "adjacent")):
            out["electric"].append(Evidence("at_road", HIGH, f"Electric = {listing.electric}", src))
        elif any(k in e for k in ("on site", "on property", "installed", "in place", "connected", "to property", "amp")):
            out["electric"].append(Evidence("on_site", HIGH, f"Electric = {listing.electric}", src))
        else:
            out["electric"].extend(scan_text(listing.electric, src)["electric"])

    s = listing.sewer.lower()
    if s:
        if "sewer" in s and neg.search(s) and "septic" not in s and "public" not in s:
            out["septic"].append(Evidence("required", MEDIUM, f"Sewer = {listing.sewer}", src))
        elif "public" in s or ("sewer" in s and "septic" not in s):
            out["septic"].append(Evidence("sewer", HIGH, f"Sewer = {listing.sewer}", src))
        elif "perc" in s or "approved" in s or "design" in s:
            out["septic"].append(Evidence("approved", HIGH, f"Sewer = {listing.sewer}", src))
        elif "installed" in s or "existing" in s or s.strip() in ("septic tank", "septic system", "septic"):
            # Many MLSs use plain "Septic" to mean "septic will be the system".
            status = "installed" if ("installed" in s or "existing" in s) else "required"
            out["septic"].append(Evidence(status, HIGH if status == "installed" else MEDIUM,
                                          f"Sewer = {listing.sewer}", src))
        elif "none" in s or "needed" in s or "required" in s:
            out["septic"].append(Evidence("needed", HIGH, f"Sewer = {listing.sewer}", src))
        else:
            out["septic"].extend(scan_text(listing.sewer, src)["septic"])

    r = listing.road_access.lower()
    if r:
        if "none" in r or "landlocked" in r or "no access" in r:
            out["access"].append(Evidence("landlocked", HIGH, f"Road access = {listing.road_access}", src))
        elif "easement" in r:
            out["access"].append(Evidence("easement", HIGH, f"Road access = {listing.road_access}", src))
        elif "private" in r:
            out["access"].append(Evidence("private_road", HIGH, f"Road access = {listing.road_access}", src))
        elif any(k in r for k in ("county", "city", "state", "public", "paved", "highway", "frontage")):
            out["access"].append(Evidence("public_road", HIGH, f"Road access = {listing.road_access}", src))
        else:
            out["access"].extend(scan_text(listing.road_access, src)["access"])
    return out
