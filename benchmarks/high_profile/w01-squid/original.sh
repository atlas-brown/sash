#!/bin/sh

# https://bugzilla.redhat.com/show_bug.cgi?id=1202858

restart() {
	stop
	RETVAL=$?
	if [ $RETVAL -eq 0 ] ; then
		rm -rf $SQUID_PIDFILE_DIR/* # bug here
		start
	else
		echo "Failure stopping squid or stopping squid took too long. Please check before restarting."
		return 1
        fi
}
