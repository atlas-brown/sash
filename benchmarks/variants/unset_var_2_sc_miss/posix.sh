#!/bin/sh

cd vscode || { echo "'vscode' dir not found"; exit 1; }

git add .
git reset -q --hard HEAD

if [ -f "${FILE}" ]; then # bug here: FILE is unset (unbound)
  git apply --reject "${FILE}"
fi

# read -p "Press any key when the conflict have been resolved..." -n1 -s
read REPLY # the read is used only to pause the script

git diff -U1 > "${FILE}"

cd ..

echo "The patch has been generated."
