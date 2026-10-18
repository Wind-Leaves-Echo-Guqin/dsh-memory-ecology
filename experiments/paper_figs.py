# -*- coding: utf-8 -*-
"""论文图表生成（RQ1 消融）——读 results/summary.json，输出 paper/figs/*.png"""
import json
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).parent
OUT = HERE.parent / "figs"
OUT.mkdir(exist_ok=True)

plt.rcParams.update({
    "font.family": ["DejaVu Sans", "Microsoft YaHei"],
    "axes.unicode_minus": False,
    "axes.edgecolor": "#d0d5dd", "axes.linewidth": 0.8,
    "axes.grid": True, "grid.color": "#eef1f5", "grid.linewidth": 0.7,
    "font.size": 10.5,
})
INK = "#16213e"
COLORS = {"raw": "#dc2626", "g1": "#0e9f8a", "full": "#2f5fd0"}
LABELS = {"raw": "No governance (A0)", "g1": "Write gate only (A1)", "full": "Full lifecycle (A2)"}


def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)


def mean_curve(rs, key):
    curves = [r["curve"] for r in rs]
    n = len(curves[0])
    mean = [statistics.mean(c[i][key] for c in curves) for i in range(n)]
    std = [statistics.pstdev(c[i][key] for c in curves) for i in range(n)]
    return range(1, n + 1), mean, std


def main():
    data = json.loads((HERE / "results" / "summary.json").read_text(encoding="utf-8"))
    arms = ["raw", "g1", "full"]

    # --- 图A：库增长 + L1 成本 ---
    fig, axes = plt.subplots(1, 2, figsize=(9.6, 3.8))
    for arm in arms:
        rs = [r for r in data if r["arm"] == arm]
        x, m, s = mean_curve(rs, "size")
        axes[0].plot(x, m, color=COLORS[arm], lw=2, label=LABELS[arm])
        axes[0].fill_between(x, [a - b for a, b in zip(m, s)], [a + b for a, b in zip(m, s)],
                             color=COLORS[arm], alpha=0.12)
        x, m, s = mean_curve(rs, "l1_chars")
        axes[1].plot(x, m, color=COLORS[arm], lw=2)
        axes[1].fill_between(x, [a - b for a, b in zip(m, s)], [a + b for a, b in zip(m, s)],
                             color=COLORS[arm], alpha=0.12)
    axes[0].axhline(60, color="#94a3b8", lw=1, ls=":", label="fact universe (60)")
    axes[1].axhline(2550, color="#94a3b8", lw=1, ls=":", label="L1 trigger (2,550 chars)")
    axes[0].set_xlabel("Session"); axes[0].set_ylabel("L2 detail entries (active)")
    axes[1].set_xlabel("Session"); axes[1].set_ylabel("L1 resident memory (chars)")
    axes[0].set_title("(a) Library growth", fontsize=11, loc="left")
    axes[1].set_title("(b) L1 injection cost", fontsize=11, loc="left")
    for ax in axes:
        style(ax)
    axes[0].legend(frameon=False, fontsize=8.5, loc="upper left")
    axes[1].legend(frameon=False, fontsize=8.5, loc="upper left")
    fig.tight_layout()
    fig.savefig(OUT / "fig-ablation-growth.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # --- 图B：终态指标汇总 ---
    metrics = [
        ("final_size", "Active L2 entries (final)", 0),
        ("dup_ratio", "Near-duplicate pair ratio", 0),
        ("contradictions_unresolved", "Unresolved contradictions", 0),
        ("precision_at_3", "Retrieval precision@3", 0.9),
    ]
    fig, axes = plt.subplots(1, 4, figsize=(11.5, 3.2))
    for ax, (key, label, ymin) in zip(axes, metrics):
        means, stds = [], []
        for arm in arms:
            rs = [r for r in data if r["arm"] == arm]
            means.append(statistics.mean(r[key] for r in rs))
            stds.append(statistics.pstdev(r[key] for r in rs))
        bars = ax.bar([LABELS[a].split(" (")[0] for a in arms], means,
                      0.6, color=[COLORS[a] for a in arms], alpha=0.92,
                      yerr=stds, capsize=4, error_kw=dict(lw=1, ecolor="#667085"))
        for b, m in zip(bars, means):
            ax.text(b.get_x() + b.get_width() / 2, m, f"{m:.3f}" if m < 1 else f"{m:.0f}",
                    ha="center", va="bottom", fontsize=8.5, color=INK)
        ax.set_title(label, fontsize=10, loc="left")
        ax.set_ylim(ymin=min(ymin, 0), ymax=max(means) * 1.28 + (0.02 if max(means) < 2 else 20))
        ax.tick_params(axis="x", labelsize=8, rotation=12)
        style(ax)
        ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(OUT / "fig-ablation-summary.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    # --- 图C：门④生命周期（A2 独有）---
    fig, ax = plt.subplots(figsize=(5.2, 3.2))
    rs = [r for r in data if r["arm"] == "full"]
    cats = ["Dormant (L2)", "Archived (reversible)"]
    means = [statistics.mean(r["dormant"] for r in rs), statistics.mean(r["archived"] for r in rs)]
    stds = [statistics.pstdev(r["dormant"] for r in rs), statistics.pstdev(r["archived"] for r in rs)]
    bars = ax.bar(cats, means, 0.5, color=["#7c3aed", "#d97706"], alpha=0.9,
                  yerr=stds, capsize=4, error_kw=dict(lw=1, ecolor="#667085"))
    for b, m in zip(bars, means):
        ax.text(b.get_x() + b.get_width() / 2, m + 0.5, f"{m:.1f}", ha="center",
                fontsize=9.5, color=INK, fontweight="bold")
    ax.set_title("Lifecycle actions by gate 4 (A2, final state)", fontsize=10, loc="left")
    ax.set_ylabel("Entries")
    style(ax)
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(OUT / "fig-ablation-lifecycle.png", dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)

    print("saved:", [p.name for p in OUT.glob("fig-ablation-*.png")])


if __name__ == "__main__":
    main()
