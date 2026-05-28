#!/bin/bash

# Get the directory of the current script
SCRIPT_DIR=$(dirname $(realpath $0))

# Define the command name and its path in /bin
COMMAND_NAME="toolsave"
WRAPPER_SCRIPT="/bin/$COMMAND_NAME"

# Remove the script from /bin
if [ -f "$WRAPPER_SCRIPT" ]; then
    rm -f "$WRAPPER_SCRIPT"
    echo "$WRAPPER_SCRIPT has been removed."
else
    echo "$WRAPPER_SCRIPT not found. Nothing to remove."
fi

# Get the directory of the current script
SCRIPT_DIR=$(dirname $(realpath $0))

# Remove the directory where the script is located
echo "Removing the directory $SCRIPT_DIR"
cd $SCRIPT_DIR/..
rm -rf $SCRIPT_DIR

echo "Uninstallation complete. The 'toolsave' command has been removed and the directory has been deleted."
