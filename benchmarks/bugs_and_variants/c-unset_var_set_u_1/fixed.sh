#!/bin/sh

# Note(TheJulia): We should proceed with attempting to collect information
# even if a command fails, and as such set -e should not be present.
set -ux
#set -o pipefail # no pipelines are used in this script, so this has no effect

# Note(TheJulia): If there is a workspace variable, we want to utilize that as
# the preference of where to put logs
SCRIPT_HOME="$(cd "$(dirname "$0")" && pwd)"
LOG_LOCATION="${WORKSPACE:-${SCRIPT_HOME}/..}/logs"

echo "Making logs directory and collecting logs."
[ -d ${LOG_LOCATION} ] || mkdir -p ${LOG_LOCATION}

if [ -z "${TEST_VM_NODE_NAMES+x}" ]; then
    sudo cp /var/log/libvirt/baremetal_logs/testvm[[:digit:]]_console.log ${LOG_LOCATION}
    sudo chown $USER ${LOG_LOCATION}/testvm[[:digit:]]_console.log
    sudo chmod o+r ${LOG_LOCATION}/testvm[[:digit:]]_console.log
else
    for TEST_VM_NODE_NAME in ${TEST_VM_NODE_NAMES}; do
        sudo cp /var/log/libvirt/baremetal_logs/${TEST_VM_NODE_NAME}_console.log ${LOG_LOCATION}
        sudo chown $USER ${LOG_LOCATION}/${TEST_VM_NODE_NAME}_console.log
        sudo chmod o+r ${LOG_LOCATION}/${TEST_VM_NODE_NAME}_console.log
    done
fi
dmesg > ${LOG_LOCATION}/dmesg.log 2>&1
# NOTE(TheJulia): Netstat exits with error code 5 when --version is used.
sudo netstat -apn > ${LOG_LOCATION}/netstat.log 2>&1
if $(iptables --version >/dev/null 2>&1); then
    sudo iptables -L -n -v > ${LOG_LOCATION}/iptables.log 2>&1
fi
if $(ip link >/dev/null 2>&1); then
    ip -s link > ${LOG_LOCATION}/interface_counters.log 2>&1
fi
if $(journalctl --version >/dev/null 2>&1); then
    sudo journalctl -u ironic-api > ${LOG_LOCATION}/ironic-api.log 2>&1
    sudo journalctl -u ironic-conductor > ${LOG_LOCATION}/ironic-conductor.log 2>&1
else
   sudo cp /var/log/upstart/ironic-api.log ${LOG_LOCATION}/
   sudo cp /var/log/upstart/ironic-conductor.log ${LOG_LOCATION}/
fi
sudo chown $USER ${LOG_LOCATION}/ironic-api.log
sudo chown $USER ${LOG_LOCATION}/ironic-conductor.log
# In CI scenarios, we want other users to be able to read the logs.
sudo chmod o+r ${LOG_LOCATION}/ironic-api.log
sudo chmod o+r ${LOG_LOCATION}/ironic-conductor.log
