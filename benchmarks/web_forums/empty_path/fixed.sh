#!/bin/sh

# https://segmentfault.com/q/1010000000158149



# -bash: grep: command not found
# -bash: grep: command not found
# env: bash: No such file or directory
# env: bash: No such file or directory
# env: bash: No such file or directory
# env: bash: No such file or directory
# env: bash: No such file or directory
# -bash: grep: command not found
# -bash: cat: command not found
# -bash: grep: command not found
# -bash: grep: command not found
# -bash: grep: command not found
# -bash: grep: command not found

grep "PATH" /etc/profile
grep "PATH" /etc/profile.d/*.sh
bash -c "echo \$PATH"
bash -c "env | grep PATH"
bash -c "env | grep PATH"
bash -c "env | grep PATH"
grep "PATH" /etc/profile
cat /etc/profile
grep "PATH" /etc/profile.d/*.sh
grep "PATH" /etc/profile.d/*.sh
grep "PATH" /etc/profile.d/*.sh
grep "PATH" /etc/profile.d/*.sh
