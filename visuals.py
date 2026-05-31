"""
NeuroDigest — visual generation for Guidelines Edition.

Pipeline:
  1. extract_visual_data()   — Claude call → structured JSON (max 2 visuals)
  2. render_visual()         — dispatch to renderer
  3. generate_visuals()      — orchestrate extraction + rendering
  4. visuals_html_block()    — HTML snippet for email body
  5. visuals_to_attachments()— Resend attachment dicts
"""

import base64
import io
import json
import os
import re
import textwrap
from datetime import datetime
from pathlib import Path

import anthropic
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch
import numpy as np

# ── Palette (Okabe-Ito, colorblind-safe) ─────────────────────────────────────
C_BLUE   = "#0072B2"
C_ORANGE = "#E69F00"
C_GREEN  = "#009E73"
C_NAVY   = "#1a1a2e"
C_GREY   = "#999999"
C_LGREY  = "#e0e0dc"
C_BG     = "#ffffff"
PALETTE  = [C_BLUE, C_ORANGE, C_GREEN, "#CC3311"]

DPI       = 300
WATERMARK = "NeuroDigest"

plt.rcParams.update({
    "font.family":        "DejaVu Sans",
    "figure.facecolor":   C_BG,
    "axes.facecolor":     C_BG,
    "savefig.facecolor":  C_BG,
    "savefig.bbox":       "tight",
    "savefig.pad_inches": 0.15,
    "savefig.dpi":        DPI,
    "axes.spines.top":    False,
    "axes.spines.right":  False,
})


# ── Extraction prompt ─────────────────────────────────────────────────────────
EXTRACTION_SYSTEM = """You are a medical data extraction engine.
Given a neurology clinical guideline summary, extract data for at most 2 high-value visualizations.

Return ONLY a valid JSON object — no preamble, no markdown fences.

Schema:
{
  "guideline_title": string,
  "source": string,
  "year": integer,
  "visuals": [
    {
      "type": "flowchart" | "table" | "bar_chart" | "timeline" | "schematic",
      "title": string,
      "description": string,
      "data": {}
    }
  ]
}

Data structure by type:

flowchart:
{ "nodes": [{"id": string, "label": string, "type": "decision"|"action"|"endpoint"}],
  "edges": [{"from": string, "to": string, "label": string}] }
— max 8 nodes; labels ≤ 6 words; linear or simple branching only

table:
{ "headers": [string], "rows": [[string]] }
— max 3 columns, max 8 rows; cell text ≤ 8 words

bar_chart:
{ "x_label": string, "y_label": string,
  "series": [{"name": string, "values": [{"label": string, "value": number}]}] }
— max 2 series, max 6 bars; ONLY use if you have real numeric values

timeline:
{ "events": [{"time": string, "label": string, "note": string}] }
— max 7 events; "time" ≤ 5 chars; "label" ≤ 5 words; "note" ≤ 8 words

schematic:
{
  "caption": string,
  "compartments": [
    {
      "id": string,
      "label": string,
      "color": string,
      "entities": [
        { "id": string, "label": string, "sublabel": string,
          "type": "molecule"|"cell"|"receptor"|"barrier"|"organ"|"process",
          "color": string }
      ]
    }
  ],
  "flows": [
    { "from": string, "to": string, "label": string,
      "style": "arrow"|"inhibit"|"dashed" }
  ]
}
— Use for pathophysiology, disease mechanisms, BBB models, receptor signalling, drug targets.
— max 4 compartments; max 3 entities per compartment; max 8 flows.
— compartment colors: soft pastels (e.g. "#fce4ec", "#e3f2fd", "#e8f5e9", "#fff8e1").
— entity colors: vivid but not neon (e.g. "#e53935", "#1565c0", "#2e7d32", "#f57f17").
— labels ≤ 4 words; sublabel ≤ 5 words (use for e.g. "AQP4-IgG", "IL-6↑", "CD20+").
— flows reference entity IDs, not compartment IDs.

Rules:
- Maximum 2 visuals. Choose the 2 most informative and complementary.
- USE schematic when the topic has a clear mechanism, signalling pathway, or anatomical model
  (e.g. NMOSD, neuroinflammation, Alzheimer amyloid cascade, BBB disruption, myasthenia gravis,
  anti-NMDAR encephalitis, Parkinson α-synuclein propagation).
- Prefer flowchart for clinical algorithms; table for dosing/criteria; timeline for monitoring.
- Only use bar_chart with real numeric values.
- Keep ALL text short — it will be rendered inside graphic elements.
- Never invent numeric data.
"""


