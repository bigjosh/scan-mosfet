import csv
import numpy as np
import matplotlib.pyplot as plt

filename = r"D:\Github\scan-mosfet\scan-mosfet-20251129_173739.csv"

vgs_list = []
ids_list = []

# 1. Read Data
try:
    with open(filename, 'r') as f:
        reader = csv.reader(f)
        next(reader) # Skip header
        for row in reader:
            if not row or not row[0].strip(): continue
            try:
                vds = float(row[0])
                vgs = float(row[1])
                ids = float(row[2])
                
                # Filter for Vds = 8.0
                if abs(vds - 8.0) < 0.1:
                    vgs_list.append(vgs)
                    ids_list.append(ids)
            except ValueError:
                continue
except FileNotFoundError:
    print("File not found")
    exit(1)

vgs = np.array(vgs_list)
ids = np.array(ids_list)

# Sort
idx = np.argsort(vgs)
vgs = vgs[idx]
ids = ids[idx]

# 2. Quadratic Fit
coeffs = np.polyfit(vgs, ids, 2)
p = np.poly1d(coeffs)

# Generate smooth curve for plotting
vgs_fit = np.linspace(min(vgs), max(vgs), 100)
ids_fit = p(vgs_fit)

equation = f"Ids = {coeffs[0]:.2f}*Vgs² {coeffs[1]:+.2f}*Vgs {coeffs[2]:+.2f}"

# 3. Plot
plt.figure(figsize=(10, 6))
plt.scatter(vgs, ids, color='blue', label='Measured Data (Vds=8V)', zorder=5)
plt.plot(vgs_fit, ids_fit, 'r--', linewidth=2, label=f'Quadratic Fit\n{equation}')

plt.title(f'MOSFET Transfer Characteristic (Vds=8V)\n{equation}', fontsize=14)
plt.xlabel('Vgs (V)', fontsize=12)
plt.ylabel('Ids (uA)', fontsize=12)
plt.grid(True, alpha=0.3)
plt.legend(fontsize=12)

# Add text box with R^2
yhat = p(vgs)
ybar = np.sum(ids)/len(ids)
ssreg = np.sum((yhat-ybar)**2)
sstot = np.sum((ids-ybar)**2)
r2 = ssreg / sstot
plt.text(0.05, 0.95, f'R² = {r2:.4f}', transform=plt.gca().transAxes, 
         fontsize=12, verticalalignment='top', bbox=dict(boxstyle='round', facecolor='white', alpha=0.8))

plt.tight_layout()
print("Displaying plot...")
plt.show()
