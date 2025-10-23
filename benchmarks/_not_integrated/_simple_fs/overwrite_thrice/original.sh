#!/bin/bash
# https://stackoverflow.com/questions/49138117/not-able-to-overwrite-the-file-properly-through-unix-script
x=$(pwd)

echo "libname sasdata '$x';" > $x/chk.sas # 1

echo "proc print data=sasdata.data ;" > $x/chk.sas # 2
echo "run;" > $x/chk.sas # 3
sas chk.sas
exit 0
