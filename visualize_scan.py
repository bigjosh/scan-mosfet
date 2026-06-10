#!/usr/bin/env python3
"""
Real-time visualization of MOSFET scan data
Displays a heatmap with Vgs on X-axis, Vds on Y-axis, and Ids as color
"""

import argparse
import csv
import time
import os
import sys
import numpy as np
import matplotlib.pyplot as plt
from matplotlib import cm, colors
from mpl_toolkits.mplot3d import Axes3D
from matplotlib.animation import FuncAnimation
from pathlib import Path


class ScanVisualizer:
    def __init__(self, csv_file: str, refresh_rate: float = 1.0):
        """
        Initialize the visualizer
        
        Args:
            csv_file: Path to the CSV file to monitor
            refresh_rate: Update interval in seconds
        """
        self.csv_file = csv_file
        self.refresh_rate = refresh_rate
        self.last_modified = 0
        self.max_vds_seen = -1  # Track maximum Vds to detect new rows
        
        # Data storage
        self.vds_values = []
        self.vgs_values = []
        self.ids_matrix = None
        
        # Setup plot
        self.fig = plt.figure(figsize=(12, 10))
        self.ax = self.fig.add_subplot(111, projection='3d')
        self.surf = None
        self.colorbar = None
        
        # Initial plot setup
        self.ax.set_xlabel('Vgs (V)', fontsize=12)
        self.ax.set_ylabel('Vds (V)', fontsize=12)
        self.ax.set_zlabel('Ids (mA)', fontsize=12)
        self.ax.set_title('MOSFET Characterization: Ids vs Vgs and Vds', fontsize=14)
        
    def fit_surface_and_derivative(self):
        """Fit a polynomial surface and compute dIds/dVgs"""
        if self.ids_matrix is None:
            return None, None, None, None
            
        X, Y = np.meshgrid(self.vgs_values, self.vds_values)
        Z = self.ids_matrix * 1000  # Convert to mA
        
        # Flatten valid data
        mask = ~np.isnan(Z)
        x_flat = X[mask]
        y_flat = Y[mask]
        z_flat = Z[mask]
        
        degree = 6
        min_points = (degree + 1) * (degree + 2) // 2 + 10  # Terms + margin
        
        if len(z_flat) < min_points:
            return X, Y, Z, np.zeros_like(Z)
            
        # Build design matrix for polynomial
        # Terms: x^k * y^(d-k) for d in 0..degree
        A = []
        for i in range(len(x_flat)):
            xi, yi = x_flat[i], y_flat[i]
            row = []
            for d in range(degree + 1):
                for k in range(d + 1):
                    row.append((xi**k) * (yi**(d-k)))
            A.append(row)
            
        # Solve for coefficients
        try:
            coeffs, _, _, _ = np.linalg.lstsq(A, z_flat, rcond=None)
        except:
            return X, Y, Z, np.zeros_like(Z)
            
        # Evaluate fitted surface and derivative on grid
        Z_fit = np.zeros_like(X)
        dZ_dx = np.zeros_like(X)  # dIds/dVgs (Transconductance)
        
        rows, cols = X.shape
        for r in range(rows):
            for c in range(cols):
                xi = X[r, c]
                yi = Y[r, c]
                
                val = 0.0
                grad = 0.0
                idx = 0
                
                for d in range(degree + 1):
                    for k in range(d + 1):
                        # Surface value
                        val += coeffs[idx] * (xi**k) * (yi**(d-k))
                        
                        # Derivative w.r.t x (Vgs)
                        if k > 0:
                            grad += coeffs[idx] * k * (xi**(k-1)) * (yi**(d-k))
                        
                        idx += 1
                
                Z_fit[r, c] = val
                dZ_dx[r, c] = grad
                
        return X, Y, Z_fit, dZ_dx

    def read_csv(self):
        """Read the CSV file and parse the matrix data
        
        Returns:
            True if data was read and updated, False otherwise
        """
        if not os.path.exists(self.csv_file):
            return False
        
        # Check if file has been modified
        modified_time = os.path.getmtime(self.csv_file)
        if modified_time <= self.last_modified:
            return False
        
        self.last_modified = modified_time
        
        # Track if we found a new maximum Vds
        new_vds_max = False
        
        try:
            with open(self.csv_file, 'r') as f:
                reader = csv.reader(f)
                rows = list(reader)
            
            if len(rows) < 2:
                return False
            
            # Parse header to determine format
            header = rows[0]
            
            # CHECK FOR NEW 3-COLUMN FORMAT
            # Check if header contains Vds and Vgs columns (in any position)
            if any('Vds' in h for h in header) and any('Vgs' in h for h in header):
                # New format: Vds, Vgs, Ids(uA)
                data_points = []
                for row in rows[1:]:
                    if not row or not row[0].strip(): continue
                    try:
                        vds = float(row[0])
                        vgs = float(row[1])
                        ids_ua = float(row[2])
                        data_points.append((vds, vgs, ids_ua))
                    except (ValueError, IndexError):
                        continue
                
                if not data_points:
                    return False
                
                # Extract unique sorted axes
                self.vds_values = sorted(list(set(p[0] for p in data_points)))
                self.vgs_values = sorted(list(set(p[1] for p in data_points)))
                
                # Create matrix (filled with NaNs)
                shape = (len(self.vds_values), len(self.vgs_values))
                self.ids_matrix = np.full(shape, np.nan)
                
                # Fill matrix
                # Map values to indices
                vds_map = {v: i for i, v in enumerate(self.vds_values)}
                vgs_map = {v: i for i, v in enumerate(self.vgs_values)}
                
                for vds, vgs, ids_ua in data_points:
                    r = vds_map[vds]
                    c = vgs_map[vgs]
                    # Convert uA to Amps for consistency with existing plotting logic
                    self.ids_matrix[r, c] = ids_ua * 1e-6
                
                return True

            # OLD MATRIX FORMAT LOGIC
            # Parse header to get Vds values
            header = rows[0]
            self.vds_values = [float(v) for v in header[1:] if v.strip()]
            
            # Check if we have a new maximum Vds
            if self.vds_values:
                current_max_vds = max(self.vds_values)
                if current_max_vds > self.max_vds_seen:
                    new_vds_max = True
                    self.max_vds_seen = current_max_vds
                    print(f"New Vds row detected: {current_max_vds:.2f}V - Refreshing display")
            
            # Parse data rows
            vgs_values = []
            ids_data = []
            
            for row in rows[1:]:
                if not row or not row[0].strip():
                    continue
                
                try:
                    vgs = float(row[0])
                    vgs_values.append(vgs)
                    
                    # Parse Ids values for this Vgs
                    ids_row = []
                    for i, val in enumerate(row[1:], 1):
                        if i <= len(self.vds_values):
                            if val.strip():
                                try:
                                    ids = float(val)
                                    ids_row.append(ids)
                                except ValueError:
                                    ids_row.append(np.nan)
                            else:
                                ids_row.append(np.nan)
                    
                    ids_data.append(ids_row)
                except ValueError:
                    continue
            
            self.vgs_values = vgs_values
            
            # Convert to numpy array and transpose so that:
            # - rows correspond to Vds (Y-axis)
            # - columns correspond to Vgs (X-axis)
            if ids_data:
                # Pad rows to ensure rectangular array
                max_len = len(self.vds_values)
                ids_data_padded = [row + [np.nan] * (max_len - len(row)) for row in ids_data]
                self.ids_matrix = np.array(ids_data_padded).T  # Transpose
                return True
            
        except Exception as e:
            print(f"Error reading CSV: {e}")
            return False
        
        return False
    
    def update_plot(self, frame=None):
        """Update the plot with new data"""
        if not self.read_csv():
            return
        
        if self.ids_matrix is None or len(self.vgs_values) == 0:
            return
        
        # Clear the axes to redraw
        self.ax.clear()
        
        # Fit surface and calculate derivative
        X, Y, Z_fit, dZ_dx = self.fit_surface_and_derivative()
        
        if X is None:
            return

        # Normalize derivative for coloring
        if np.nanmax(dZ_dx) > np.nanmin(dZ_dx):
            norm = colors.Normalize(vmin=np.nanmin(dZ_dx), vmax=np.nanmax(dZ_dx))
            face_colors = cm.viridis(norm(dZ_dx))
        else:
            face_colors = cm.viridis(np.zeros_like(dZ_dx))
            norm = colors.Normalize(vmin=0, vmax=1)
        
        # Plot fitted surface with some transparency
        self.surf = self.ax.plot_surface(
            X, Y, Z_fit,
            facecolors=face_colors,
            linewidth=0,
            antialiased=True,
            rstride=1,
            cstride=1,
            shade=False,
            alpha=0.8
        )
        
        # Overlay actual data points
        # Create coordinate grids matching the data
        X_raw, Y_raw = np.meshgrid(self.vgs_values, self.vds_values)
        Z_raw = self.ids_matrix * 1000  # Convert to mA
        
        # Flatten arrays for scatter plot
        x_flat = X_raw.flatten()
        y_flat = Y_raw.flatten()
        z_flat = Z_raw.flatten()
        
        # Filter out NaNs to keep plot clean
        mask = ~np.isnan(z_flat)
        self.ax.scatter(
            x_flat[mask], 
            y_flat[mask], 
            z_flat[mask], 
            c='k', 
            marker='.', 
            s=20, 
            depthshade=True,
            label='Measured Data'
        )
        
        # Add mappable for colorbar (Transconductance)
        mappable = cm.ScalarMappable(cmap='viridis', norm=norm)
        mappable.set_array(dZ_dx)
        
        if self.colorbar is None:
            self.colorbar = self.fig.colorbar(mappable, ax=self.ax, label='d(Ids)/d(Vgs) [mS]', shrink=0.5, aspect=10)
        else:
            self.colorbar.update_normal(mappable)

        # Set labels again after clear
        self.ax.set_xlabel('Vgs (V)', fontsize=12)
        self.ax.set_ylabel('Vds (V)', fontsize=12)
        self.ax.set_zlabel('Ids (mA)', fontsize=12)
        
        # Update title with data info
        self.ax.set_title(
            f'MOSFET Characterization: Ids vs Vgs and Vds\n'
            f'Surface=Poly Fit (Color=Transconductance), Points=Measured',
            fontsize=14
        )
        
        self.fig.canvas.draw_idle()
    
    def run_live(self):
        """Run live monitoring with animation"""
        print(f"Monitoring: {self.csv_file}")
        print(f"Refresh rate: {self.refresh_rate}s")
        print("Close the plot window to exit.")
        
        # Create animation that updates the plot
        anim = FuncAnimation(
            self.fig,
            self.update_plot,
            interval=int(self.refresh_rate * 1000),
            cache_frame_data=False
        )
        
        plt.tight_layout()
        plt.show()
    
    def show_static(self):
        """Display final static plot"""
        self.read_csv()
        self.update_plot()
        plt.tight_layout()
        plt.show()