def extract_visual_data(guideline: dict, client: anthropic.Anthropic) -> dict | None:
    text = _guideline_to_text(guideline)

    def _call(prefix=""):
        r = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=3000,
            system=EXTRACTION_SYSTEM,
            messages=[{"role": "user", "content": f"{prefix}Extract visualization data:\n\n{text}"}],
        )
        if not r.content:
            return None
        raw = r.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    result = _call() or _call("Return ONLY raw JSON, no text outside the object. ")
    if result:
        visuals = result.get("visuals", [])
        print(f"  Extracted {len(visuals)} visual(s): {[v['type'] for v in visuals]}")
    else:
        print("  Visual extraction failed — skipping visuals")
    return result


def _guideline_to_text(g: dict) -> str:
    parts = [
        f"Topic: {g.get('specific_topic', g.get('topic', ''))}",
        f"Headline: {g.get('guideline_headline', '')}",
        "",
    ]
    for th in g.get("themes", []):
        parts += [f"## {th.get('title', '')}", th.get("body", ""), ""]
    recs = g.get("key_recommendations", [])
    if recs:
        parts += ["Recommendations:"] + [f"- {r}" for r in recs] + [""]
    for s in g.get("sources", []):
        parts.append(f"Source: {s.get('title','')} ({s.get('issuing_body','')} {s.get('year','')})")
    return "\n".join(parts)


# ── Shared helpers ────────────────────────────────────────────────────────────
def _watermark(fig: plt.Figure, source: str = "") -> None:
    label = f"{source}   ·   {WATERMARK}" if source else WATERMARK
    fig.text(0.99, 0.01, label, ha="right", va="bottom",
             fontsize=7, color=C_GREY, fontstyle="italic")


def _save(fig: plt.Figure, stem: Path) -> tuple[Path, Path]:
    png = stem.with_suffix(".png")
    pdf = stem.with_suffix(".pdf")
    fig.savefig(png, format="png")
    fig.savefig(pdf, format="pdf")
    plt.close(fig)
    print(f"    → {png.name}")
    return png, pdf


def _wrap(text: str, width: int) -> str:
    return "\n".join(textwrap.wrap(text, width=width))


