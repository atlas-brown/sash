#!/bin/sh

DateForFileName=`date +%Y-%m-%d-%H-%M-%S`
DateTimeForLog=$(date +"%m/%d/%Y %l:%M %p")
StdOutPutlogFile='/tmp/Suganya/LofFileCheck'
StdErrorLogFile='/tmp/Suganya/LofFileCheckError'
ScriptName=$(basename $0 | cut -d'.' -f1)
#function to capture common error logs with timestamp
OutputLog()
 {
  read IN
  echo $DateTimeForLog-$ScriptName-"Information"-$IN >> $StdOutPutlogFile
 }
errorLog()
{
 read IN
 echo "error"
 echo $DateTimeForLog-$ScriptName-"Error"-$IN >> $StdErrorLogFile
 }
Customoutput()
 {
 echo $DateTimeForLog-$ScriptName-"Information"-$1 >> $StdOutPutlogFile
 }
#######set of commands#########
{
echo 'started'
ls -la
cd /tmp/kjhdakdha
ls -la
} 2> errorLog 1> OutputLog # bug here (1, 2): redirect stdout to function, redirect stderr to function
