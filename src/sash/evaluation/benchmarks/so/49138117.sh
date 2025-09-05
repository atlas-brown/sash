#!/bin/bash

x=$(pwd)

echo "libname sasdata '$x';" > $x/chk.sas         

echo "proc print data=sasdata.data ;" > $x/chk.sas
echo "run;" > $x/chk.sas
sas chk.sas
exit 0