# ── Flowchart ─────────────────────────────────────────────────────────────────
def render_flowchart(visual: dict, stem: Path, source: str = "") -> tuple[Path, Path]:
    data  = visual["data"]
    nodes = {n["id"]: n for n in data.get("nodes", [])}
    edges = data.get("edges", [])

    # Build adjacency
    children: dict = {nid: [] for nid in nodes}
    parents:  dict = {nid: [] for nid in nodes}
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f in children and t in children:
            children[f].append(t)
            parents[t].append(f)

    # BFS layering from roots
    roots = [nid for nid in nodes if not parents[nid]] or [list(nodes.keys())[0]]
    layers: dict = {}
    queue = [(r, 0) for r in roots]
    visited: set = set()
    while queue:
        nid, d = queue.pop(0)
        if nid in visited:
            continue
        visited.add(nid)
        layers[nid] = max(layers.get(nid, 0), d)
        for c in children[nid]:
            queue.append((c, d + 1))

    layer_nodes: dict = {}
    for nid, d in layers.items():
        layer_nodes.setdefault(d, []).append(nid)

    n_layers = max(layers.values()) + 1 if layers else 1
    max_per_layer = max(len(v) for v in layer_nodes.values()) if layer_nodes else 1

    # Figure dimensions in inches — scale with content
    COL_W  = 2.8   # inches per column
    ROW_H  = 1.5   # inches per layer
    FIG_W  = max(7, max_per_layer * COL_W + 1.5)
    FIG_H  = max(5, n_layers * ROW_H + 1.5)

    # Data-space coords: x ∈ [0, FIG_W], y ∈ [0, FIG_H]
    BOX_W  = COL_W * 0.82
    BOX_H  = 0.55
    DIAM_W = COL_W * 0.70
    DIAM_H = 0.45

    positions: dict = {}
    for depth, nids in layer_nodes.items():
        y = FIG_H - (depth + 0.7) * ROW_H
        xs = np.linspace(FIG_W / (len(nids) + 1),
                         FIG_W * len(nids) / (len(nids) + 1),
                         len(nids))
        for nid, x in zip(nids, xs):
            positions[nid] = (x, y)

    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    ax.set_xlim(0, FIG_W)
    ax.set_ylim(0, FIG_H)
    ax.axis("off")

    # Draw edges
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if f not in positions or t not in positions:
            continue
        x1, y1 = positions[f]
        x2, y2 = positions[t]
        ntype_f = nodes[f].get("type", "action")
        y_start = y1 - (DIAM_H if ntype_f == "decision" else BOX_H / 2)
        y_end   = y2 + BOX_H / 2

        ax.annotate("", xy=(x2, y_end), xytext=(x1, y_start),
                    arrowprops=dict(
                        arrowstyle="-|>", color=C_GREY, lw=1.0,
                        mutation_scale=12,
                        connectionstyle="arc3,rad=0.0",
                    ))
        lbl = e.get("label", "")
        if lbl:
            mx = (x1 + x2) / 2
            my = (y_start + y_end) / 2
            ax.text(mx + 0.1, my, lbl, fontsize=7, color=C_GREY,
                    ha="left", va="center", fontstyle="italic")

    # Draw nodes
    for nid, node in nodes.items():
        if nid not in positions:
            continue
        x, y    = positions[nid]
        ntype   = node.get("type", "action")
        wrapped = _wrap(node["label"], 20)

        if ntype == "decision":
            # Diamond via polygon
            pts = [(x,           y + DIAM_H),
                   (x + DIAM_W,  y),
                   (x,           y - DIAM_H),
                   (x - DIAM_W,  y)]
            poly = plt.Polygon(pts, closed=True,
                               facecolor="#FFF3CD", edgecolor=C_ORANGE,
                               linewidth=1.8, zorder=3)
            ax.add_patch(poly)
            ax.text(x, y, wrapped, ha="center", va="center",
                    fontsize=7.5, color=C_NAVY, fontweight="bold",
                    zorder=4, linespacing=1.35)

        elif ntype == "endpoint":
            box = FancyBboxPatch((x - BOX_W / 2, y - BOX_H / 2),
                                  BOX_W, BOX_H,
                                  boxstyle="round,pad=0.05",
                                  facecolor=C_NAVY, edgecolor=C_NAVY,
                                  linewidth=1.5, zorder=3)
            ax.add_patch(box)
            ax.text(x, y, wrapped, ha="center", va="center",
                    fontsize=7.5, color="white", fontweight="bold",
                    zorder=4, linespacing=1.35)

        else:  # action
            box = FancyBboxPatch((x - BOX_W / 2, y - BOX_H / 2),
                                  BOX_W, BOX_H,
                                  boxstyle="round,pad=0.05",
                                  facecolor="#E8F4FD", edgecolor=C_BLUE,
                                  linewidth=1.5, zorder=3)
            ax.add_patch(box)
            ax.text(x, y, wrapped, ha="center", va="center",
                    fontsize=7.5, color=C_NAVY,
                    zorder=4, linespacing=1.35)

    ax.set_title(visual["title"], fontsize=12, fontweight="bold",
                 color=C_NAVY, pad=10, loc="left", x=0.02)
    _watermark(fig, source)
    return _save(fig, stem)


