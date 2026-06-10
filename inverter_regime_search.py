import argparse
import math
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401  (needed for 3D projection)


@dataclass
class ThresholdSolution:
    Voff: float
    Von: float
    NM_H: float
    NM_L: float
    VOH_min: float
    VOL_max: float
    score: float        # here: NM_min = min(NM_H, NM_L)
    prod_nm: float      # optional: NM_H * NM_L, used as tie-breaker


@dataclass
class CircuitSolution:
    R: float
    Vdd: float
    thresholds: Optional[ThresholdSolution]
    I_on: float
    I_off: float
    regime: str


def load_mosfet_csv(path: str) -> pd.DataFrame:
    """
    Load a MOSFET Id(Vds, Vgs) CSV of the form:
        Vds (V), Vgs (V), Ids (uA)  (names and units may vary)
    Returns a DataFrame with columns: Vds [V], Vgs [V], Id [A].
    """
    df = pd.read_csv(path)
    # Normalize column names
    df.columns = [c.strip() for c in df.columns]

    # Identify columns heuristically
    vds_col = None
    vgs_col = None
    ids_col = None

    for c in df.columns:
        cl = c.lower()
        if vds_col is None and cl.startswith("vds"):
            vds_col = c
        if vgs_col is None and cl.startswith("vgs"):
            vgs_col = c

    # Fallbacks if names are slightly different
    if vds_col is None:
        for c in df.columns:
            if "vds" in c.lower():
                vds_col = c
                break
    if vgs_col is None:
        for c in df.columns:
            if "vgs" in c.lower():
                vgs_col = c
                break
    if vds_col is None or vgs_col is None:
        raise ValueError("Could not infer Vds/Vgs columns from CSV header.")

    # Ids: find first col with 'id'
    for c in df.columns:
        if "id" in c.lower():
            ids_col = c
            break
    if ids_col is None:
        raise ValueError("Could not infer Id column from CSV header.")

    # Unit scaling for Id
    ids_name_lower = ids_col.lower()
    if "ua" in ids_name_lower:
        scale = 1e-6
    elif "ma" in ids_name_lower:
        scale = 1e-3
    else:
        scale = 1.0  # assume amps

    out = pd.DataFrame()
    out["Vds"] = pd.to_numeric(df[vds_col], errors="coerce")
    out["Vgs"] = pd.to_numeric(df[vgs_col], errors="coerce")
    out["Id"] = pd.to_numeric(df[ids_col], errors="coerce") * scale

    out = out.dropna()
    return out


def nearest_vgs_slice(df: pd.DataFrame, Vin: float) -> pd.DataFrame:
    """Return Id vs Vds slice at the measured Vgs closest to Vin."""
    vgs_vals = np.sort(df["Vgs"].unique())
    idx = int(np.argmin(np.abs(vgs_vals - Vin)))
    vgs0 = vgs_vals[idx]
    dv = df[df["Vgs"] == vgs0].copy()
    dv = dv.sort_values("Vds")
    return dv


def solve_vout_for_vin(df: pd.DataFrame, Vin: float, R: float, Vdd: float) -> float:
    """
    For a given Vin, solve load-line:
        (Vdd - Vout)/R = Id(Vds = Vout, Vgs ~ Vin)
    using nearest measured Vgs slice and discrete Vds points.
    Returns Vout (a chosen Vds sample).
    """
    dv = nearest_vgs_slice(df, Vin)
    if dv.empty:
        return float("nan")
    Vds = dv["Vds"].values
    Id = dv["Id"].values
    Ir = (Vdd - Vds) / R
    idx = int(np.argmin(np.abs(Id - Ir)))
    return float(Vds[idx])


