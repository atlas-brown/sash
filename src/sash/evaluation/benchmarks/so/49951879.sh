ql_remove_locks(){
  local pid="$$";
  declare -i count=0;
  ql_pid="$pid" ql_node_ls_all | while read line; do
    count=$((count+1));
    echo "count: $count";
    echo "deleting lock: $line";
    rm -rf "$line";
  done;
  echo "quicklock: $count lock(s) removed."
}
ql_remove_locks
