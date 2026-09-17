"""Is the overview gauge actually laid out, or did WebKit collapse it?

ONE definition, because there are two drivers that ask: `tests/ui_smoke.py`
against the repository's web assets, and `tests/frozen_ui_smoke.py` against the
bundle the user launches. They had a copy each, with the same two numbers in
both, and on 2026-09-16 exactly what that costs happened: the results-polish
design scaled the gauge from 200x140 to 156x117, E62 fixed the copy in
`ui_smoke`, the install ran, and the OTHER copy refused the promotion at the
frozen smoke -- correctly, and after a full build. LESSONS 20 (a gate covering
one call site of a mechanism) and LESSONS 55 (the same rule written twice) in
the same failure.

WHAT IS BEING ASSERTED. Not a size. The failure this exists for is an element
whose used size WebKit resolved to nothing while its DOM looked healthy: paths
present, number present, and a blank card in front of the user. So:

  * it has a box at all;
  * that box is at the proportions of its own viewBox -- a sliver fails, and
    0x0 has no proportions;
  * the card reserves the height it takes (that collapse took the wrapper too);
  * and it is a real share of that card rather than a token mark in it.

Every one of those is false for the collapse and true at whatever size a
design picks, so a redesign does not have to come back here. Verified red on
the real page for a 0x0 collapse, a 156x8 sliver and a proportional 24x18
shrink; green for the gauge as shipped.
"""
from __future__ import annotations

#: Reads the geometry in the page. Written to work in both drivers: `#gauge`
#: is the SVG itself in the repository markup, and the frozen driver resolves
#: it defensively in case the bundle ever wraps it.
MEASURE_JS = '''(() => {
  const g = document.getElementById("gauge");
  const svg = g && (g.matches("svg") ? g : g.querySelector("svg"));
  const wrap = g && (g.closest(".gauge-wrap") || g.parentElement);
  if (!svg || !wrap) return null;
  const b = svg.getBoundingClientRect();
  const w = wrap.getBoundingClientRect();
  const box = (svg.getAttribute("viewBox") || "").split(/\\s+/).map(Number);
  return {width: b.width, height: b.height,
          wrapWidth: w.width, wrapHeight: w.height,
          vbWidth: box[2] || 0, vbHeight: box[3] || 0,
          paths: svg.querySelectorAll("path").length,
          text: (svg.querySelector("text") || {}).textContent || ""};
})()'''

#: How far the drawn box may drift from its viewBox's proportions. Small: the
#: design scales the gauge, it does not stretch it -- 156/117 and 200/150 are
#: the same ratio to the digit.
ASPECT_TOLERANCE = 0.02

#: The share of its card the gauge must occupy. Measured as shipped: 156 of
#: 265.67, so 0.59. The floor is tied to the card rather than to a pixel count
#: precisely so the next design change does not land here.
MINIMUM_SHARE_OF_CARD = 0.4


def is_laid_out(measurement) -> bool:
    """True when the gauge is drawn as itself inside its card."""
    if not measurement:
        return False
    width = measurement.get("width", 0) or 0
    height = measurement.get("height", 0) or 0
    vb_width = measurement.get("vbWidth", 0) or 0
    vb_height = measurement.get("vbHeight", 0) or 0
    wrap_width = measurement.get("wrapWidth", 0) or 0
    wrap_height = measurement.get("wrapHeight", 0) or 0
    if width <= 0 or height <= 0 or vb_height <= 0 or vb_width <= 0:
        return False
    own_ratio = vb_width / vb_height
    if abs(width / height - own_ratio) > ASPECT_TOLERANCE * own_ratio:
        return False
    if wrap_height < height - 1:
        return False
    if width < MINIMUM_SHARE_OF_CARD * wrap_width:
        return False
    return (measurement.get("paths", 0) >= 2
            and "%" in (measurement.get("text") or ""))
