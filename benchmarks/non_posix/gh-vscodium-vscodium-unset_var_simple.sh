#!/bin/bash

# https://github.com/VSCodium/vscodium/commit/97eb57c196159bb0bcebc04adf5d5dac0885fb4d

FILE="../patches/${1}.patch"

cd vscode || { echo "'vscode' dir not found"; exit 1; }

git add .
git reset -q --hard HEAD

if [[ -f "${file}" ]]; then # bug here: file is unset
  git apply --reject "${FILE}"
fi

read -p "Press any key when the conflict have been resolved..." -n1 -s

git diff -U1 > "${FILE}"

cd ..

echo "The patch has been generated."