def main():
    parser = argparse.ArgumentParser(
        description='Visualize MOSFET scan data in real-time',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    
    parser.add_argument('csv_file', type=str, nargs='?', default=None,
                        help='CSV file to visualize (if not specified, finds most recent)')
    parser.add_argument('--refresh', type=float, default=1.0,
                        help='Refresh rate in seconds for live monitoring')
    parser.add_argument('--static', action='store_true',
                        help='Show static plot (no live updates)')
    
    args = parser.parse_args()
    
    # Find CSV file
    csv_file = args.csv_file
    
    if csv_file is None:
        # Find most recent scan-mosfet CSV file
        csv_files = list(Path('.').glob('scan-mosfet-*.csv'))
        if not csv_files:
            print("Error: No scan-mosfet-*.csv files found in current directory")
            print("Please specify a CSV file or run scan_mosfet.py first")
            sys.exit(1)
        
        # Get most recent file
        csv_file = max(csv_files, key=lambda p: p.stat().st_mtime)
        print(f"Auto-detected file: {csv_file}")
    
    if not os.path.exists(csv_file):
        print(f"Error: File not found: {csv_file}")
        sys.exit(1)
    
    # Create visualizer
    viz = ScanVisualizer(csv_file, args.refresh)
    
    # Run visualization
    if args.static:
        viz.show_static()
    else:
        viz.run_live()


if __name__ == "__main__":
    main()
