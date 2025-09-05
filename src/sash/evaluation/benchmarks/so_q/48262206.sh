#!/usr/bin/ksh
set -x
$PATH/script1.sh "
--set serveroutput on
--set feedback off
insert into table (column) values ('$1');
commit;
"
if [[ $? != 0 ]]
  then
echo "Error"
exit 3
  else
echo "Ok"
fi

