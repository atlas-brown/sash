#! /bin/sh

# I've denotated all the buggy lines with the string "bug here", so you can search for it to find the bugs we care about
# Ideally scroll manually and read all of my comments



# 1. https://stackoverflow.com/questions/48195715/sh-script-to-replace-text-in-multiple-files
# See comments below for ShellCheck info

# There's a bug here not mentioned in the question: loop will only loop once,
# because $DIR does not end with /* (which would expand and create a list to iterate over)

# Warning about looping only once in all possible executions sounds reasonable to me

# check for bug 1: loop iteratee expands to <= 1 word [for now expansion = variable lookup]
# check for bug 2: useless test [syntactic]

OLD="/net/origin/devdata1/slin"
NEW="/toolscommon/test/HATS"
DIR="/home/AutoTest"
for f in $DIR; do # bug 1: should have been $DIR/* (see above) (ShellCheck does not detect this (in this case))
  cp $f $f.bak
  sed 's+$OLD+$NEW+g' $f.bak > $f # bug 1: single-quoted sed pattern (ShellCheck detects this)
  [ -f "$f" ] # bug 2: missing '&&' between 'test' and 'rm' (heuristic: useless code, ShellCheck doesn't detect this)
  rm -f $f.bak
done

# ----------------------------------------

# 2. https://stackoverflow.com/questions/49043790/customized-function-for-error-logs-scripting
# ShellCheck does not detect this

# check: function name used in redirection [could be approximated syntactically]

DateForFileName=`date +%Y-%m-%d-%H-%M-%S`
DateTimeForLog=$(date +"%m/%d/%Y %l:%M %p")
StdOutPutlogFile='/tmp/Suganya/LofFileCheck'
StdErrorLogFile='/tmp/Suganya/LofFileCheckError'
ScriptName=$(basename $0 | cut -d'.' -f1)

#function to capture common error logs with timestamp
OutputLog() {
  read IN
  echo $DateTimeForLog-$ScriptName-"Information"-$IN >> $StdOutPutlogFile
}

errorLog() {
  read IN
  echo "error"
  echo $DateTimeForLog-$ScriptName-"Error"-$IN >> $StdErrorLogFile
}

Customoutput() {
  echo $DateTimeForLog-$ScriptName-"Information"-$1 >> $StdOutPutlogFile
}

#######set of commands#########
{
  echo 'started'
  ls -la
  cd /tmp/kjhdakdha
  ls -la
} 2> errorLog 1> OutputLog # bug here: output is redirected to a function

# ----------------------------------------

# 3. https://github.com/ohmyzsh/ohmyzsh/commit/f7bf566555a2c0e87deba5dfb3e344f23f4a51bb
# ShellCheck does not detect this

# This is (believe it or not) the simplest "unset variable" script we have

# Use colors, but only if connected to a terminal, and that terminal
# supports them.
if [ -t 1 ]; then
  RB_RED=$(printf '\033[38;5;196m')
  RB_ORANGE=$(printf '\033[38;5;202m')
  RB_YELLOW=$(printf '\033[38;5;226m')
  RB_GREEN=$(printf '\033[38;5;082m')
  RB_BLUE=$(printf '\033[38;5;021m')
  RB_INDIGO=$(printf '\033[38;5;093m')
  RB_VIOLET=$(printf '\033[38;5;163m')

  RED=$(printf '\033[31m')
  GREEN=$(printf '\033[32m')
  YELLOW=$(printf '\033[33m')
  BLUE=$(printf '\033[34m')
  BOLD=$(printf '\033[1m')
  RESET=$(printf '\033[m')
else
  RB_RED=""
  RB_ORANGE=""
  RB_YELLOW=""
  RB_GREEN=""
  RB_BLUE=""
  RB_INDIGO=""
  RB_VIOLET=""

  RED=""
  GREEN=""
  YELLOW=""
  BLUE=""
  BOLD=""
  RESET=""
fi

cd "$ZSH"

