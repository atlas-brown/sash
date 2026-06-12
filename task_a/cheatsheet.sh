# Variables
W="$1"  # User input
read W # User input (interactive)
Y="$HOME"  # Sensitive paths
Z="/tmp"  # Arbitrary paths


# Conditionals
if [ -z "$X" ]; then  # Emptiness check (-n for non-emptiness)
    :  # No-op
fi

if [ "$X" = "/tmp" ]; then  # Equality check (!= for inequality)
    :
fi

if [ -e "$X" ]; then  # File existence check (-d for directory check)
    :
fi


# Creating files and directories
touch    "$FILE"
mkdir    some/"$DIR"  # Complains if some/ doesn't exist
mkdir -p some/"$DIR"  # Creates some/ if it doesn't exist


# Unknown values (SaSh does not reason about command substitution outputs and arithmetic expansion outputs)
X="$(whatever)"
X="$(( 1 + 1 ))"  # SaSh does not know the output is "2"


# Copying and moving
cp    file nothing_or_dir_or_file
cp -r dir  nothing_or_dir
mv    file nothing_or_dir_or_file
mv -r dir  nothing_or_dir


# Deleting
rm    file
rm -r dir


# Globbing (SaSh can only approximately reason about these: it always treats them as "many files")
*  # Unquoted asterisks are substituted with all files and directories of the current working directory, thus something like 'rm -r *' is very dangerous


# Word splitting
X="a b c"
rm $X  # This will delete a, b and c, not "a b c"
rm $1  # What will this delete? SaSh cannot precisely know


# Loops
for X in a b c; do
    :  # X will be a, b, and c, respectively
done

for X in $1; do
    :  # Impossible to know what X will be, SaSh can only approximate
done