# ── Table ─────────────────────────────────────────────────────────────────────
def render_table(visual: dict, stem: Path, source: str = "") -> tuple[Path, Path]:
    data    = visual["data"]
    headers = data.get("headers", [])
    rows    = data.get("rows", [])
    n_cols  = len(headers)

    # Pad rows
    rows = [r + [""] * max(0, n_cols - len(r)) for r in rows]

    FIG_W = min(12, max(7, n_cols * 3.2))
    FIG_H = max(2.5, 0.6 + len(rows) * 0.48 + 0.6)

    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    ax.axis("off")

    tbl = ax.table(
        cellText=rows,
        colLabels=headers,
        cellLoc="left",
        loc="center",
        colWidths=[1 / n_cols] * n_cols,
    )
    tbl.auto_set_font_size(False)
    tbl.set_fontsize(9)
    tbl.scale(1, 1.6)

    for j in range(n_cols):
        cell = tbl[0, j]
        cell.set_facecolor(C_NAVY)
        cell.set_text_props(color="white", fontweight="bold")
        cell.set_edgecolor(C_NAVY)

    for i in range(1, len(rows) + 1):
        for j in range(n_cols):
            cell = tbl[i, j]
            cell.set_facecolor("#EEF4FB" if i % 2 == 0 else C_BG)
            cell.set_edgecolor(C_LGREY)
            cell.set_text_props(color=C_NAVY)

    ax.set_title(visual["title"], fontsize=12, fontweight="bold",
                 color=C_NAVY, pad=14, loc="left", x=0.0)
    _watermark(fig, source)
    return _save(fig, stem)


# ── Bar chart ─────────────────────────────────────────────────────────────────
def render_bar_chart(visual: dict, stem: Path, source: str = "") -> tuple[Path, Path]:
    data   = visual["data"]
    series = data.get("series", [])
    if not series:
        raise ValueError("No series data")

    all_labels = list(dict.fromkeys(
        v["label"] for s in series for v in s.get("values", [])
    ))

    n_groups = len(all_labels)
    n_series = len(series)
    bar_h    = 0.6 / max(n_series, 1)
    offsets  = np.linspace(-(n_series - 1) * bar_h / 2,
                            (n_series - 1) * bar_h / 2, n_series)

    FIG_W = 9
    FIG_H = max(4, n_groups * 0.7 + 1.8)
    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))

    y_pos = np.arange(n_groups)
    for si, (s, offset) in enumerate(zip(series, offsets)):
        val_map = {v["label"]: v["value"] for v in s.get("values", [])}
        vals    = [val_map.get(lbl, 0) for lbl in all_labels]
        max_v   = max(vals) if max(vals) else 1
        bars    = ax.barh(y_pos + offset, vals, height=bar_h * 0.85,
                          color=PALETTE[si % len(PALETTE)],
                          label=s["name"], alpha=0.90)
        for bar, val in zip(bars, vals):
            if val:
                ax.text(val + max_v * 0.015,
                        bar.get_y() + bar.get_height() / 2,
                        f"{val:g}", va="center", ha="left",
                        fontsize=8, color=C_NAVY)

    ax.set_yticks(y_pos)
    ax.set_yticklabels(all_labels, fontsize=9, color=C_NAVY)
    ax.set_xlabel(data.get("x_label", ""), fontsize=9, color=C_GREY)
    ax.tick_params(colors=C_GREY, length=3)
    ax.spines["left"].set_color(C_LGREY)
    ax.spines["bottom"].set_color(C_LGREY)
    ax.set_axisbelow(True)
    ax.xaxis.grid(True, color=C_LGREY, linewidth=0.5)
    if n_series > 1:
        ax.legend(fontsize=8, frameon=False)

    ax.set_title(visual["title"], fontsize=12, fontweight="bold",
                 color=C_NAVY, pad=10, loc="left")
    _watermark(fig, source)
    return _save(fig, stem)


