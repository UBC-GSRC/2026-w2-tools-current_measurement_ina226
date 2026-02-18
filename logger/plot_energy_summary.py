#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Reads CSV recordings from the 'recordings/' folder and produces a bar chart
comparing total energy (Wh) per file, with error bars showing the standard
deviation of interval energy contributions (Wh).

- X axis: file names (rename files to the component under test)
- Y axis: Watt-hours (Wh)
- Error bars: ± std deviation of per-interval Wh inside each recording
- Output: displays the chart and also saves a PNG in 'figures/'.

Usage:
    python plot_energy_summary.py
Options:
    python plot_energy_summary.py --folder recordings --savefig figures\\energy_summary.png --show
    python plot_energy_summary.py --sort desc --show
"""
import os
import glob
import argparse
from datetime import datetime

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

def load_and_compute_energy(csv_path):
    """
    Returns:
      dict with:
        - label: derived from file name (no extension)
        - total_wh: total energy in Wh
        - std_interval_wh: std dev of interval energy (Wh)
        - mean_power_w: average power in W
        - std_power_w: std dev of instantaneous power (W)
        - duration_h: total duration in hours
        - n_rows: number of data rows used
        - n_intervals: number of time intervals used
    """
    df = pd.read_csv(csv_path)

    if "timestamp_local" not in df.columns:
        raise ValueError(f"{os.path.basename(csv_path)} missing 'timestamp_local' column")

    # Use measured power if available, else compute V * mA
    if "bus_power_mW" in df.columns:
        power_mw = pd.to_numeric(df["bus_power_mW"], errors="coerce")
    elif {"bus_V", "current_mA"}.issubset(df.columns):
        bus_v = pd.to_numeric(df["bus_V"], errors="coerce")
        current_ma = pd.to_numeric(df["current_mA"], errors="coerce")
        power_mw = bus_v * current_ma  # (V * mA) = mW
    else:
        raise ValueError(
            f"{os.path.basename(csv_path)} has neither 'bus_power_mW' nor both 'bus_V' and 'current_mA'."
        )

    # Parse timestamps & sort
    t = pd.to_datetime(df["timestamp_local"], errors="coerce")
    valid = t.notna() & power_mw.notna()
    df = pd.DataFrame({"t": t[valid], "power_mw": power_mw[valid]}).sort_values("t")

    # Drop duplicates and non-monotonic times
    df = df[~df["t"].duplicated(keep="first")]
    df = df[df["t"].diff().dt.total_seconds().fillna(0) >= 0]

    if len(df) < 2:
        # Not enough samples to integrate
        return None

    # Time deltas and trapezoidal integration
    dt_s = df["t"].diff().dt.total_seconds().to_numpy()
    P_mW = df["power_mw"].to_numpy()

    P_avg_mW = (P_mW[1:] + P_mW[:-1]) / 2.0
    dt_s = dt_s[1:]

    # Sanity filter
    good = np.isfinite(P_avg_mW) & np.isfinite(dt_s) & (dt_s > 0)
    P_avg_mW = P_avg_mW[good]
    dt_s = dt_s[good]

    if P_avg_mW.size == 0:
        return None

    # Energy per interval (Wh) = (mW * s) / (1000 * 3600)
    interval_wh = (P_avg_mW * dt_s) / (1000.0 * 3600.0)
    total_wh = float(interval_wh.sum())
    std_interval_wh = float(interval_wh.std(ddof=1)) if interval_wh.size > 1 else 0.0

    power_w = P_mW / 1000.0
    mean_power_w = float(np.nanmean(power_w))
    std_power_w = float(np.nanstd(power_w, ddof=1)) if power_w.size > 1 else 0.0

    duration_h = float((df["t"].iloc[-1] - df["t"].iloc[0]).total_seconds() / 3600.0)

    return {
        "label": os.path.splitext(os.path.basename(csv_path))[0],
        "total_wh": total_wh,
        "std_interval_wh": std_interval_wh,
        "mean_power_w": mean_power_w,
        "std_power_w": std_power_w,
        "duration_h": duration_h,
        "n_rows": int(len(df)),
        "n_intervals": int(len(interval_wh)),
    }

def make_bar_plot(results, title="Energy by Recording (total Wh, ± std of interval Wh)"):
    labels = [r["label"] for r in results]
    totals = np.array([r["total_wh"] for r in results], dtype=float)
    errs = np.array([r["std_interval_wh"] for r in results], dtype=float)

    fig, ax = plt.subplots(figsize=(max(8, 1.2 * len(labels)), 6))
    bars = ax.bar(
        labels, totals, yerr=errs, capsize=6, color="#4C78A8",
        alpha=0.95, edgecolor="white", linewidth=0.8
    )

    ax.set_ylabel("Energy (Wh)")
    ax.set_xlabel("Recording (file name)")
    ax.set_title(title)
    ax.grid(axis="y", linestyle="--", alpha=0.4)

    # 🔢 Dynamic Y-axis: 0 → (max bar height incl. error + 0.5)
    if len(totals):
        # Include error bars in the max so they aren't clipped
        y_max = float(np.nanmax(totals + errs))
        # Guard against degenerate values
        if not np.isfinite(y_max):
            y_max = 0.0
        ax.set_ylim(0, y_max + 0.5)
    else:
        ax.set_ylim(0, 0.5)

    # X labels
    plt.setp(ax.get_xticklabels(), rotation=25, ha="right")

    # Annotations above bars
    for bar, val, r in zip(bars, totals, results):
        ax.annotate(
            f"{val:.3f} Wh\n({r['duration_h']:.2f} h)",
            xy=(bar.get_x() + bar.get_width() / 2, bar.get_height()),
            xytext=(0, 6),
            textcoords="offset points",
            ha="center", va="bottom",
            fontsize=9, color="#222"
        )

    # Leave some headroom for text/title
    plt.tight_layout(rect=[0, 0, 1, 0.92])
    return fig, ax

def main():
    parser = argparse.ArgumentParser(description="Summarize and plot Wh per recording file (bar chart with std).")
    parser.add_argument("--folder", default="recordings", help="Folder containing CSV files.")
    parser.add_argument("--pattern", default="*.csv", help="Glob pattern for files.")
    parser.add_argument("--savefig", default=None, help="Path to save the PNG (optional).")
    parser.add_argument("--show", action="store_true", help="Show the chart window.")
    parser.add_argument("--sort", choices=["none", "asc", "desc"], default="none",
                        help="Sort bars by total Wh.")
    args = parser.parse_args()

    csv_paths = sorted(glob.glob(os.path.join(args.folder, args.pattern)))
    if not csv_paths:
        print(f"No CSV files found in: {os.path.join(args.folder, args.pattern)}")
        return

    results = []
    for p in csv_paths:
        try:
            r = load_and_compute_energy(p)
            if r is None:
                print(f"[WARN] Skipped {os.path.basename(p)} (insufficient usable rows).")
                continue
            results.append(r)
            print(f"{r['label']}: total={r['total_wh']:.3f} Wh, "
                  f"meanP={r['mean_power_w']:.2f} W, stdP={r['std_power_w']:.2f} W, "
                  f"dur={r['duration_h']:.2f} h, rows={r['n_rows']}, intervals={r['n_intervals']}")
        except Exception as e:
            print(f"[ERROR] {os.path.basename(p)}: {e}")

    if not results:
        print("No valid files to plot.")
        return

    # Optional sorting
    if args.sort != "none":
        reverse = args.sort == "desc"
        results = sorted(results, key=lambda r: r["total_wh"], reverse=reverse)

    fig, ax = make_bar_plot(results)

    # Save figure
    if args.savefig:
        out_path = args.savefig
    else:
        os.makedirs("figures", exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_path = os.path.join("figures", f"energy_summary_{ts}.png")

    fig.savefig(out_path, dpi=150)
    print(f"Saved figure to: {os.path.abspath(out_path)}")

    if args.show:
        plt.show()
    else:
        plt.close(fig)

if __name__ == "__main__":
    main()