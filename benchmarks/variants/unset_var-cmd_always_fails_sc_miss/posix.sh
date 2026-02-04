#!/bin/sh -x

. credentials.sh
OPTARG=""
_set_OPTARGS() {
    OPTARGS= # variant: `OPTARGS` is unbound, even though it appears as an assignment.
}
while getopts :i:x:n name
do
    case $name in
        x)  inputfile="$OPTARG" ;;
        i)  outputPath="$OPTARGS" ;; # bug here (1): should be $OPTARG
        n)  dirName="$OPTARG" ;;
    esac
done

if [ ! "$dirName" ]
then
    mkdir $dirName || echo "error while creating dir" # bug here (2): mkdir always fails because of the if condition
fi
while read -r line;
do
    touch "$line"
    mv  "$line" "$dirName"
done < $inputfile
