"""
Build the Lactalis B2B Reco Engine architecture diagram as a PNG.
Pure matplotlib. v3 — fixed title clipping + elbow routing for cross-stage arrows.
"""

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import matplotlib.lines as mlines

# ── Brand palette ──────────────────────────────────────────────────────────────
DARK_BLUE   = "#004B85"
MID_BLUE    = "#3F4097"
LIGHT_BLUE  = "#5BC5F2"
GOLD_CLR    = "#E8C759"
BRONZE_CLR  = "#9FAAB5"
TEAL        = "#1A5276"
MEDIUM_BLU  = "#2471A3"
BG          = "#FFFFFF"
BAND_BG     = "#EBF4FC"
TEXT_WHITE  = "#FFFFFF"
TEXT_DARK   = "#1A2A3A"
TEXT_NAVY   = "#3F4097"
ARROW_CLR   = "#1A3C5E"

# ── Canvas ─────────────────────────────────────────────────────────────────────
FIG_W, FIG_H = 22.0, 11.5

fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
ax.set_xlim(0, FIG_W)
ax.set_ylim(0, FIG_H)
ax.axis("off")
fig.patch.set_facecolor(BG)
ax.set_facecolor(BG)

# ── Title ──────────────────────────────────────────────────────────────────────
ax.text(
    FIG_W / 2, FIG_H - 0.42,
    "Lactalis B2B Personalized Recommendation Engine — Architecture",
    ha="center", va="center",
    fontsize=17, fontweight="bold", color=DARK_BLUE,
    fontfamily="DejaVu Sans"
)

# ── Layout constants ────────────────────────────────────────────────────────────
ML, MR, MB, MT = 0.35, 0.35, 0.55, 0.85
BAND_H = 0.54
GAP    = 0.28

USABLE_W = FIG_W - ML - MR
USABLE_H = FIG_H - MB - MT
SW = (USABLE_W - 3 * GAP) / 4
SH = USABLE_H

sx = [ML + i * (SW + GAP) for i in range(4)]
sy = MB

NW = SW - 0.28
NX_OFF = (SW - NW) / 2


# ── Helpers ────────────────────────────────────────────────────────────────────
def stage_box(ax, x, y, w, h, title, band_color, band_h=BAND_H):
    outer = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.05",
        linewidth=2, edgecolor=band_color,
        facecolor=BAND_BG, zorder=1
    )
    ax.add_patch(outer)
    band = plt.Rectangle(
        (x + 0.02, y + h - band_h), w - 0.04, band_h - 0.02,
        linewidth=0, facecolor=band_color, zorder=2
    )
    ax.add_patch(band)
    ax.text(
        x + w / 2, y + h - band_h / 2,
        title,
        ha="center", va="center",
        fontsize=9, fontweight="bold", color=TEXT_WHITE,
        zorder=3, fontfamily="DejaVu Sans",
        clip_on=False       # allow text to render fully
    )


def node_box(ax, x, y, w, h, lines, fill, text_color=TEXT_WHITE, fs=8.5):
    rect = FancyBboxPatch(
        (x, y), w, h,
        boxstyle="round,pad=0.06",
        linewidth=1.0, edgecolor="#FFFFFF55",
        facecolor=fill, zorder=5
    )
    ax.add_patch(rect)
    ax.text(
        x + w / 2, y + h / 2,
        "\n".join(lines),
        ha="center", va="center",
        fontsize=fs, color=text_color,
        zorder=6, linespacing=1.45,
        fontfamily="DejaVu Sans"
    )
    return dict(
        cr=(x + w, y + h / 2),
        cl=(x,     y + h / 2),
        ct=(x + w / 2, y + h),
        cb=(x + w / 2, y),
        xc=x + w / 2,
        yc=y + h / 2,
        bbox=(x, y, w, h)
    )


def draw_elbow_arrow(ax, x0, y0, x1, y1, mid_x, color=ARROW_CLR, lw=1.6):
    """Draw right-angle arrow: (x0,y0) → (mid_x,y0) → (mid_x,y1) → (x1,y1)."""
    ax.annotate(
        "",
        xy=(x1, y1), xytext=(x0, y0),
        arrowprops=dict(
            arrowstyle="-|>", color=color, lw=lw,
            mutation_scale=11,
            connectionstyle=f"arc,angleA=0,angleB=180,armA={abs(mid_x-x0)*72:.0f},armB={abs(x1-mid_x)*72:.0f},rad=4"
        ),
        zorder=9
    )


