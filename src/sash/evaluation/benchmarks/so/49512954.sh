#! /bin/sh
if [ -d "$/root/folder" ];then
   echo "folder file already exist"
else
   echo "Settings in progress" \
&& mkdir folder   \
&& chmod 7777 folder \
&& cd folder \
&& mkdir FolderConnector \
&& chmod 7777 FolderConnector \
&& cd FolderConnector \
&& mkdir ClientInput \
&& mkdir ClientOutput \
&& chmod 7777 ClientInput \
&& chmod 7777 ClientOutput \
&& cat /home/data/RTV/file2 >>/etc/exports \
&& exportfs -r \
&& exportfs \
&& echo "Settings completed successfully."  
fi

