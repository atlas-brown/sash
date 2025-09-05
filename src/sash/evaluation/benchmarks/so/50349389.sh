#!/bin/bash
#ps -aux | grep abcd > /home/test1.txt
var= grep -o -i abcd /home/test1.txt | wc -l
threshold=15
if [ $var -lt $threshold ]; then
echo "One of the service is down on $HOSTNAME" >mail.txt
mailx -s "Application alert on $HOSTNAME" myname@domain.com <mail.txt
fi
if [ $var -eq $threshold ]; then
echo "All services are up and running fine on $HOSTNAME" >mail.txt
mailx -s "Application alert on $HOSTNAME" myname@domain.com <mail.txt
fi
exit;

