"""Matplotlib/Seaborn chart functions with one consistent visual system.

Conventions
  * Categorical hues in a fixed order (CATEGORICAL), never cycled by rank.
  * Sequential magnitude = one hue light->dark; diverging = blue <-> red with a
    neutral grey midpoint (used for ERR around 1.0 and for correlations).
  * One y-axis per panel. The Pareto chart is two stacked panels sharing x,
    not a dual-axis plot.
  * Text uses ink colours; series colours are only for marks.
Every function returns the Figure and optionally saves a PNG to reports/figures.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as mtick
import numpy as np
import pandas as pd
import seaborn as sns
from matplotlib.colors import LinearSegmentedColormap

FIG_DIR = Path(__file__).resolve().parents[1] / "reports" / "figures"

# Validated reference palette (light mode).
CATEGORICAL = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SEQ_BLUE = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
DIVERGING = LinearSegmentedColormap.from_list("blue_grey_red", ["#184f95", "#6da7ec", "#f0efec", "#ec835a", "#b8302f"])
SEQUENTIAL = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)

SOURCE_NOTE = "Source: CMS HRRP FY 2026 (performance period Jul 2021 - Jun 2024)"


def set_style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"], "font.size": 10,
        "text.color": INK, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "semibold", "axes.titlelocation": "left",
        "axes.edgecolor": AXIS, "axes.linewidth": 0.8, "axes.grid": True, "grid.color": GRID,
        "grid.linewidth": 0.6, "axes.axisbelow": True, "axes.spines.top": False, "axes.spines.right": False,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.labelcolor": INK_2, "ytick.labelcolor": INK_2,
        "legend.frameon": False, "figure.dpi": 110, "savefig.dpi": 150, "savefig.bbox": "tight",
    })


def _finish(fig, name: str | None, note: str = SOURCE_NOTE):
    if note:
        fig.text(0.01, -0.02, note, fontsize=8, color=MUTED, ha="left", va="top")
    if name:
        FIG_DIR.mkdir(parents=True, exist_ok=True)
        fig.savefig(FIG_DIR / f"{name}.png")
    return fig


def err_distributions(df: pd.DataFrame, labels: dict, title: str, name: str | None = "01_err_distribution_by_measure"):
    """Small multiples: ERR histogram per measure with the 1.0 reference line."""
    codes = sorted(df["MEASURE_CODE"].unique())
    fig, axes = plt.subplots(2, 3, figsize=(12, 6.2), sharex=True)
    bins = np.arange(0.45, 1.66, 0.02)
    for ax, code in zip(axes.flat, codes):
        s = df.loc[df["MEASURE_CODE"] == code, "EXCESS_READMISSION_RATIO"]
        ax.hist(s, bins=bins, color=CATEGORICAL[0], edgecolor=SURFACE, linewidth=0.6)
        ax.axvline(1.0, color=INK_2, lw=1, ls="--")
        ax.set_title(f"{labels.get(code, code)}", fontsize=10)
        ax.text(0.98, 0.95, f"n = {len(s):,}\nmedian = {s.median():.3f}", transform=ax.transAxes,
                ha="right", va="top", fontsize=8.5, color=INK_2)
        ax.grid(axis="x", visible=False)
    for ax in axes[-1]:
        ax.set_xlabel("Excess readmission ratio (ERR)")
    for ax in axes[:, 0]:
        ax.set_ylabel("Hospitals")
    fig.suptitle(title,
                 x=0.01, ha="left", fontsize=13, fontweight="semibold")
    fig.tight_layout()
    return _finish(fig, name, SOURCE_NOTE + ". Dashed line: ERR = 1 (predicted = expected).")


def penalty_rate_by_measure(tbl: pd.DataFrame, name: str | None = "02_penalty_rate_by_measure"):
    """Horizontal bars of the share of hospitals with penalty indicator Y, with Wilson CIs."""
    t = tbl.sort_values("rate")
    fig, ax = plt.subplots(figsize=(8, 3.8))
    y = np.arange(len(t))
    ax.barh(y, t["rate"] * 100, color=CATEGORICAL[0], height=0.62)
    ax.errorbar(t["rate"] * 100, y, xerr=[(t["rate"] - t["ci_low"]) * 100, (t["ci_high"] - t["rate"]) * 100],
                fmt="none", ecolor=INK_2, elinewidth=1, capsize=3)
    for yi, (r, n) in enumerate(zip(t["rate"], t["n"])):
        ax.text(r * 100 + 2.2, yi, f"{r:.0%}  (n={n:,})", va="center", fontsize=9, color=INK_2)
    ax.set_yticks(y, t["label"])
    ax.xaxis.set_major_formatter(mtick.PercentFormatter())
    ax.set_xlim(0, max(t["ci_high"]) * 100 + 18)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("Hospitals flagged (penalty indicator = Y) among hospitals with an ERR")
    ax.set_title("Share of hospitals whose ERR exceeds their peer-group median")
    fig.tight_layout()
    return _finish(fig, name, SOURCE_NOTE + ". Bars: 95% Wilson CI.")


def group_mean_dotplot(res: pd.DataFrame, labels: dict, a: str, b: str, title: str,
                       name: str | None = None):
    """Mean difference (a - b) per measure with 95% CI; zero line = no difference."""
    t = res.sort_values("mean_diff")
    fig, ax = plt.subplots(figsize=(8, 3.8))
    y = np.arange(len(t))
    ax.axvline(0, color=AXIS, lw=1)
    ax.hlines(y, t["ci95_low"], t["ci95_high"], color=CATEGORICAL[0], lw=2)
    ax.plot(t["mean_diff"], y, "o", ms=8, color=CATEGORICAL[0], mec=SURFACE, mew=2)
    ax.set_yticks(y, [labels.get(c, c) for c in t["MEASURE_CODE"]])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(f"Mean ERR difference ({a} minus {b}), 95% CI")
    ax.set_title(title)
    fig.tight_layout()
    return _finish(fig, name)


def histogram(series: pd.Series, xlabel: str, title: str, bins, name: str | None = None, pct_axis=False):
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.hist(series.dropna(), bins=bins, color=CATEGORICAL[0], edgecolor=SURFACE, linewidth=0.6)
    if pct_axis:
        ax.xaxis.set_major_formatter(mtick.PercentFormatter(decimals=1))
    ax.set_xlabel(xlabel); ax.set_ylabel("Hospitals"); ax.set_title(title)
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    return _finish(fig, name)


def state_bars(t: pd.DataFrame, value: str, xlabel: str, title: str, name: str | None = None,
               highlight_national: float | None = None):
    """All states sorted, horizontal bars (no map dependency)."""
    t = t.sort_values(value)
    fig, ax = plt.subplots(figsize=(8, 11))
    ax.barh(t["STATE"], t[value], color=CATEGORICAL[0], height=0.7)
    if highlight_national is not None:
        ax.axvline(highlight_national, color=INK_2, ls="--", lw=1)
        ax.text(highlight_national, len(t) - 0.2, " national", color=INK_2, fontsize=8.5, va="bottom")
    ax.tick_params(axis="y", labelsize=8)
    ax.margins(y=0.01)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(xlabel); ax.set_title(title)
    fig.tight_layout()
    return _finish(fig, name)


def corr_heatmap(rho: pd.DataFrame, labels: dict, title: str, name: str | None = "06_spearman_correlation"):
    mask = np.triu(np.ones_like(rho, dtype=bool), k=0)  # hide diagonal (always 1) and upper triangle
    fig, ax = plt.subplots(figsize=(9, 7.5))
    sns.heatmap(rho.rename(index=labels, columns=labels), mask=mask, cmap=DIVERGING, vmin=-1, vmax=1,
                center=0, annot=True, fmt=".2f", annot_kws={"fontsize": 8, "color": INK},
                linewidths=2, linecolor=SURFACE, cbar_kws={"label": "Spearman rho", "shrink": 0.7}, ax=ax)
    ax.grid(False)
    ax.set_title(title)
    fig.tight_layout()
    return _finish(fig, name, SOURCE_NOTE + ". Pairwise-complete observations; see n matrix in reports.")


def hexbin_relationship(df: pd.DataFrame, x: str, y: str, xlabel: str, ylabel: str, title: str,
                        name: str | None = None, rho: float | None = None):
    d = df[[x, y]].dropna()
    fig, ax = plt.subplots(figsize=(7.5, 5))
    hb = ax.hexbin(d[x], d[y], gridsize=35, cmap=SEQUENTIAL, mincnt=1, linewidths=0.2, edgecolors=SURFACE)
    # Binned medians make the trend readable without implying a model.
    bins = pd.qcut(d[x], 10, duplicates="drop")
    med = d.groupby(bins, observed=True).agg(xm=(x, "median"), ym=(y, "median"))
    ax.plot(med["xm"], med["ym"], "-o", color=CATEGORICAL[1], lw=2, ms=6, mec=SURFACE, mew=1.5,
            label="Median by decile of x")
    fig.colorbar(hb, ax=ax, label="Hospitals per cell", shrink=0.8)
    ax.legend(loc="upper left")
    if rho is not None:
        ax.text(0.98, 0.97, f"Spearman rho = {rho:.2f}\nn = {len(d):,}", transform=ax.transAxes,
                ha="right", va="top", fontsize=9, color=INK_2)
    ax.set_xlabel(xlabel); ax.set_ylabel(ylabel); ax.set_title(title)
    fig.tight_layout()
    return _finish(fig, name)


def peer_group_panels(t: pd.DataFrame, left_title: str, right_title: str,
                      name: str | None = "08_peer_group_err_vs_penalty"):
    """Two panels, same x (peer group): mean ERR (unadjusted) vs. penalty share (peer-adjusted)."""
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8))
    x = t["PEER_GROUP"].astype(int)
    # Dots on stems from the ERR = 1 reference (bars would need a zero baseline).
    a1.axhline(1.0, color=INK_2, ls="--", lw=1)
    a1.vlines(x, 1.0, t["mean_err"], color=CATEGORICAL[0], lw=2)
    a1.plot(x, t["mean_err"], "o", ms=9, color=CATEGORICAL[0], mec=SURFACE, mew=2)
    for xi, v in zip(x, t["mean_err"]):
        a1.text(xi + 0.12, v, f"{v:.3f}", va="center", fontsize=8.5, color=INK_2)
    pad = max(abs(t["mean_err"] - 1)) * 1.4
    a1.set_ylim(1 - pad, 1 + pad)
    a1.set_title(left_title); a1.set_ylabel("Mean ERR (all measures)")
    a2.bar(x, t["penalized_share"] * 100, color=CATEGORICAL[0], width=0.6)
    a2.yaxis.set_major_formatter(mtick.PercentFormatter())
    a2.set_title(right_title); a2.set_ylabel("Hospitals with any payment reduction")
    for ax in (a1, a2):
        ax.set_xlabel("Peer group (1 = lowest dual-eligible proportion, 5 = highest)")
        ax.set_xticks(x); ax.grid(axis="x", visible=False)
    fig.tight_layout()
    return _finish(fig, name, SOURCE_NOTE + ". Dashed line: ERR = 1.")


def heatmap_region_measure(pivot: pd.DataFrame, labels: dict, name: str | None = "09_region_measure_err"):
    fig, ax = plt.subplots(figsize=(9, 5))
    lim = np.nanmax(np.abs(pivot.to_numpy() - 1))
    sns.heatmap(pivot.rename(columns=labels), cmap=DIVERGING, center=1.0, vmin=1 - lim, vmax=1 + lim,
                annot=True, fmt=".3f", annot_kws={"fontsize": 8, "color": INK}, linewidths=2,
                linecolor=SURFACE, cbar_kws={"label": "Mean ERR"}, ax=ax)
    ax.grid(False); ax.set_xlabel(""); ax.set_ylabel("")
    ax.set_title("Mean ERR by Census division and measure (blue < 1 < red)")
    plt.setp(ax.get_xticklabels(), rotation=25, ha="right")
    fig.tight_layout()
    return _finish(fig, name)


def pareto_chart(t: pd.DataFrame, category_label: str, value_label: str, title: str,
                 name: str | None = None, note: str = SOURCE_NOTE):
    """Pareto as two aligned panels (contribution bars over cumulative %), no dual axis."""
    fig, (top, bot) = plt.subplots(2, 1, figsize=(9, 6), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    x = np.arange(len(t))
    colors = [CATEGORICAL[0] if v else "#9ec5f4" for v in t["VITAL_FEW"]]
    top.bar(x, t["VALUE"], color=colors, width=0.65)
    for xi, (v, s) in enumerate(zip(t["VALUE"], t["SHARE_PCT"])):
        top.text(xi, v, f"{s:.0f}%", ha="center", va="bottom", fontsize=9, color=INK_2)
    top.set_ylabel(value_label); top.grid(axis="x", visible=False); top.set_title(title)
    bot.plot(x, t["CUMULATIVE_PCT"], "-o", color=CATEGORICAL[0], lw=2, ms=8, mec=SURFACE, mew=2)
    bot.axhline(80, color=INK_2, ls="--", lw=1)
    bot.text(len(t) - 0.6, 81.5, "80%", color=INK_2, fontsize=8.5, ha="right")
    bot.set_ylim(0, 105); bot.yaxis.set_major_formatter(mtick.PercentFormatter())
    bot.set_ylabel("Cumulative share"); bot.grid(axis="x", visible=False)
    bot.set_xticks(x, t.iloc[:, 0], rotation=20, ha="right"); bot.set_xlabel(category_label)
    fig.tight_layout()
    return _finish(fig, name, note + ". Dark bars: categories reaching 80% cumulative.")
