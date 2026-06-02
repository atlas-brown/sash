#!/usr/bin/env python3
"""
Generate (optionally up-sampled) train / test splits of the Covertype data set.
"""

import argparse
import os
import tempfile
import numpy as np
from numpy.lib.format import open_memmap
from sklearn.datasets import fetch_covtype
from sklearn.model_selection import train_test_split


def stream_balanced_oversample(
    X,
    y,
    target_rows,
    *,
    output_dir: str,
    random_state: int = 0,
    block_size: int = 500_000,
):
    n_features = X.shape[1]
    rng = np.random.default_rng(random_state)
    
    unique_classes, class_counts = np.unique(y, return_counts=True)
    n_classes = len(unique_classes)
    
    print(f"Original data: {len(X):,} rows, {n_classes} classes")
    print(f"Target: {target_rows:,} rows")
    
    X_out = open_memmap(
        os.path.join(output_dir, "X_temp.npy"),
        mode="w+",
        dtype=X.dtype,
        shape=(target_rows, n_features),
    )
    y_out = open_memmap(
        os.path.join(output_dir, "y_temp.npy"),
        mode="w+",
        dtype=y.dtype,
        shape=(target_rows,),
    )
    
    samples_per_class = target_rows // n_classes
    remainder = target_rows % n_classes
    
    filled = 0
    
    for class_idx, class_label in enumerate(unique_classes):
        class_mask = (y == class_label)
        class_indices = np.where(class_mask)[0]
        n_available = len(class_indices)
        
        n_needed = samples_per_class + (1 if class_idx < remainder else 0)
        
        print(f"Class {class_label}: sampling {n_needed:,} from {n_available:,} available")
        
        sampled_indices = rng.choice(class_indices, size=n_needed, replace=(n_needed > n_available))
        
        # write in blocks to avoid memory issues
        for block_start in range(0, n_needed, block_size):
            block_end = min(block_start + block_size, n_needed)
            block_indices = sampled_indices[block_start:block_end]
            block_size_actual = len(block_indices)
            
            X_out[filled:filled + block_size_actual] = X[block_indices]
            y_out[filled:filled + block_size_actual] = y[block_indices]
            filled += block_size_actual
        
    # shuffle the output to mix classes
    shuffle_indices = rng.permutation(target_rows)
    
    # shuffle in blocks to avoid loading everything into memory
    X_shuffled = open_memmap(
        os.path.join(output_dir, "X_shuffled.npy"),
        mode="w+",
        dtype=X.dtype,
        shape=(target_rows, n_features),
    )
    y_shuffled = open_memmap(
        os.path.join(output_dir, "y_shuffled.npy"),
        mode="w+",
        dtype=y.dtype,
        shape=(target_rows,),
    )
    
    for block_start in range(0, target_rows, block_size):
        block_end = min(block_start + block_size, target_rows)
        block_indices = shuffle_indices[block_start:block_end]
        X_shuffled[block_start:block_end] = X_out[block_indices]
        y_shuffled[block_start:block_end] = y_out[block_indices]
    
    del X_out, y_out
    os.remove(os.path.join(output_dir, "X_temp.npy"))
    os.remove(os.path.join(output_dir, "y_temp.npy"))
    
    return X_shuffled, y_shuffled


def atomic_save_memmap(arr, path: str, *, block: int = 500_000) -> None:
    tmp = f"{path}.tmp"
    fp = open_memmap(tmp, mode="w+", dtype=arr.dtype, shape=arr.shape)
    
    for start in range(0, len(arr), block):
        end = min(start + block, len(arr))
        if arr.ndim == 1:
            fp[start:end] = arr[start:end]
        else:
            fp[start:end, :] = arr[start:end, :]
        if start % (block * 10) == 0:
            print(f"  Saving {path}: {start:,}/{len(arr):,} rows")
    
    fp.flush()
    del fp
    os.replace(tmp, path)


