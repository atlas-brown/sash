#!/bin/sh
arch=$(uname -m)
OS=$(uname)
link=""
case $OS in
  "Linux")
    case $arch in 
      "x86_64" | "amd64")
      echo "installing x86 binary"
       link="https://github.com/Z3Prover/z3/releases/download/z3-4.14.1/z3-4.14.1-x64-glibc-2.35.zip"
       ;;
      "aarch64" | "arm64")
      echo "installing arm64 binary"
      link="https://github.com/Z3Prover/z3/releases/download/z3-4.14.1/z3-4.14.1-arm64-glibc-2.34.zip"
      ;;
      *)
      echo "Don't know what to do for Linux arch $arch" ; exit 1 
      ;;
    esac 
    ;;
    *)
    echo "got unknown os $OS"
    exit 1
    ;;
esac

mkdir z3install
cd z3install || exit 1
rm -fr ./*
wget -O z3zip $link
unzip z3zip 
cd z3-* || exit 1
cp bin/* /usr/local/bin
cp include/* /usr/local/include

