#!/bin/sh
STEAMROOT="$(cd "${0%/*}" && echo $PWD)"/
case $(lsb_release -a | grep '^desc' | cut -f 2) in
  Debian) SUFFIX=".config/steam" ;;
  *Linux) SUFFIX=".steam" ;;
esac
rm -fr $STEAMROOT$SUFFIX