# Parse arguments
parser = argparse.ArgumentParser()
g = parser.add_mutually_exclusive_group()
g.add_argument("--small", action="store_true", help="≈5 GB up-sampled dataset")
g.add_argument("--min", action="store_true", help="No oversampling – use original rows")
parser.add_argument(
    "--rows",
    type=int,
    default=None,
    help="Exact row count to generate (overrides preset sizes)",
)
args = parser.parse_args()

print("Fetching Covertype dataset...")
X, y = fetch_covtype(data_home="inputs", download_if_missing=True, return_X_y=True)
print(f"Loaded: {X.shape[0]:,} rows × {X.shape[1]} features")

out_dir = os.getenv("TMP", tempfile.gettempdir())
os.makedirs(out_dir, exist_ok=True)

if args.min:
    print("Creating minimal dataset (no oversampling)...")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=0
    )
else:
    scale_bytes = 5_000_000_000 if args.small else 10_000_000_000
    n_target = int(scale_bytes / (X.shape[1] * 8))
    if args.rows is not None:
        n_target = args.rows
    
    X_bal, y_bal = stream_balanced_oversample(
        X, y, n_target,
        output_dir=out_dir,
        random_state=0
    )
    
    n_total = len(X_bal)
    n_test = int(n_total * 0.2)
    n_train = n_total - n_test
    
    rng = np.random.default_rng(0)
    indices = rng.permutation(n_total)
    train_idx = indices[:n_train]
    test_idx = indices[n_train:]
    
    print(f"Train: {n_train:,} rows, Test: {n_test:,} rows")
    
    X_train_path = os.path.join(out_dir, "X_train.npy")
    X_train_fp = open_memmap(X_train_path + ".tmp", mode="w+", dtype=X.dtype, shape=(n_train, X.shape[1]))
    for i, idx in enumerate(train_idx):
        X_train_fp[i] = X_bal[idx]
        if i % 100_000 == 0:
            print(f"  {i:,}/{n_train:,}")
    X_train_fp.flush()
    del X_train_fp
    os.replace(X_train_path + ".tmp", X_train_path)
    
    y_train_path = os.path.join(out_dir, "y_train.npy")
    y_train_fp = open_memmap(y_train_path + ".tmp", mode="w+", dtype=y.dtype, shape=(n_train,))
    for i, idx in enumerate(train_idx):
        y_train_fp[i] = y_bal[idx]
    y_train_fp.flush()
    del y_train_fp
    os.replace(y_train_path + ".tmp", y_train_path)
    
    X_test_path = os.path.join(out_dir, "X_test.npy")
    X_test_fp = open_memmap(X_test_path + ".tmp", mode="w+", dtype=X.dtype, shape=(n_test, X.shape[1]))
    for i, idx in enumerate(test_idx):
        X_test_fp[i] = X_bal[idx]
        if i % 100_000 == 0:
            print(f"  {i:,}/{n_test:,}")
    X_test_fp.flush()
    del X_test_fp
    os.replace(X_test_path + ".tmp", X_test_path)
    
    y_test_path = os.path.join(out_dir, "y_test.npy")
    y_test_fp = open_memmap(y_test_path + ".tmp", mode="w+", dtype=y.dtype, shape=(n_test,))
    for i, idx in enumerate(test_idx):
        y_test_fp[i] = y_bal[idx]
    y_test_fp.flush()
    del y_test_fp
    os.replace(y_test_path + ".tmp", y_test_path)
    
    # Cleanup
    del X_bal, y_bal
    os.remove(os.path.join(out_dir, "X_shuffled.npy"))
    os.remove(os.path.join(out_dir, "y_shuffled.npy"))
    
    exit(0)

for name, arr in {
    "X_train": X_train,
    "X_test": X_test,
    "y_train": y_train,
    "y_test": y_test,
}.items():
    path = os.path.join(out_dir, f"{name}.npy")
    print(f"Saving {name}...")
    atomic_save_memmap(arr, path)
