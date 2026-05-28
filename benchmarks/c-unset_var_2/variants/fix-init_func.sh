#!/bin/sh

FILE="../patches/${1}.patch"

# -----
mydef() { # diff: create an indirect variable definition of file=FILE that shellcheck doesn't understand
  echo "$2" > tmp
  read "$1" < tmp
  rm tmp
}
mydef file "$FILE"
# -----

cd vscode || { echo "'vscode' dir not found"; exit 1; }

git add .
git reset -q --hard HEAD

if [ -f "${file}" ]; then # NO bug here: file is set
  git apply --reject "${FILE}"
fi

# read -p "Press any key when the conflict have been resolved..." -n1 -s
read REPLY # the read is used only to pause the script

git diff -U1 > "${FILE}"

cd ..

echo "The patch has been generated."