# ── Timeline ─────────────────────────────────────────────────────────────────
def render_timeline(visual: dict, stem: Path, source: str = "") -> tuple[Path, Path]:
    events = visual["data"].get("events", [])
    if not events:
        raise ValueError("No events")

    n     = len(events)
    FIG_W = max(10, n * 1.6)
    FIG_H = 4.5

    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    ax.set_xlim(-0.5, n - 0.5)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # Centre spine
    ax.axhline(0.5, color=C_BLUE, lw=2.5, zorder=1, solid_capstyle="round")

    ABOVE = 0.5   # alternates above / below centreline
    for i, ev in enumerate(events):
        above  = (i % 2 == 0)
        x      = float(i)

        # Dot
        ax.plot(x, 0.5, "o", color=C_BLUE, markersize=11, zorder=3)
        ax.plot(x, 0.5, "o", color="white", markersize=5,  zorder=4)

        # Time chip
        y_chip = 0.50 + (0.10 if above else -0.10)
        ax.text(x, y_chip, ev.get("time", ""),
                ha="center", va="center",
                fontsize=8.5, fontweight="bold", color=C_NAVY,
                bbox=dict(boxstyle="round,pad=0.25", facecolor="white",
                          edgecolor=C_BLUE, linewidth=1.2))

        # Event label
        y_lbl = 0.50 + (0.27 if above else -0.27)
        lbl   = _wrap(ev.get("label", ""), 13)
        ax.text(x, y_lbl, lbl,
                ha="center", va="center" if above else "center",
                fontsize=8, color=C_NAVY, fontweight="bold",
                linespacing=1.3)

        # Note
        note = _wrap(ev.get("note", ""), 16)
        if note:
            y_note = 0.50 + (0.50 if above else -0.50)
            ax.text(x, y_note, note,
                    ha="center", va="center",
                    fontsize=7, color=C_GREY, linespacing=1.3)

    ax.set_title(visual["title"], fontsize=12, fontweight="bold",
                 color=C_NAVY, pad=10, loc="left", x=0.0)
    _watermark(fig, source)
    return _save(fig, stem)


# ── Schematic ─────────────────────────────────────────────────────────────────
# Biological shape helpers
def _pill(ax, cx, cy, w, h, color, text, sublabel="", fontsize=8.5, textcolor="white"):
    """Draw a pill-shaped entity badge."""
    box = FancyBboxPatch((cx - w/2, cy - h/2), w, h,
                          boxstyle="round,pad=0.04",
                          facecolor=color, edgecolor="white",
                          linewidth=1.2, zorder=4)
    ax.add_patch(box)
    if sublabel:
        ax.text(cx, cy + h*0.12, text, ha="center", va="center",
                fontsize=fontsize, color=textcolor, fontweight="bold",
                zorder=5, linespacing=1.2)
        ax.text(cx, cy - h*0.28, sublabel, ha="center", va="center",
                fontsize=fontsize - 1.5, color=textcolor, alpha=0.88,
                zorder=5)
    else:
        ax.text(cx, cy, text, ha="center", va="center",
                fontsize=fontsize, color=textcolor, fontweight="bold",
                zorder=5, linespacing=1.2)


def _compartment_band(ax, y0, height, color, label, fig_w):
    """Draw a horizontal biological compartment band."""
    band = mpatches.Rectangle((0, y0), fig_w, height,
                                facecolor=color, edgecolor="#cccccc",
                                linewidth=0.6, zorder=1, alpha=0.55)
    ax.add_patch(band)
    ax.text(0.22, y0 + height / 2, label,
            ha="center", va="center",
            fontsize=8.5, color="#555555", fontweight="bold",
            rotation=90, zorder=2)


