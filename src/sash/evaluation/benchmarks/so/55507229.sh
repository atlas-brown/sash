#!/bin/sh
set -eu

while true
do
  case $1 in
    -h|--help)
      echo "-h"
      exit
      ;;
    --)
      shift
      break
      ;;
    -?*)
      echo "unknown"
      ;;
    *)
      break
  esac

  shift
done

while IFS=, read -r f1 
do
  echo $f1
done <"${1:-/dev/stdin}"