def draw_straight_arrow(ax, x0, y0, x1, y1, color=ARROW_CLR, lw=1.6):
    ax.annotate(
        "",
        xy=(x1, y1), xytext=(x0, y0),
        arrowprops=dict(
            arrowstyle="-|>", color=color, lw=lw,
            mutation_scale=12,
            connectionstyle="arc3,rad=0.0"
        ),
        zorder=9
    )


def draw_path_arrow(ax, points, color=ARROW_CLR, lw=1.5):
    """Draw multi-segment line with arrowhead at the last segment."""
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    # draw path segments
    for i in range(len(points) - 2):
        ax.plot([xs[i], xs[i+1]], [ys[i], ys[i+1]], color=color, lw=lw, zorder=9)
    # last segment with arrowhead
    ax.annotate(
        "",
        xy=points[-1], xytext=points[-2],
        arrowprops=dict(
            arrowstyle="-|>", color=color, lw=lw,
            mutation_scale=11,
        ),
        zorder=9
    )


def nodes_layout(n, stage_h, top_pad=0.26, bot_pad=0.26, node_gap=0.22):
    content_h = stage_h - BAND_H - top_pad - bot_pad
    node_h = (content_h - (n - 1) * node_gap) / n
    ys = []
    y_cursor = sy + stage_h - BAND_H - top_pad - node_h
    for _ in range(n):
        ys.append(y_cursor)
        y_cursor -= node_h + node_gap
    return ys, node_h


# ── STAGE 1 — Data Sources ─────────────────────────────────────────────────────
stage_box(ax, sx[0], sy, SW, SH, "STAGE 1  ·  Data Sources", DARK_BLUE)
ys1, nh1 = nodes_layout(3, SH)
nx1 = sx[0] + NX_OFF

SAP = node_box(ax, nx1, ys1[0], NW, nh1,
               ["SAP ERP", "Rep orders · Product master", "Stock by DC"],
               DARK_BLUE)
SF  = node_box(ax, nx1, ys1[1], NW, nh1,
               ["Salesforce CRM", "Accounts · Segmentation",
                "E-commerce orders · Favourites"],
               DARK_BLUE)
EXT = node_box(ax, nx1, ys1[2], NW, nh1,
               ["External Signals", "Weather · Fuel price index",
                "School / holiday calendar"],
               DARK_BLUE)

# ── STAGE 2 — Unity Catalog ───────────────────────────────────────────────────
stage_box(ax, sx[1], sy, SW, SH, "STAGE 2  ·  Unity Catalog  (governed)", DARK_BLUE)
ys2, nh2 = nodes_layout(3, SH)
nx2 = sx[1] + NX_OFF

GLD = node_box(ax, nx2, ys2[0], NW, nh2,
               ["Gold", "dim_customer · dim_product · dim_dc",
                "fact_orders · customer_favorites",
                "stock_by_dc · signal_*"],
               GOLD_CLR, TEXT_NAVY, fs=8.0)
SLV = node_box(ax, nx2, ys2[1], NW, nh2,
               ["Silver", "Cleaned · conformed"],
               LIGHT_BLUE, TEXT_DARK)
BRZ = node_box(ax, nx2, ys2[2], NW, nh2,
               ["Bronze", "Raw · source-tagged"],
               BRONZE_CLR, TEXT_WHITE)

# Bronze → Silver → Gold (upward inside Stage 2)
for top_n, bot_n in [(SLV, BRZ), (GLD, SLV)]:
    draw_straight_arrow(ax, top_n["cb"][0], top_n["cb"][1],
                        bot_n["ct"][0], bot_n["ct"][1],
                        color=DARK_BLUE, lw=1.3)

# ── STAGE 3 — Recommendation Engine ──────────────────────────────────────────
stage_box(ax, sx[2], sy, SW, SH,
          "STAGE 3  ·  Recommendation Engine\nDatabricks SQL + Delta",
          MID_BLUE)
ys3, nh3 = nodes_layout(4, SH, top_pad=0.22, bot_pad=0.22, node_gap=0.18)
nx3 = sx[2] + NX_OFF

SQL = node_box(ax, nx3, ys3[0], NW, nh3,
               ["SQL Scoring",
                "Segment affinity + contextual signal boosts"],
               MID_BLUE)
OOS = node_box(ax, nx3, ys3[1], NW, nh3,
               ["Out-of-Stock Guardrail",
                "Hard filter — removes unfulfillable SKUs"],
               MID_BLUE)
FMA = node_box(ax, nx3, ys3[2], NW, nh3,
               ["Foundation Model API  ·  ai_query",
                "Natural-language rationale generation"],
               TEAL)
OUT = node_box(ax, nx3, ys3[3], NW, nh3,
               ["Outputs",
                "reco_candidates · reco_rationale",
                "vw_reco_full"],
               MEDIUM_BLU)