def render_schematic(visual: dict, stem: Path, source: str = "") -> tuple[Path, Path]:
    """
    Render a biological mechanism/pathophysiology schematic.
    Compartments are horizontal bands; entities are pill badges;
    flows are styled arrows between entity positions.
    """
    data         = visual["data"]
    compartments = data.get("compartments", [])
    flows        = data.get("flows", [])
    caption      = data.get("caption", "")

    n_comp  = len(compartments)
    FIG_W   = 9.0
    COMP_H  = 1.7          # height per compartment band
    FIG_H   = max(4.5, n_comp * COMP_H + 1.2)

    fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
    ax.set_xlim(0, FIG_W)
    ax.set_ylim(0, FIG_H)
    ax.axis("off")

    # Entity position registry {entity_id: (cx, cy)}
    positions: dict[str, tuple] = {}
    PILL_W, PILL_H = 1.55, 0.52

    # Draw compartment bands + place entity pills
    for ci, comp in enumerate(compartments):
        y0     = FIG_H - (ci + 1) * COMP_H
        color  = comp.get("color", "#f5f5f5")
        label  = comp.get("label", "")
        entities = comp.get("entities", [])

        _compartment_band(ax, y0, COMP_H, color, label, FIG_W)

        # Distribute entities evenly inside the band
        n_ent = len(entities)
        xs    = np.linspace(1.2, FIG_W - 0.8, max(n_ent, 1))
        cy    = y0 + COMP_H / 2

        for ent, cx in zip(entities, xs):
            eid     = ent["id"]
            elabel  = "\n".join(textwrap.wrap(ent.get("label", ""), 12))
            esub    = ent.get("sublabel", "")
            ecolor  = ent.get("color", "#0072B2")
            positions[eid] = (cx, cy)
            _pill(ax, cx, cy, PILL_W, PILL_H, ecolor, elabel, esub)

    # Draw flows
    FLOW_COLORS = {"arrow": "#444444", "inhibit": "#CC3311", "dashed": "#888888"}
    for flow in flows:
        fid, tid = flow.get("from"), flow.get("to")
        if fid not in positions or tid not in positions:
            continue
        x1, y1 = positions[fid]
        x2, y2 = positions[tid]
        style  = flow.get("style", "arrow")
        color  = FLOW_COLORS.get(style, "#444444")
        ls     = "--" if style == "dashed" else "-"

        # Arrow tip: inhibit = flat bar, else normal arrowhead
        arrowstyle = "-|>" if style != "inhibit" else "-["
        rad = 0.25 if abs(y2 - y1) < 0.1 else 0.0  # curve if same row

        ax.annotate("", xy=(x2, y2 + PILL_H/2 * np.sign(y1-y2)),
                    xytext=(x1, y1 - PILL_H/2 * np.sign(y1-y2)),
                    arrowprops=dict(
                        arrowstyle=arrowstyle, color=color, lw=1.4,
                        linestyle=ls, mutation_scale=11,
                        connectionstyle=f"arc3,rad={rad}",
                    ), zorder=3)

        lbl = flow.get("label", "")
        if lbl:
            mx = (x1 + x2) / 2 + (0.12 if rad else 0)
            my = (y1 + y2) / 2
            ax.text(mx, my, lbl, fontsize=7, color=color, ha="center",
                    va="center", fontstyle="italic",
                    bbox=dict(boxstyle="round,pad=0.15", facecolor="white",
                              edgecolor="none", alpha=0.8), zorder=5)

    # Caption
    if caption:
        fig.text(0.5, 0.01, caption, ha="center", va="bottom",
                 fontsize=7.5, color="#666666", fontstyle="italic",
                 wrap=True)

    ax.set_title(visual["title"], fontsize=12, fontweight="bold",
                 color=C_NAVY, pad=10, loc="left", x=0.03)
    _watermark(fig, source)
    return _save(fig, stem)


# ── Input sanitisation (runs before every renderer) ───────────────────────────
def _trunc(text: str, max_words: int) -> str:
    """Truncate to max_words words, appending '…' if cut."""
    words = str(text).split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"


def _sanitize_flowchart(data: dict) -> dict:
    """Clamp nodes ≤ 8, labels ≤ 6 words, keep only edges between existing nodes."""
    nodes = data.get("nodes", [])[:8]
    node_ids = {n["id"] for n in nodes}
    for n in nodes:
        n["label"] = _trunc(n.get("label", ""), 6)
        if n.get("type") not in ("decision", "action", "endpoint"):
            n["type"] = "action"
    edges = [e for e in data.get("edges", [])
             if e.get("from") in node_ids and e.get("to") in node_ids]
    for e in edges:
        e["label"] = _trunc(e.get("label", ""), 4)
    return {"nodes": nodes, "edges": edges}


