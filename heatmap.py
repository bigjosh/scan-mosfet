import argparse
import math
import os
from dataclasses import dataclass
from typing import Optional, List, Dict, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm


# ----------------------------------------------------------------------
# Data structures
# ----------------------------------------------------------------------

@dataclass
class ThresholdSolution:
    Voff: float
    Von: float
    NM_H: float
    NM_L: float
    VOH_min: float
    VOL_max: float
    NM_min: float  # min(NM_H, NM_L)


@dataclass
class CircuitSolution:
    R: float
    Vdd: float
    thresholds: Optional[ThresholdSolution]
    I_on: float
    I_off: float
    regime: str  # optional classification


# ----------------------------------------------------------------------
# CSV loading and 2D Id(Vds, Vgs) interpolation
# ----------------------------------------------------------------------

def load_mosfet_csv(path: str) -> pd.DataFrame:
    """
    Load a MOSFET Id(Vds, Vgs) CSV.

    Expected columns (names can vary; inferred heuristically):
        Vds (V), Vgs (V), Ids (uA/mA/A)
    Returns DataFrame with columns: Vds [V], Vgs [V], Id [A].
    """
    df = pd.read_csv(path)
    df.columns = [c.strip() for c in df.columns]

    vds_col = None
    vgs_col = None
    ids_col = None

    for c in df.columns:
        cl = c.lower()
        if vds_col is None and cl.startswith("vds"):
            vds_col = c
        if vgs_col is None and cl.startswith("vgs"):
            vgs_col = c

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

    for c in df.columns:
        if "id" in c.lower():
            ids_col = c
            break
    if ids_col is None:
        raise ValueError("Could not infer Id column from CSV header.")

    ids_name_lower = ids_col.lower()
    if "ua" in ids_name_lower:
        scale = 1e-6
    elif "ma" in ids_name_lower:
        scale = 1e-3
    else:
        scale = 1.0

    out = pd.DataFrame()
    out["Vds"] = pd.to_numeric(df[vds_col], errors="coerce")
    out["Vgs"] = pd.to_numeric(df[vgs_col], errors="coerce")
    out["Id"] = pd.to_numeric(df[ids_col], errors="coerce") * scale
    out = out.dropna()
    return out


class IdInterpolator:
    """
    Bilinear interpolator for Id(Vds, Vgs) on a rectangular grid.

    Assumes data covers all combinations of unique Vds and Vgs values.
    """

    def __init__(self, df: pd.DataFrame):
        vds_vals = np.sort(df["Vds"].unique())
        vgs_vals = np.sort(df["Vgs"].unique())

        pivot = df.pivot(index="Vgs", columns="Vds", values="Id")
        pivot = pivot.reindex(index=vgs_vals, columns=vds_vals)
        if pivot.isna().any().any():
            raise ValueError("Data grid has missing points; need full Vds×Vgs grid for bilinear interpolation.")

        id_grid_vgs_vds = pivot.values  # shape (len(Vgs), len(Vds))
        self.vds = vds_vals
        self.vgs = vgs_vals
        self.Id_grid = id_grid_vgs_vds.T  # shape (len(Vds), len(Vgs))

        self.vds_min = float(self.vds[0])
        self.vds_max = float(self.vds[-1])
        self.vgs_min = float(self.vgs[0])
        self.vgs_max = float(self.vgs[-1])

    def __call__(self, vds: float, vgs: float) -> float:
        """
        Bilinear interpolation at scalar (vds, vgs).
        Clamps to [vds_min, vds_max] × [vgs_min, vgs_max].
        """
        vds = float(np.clip(vds, self.vds_min, self.vds_max))
        vgs = float(np.clip(vgs, self.vgs_min, self.vgs_max))

        # Vds indices
        i1 = int(np.searchsorted(self.vds, vds))
        if i1 == 0:
            i0 = i1 = 0
        elif i1 >= len(self.vds):
            i0 = i1 = len(self.vds) - 1
        else:
            i0 = i1 - 1

        x0 = self.vds[i0]
        x1 = self.vds[i1]
        tx = 0.0 if x1 == x0 else (vds - x0) / (x1 - x0)

        # Vgs indices
        j1 = int(np.searchsorted(self.vgs, vgs))
        if j1 == 0:
            j0 = j1 = 0
        elif j1 >= len(self.vgs):
            j0 = j1 = len(self.vgs) - 1
        else:
            j0 = j1 - 1

        y0 = self.vgs[j0]
        y1 = self.vgs[j1]
        ty = 0.0 if y1 == y0 else (vgs - y0) / (y1 - y0)

        f00 = self.Id_grid[i0, j0]
        f01 = self.Id_grid[i0, j1]
        f10 = self.Id_grid[i1, j0]
        f11 = self.Id_grid[i1, j1]

        f0 = f00 * (1 - ty) + f01 * ty
        f1 = f10 * (1 - ty) + f11 * ty
        f = f0 * (1 - tx) + f1 * tx
        return float(f)


