#!/bin/sh

gather_data()
{
  # Gather data
  df -lP && df -lP > df-lP.out
  df -lh && df -lh > df-lh.out
  [ -f /proc/swaps ] && cat /proc/swaps > proc_swaps.out
  [ -f /proc/cpuinfo ] && cat /proc/cpuinfo > cpuinfo.out
  which free && free -m > free-m.out
  which prtdiag && prtdiag > prtdiag.out
  [ -f /etc/redhat-release ] && cp /etc/redhat-release .
  [ -f /etc/issue ] && cp /etc/issue .
  which swap && swap -l > swap-l.out
  # etc etc
}

TEMPDIR=$(mktemp -d)/ # bug here: mktemp does not exist on all systems (or might fail), so TEMPDIR may be empty; diff: slash moved to definition
cd "${TEMPDIR}"
gather_data
tar cf /tmp/logs.tar "${TEMPDIR}"
gzip -9 /tmp/logs.tar
cd /
rm -rf "${TEMPDIR}"  # diff: no trailing slash here