def _sanitize_table(data: dict) -> dict:
    """Clamp to 3 columns, 8 rows, 7 words per cell."""
    headers = [_trunc(h, 5) for h in data.get("headers", [])[:3]]
    n_cols  = len(headers)
    rows    = []
    for row in data.get("rows", [])[:8]:
        rows.append([_trunc(cell, 7) for cell in (row[:n_cols] + [""] * n_cols)[:n_cols]])
    return {"headers": headers, "rows": rows}


def _sanitize_bar_chart(data: dict) -> dict:
    """Clamp to 2 series, 6 values each; ensure numeric values."""
    series = []
    for s in data.get("series", [])[:2]:
        values = []
        for v in s.get("values", [])[:6]:
            try:
                values.append({"label": _trunc(v.get("label", ""), 5),
                                "value": float(v["value"])})
            except (KeyError, TypeError, ValueError):
                pass
        if values:
            series.append({"name": _trunc(s.get("name", ""), 4), "values": values})
    return {
        "x_label": _trunc(data.get("x_label", ""), 5),
        "y_label": _trunc(data.get("y_label", ""), 5),
        "series":  series,
    }


def _sanitize_timeline(data: dict) -> dict:
    """Clamp to 6 events, short labels."""
    events = []
    for ev in data.get("events", [])[:6]:
        events.append({
            "time":  _trunc(ev.get("time", ""), 4),
            "label": _trunc(ev.get("label", ""), 5),
            "note":  _trunc(ev.get("note", ""), 7),
        })
    return {"events": events}


def _sanitize_schematic(data: dict) -> dict:
    """Clamp compartments ≤ 4, entities ≤ 3 each, flows ≤ 8, labels short."""
    compartments = []
    all_entity_ids = set()
    for comp in data.get("compartments", [])[:4]:
        entities = []
        for ent in comp.get("entities", [])[:3]:
            ent = dict(ent)
            ent["label"]    = _trunc(ent.get("label", ""), 4)
            ent["sublabel"] = _trunc(ent.get("sublabel", ""), 5)
            entities.append(ent)
            all_entity_ids.add(ent["id"])
        compartments.append({**comp, "entities": entities})
    flows = [
        {**f, "label": _trunc(f.get("label", ""), 4)}
        for f in data.get("flows", [])[:8]
        if f.get("from") in all_entity_ids and f.get("to") in all_entity_ids
    ]
    return {**data, "compartments": compartments, "flows": flows}


SANITIZERS = {
    "flowchart":  _sanitize_flowchart,
    "table":      _sanitize_table,
    "bar_chart":  _sanitize_bar_chart,
    "timeline":   _sanitize_timeline,
    "schematic":  _sanitize_schematic,
}


def _sanitize(visual: dict) -> dict:
    """Return a copy of visual with sanitised data. Logs what changed."""
    vtype = visual.get("type")
    san   = SANITIZERS.get(vtype)
    if not san:
        return visual
    original  = visual.get("data", {})
    sanitised = san(original)
    # Log truncations
    orig_str = json.dumps(original)
    san_str  = json.dumps(sanitised)
    if orig_str != san_str:
        print(f"    [sanitise] {vtype}: data normalised before rendering")
    return {**visual, "data": sanitised}


# ── Dispatch ──────────────────────────────────────────────────────────────────
RENDERERS = {
    "flowchart":  render_flowchart,
    "table":      render_table,
    "bar_chart":  render_bar_chart,
    "timeline":   render_timeline,
    "schematic":  render_schematic,
}

def render_visual(visual: dict, stem: Path, source: str = "") -> tuple[Path, Path] | None:
    renderer = RENDERERS.get(visual.get("type"))
    if not renderer:
        return None
    try:
        clean = _sanitize(visual)          # ← always sanitise first
        return renderer(clean, stem, source)
    except Exception as e:
        print(f"  Render error ({visual.get('type')}): {e}")
        return None