# ----------------------------------------------------------------------
# Load-line solver & VTC computation
# ----------------------------------------------------------------------

def solve_vout_for_vin(
    Id_interp: IdInterpolator,
    Vin: float,
    R: float,
    Vdd: float,
    n_bracket: int = 32,
) -> float:
    """
    Solve load-line equation for given Vin:

        (Vdd - Vout)/R = Id(Vout, Vin)

    using coarse sampling + bisection in Vout ∈ [vds_min, min(Vdd, vds_max)].
    """
    vds_lo = Id_interp.vds_min
    vds_hi = min(Vdd, Id_interp.vds_max)
    if vds_hi <= vds_lo:
        return vds_lo

    v_grid = np.linspace(vds_lo, vds_hi, n_bracket)
    g_vals = []
    for v in v_grid:
        Id = Id_interp(v, Vin)
        g_vals.append((Vdd - v) / R - Id)
    g_vals = np.array(g_vals)

    sign_change_idx = None
    for k in range(len(v_grid) - 1):
        if np.sign(g_vals[k]) * np.sign(g_vals[k + 1]) <= 0:
            sign_change_idx = k
            break

    if sign_change_idx is None:
        idx = int(np.argmin(np.abs(g_vals)))
        return float(v_grid[idx])

    a = float(v_grid[sign_change_idx])
    b = float(v_grid[sign_change_idx + 1])
    fa = float(g_vals[sign_change_idx])
    fb = float(g_vals[sign_change_idx + 1])

    if abs(fa) < 1e-12:
        return a
    if abs(fb) < 1e-12:
        return b

    for _ in range(24):  # fewer iterations for speed; bracket is already narrow
        m = 0.5 * (a + b)
        fm = (Vdd - m) / R - Id_interp(m, Vin)

        if fa * fm <= 0:
            b, fb = m, fm
        elif fb * fm <= 0:
            a, fa = m, fm
        else:
            if abs(fa) < abs(fb):
                b, fb = m, fm
            else:
                a, fa = m, fm

        if abs(fm) < 1e-9 * max(1.0, abs(Vdd / R)):
            return m

    return 0.5 * (a + b)