# SQL → OOS → FMA → OUT (downward inside Stage 3)
for top_n, bot_n in [(OOS, SQL), (FMA, OOS), (OUT, FMA)]:
    draw_straight_arrow(ax, top_n["cb"][0], top_n["cb"][1],
                        bot_n["ct"][0], bot_n["ct"][1],
                        color=MID_BLUE, lw=1.3)

# ── STAGE 4 — Serving / Consumption ──────────────────────────────────────────
stage_box(ax, sx[3], sy, SW, SH, "STAGE 4  ·  Serving / Consumption", DARK_BLUE)
ys4, nh4 = nodes_layout(3, SH)
nx4 = sx[3] + NX_OFF

GEN = node_box(ax, nx4, ys4[0], NW, nh4,
               ["Genie", "Natural-language analytics",
                "for sales and marketing"],
               DARK_BLUE)
DAS = node_box(ax, nx4, ys4[1], NW, nh4,
               ["AI/BI Dashboards  ·  Lakeview",
                "Operational KPIs and insights"],
               DARK_BLUE)
APP = node_box(ax, nx4, ys4[2], NW, nh4,
               ["Databricks Apps",
                "MyLactalis storefront + Engine Console"],
               DARK_BLUE)

# ── INTER-STAGE ARROWS with elbow routing ─────────────────────────────────────

# Gap midpoints (routing waypoints between stages)
mid12 = sx[0] + SW + GAP / 2   # between stage 1 and stage 2
mid23 = sx[1] + SW + GAP / 2   # between stage 2 and stage 3
mid34 = sx[2] + SW + GAP / 2   # between stage 3 and stage 4

brz_cl_x, brz_cl_y = BRZ["cl"]
brz_left_x = brz_cl_x

# SAP / SF / EXT → BRZ  (fan into Bronze at the same point, then single entry)
# Route: source_right → mid12 (at source_y) → mid12 (at brz_y) → brz_left
for src in [SAP, SF, EXT]:
    sx0, sy0 = src["cr"]
    draw_path_arrow(ax, [
        (sx0,       sy0),
        (mid12,     sy0),
        (mid12,     brz_cl_y),
        (brz_left_x, brz_cl_y)
    ], color=ARROW_CLR, lw=1.5)

# GLD → SQL  (Gold right → mid23 at GLD level → mid23 at SQL level → SQL left)
gx0, gy0 = GLD["cr"]
sx1, sy1 = SQL["cl"]
draw_path_arrow(ax, [
    (gx0,  gy0),
    (mid23, gy0),
    (mid23, sy1),
    (sx1,  sy1)
], color=ARROW_CLR, lw=1.8)

# OUT → GEN / DAS / APP  (OUT right → mid34 at OUT level → mid34 at dst level → dst left)
ox0, oy0 = OUT["cr"]
for dst in [GEN, DAS, APP]:
    dx1, dy1 = dst["cl"]
    draw_path_arrow(ax, [
        (ox0,   oy0),
        (mid34, oy0),
        (mid34, dy1),
        (dx1,   dy1)
    ], color=ARROW_CLR, lw=1.5)

# ── LEGEND STRIP ──────────────────────────────────────────────────────────────
legend_items = [
    (DARK_BLUE,   "Unity Catalog"),
    (LIGHT_BLUE,  "Delta Lake"),
    (MID_BLUE,    "Databricks SQL"),
    (TEAL,        "Foundation Model API"),
    (DARK_BLUE,   "Genie"),
    (DARK_BLUE,   "AI/BI Dashboards"),
    (DARK_BLUE,   "Databricks Apps"),
]

leg_y  = 0.14
chip_w = 0.18
chip_h = 0.13
gap_x  = 2.0
total_w = len(legend_items) * (chip_w + gap_x) - gap_x
lx = (FIG_W - total_w) / 2

for col, lbl in legend_items:
    r = FancyBboxPatch(
        (lx, leg_y), chip_w, chip_h,
        boxstyle="round,pad=0.02",
        linewidth=0, facecolor=col, zorder=5
    )
    ax.add_patch(r)
    ax.text(
        lx + chip_w + 0.10, leg_y + chip_h / 2,
        lbl, va="center", ha="left",
        fontsize=7.8, color=TEXT_DARK,
        fontfamily="DejaVu Sans"
    )
    lx += chip_w + gap_x

# ── Save ───────────────────────────────────────────────────────────────────────
out_path = "/Users/deep.basu/lactalis-reco-engine/docs/architecture.png"
fig.savefig(
    out_path,
    dpi=100,
    bbox_inches="tight",
    facecolor=BG,
    format="png"
)
plt.close(fig)
print(f"Saved: {out_path}")