# ── Orchestrate ───────────────────────────────────────────────────────────────
def generate_visuals(
    guideline: dict,
    client: anthropic.Anthropic,
    output_dir: Path,
    topic_slug: str,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)

    extracted = extract_visual_data(guideline, client)
    if not extracted:
        return []

    source_label = f"{extracted.get('source', '')} {extracted.get('year', '')}".strip()
    year  = datetime.now().strftime("%Y")
    month = datetime.now().strftime("%m")

    results = []
    for i, visual in enumerate(extracted.get("visuals", [])[:2]):   # hard cap: 2
        vtype = visual.get("type", "unknown")
        fname = f"NeuroDigest_GL_{year}_{month}_{topic_slug}_{i+1}_{vtype}"
        stem  = output_dir / fname

        print(f"  Rendering {i+1}/{min(len(extracted['visuals']),2)}: {vtype} — {visual.get('title','')[:50]}")
        paths = render_visual(visual, stem, source_label)
        if not paths:
            continue

        png_path, pdf_path = paths
        results.append({
            "type":         vtype,
            "title":        visual.get("title", ""),
            "description":  visual.get("description", ""),
            "png":          png_path,
            "pdf":          pdf_path,
            "thumb_b64":    _thumb(png_path),
            "png_filename": png_path.name,
            "pdf_filename": pdf_path.name,
        })

    print(f"  {len(results)} visual(s) ready")
    return results


def _thumb(png: Path, width: int = 560) -> str:
    try:
        from PIL import Image
        img   = Image.open(png)
        ratio = width / img.width
        img   = img.resize((width, int(img.height * ratio)), Image.LANCZOS)
        buf   = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        return base64.b64encode(png.read_bytes()).decode()


# ── Email HTML block ──────────────────────────────────────────────────────────
def visuals_html_block(visuals: list[dict]) -> str:
    if not visuals:
        return ""

    cards = ""
    for v in visuals:
        thumb = v.get("thumb_b64", "")
        img_tag = (
            f'<img src="data:image/png;base64,{thumb}" width="100%"'
            f' style="display:block;border:1px solid #e8e8e4;margin-bottom:10px"'
            f' alt="{v["title"]}">'
        ) if thumb else ""

        cards += f"""
        <div style="margin-bottom:28px">
          <p style="margin:0 0 4px;font-size:10px;font-weight:700;letter-spacing:1.5px;
                    text-transform:uppercase;color:#8b6914;
                    font-family:Helvetica,Arial,sans-serif">{v['type'].replace('_',' ')}</p>
          <p style="margin:0 0 8px;font-size:14px;font-weight:700;color:#1a1a2e;
                    font-family:Georgia,'Times New Roman',serif">{v['title']}</p>
          <p style="margin:0 0 12px;font-size:12px;color:#777;
                    font-family:Helvetica,Arial,sans-serif;line-height:1.5">{v['description']}</p>
          {img_tag}
          <p style="margin:6px 0 0;font-size:11px;color:#aaa;
                    font-family:Helvetica,Arial,sans-serif">
            <a href="cid:{v['pdf_filename']}" style="color:#8b6914;font-weight:600;text-decoration:none">↓ Download PDF</a>
          </p>
        </div>"""

    return f"""
    <tr><td style="padding:28px 40px 0">
      <div style="border-top:1px solid #ebebeb;padding-top:22px">
        <p style="margin:0 0 18px;font-size:10px;font-weight:700;letter-spacing:2px;
                  text-transform:uppercase;color:#8b6914;
                  font-family:Helvetica,Arial,sans-serif">Visual Summary</p>
        {cards}
      </div>
    </td></tr>"""


# ── Resend attachments ────────────────────────────────────────────────────────
def visuals_to_attachments(visuals: list[dict]) -> list[dict]:
    """Attach only PDF — no PNG."""
    out = []
    for v in visuals:
        p: Path = v.get("pdf")
        if p and p.exists():
            out.append({"filename": p.name, "content": list(p.read_bytes())})
    return out
