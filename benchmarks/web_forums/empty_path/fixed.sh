#!/bin/sh

# https://segmentfault.com/q/1010000000158149

# FIXED: unset PATH

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

# FIXED: grep "PATH" /etc/profile
# FIXED: grep "PATH" /etc/profile.d/*.sh
# FIXED: bash -c "echo \$PATH"
# FIXED: bash -c "env | grep PATH"
# FIXED: bash -c "env | grep PATH"
# FIXED: bash -c "env | grep PATH"
# FIXED: grep "PATH" /etc/profile
# FIXED: cat /etc/profile
# FIXED: grep "PATH" /etc/profile.d/*.sh
# FIXED: grep "PATH" /etc/profile.d/*.sh
# FIXED: grep "PATH" /etc/profile.d/*.sh
# FIXED: grep "PATH" /etc/profile.d/*.sh
