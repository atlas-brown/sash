#!/bin/sh

x=$(pwd)

echo "libname sasdata '$x';" > $x/chk.sas # file is created here

echo "proc print data=sasdata.data ;" > $x/chk.sas # bug here: file is immedietely overwritten here
echo "run;" > $x/chk.sas # bug here: file is immedietely overwritten here
sas chk.sas
exit 0
