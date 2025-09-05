#!/bin/sh
# Cd into current directory
timeout=${TIMEOUT:-60}
CURDIR=$(dirname $(realpath $0))
cd $CURDIR || exit 1

if [ -f output.tmp ] ; then
    rm output.tmp
fi 
logFile="results.log"
if [ -f results.log ] ; then 
    rm results.log
fi
# for dir in benchmarks/milestone_fs benchmarks/milestone_sc benchmarks/sc_bad benchmarks/sc_good benchmarks/highprofile/original benchmarks/highprofile/variants benchmarks/so ;
for dir in benchmarks/highprofile/original benchmarks/highprofile/variants benchmarks/so ;
do
    for file in "$dir"/* ; do 
        case "$file" in 
            *.sh | *.test)
                : "no op"
            ;;
            *)
                continue 
            ;;
        esac
        echo "Running $file"
        if [ "$dir" = "benchmarks/smoosh/tests" ] ; then 
            timeout "$timeout" python3 check_smoosh.py "$file" 1>> output.tmp
        else
            timeout "$timeout" shseer $file -z3 1>> output.tmp
        fi 
        res="$?"
        if [ "$res" = 0 ] ; then
            tail -1 output.tmp >> $logFile	
        elif [ "$res" = 124 ] ; then
            echo "{\"filename\": \"$file\", \"timeout_limit\": $timeout, \"result\": \"TIMEOUT\",\"time\":\"$timeout\"}" >> $logFile	
        else 
            echo "{\"filename\": \"$file\", \"timeout_limit\": $timeout, \"result\": \"ShseerException\", \"time\":\"$timeout\"}" >> $logFile	
        fi
    done
done 
rm output.tmp
python3 check_results.py
exit $?
