#!/bin/sh

valid_names=a|b|c|d|e
printf "Enter name to check: "
while :
do
  read NAME
  case $NAME in
    $valid_names)
      break
      ;;
    *)
      printf "Valid names are $valid_names, enter a valid name: "
  esac
done

