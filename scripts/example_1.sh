#! /usr/bin/env sh

# Name of the app
APP="Actual Budget"

update_script() {
    # Is /opt/actualbudget a directory?
    if [ ! -d /opt/actualbudget ]; then
        msg_error "No ${APP} Installation Found!"
        exit
    fi

    # Is the jq command installed?
    if ! command -v jq >/dev/null 2>&1; then
      echo "Installing jq..."
      apt-get install -y jq >/dev/null 2>&1
      echo "Installed jq!"
    fi

    echo "Updating ${APP}"

    # Read the latest release version from GitHub
    RELEASE=$(curl -s https://api.github.com/repos/actualbudget/actual-server/tags | jq --raw-output '.[0].name')  # Is there really a bug here?
    TEMPD="$(mktemp -d)"  # Create a temporary directory
    cd "${TEMPD}"

    # Download the latest version from GitHub
    wget -q https://codeload.github.com/actualbudget/actual-server/legacy.tar.gz/refs/tags/${RELEASE} -O - | tar -xz

    # Rename the app folder instead of deleting, to be able to restore it if anything goes wrong
    mv /opt/actualbudget /opt/actualbudget_bak

    # Does /opt/actualbudget even exist anymore? This doesn't seem right
    mv actualbudget-actual-server-*/* /opt/actualbudget/

    # More update logic...
    mv /opt/actualbudget_bak/.env /opt/actualbudget
    mv /opt/actualbudget_bak/server-files /opt/actualbudget/server-files
    cd /opt/actualbudget

    # Cleanup
    rm -rf "${TEMPD}"
    rm -rf /opt/actualbudget_bak
}

echo "Calling update script"
update_script
echo "Update complete!"
