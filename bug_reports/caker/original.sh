#!/bin/bash

# helper script to build and run a signed caked binary
# usage: ./scripts/run-signed.sh run sonoma-base
set -e

pushd "$(dirname ${BASH_SOURCE[0]})/.." >/dev/null
CURDIR=${PWD}
PKGDIR=${CURDIR}/dist/Caker.app
popd > /dev/null

BUILDDIR=${CURDIR}/.build/debug
RESOURCESDIR=${CURDIR}/Caker/Caker/Content
ASSETS=${BUILDDIR}/assets

sudo rm -rf ${CURDIR}/.build ${CURDIR}/*.o ${CURDIR}/*.d ${CURDIR}/*.swiftdeps ${CURDIR}/*.swiftdeps~

/usr/bin/swift build

source ${CURDIR}/Scripts/build.inc.sh
