#! /usr/bin/bash
# https://stackoverflow.com/questions/55731127/bash-script-to-identify-specific-alias-causing-a-bug
[ -e aliases.txt ] && rm -f aliases.txt
alias | sed 's/alias //' | cut -d "=" -f1 > aliases.txt # bug here: alias will output nothing in a noninteractive shell (unless sourced)
printf "File aliases.txt created with %d lines.\n" \
        "$(wc -l < <(\cat aliases.txt))"
IFS=" "
n=0
while read -r line || [ -n "$line" ]; do
    n=$((n+1))
    aliasedAs=$( alias "$line" | sed 's/alias //' )
    printf "Line %2d: %s\n" "$n" "$aliasedAs"
    unalias "$line"
    [ -z $(eval "$*" 1> /dev/null) ] && printf "********** Look up: %s\n" "$line" # check output to stderr only
    eval "${aliasedAs}"
done < <(tail aliases.txt)  # use tail + proc substitution for testing only