def compute_vtc(
    Id_interp: IdInterpolator,
    R: float,
    Vdd: float,
    n_vin: int = 121,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Compute DC transfer characteristic:

        Vin in [0, Vdd] -> Vout(Vin)
    """
    Vin = np.linspace(0.0, Vdd, n_vin)
    Vout = np.empty_like(Vin)
    for i, vin in enumerate(Vin):
        Vout[i] = solve_vout_for_vin(Id_interp, float(vin), R, Vdd)
    return Vin, Vout


# ----------------------------------------------------------------------
# Threshold search with NMmin objective and optional slope limit
# ----------------------------------------------------------------------

def find_best_thresholds(
    Vin: np.ndarray,
    Vout: np.ndarray,
    min_nm: float = 0.3,
    slope_max: Optional[float] = None,
) -> Optional[ThresholdSolution]:
    """
    Search over (Voff, Von) on the Vin grid such that:

      - For all Vin <= Voff:  Vout >= Von
      - For all Vin >= Von:   Vout <= Voff
      - Both NM_H, NM_L >= min_nm

    NM_H = VOH_min - Von,  NM_L = Voff - VOL_max
    NM_min = min(NM_H, NM_L)

    Objective: maximize NM_min; tie-breaker: maximize NM_H * NM_L.
    """
    n = len(Vin)
    if n != len(Vout):
        raise ValueError("Vin and Vout length mismatch")

    hi_min = np.minimum.accumulate(Vout)
    lo_max = np.maximum.accumulate(Vout[::-1])[::-1]

    if slope_max is not None:
        slope = np.gradient(Vout, Vin)
        slope_abs = np.abs(slope)
        slope_prefix_max = np.maximum.accumulate(slope_abs)
        slope_suffix_max = np.maximum.accumulate(slope_abs[::-1])[::-1]
    else:
        slope_prefix_max = slope_suffix_max = None

    best: Optional[ThresholdSolution] = None
    best_prod_nm = -math.inf

    for i_off in range(0, n - 2):
        Voff = Vin[i_off]
        VOH_min = hi_min[i_off]

        for j_on in range(i_off + 1, n - 1):
            Von = Vin[j_on]
            VOL_max = lo_max[j_on]

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

            if slope_max is not None:
                slope_low_max = float(slope_prefix_max[i_off])
                slope_high_max = float(slope_suffix_max[j_on])
                if slope_low_max > slope_max or slope_high_max > slope_max:
                    continue

            if best is None:
                best = ThresholdSolution(
                    Voff=float(Voff),
                    Von=float(Von),
                    NM_H=float(NM_H),
                    NM_L=float(NM_L),
                    VOH_min=float(VOH_min),
                    VOL_max=float(VOL_max),
                    NM_min=float(NM_min),
                )
                best_prod_nm = float(prod_nm)
            else:
                if NM_min > best.NM_min + 1e-9:
                    best = ThresholdSolution(
                        Voff=float(Voff),
                        Von=float(Von),
                        NM_H=float(NM_H),
                        NM_L=float(NM_L),
                        VOH_min=float(VOH_min),
                        VOL_max=float(VOL_max),
                        NM_min=float(NM_min),
                    )
                    best_prod_nm = float(prod_nm)
                elif abs(NM_min - best.NM_min) <= 1e-9 and prod_nm > best_prod_nm + 1e-12:
                    best = ThresholdSolution(
                        Voff=float(Voff),
                        Von=float(Von),
                        NM_H=float(NM_H),
                        NM_L=float(NM_L),
                        VOH_min=float(VOH_min),
                        VOL_max=float(VOL_max),
                        NM_min=float(NM_min),
                    )
                    best_prod_nm = float(prod_nm)

    return best


# ----------------------------------------------------------------------
# Circuit evaluation and classification
# ----------------------------------------------------------------------

def classify_regime(
    thresholds: Optional[ThresholdSolution],
    I_on: float,
    strong_nm: float,
    strong_I_on: float,
    weak_I_on: float,
) -> str:
    if thresholds is None:
        return "no_logic"

    nm_min = thresholds.NM_min
    I_on_abs = abs(I_on)

    if nm_min >= strong_nm and I_on_abs >= strong_I_on:
        return "strong_digital"
    if nm_min >= strong_nm and I_on_abs >= weak_I_on:
        return "medium_digital"
    if I_on_abs >= weak_I_on:
        return "weak_digital"
    return "marginal"


def evaluate_circuit(
    Id_interp: IdInterpolator,
    R: float,
    Vdd: float,
    min_nm: float,
    strong_nm: float,
    strong_I_on: float,
    weak_I_on: float,
    slope_max: Optional[float],
    n_vin: int = 121,
) -> CircuitSolution:
    Vin, Vout = compute_vtc(Id_interp, R, Vdd, n_vin=n_vin)
    thresholds = find_best_thresholds(Vin, Vout, min_nm=min_nm, slope_max=slope_max)

    Vout_low_in = Vout[-1]
    Vout_high_in = Vout[0]
    I_on = (Vdd - Vout_low_in) / R
    I_off = (Vdd - Vout_high_in) / R

    regime = classify_regime(thresholds, I_on, strong_nm, strong_I_on, weak_I_on)

    return CircuitSolution(
        R=float(R),
        Vdd=float(Vdd),
        thresholds=thresholds,
        I_on=float(I_on),
        I_off=float(I_off),
        regime=regime,
    )


# ----------------------------------------------------------------------
# Parallel sweep
# ----------------------------------------------------------------------

# Globals for worker processes
_GLOBAL_INTERP = None
_GLOBAL_PARAMS = None


def _init_worker(interp: IdInterpolator, params: Dict):
    global _GLOBAL_INTERP, _GLOBAL_PARAMS
    _GLOBAL_INTERP = interp
    _GLOBAL_PARAMS = params


def _worker_task(task: Tuple[float, float]) -> Dict:
    """
    Worker function: takes (R, Vdd), returns result row dict.
    """
    global _GLOBAL_INTERP, _GLOBAL_PARAMS
    R, Vdd = task
    sol = evaluate_circuit(
        _GLOBAL_INTERP,
        R,
        Vdd,
        min_nm=_GLOBAL_PARAMS["min_nm"],
        strong_nm=_GLOBAL_PARAMS["strong_nm"],
        strong_I_on=_GLOBAL_PARAMS["strong_I_on"],
        weak_I_on=_GLOBAL_PARAMS["weak_I_on"],
        slope_max=_GLOBAL_PARAMS["slope_max"],
        n_vin=_GLOBAL_PARAMS["n_vin"],
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
            "nm_min": sol.thresholds.NM_min,
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
        })
    return row


def sweep_space_parallel(
    Id_interp: IdInterpolator,
    R_values: np.ndarray,
    Vdd_values: np.ndarray,
    min_nm: float,
    strong_nm: float,
    strong_I_on: float,
    weak_I_on: float,
    slope_max: Optional[float],
    n_vin: int,
    workers: int,
) -> pd.DataFrame:
    """
    Parallel sweep over (R, Vdd) using ProcessPoolExecutor + tqdm.
    """
    tasks: List[Tuple[float, float]] = []
    for Vdd in Vdd_values:
        for R in R_values:
            tasks.append((float(R), float(Vdd)))

    params = {
        "min_nm": min_nm,
        "strong_nm": strong_nm,
        "strong_I_on": strong_I_on,
        "weak_I_on": weak_I_on,
        "slope_max": slope_max,
        "n_vin": n_vin,
    }

    results: List[Dict] = []

    with ProcessPoolExecutor(
        max_workers=workers,
        initializer=_init_worker,
        initargs=(Id_interp, params),
    ) as ex:
        futures = [ex.submit(_worker_task, t) for t in tasks]
        for f in tqdm(as_completed(futures), total=len(futures), desc="Sweeping (R, Vdd)"):
            row = f.result()
            results.append(row)

    return pd.DataFrame(results)


# ----------------------------------------------------------------------
# Interactive heatmap
# ----------------------------------------------------------------------

def plot_heatmap_interactive(df_res: pd.DataFrame, out_prefix: str) -> None:
    """
    High-resolution heatmap:

      x-axis: Vdd
      y-axis: R (log)
      color: nm_min

    Hover shows: Vdd, R, nm_min, Voff, Von, I_on.
    """
    nm_pivot = df_res.pivot(index="R", columns="Vdd", values="nm_min")
    Voff_pivot = df_res.pivot(index="R", columns="Vdd", values="Voff")
    Von_pivot = df_res.pivot(index="R", columns="Vdd", values="Von")
    Ion_pivot = df_res.pivot(index="R", columns="Vdd", values="I_on")

    R_axis = nm_pivot.index.values
    Vdd_axis = nm_pivot.columns.values

    nm_grid = nm_pivot.values
    Voff_grid = Voff_pivot.values
    Von_grid = Von_pivot.values
    Ion_grid = Ion_pivot.values

    customdata = np.stack([Voff_grid, Von_grid, nm_grid, Ion_grid], axis=-1)

    fig = go.Figure(
        data=go.Heatmap(
            x=Vdd_axis,
            y=R_axis,
            z=nm_grid,
            colorscale="Viridis",
            colorbar_title="NM_min [V]",
            zmin=np.nanmin(nm_grid),
            zmax=np.nanmax(nm_grid),
            customdata=customdata,
            hovertemplate=(
                "Vdd = %{x:.3g} V<br>"
                "R = %{y:.3g} Ω<br>"
                "NM_min = %{customdata[2]:.3g} V<br>"
                "Voff = %{customdata[0]::.3g} V<br>"
                "Von = %{customdata[1]:.3g} V<br>"
                "I_on = %{customdata[3]:.3g} A"
                "<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title="NM_min heatmap (optimal Voff/Von for each R, Vdd)",
        xaxis_title="Vdd [V]",
        yaxis_title="R [Ω]",
    )

    fig.update_yaxes(type="log")
    fig.write_html(f"{out_prefix}_nmmin_heatmap.html")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Search inverter operating regimes from MOSFET CSV data using continuous Id(Vds,Vgs)."
    )
    parser.add_argument("csv", help="Path to MOSFET Id(Vds,Vgs) CSV file.")
    parser.add_argument("--out-prefix", default="mosfet_search", help="Prefix for output files.")
    parser.add_argument("--r-min", type=float, default=1e3, help="Minimum pull-up resistor [Ω].")
    parser.add_argument("--r-max", type=float, default=1e6, help="Maximum pull-up resistor [Ω].")
    parser.add_argument("--r-count", type=int, default=40, help="Number of R samples (log-spaced).")
    parser.add_argument("--vdd-min", type=float, default=None, help="Minimum Vdd [V]. Default: 0.5*max(Vds).")
    parser.add_argument("--vdd-max", type=float, default=None, help="Maximum Vdd [V]. Default: max(Vds).")
    parser.add_argument("--vdd-count", type=int, default=40, help="Number of Vdd samples (linear).")
    parser.add_argument("--min-nm", type=float, default=0.3, help="Minimum per-side noise margin [V].")
    parser.add_argument("--strong-nm", type=float, default=0.8, help="Noise margin [V] for strong/medium classification.")
    parser.add_argument("--weak-I-on", type=float, default=10e-6, help="Minimum I_on [A] for weak_digital.")
    parser.add_argument("--strong-I-on", type=float, default=100e-6, help="Minimum I_on [A] for strong_digital.")
    parser.add_argument("--slope-max", type=float, default=None, help="Optional max |dVout/dVin| in low/high regions.")
    parser.add_argument("--n-vin", type=int, default=121, help="Number of Vin samples for VTC per circuit.")
    parser.add_argument("--workers", type=int, default=None, help="Number of worker processes (default: cpu_count).")

    args = parser.parse_args()

    df = load_mosfet_csv(args.csv)
    Id_interp = IdInterpolator(df)
    vds_max = float(Id_interp.vds_max)

    vdd_min = 0.5 * vds_max if args.vdd_min is None else args.vdd_min
    vdd_max = vds_max if args.vdd_max is None else args.vdd_max
    if vdd_min <= 0 or vdd_max <= 0 or vdd_max <= vdd_min:
        raise ValueError("Invalid Vdd range.")

    R_values = np.logspace(math.log10(args.r_min), math.log10(args.r_max), args.r_count)
    Vdd_values = np.linspace(vdd_min, vdd_max, args.vdd_count)

    workers = args.workers or None

    df_res = sweep_space_parallel(
        Id_interp,
        R_values=R_values,
        Vdd_values=Vdd_values,
        min_nm=args.min_nm,
        strong_nm=args.strong_nm,
        strong_I_on=args.strong_I_on,
        weak_I_on=args.weak_I_on,
        slope_max=args.slope_max,
        n_vin=args.n_vin,
        workers=workers,
    )

    # Derive output prefix from input CSV filename (without extension)
    out_prefix = args.out_prefix
    if out_prefix == "mosfet_search":
        # Default: use input CSV basename
        out_prefix = os.path.splitext(os.path.basename(args.csv))[0]

    df_res.to_csv(f"{out_prefix}_results.csv", index=False)
    plot_heatmap_interactive(df_res, out_prefix)


if __name__ == "__main__":
    main()
