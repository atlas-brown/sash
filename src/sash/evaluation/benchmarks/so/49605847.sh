#!/bin/sh
current_time=$(date "+%Y.%m.%d-%H.%M.%S")
tail -n 0 -F hive-server2.log | \
while read LINE
do
if [ `echo "$LINE" | grep -c "DROP" ` -gt 0 ]
then
  AuditTypeID=14
  QueryResult="$(grep -oEi 'DROP TABLE [a-zA-Z][a-zA-Z0-9_]*' hive-server2.log | sed -n \$p)"
echo -e "$QueryResult" >/dev/null < op.txt
cp op.txt op/op.txt.$current_time
fi
done

