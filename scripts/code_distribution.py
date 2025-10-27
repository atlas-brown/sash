import os
import yaml
from collections import Counter
import matplotlib.pyplot as plt

# Grab all info.yaml files from benchmark directories
def gather_benchmark_info(root_dir):
    benchmark_info = []
    for subdir, _, files in os.walk(root_dir):
        for file in files:
            if file == 'info.yaml':
                file_path = os.path.join(subdir, file)
                with open(file_path, 'r') as f:
                    info = yaml.safe_load(f)
                    benchmark_info.append(info)
    return benchmark_info

root_directory = '../benchmarks'
benchmarks = gather_benchmark_info(root_directory)

code_distribution = Counter()
for benchmark in benchmarks:
    errors = benchmark.get('ground_truth', {}).get('errors', [])
    for error in errors:
        code = error.get('code')
        for c in (code if isinstance(code, list) else [code]):
            code_distribution[c] += 1

code_distribution = dict(sorted(code_distribution.items(), key=lambda item: item[1], reverse=True))

# Plotting
codes = list(code_distribution.keys())
counts = list(code_distribution.values())
plt.figure(figsize=(10, 5))
plt.bar(codes, counts)

# Plot numbers on top of bars
for i, count in enumerate(counts):
    plt.text(i, count + 0.5, str(count), ha='center')

plt.gca().spines['top'].set_visible(False)
plt.gca().spines['right'].set_visible(False)
plt.xlabel('Error code')
plt.ylabel('Frequency')
plt.xticks(rotation=80)
plt.tight_layout()
plt.show()