def compute_vtc(df: pd.DataFrame, R: float, Vdd: float, n_vin: int = 81) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute quasi-static DC VTC for inverter:
        Vin in [0, Vdd]  ->  Vout = F(Vin)
    """
    Vin = np.linspace(0.0, Vdd, n_vin)
    Vout = np.array([solve_vout_for_vin(df, float(vin), R, Vdd) for vin in Vin])
    return Vin, Vout


def find_best_thresholds(
    Vin: np.ndarray,
    Vout: np.ndarray,
    min_nm: float = 0.3,
) -> Optional[ThresholdSolution]:
    """
    Search over (Voff, Von) pairs on the Vin grid s.t. inverter is cascadable
    and both noise margins >= min_nm.

    Conditions for a pair (Voff, Von), with Voff < Von:
      - For all Vin <= Voff: Vout >= Von   (high output is above high input threshold)
      - For all Vin >= Von: Vout <= Voff   (low output is below low input threshold)

    Objective:
      - maximize NM_min = min(NM_H, NM_L)
      - tie-breaker: maximize NM_H * NM_L
    """
    n = len(Vin)
    if n != len(Vout):
        raise ValueError("Vin and Vout length mismatch")

    # prefix min of Vout: hi_min[i] = min_{k <= i} Vout[k]
    hi_min = np.minimum.accumulate(Vout)
    # suffix max of Vout: lo_max[i] = max_{k >= i} Vout[k]
    lo_max = np.maximum.accumulate(Vout[::-1])[::-1]

    best: Optional[ThresholdSolution] = None

    for i_off in range(0, n - 2):
        Voff = Vin[i_off]
        VOH_min = hi_min[i_off]

        for j_on in range(i_off + 1, n - 1):
            Von = Vin[j_on]
            VOL_max = lo_max[j_on]

            # Cascadability conditions
            if VOH_min < Von:
                continue
            if VOL_max > Voff:
                continue

            NM_H = VOH_min - Von
            NM_L = Voff - VOL_max
            if NM_H < min_nm or NM_L < min_nm:
                continue

            NM_min = min(NM_H, NM_L)
            prod_nm = NM_H * NM_L

            if best is None:
                best = ThresholdSolution(
                    Voff=float(Voff),
                    Von=float(Von),
                    NM_H=float(NM_H),
                    NM_L=float(NM_L),
                    VOH_min=float(VOH_min),
                    VOL_max=float(VOL_max),
                    score=float(NM_min),
                    prod_nm=float(prod_nm),
                )
            else:
                # Primary: maximize NM_min
                if NM_min > best.score + 1e-9:
                    best = ThresholdSolution(
                        Voff=float(Voff),
                        Von=float(Von),
                        NM_H=float(NM_H),
                        NM_L=float(NM_L),
                        VOH_min=float(VOH_min),
                        VOL_max=float(VOL_max),
                        score=float(NM_min),
                        prod_nm=float(prod_nm),
                    )
                # Secondary: if NM_min effectively equal, maximize NM_H * NM_L
                elif abs(NM_min - best.score) <= 1e-9 and prod_nm > best.prod_nm + 1e-12:
                    best = ThresholdSolution(
                        Voff=float(Voff),
                        Von=float(Von),
                        NM_H=float(NM_H),
                        NM_L=float(NM_L),
                        VOH_min=float(VOH_min),
                        VOL_max=float(VOL_max),
                        score=float(NM_min),
                        prod_nm=float(prod_nm),
                    )

    return best


def classify_regime(solution: CircuitSolution, strong_nm: float, strong_I_on: float, weak_I_on: float) -> str:
    """
    Classify a circuit into regimes based on noise margin and ON current.
    """
    if solution.thresholds is None:
        return "no_logic"

    nm_min = min(solution.thresholds.NM_H, solution.thresholds.NM_L)
    I_on = abs(solution.I_on)

    if nm_min >= strong_nm and I_on >= strong_I_on:
        return "strong_digital"
    if nm_min >= strong_nm and I_on >= weak_I_on:
        return "medium_digital"
    if nm_min >= 0.0 and I_on >= weak_I_on:
        return "weak_digital"
    return "marginal"


def evaluate_circuit(
    df: pd.DataFrame,
    R: float,
    Vdd: float,
    min_nm: float,
    strong_nm: float,
    strong_I_on: float,
    weak_I_on: float,
    n_vin: int = 81,
) -> CircuitSolution:
    """
    Evaluate one inverter configuration for cascaded digital operation.
    """
    Vin, Vout = compute_vtc(df, R, Vdd, n_vin=n_vin)
    thresholds = find_best_thresholds(Vin, Vout, min_nm=min_nm)

    # Static currents: approximate using endpoints of VTC
    Vout_low_in = Vout[-1]  # output when Vin ~ Vdd
    Vout_high_in = Vout[0]  # output when Vin ~ 0
    I_on = (Vdd - Vout_low_in) / R
    I_off = (Vdd - Vout_high_in) / R

    circuit = CircuitSolution(
        R=R,
        Vdd=Vdd,
        thresholds=thresholds,
        I_on=float(I_on),
        I_off=float(I_off),
        regime="",
    )
    circuit.regime = classify_regime(circuit, strong_nm=strong_nm, strong_I_on=strong_I_on, weak_I_on=weak_I_on)
    return circuit


def sweep_space(
    df: pd.DataFrame,
    R_values: np.ndarray,
    Vdd_values: np.ndarray,
    min_nm: float,
    strong_nm: float,
    strong_I_on: float,
    weak_I_on: float,
    n_vin: int = 81,
) -> pd.DataFrame:
    """
    Sweep the (R, Vdd) parameter space and evaluate each circuit.
    Returns a DataFrame of results.
    """
    results: List[Dict] = []
    for Vdd in Vdd_values:
        for R in R_values:
            sol = evaluate_circuit(
                df, float(R), float(Vdd),
                min_nm=min_nm,
                strong_nm=strong_nm,
                strong_I_on=strong_I_on,
                weak_I_on=weak_I_on,
                n_vin=n_vin,
            )
            row = {
                "R": sol.R,
                "Vdd": sol.Vdd,
                "regime": sol.regime,
                "I_on": sol.I_on,
                "I_off": sol.I_off,
            }
            if sol.thresholds is not None:
                row.update({
                    "Voff": sol.thresholds.Voff,
                    "Von": sol.thresholds.Von,
                    "NM_H": sol.thresholds.NM_H,
                    "NM_L": sol.thresholds.NM_L,
                    "VOH_min": sol.thresholds.VOH_min,
                    "VOL_max": sol.thresholds.VOL_max,
                    "nm_min": min(sol.thresholds.NM_H, sol.thresholds.NM_L),
                    "score_nm_min": sol.thresholds.score,
                    "score_prod_nm": sol.thresholds.prod_nm,
                })
            else:
                row.update({
                    "Voff": math.nan,
                    "Von": math.nan,
                    "NM_H": math.nan,
                    "NM_L": math.nan,
                    "VOH_min": math.nan,
                    "VOL_max": math.nan,
                    "nm_min": math.nan,
                    "score_nm_min": math.nan,
                    "score_prod_nm": math.nan,
                })
            results.append(row)
    return pd.DataFrame(results)


def plot_phase_diagram(df_res: pd.DataFrame, out_prefix: str) -> None:
    """
    3D plot: Vdd (X) vs R (Y) vs nm_min (Z), colored by regime,
    with labels at each point showing optimal Voff/Von.
    """
    regimes = df_res["regime"].unique()
    colors = {
        "no_logic": "lightgray",
        "marginal": "gold",
        "weak_digital": "tab:blue",
        "medium_digital": "tab:green",
        "strong_digital": "tab:red",
    }
    markers = {
        "no_logic": "x",
        "marginal": "o",
        "weak_digital": "o",
        "medium_digital": "o",
        "strong_digital": "o",
    }

    fig = plt.figure(figsize=(9, 7))
    ax = fig.add_subplot(111, projection="3d")

    # Scatter all regimes
    for reg in regimes:
        sub = df_res[df_res["regime"] == reg]
        if sub.empty:
            continue

        # Use nm_min as Z; for no_logic it will be NaN, so drop those points for plotting
        sub_plot = sub.copy()
        sub_plot = sub_plot[~sub_plot["nm_min"].isna()]
        if sub_plot.empty:
            continue

        ax.scatter(
            sub_plot["Vdd"],
            sub_plot["R"],
            sub_plot["nm_min"],
            c=colors.get(reg, "black"),
            marker=markers.get(reg, "o"),
            label=reg,
            alpha=0.7,
            edgecolors="none",
        )

    # Labels: Voff/Von at each point with valid thresholds
    for _, row in df_res.iterrows():
        Voff = row["Voff"]
        Von = row["Von"]
        nm_min = row["nm_min"]
        if math.isnan(Voff) or math.isnan(Von) or math.isnan(nm_min):
            continue
        label = f"R={row['R']:.0e}\nVdd={row['Vdd']:.1f}\n{Voff:.2g}/{Von:.2g}"
        ax.text(
            row["Vdd"],
            row["R"],
            nm_min,
            label,
            fontsize=5,
            ha="center",
            va="bottom",
        )

    ax.set_xlabel("Vdd [V]")
    ax.set_ylabel("R [Ω]")
    ax.set_zlabel("NM_min [V] (min of high/low noise margins)")

    ax.set_title("Inverter operating regimes: Vdd vs R vs NM_min")
    # A light grid
    ax.xaxis._axinfo["grid"]["linestyle"] = "--"
    ax.yaxis._axinfo["grid"]["linestyle"] = "--"
    ax.zaxis._axinfo["grid"]["linestyle"] = "--"
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{out_prefix}_phase_diagram_3d.png", dpi=200)


def plot_best_per_vdd(df_res: pd.DataFrame, out_prefix: str) -> None:
    """
    For each Vdd, find the strong/medium/weak_digital circuit with
    maximum I_on (within each regime) and plot them.
    """
    regimes_order = ["strong_digital", "medium_digital", "weak_digital"]
    colors = {
        "strong_digital": "tab:red",
        "medium_digital": "tab:green",
        "weak_digital": "tab:blue",
    }
    markers = {
        "strong_digital": "o",
        "medium_digital": "s",
        "weak_digital": "D",
    }

    fig, ax = plt.subplots(figsize=(7, 5))
    for reg in regimes_order:
        sub = df_res[df_res["regime"] == reg]
        if sub.empty:
            continue
        best_rows = []
        for Vdd in sorted(sub["Vdd"].unique()):
            sub_v = sub[sub["Vdd"] == Vdd]
            if sub_v.empty:
                continue
            best = sub_v.sort_values("I_on", ascending=False).iloc[0]
            best_rows.append(best)
        if not best_rows:
            continue
        best_df = pd.DataFrame(best_rows)
        ax.plot(best_df["Vdd"], best_df["R"], label=f"{reg} (max I_on)",
                color=colors[reg], marker=markers[reg])

    ax.set_xlabel("Vdd [V]")
    ax.set_ylabel("R [Ω]")
    ax.set_yscale("log")
    ax.set_title("Best R per Vdd (max I_on) within each regime")
    ax.grid(True, which="both", ls="--", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    fig.savefig(f"{out_prefix}_best_per_vdd.png", dpi=200)


def main():
    parser = argparse.ArgumentParser(description="Search inverter operating regimes from MOSFET CSV data.")
    parser.add_argument("csv", help="Path to MOSFET Id(Vds,Vgs) CSV file.")
    parser.add_argument("--out-prefix", default="mosfet_search", help="Prefix for output plots and CSV.")
    parser.add_argument("--r-min", type=float, default=1e3, help="Minimum pull-up resistor [Ω].")
    parser.add_argument("--r-max", type=float, default=1e6, help="Maximum pull-up resistor [Ω].")
    parser.add_argument("--r-count", type=int, default=25, help="Number of R samples (log-spaced).")
    parser.add_argument("--vdd-min", type=float, default=None, help="Minimum Vdd [V]. Default: 0.5*max(Vds).")
    parser.add_argument("--vdd-max", type=float, default=None, help="Maximum Vdd [V]. Default: max(Vds).")
    parser.add_argument("--vdd-count", type=int, default=15, help="Number of Vdd samples (linear).")
    parser.add_argument("--min-nm", type=float, default=0.3, help="Minimum noise margin [V] to count as digital.")
    parser.add_argument("--strong-nm", type=float, default=0.8, help="Noise margin [V] for strong/medium regimes.")
    parser.add_argument("--weak-I-on", type=float, default=10e-6, help="Minimum I_on [A] for weak_digital.")
    parser.add_argument("--strong-I-on", type=float, default=100e-6, help="Minimum I_on [A] for strong_digital.")
    parser.add_argument("--n-vin", type=int, default=81, help="Number of Vin samples for VTC computation.")

    args = parser.parse_args()

    df = load_mosfet_csv(args.csv)
    vds_max = float(df["Vds"].max())

    if args.vdd_min is None:
        vdd_min = 0.5 * vds_max
    else:
        vdd_min = args.vdd_min
    if args.vdd_max is None:
        vdd_max = vds_max
    else:
        vdd_max = args.vdd_max

    if vdd_min <= 0 or vdd_max <= 0 or vdd_max <= vdd_min:
        raise ValueError("Invalid Vdd range.")

    R_values = np.logspace(math.log10(args.r_min), math.log10(args.r_max), args.r_count)
    Vdd_values = np.linspace(vdd_min, vdd_max, args.vdd_count)

    df_res = sweep_space(
        df,
        R_values=R_values,
        Vdd_values=Vdd_values,
        min_nm=args.min_nm,
        strong_nm=args.strong_nm,
        strong_I_on=args.strong_I_on,
        weak_I_on=args.weak_I_on,
        n_vin=args.n_vin,
    )

    df_res.to_csv(f"{args.out_prefix}_results.csv", index=False)
    plot_phase_diagram(df_res, args.out_prefix)
    plot_best_per_vdd(df_res, args.out_prefix)
    
    print(f"Results saved to {args.out_prefix}_results.csv")
    print(f"Phase diagram saved to {args.out_prefix}_phase_diagram_3d.png")
    print(f"Best per Vdd plot saved to {args.out_prefix}_best_per_vdd.png")
    plt.show()


if __name__ == "__main__":
    main()