# Set git-config values known to fix git errors
# Line endings (#4069)
git config core.eol lf
git config core.autocrlf false
# zeroPaddedFilemode fsck errors (#4963)
git config fsck.zeroPaddedFilemode ignore
git config fetch.fsck.zeroPaddedFilemode ignore
git config receive.fsck.zeroPaddedFilemode ignore
# autostash on rebase (#7172)
resetAutoStash=$(git config --bool rebase.autoStash 2>&1)
git config rebase.autoStash true

# Update upstream remote to ohmyzsh org
remote=$(git remote -v | awk '/https:\/\/github\.com\/robbyrussell\/oh-my-zsh\.git/{ print $1; exit }')
if [ -n "$remote" ]; then
  git remote set-url "$remote" "https://github.com/ohmyzsh/ohmyzsh.git"
fi

printf "${BLUE}%s${NORMAL}\n" "Updating Oh My Zsh" # bug here: NORMAL is unset
if git pull --rebase --stat origin master
then
  printf '%s         %s__      %s           %s        %s       %s     %s__   %s\n' $RB_RED $RB_ORANGE $RB_YELLOW $RB_GREEN $RB_BLUE $RB_INDIGO $RB_VIOLET $RB_RESET
  printf '%s  ____  %s/ /_    %s ____ ___  %s__  __  %s ____  %s_____%s/ /_  %s\n' $RB_RED $RB_ORANGE $RB_YELLOW $RB_GREEN $RB_BLUE $RB_INDIGO $RB_VIOLET $RB_RESET
  printf '%s / __ \%s/ __ \  %s / __ `__ \%s/ / / / %s /_  / %s/ ___/%s __ \ %s\n' $RB_RED $RB_ORANGE $RB_YELLOW $RB_GREEN $RB_BLUE $RB_INDIGO $RB_VIOLET $RB_RESET
  printf '%s/ /_/ /%s / / / %s / / / / / /%s /_/ / %s   / /_%s(__  )%s / / / %s\n' $RB_RED $RB_ORANGE $RB_YELLOW $RB_GREEN $RB_BLUE $RB_INDIGO $RB_VIOLET $RB_RESET
  printf '%s\____/%s_/ /_/ %s /_/ /_/ /_/%s\__, / %s   /___/%s____/%s_/ /_/  %s\n' $RB_RED $RB_ORANGE $RB_YELLOW $RB_GREEN $RB_BLUE $RB_INDIGO $RB_VIOLET $RB_RESET
  printf '%s    %s        %s           %s /____/ %s       %s     %s          %s\n' $RB_RED $RB_ORANGE $RB_YELLOW $RB_GREEN $RB_BLUE $RB_INDIGO $RB_VIOLET $RB_RESET
  printf "${BLUE}%s\n" "Hooray! Oh My Zsh has been updated and/or is at the current version."
  printf "${BLUE}${BOLD}%s${RESET}\n" "To keep up on the latest news and updates, follow us on twitter: https://twitter.com/ohmyzsh"
  printf "${BLUE}${BOLD}%s${RESET}\n" "Get your Oh My Zsh swag at: https://shop.planetargon.com/collections/oh-my-zsh"
else
  printf "${RED}%s${RESET}\n" 'There was an error updating. Try again later?'
fi

# Unset git-config values set just for the upgrade
case "$resetAutoStash" in
  "") git config --unset rebase.autoStash ;;
  *) git config rebase.autoStash "$resetAutoStash" ;;
esac

# ----------------------------------------

# 4. https://unix.stackexchange.com/questions/560038/while-loop-deletes-all-files-and-becomes-stuck-in-loop
# ShellCheck does not detect this

touch while/151234
touch while/152355
touch while/151694
touch while/153699
touch while/156946
NUMSNAPS=$(ls while | awk '{print $1}' | wc -l)
RETAIN=2

# condition of the loop is constant [compare condition across iterations, 2 unfoldings should be good]

while [ "$RETAIN" -le "$NUMSNAPS" ]; do # bug here: RETAIN is not recalculated so the condition is constant
  OLDEST=$(ls | awk '{print $1}' | head -n 1)
  rm "$OLDEST"
done
