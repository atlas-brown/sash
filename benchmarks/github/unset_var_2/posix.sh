#!/bin/sh

FILE="../patches/${1}.patch"

cd vscode || { echo "'vscode' dir not found"; exit 1; }

git add .
git reset -q --hard HEAD

if [ -f "${file}" ]; then # bug here: file is unset
  git apply --reject "${FILE}"
fi

# read -p "Press any key when the conflict have been resolved..." -n1 -s
read REPLY # the read is used only to pause the script

git diff -U1 > "${FILE}"

cd ..

echo "The patch has been generated."
