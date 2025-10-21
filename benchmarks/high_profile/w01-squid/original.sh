#!/bin/sh

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
