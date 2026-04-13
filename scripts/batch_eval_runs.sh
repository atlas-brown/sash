#!/bin/sh

set -x

# 10s with DFS.
./evaluation.py --timeout 20 --csv "tmp.csv" || true
mlr --icsv --opprint cut -f benchmark,kind,timed_out,exec_time,solver_time,time \
  "tmp.csv" > "../results/timeout_10_solver_timeout_10_with_dfs_filtered.txt"

# # 30s with DFS.
# ./evaluation.py --timeout 60 --csv "tmp.csv" || true
# mlr --icsv --opprint cut -f benchmark,kind,timed_out,exec_time,solver_time,time \
#   "tmp.csv" > "../results/timeout_30_solver_timeout_30_with_dfs_filtered.txt"

# # 60s with DFS.
# ./evaluation.py --timeout 120 --csv "tmp.csv" || true
# mlr --icsv --opprint cut -f benchmark,kind,timed_out,exec_time,solver_time,time \
#   "tmp.csv" > "../results/timeout_60_solver_timeout_60_with_dfs_filtered.txt"

# # 10s without DFS.
# ./evaluation.py --timeout 20 -D --csv "tmp.csv" || true
# mlr --icsv --opprint cut -f benchmark,kind,timed_out,exec_time,solver_time,time \
#   "tmp.csv" > "../results/timeout_10_solver_timeout_10_without_dfs_filtered.txt"

# # 30s without DFS.
# ./evaluation.py --timeout 60 -D --csv "tmp.csv" || true
# mlr --icsv --opprint cut -f benchmark,kind,timed_out,exec_time,solver_time,time \
#   "tmp.csv" > "../results/timeout_30_solver_timeout_30_without_dfs_filtered.txt"

# # 60s without DFS.
# ./evaluation.py --timeout 120 -D --csv "tmp.csv" || true
# mlr --icsv --opprint cut -f benchmark,kind,timed_out,exec_time,solver_time,time \
#   "tmp.csv" > "../results/timeout_60_solver_timeout_60_without_dfs_filtered.txt"

rm "tmp.csv"
