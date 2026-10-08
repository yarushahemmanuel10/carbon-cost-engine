"""Server-drawn SVG charts (no JavaScript, no CDN: they work offline and print).

Colour follows the method in the project's chart notes:
  * one series  -> one hue, no legend (the title says what it is);
  * categories  -> fixed palette slots in fixed order, keyed to the NRM1 element
                   code (an element keeps its colour on every chart);
  * every mark has a hover tooltip (<title>), and every chart sits next to a
    table with the same numbers, so nothing depends on colour alone.
The palette was checked with a colour-vision validator (adjacent pairs pass;
three light hues are below 3:1 contrast, so labels and tables carry the values).
"""
from __future__ import annotations

from html import escape

SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
ONE_HUE = "#2a78d6"
INK, MUTED, GRID, SURFACE = "#1d2433", "#5b6475", "#e4e7ec", "#ffffff"
FONT = 'font-family="system-ui,-apple-system,Segoe UI,sans-serif"'


def element_colours(codes):
    """Fixed colour per element code, in code order, so colours never depend on rank."""
    ordered = sorted(set(codes), key=lambda c: [int(p) for p in c.split(".")])
    return {c: SERIES[i % len(SERIES)] for i, c in enumerate(ordered)}


def _ticks(lo, hi, target=6):
    """Round tick values (steps of 1, 2, 2.5 or 5 x 10^k) covering lo..hi."""
    import math
    span = max(hi - lo, 1e-9)
    raw = span / max(target - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, t = [], start
    while t < hi + step * 0.999:
        ticks.append(round(t, 10))
        t += step
    return ticks


def _nice_max(v):
    return _ticks(0, v)[-1] if v > 0 else 1.0


def _bar_path(x, y, w, h, r=4):
    """A horizontal bar: square at the baseline (left), 4px rounded at the data end."""
    if w <= 0:
        return ""
    r = min(r, w, h / 2)
    return (f"M{x:.1f},{y:.1f}h{w - r:.1f}a{r},{r} 0 0 1 {r},{r}v{h - 2 * r:.1f}"
            f"a{r},{r} 0 0 1 -{r},{r}h-{w - r:.1f}z")


def bar_chart(rows, title, unit="tCO₂e", width=1080):
    """Horizontal bars, sorted largest first. rows: [(label, value, tooltip)]."""
    rows = sorted(rows, key=lambda r: r[1], reverse=True)
    left, right, band, bar = 230, 70, 30, 18
    top = 34
    h = top + band * len(rows) + 48
    plot = width - left - right
    ticks = _ticks(0, max((r[1] for r in rows), default=1))
    vmax = ticks[-1]
    out = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img" aria-label="{escape(title)}" {FONT} font-size="13">',
           f'<text x="0" y="16" fill="{INK}" font-weight="600" font-size="14">{escape(title)}</text>']
    for tv in ticks:
        x = left + plot * tv / vmax
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top - 6}" y2="{h - 44}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{h - 28}" fill="{MUTED}" font-size="11" text-anchor="middle">{tv:,.0f}</text>')
    for i, (label, value, tip) in enumerate(rows):
        y = top + i * band + (band - bar) / 2
        w = plot * value / vmax if vmax else 0
        out.append(f'<text x="{left - 10}" y="{y + bar / 2 + 4:.1f}" fill="{INK}" text-anchor="end">{escape(label)}</text>')
        out.append(f'<g><title>{escape(tip)}</title><rect x="{left}" y="{y - 5:.1f}" width="{plot}" height="{bar + 10}" fill="transparent"/>'
                   f'<path d="{_bar_path(left, y, w, bar)}" fill="{ONE_HUE}"/></g>')
        out.append(f'<text x="{left + w + 6:.1f}" y="{y + bar / 2 + 4:.1f}" fill="{INK}" font-size="12">{value:,.1f}</text>')
    out.append(f'<text x="{left + plot / 2:.1f}" y="{h - 6}" fill="{MUTED}" font-size="12" text-anchor="middle">{escape(unit)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def stacked_bars(options, colours, names, title, unit="kgCO₂e per m² GIA", width=1080):
    """One horizontal bar per design option, split into elements.

    options: [(option label, {element code: value}, total)]; colours/names: by code.
    """
    left, right, band, bar = 210, 80, 40, 22
    codes = [c for c in colours if any(o[1].get(c) for o in options)]
    legend_rows = (len(codes) + 3) // 4
    top = 34 + legend_rows * 20 + 8
    h = top + band * len(options) + 48
    plot = width - left - right
    ticks = _ticks(0, max((o[2] for o in options), default=1))
    vmax = ticks[-1]
    out = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img" aria-label="{escape(title)}" {FONT} font-size="13">',
           f'<text x="0" y="16" fill="{INK}" font-weight="600" font-size="14">{escape(title)}</text>']
    for i, c in enumerate(codes):                         # legend: swatch beside ink-coloured text
        lx, ly = (i % 4) * (width / 4), 34 + (i // 4) * 20
        out.append(f'<rect x="{lx:.1f}" y="{ly - 9}" width="10" height="10" rx="2" fill="{colours[c]}"/>'
                   f'<text x="{lx + 15:.1f}" y="{ly}" fill="{INK}" font-size="12">{escape(c)} {escape(names[c])}</text>')
    for tv in ticks:
        x = left + plot * tv / vmax
        out.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top - 6}" y2="{h - 44}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{x:.1f}" y="{h - 28}" fill="{MUTED}" font-size="11" text-anchor="middle">{tv:,.0f}</text>')
    for i, (label, parts, total) in enumerate(options):
        y = top + i * band + (band - bar) / 2
        out.append(f'<text x="{left - 10}" y="{y + bar / 2 + 4:.1f}" fill="{INK}" text-anchor="end">{escape(label)}</text>')
        x = left
        present = [c for c in codes if parts.get(c)]
        for j, c in enumerate(present):
            w = plot * parts[c] / vmax
            gap = 2 if j < len(present) - 1 else 0            # 2px surface gap between segments
            last = j == len(present) - 1
            shape = (f'<path d="{_bar_path(x, y, max(w - gap, 0.5), bar)}" fill="{colours[c]}"/>' if last else
                     f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w - gap, 0.5):.1f}" height="{bar}" fill="{colours[c]}"/>')
            share = parts[c] / total if total else 0
            out.append(f'<g><title>{escape(label)}: {escape(c)} {escape(names[c])}, {parts[c]:,.1f} ({share:.0%})</title>{shape}</g>')
            x += w
        out.append(f'<text x="{x + 6:.1f}" y="{y + bar / 2 + 4:.1f}" fill="{INK}" font-size="12" font-weight="600">{total:,.1f}</text>')
    out.append(f'<text x="{left + plot / 2:.1f}" y="{h - 6}" fill="{MUTED}" font-size="12" text-anchor="middle">{escape(unit)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def scatter(points, title, x_unit, y_unit, width=1080, height=460):
    """Cost (x) against carbon (y). points: [(label, x, y)]. One hue; identity by direct labels.

    Points that sit close together get their labels stacked to one side with thin
    leader lines, so labels never overlap each other.
    """
    left, right, top, bottom = 80, 260, 40, 56
    pw, ph = width - left - right, height - top - bottom
    xs, ys = [p[1] for p in points], [p[2] for p in points]
    pad = max((max(xs) - min(xs)) * 0.15, max(xs) * 0.03) if xs else 1
    xt = _ticks(min(xs) - pad, max(xs) + pad) if xs else [0, 1]
    yt = _ticks(0, max(ys) * 1.05) if ys else [0, 1]
    x0, x1, y0, y1 = xt[0], xt[-1], 0.0, yt[-1]

    def sx(v):
        return left + pw * (v - x0) / (x1 - x0)

    def sy(v):
        return top + ph * (1 - (v - y0) / (y1 - y0))

    out = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{escape(title)}" {FONT} font-size="13">',
           f'<text x="0" y="16" fill="{INK}" font-weight="600" font-size="14">{escape(title)}</text>']
    for yv in yt:
        out.append(f'<line x1="{left}" x2="{left + pw}" y1="{sy(yv):.1f}" y2="{sy(yv):.1f}" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{left - 8}" y="{sy(yv) + 4:.1f}" fill="{MUTED}" font-size="11" text-anchor="end">{yv:,.0f}</text>')
    for xv in xt:
        out.append(f'<text x="{sx(xv):.1f}" y="{top + ph + 18}" fill="{MUTED}" font-size="11" text-anchor="middle">{xv:,.0f}</text>')
    out.append(f'<text x="{left + pw / 2}" y="{height - 8}" fill="{MUTED}" font-size="12" text-anchor="middle">{escape(x_unit)}</text>')
    out.append(f'<text x="16" y="{top + ph / 2}" fill="{MUTED}" font-size="12" text-anchor="middle" '
               f'transform="rotate(-90 16 {top + ph / 2})">{escape(y_unit)}</text>')
    # labels go in a column to the right of the plot, in the points' vertical order,
    # at least 20px apart, each joined to its point by a thin leader line
    placed = sorted(((sy(p[2]), p) for p in points), key=lambda t: t[0])
    label_y, last = [], -1e9
    for py, _ in placed:
        y = max(py, last + 20)
        label_y.append(y)
        last = y
    lx = left + pw + 24
    for (py, (label, xv, yv)), ly in zip(placed, label_y):
        cx = sx(xv)
        out.append(f'<polyline points="{cx + 8:.1f},{py:.1f} {lx - 14:.1f},{ly:.1f} {lx - 4:.1f},{ly:.1f}" fill="none" stroke="{GRID}" stroke-width="1"/>')
        out.append(f'<text x="{lx:.1f}" y="{ly + 4:.1f}" fill="{INK}" font-size="12">{escape(label)}</text>')
    for py, (label, xv, yv) in placed:
        cx = sx(xv)
        out.append(f'<g><title>{escape(label)}: {xv:,.0f} {escape(x_unit)}, {yv:,.1f} {escape(y_unit)}</title>'
                   f'<circle cx="{cx:.1f}" cy="{py:.1f}" r="14" fill="transparent"/>'
                   f'<circle cx="{cx:.1f}" cy="{py:.1f}" r="6" fill="{ONE_HUE}" stroke="{SURFACE}" stroke-width="2"/></g>')
    out.append("</svg>")
    return "\n".join(out)
