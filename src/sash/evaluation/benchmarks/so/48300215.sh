#!/bin/sh -f

# Create dir structure for testing
rm -rf audience
mkdir audience
mkdir audience/dir1 audience/dir2 audience/dir3
mkdir audience/dir1/ipxact audience/dir2/ipxact audience/dir3/ipxact
touch audience/dir1/ipxact/crr.ya.na.aa.xml
echo "<spirit:name>crr.ya.na.aa</spirit:name>" >   audience/dir1/ipxact/crr.ya.na.aa.xml
touch audience/dir2/ipxact/crr.ya.na.bb.xml
echo "<spirit:name>crr.ya.na.bb</spirit:name>" >   audience/dir2/ipxact/crr.ya.na.bb.xml
touch audience/dir3/ipxact/crr.ya.na.cc.xml
echo "<spirit:name>crr.ya.na.cc</spirit:name>" >   audience/dir3/ipxact/crr.ya.na.cc.xml

# Create a dir for ipxact_drop files if it does not exist
mkdir -p ipxact_drop
rm -rf ipxact_drop/*
cp audience/*/ipxact/*.xml ipxact_drop/

ls ipxact_drop/ > ipxact_drop_files.log

cat ipxact_drop_files.log | \
awk '{ split($0,a,"."); print a[length(a)-1] "." a[length(a)] }' ipxact_drop_files.log > file_names.log

cat ipxact_drop_files.log | \
awk '{ split($0,a,"."); print "mv ipxact_drop/" $0 " ipxact_drop/" a[length(a)-1] "." a[length(a)] }' ipxact_drop_files.log > command.log

chmod +x command.log
./command.log

while read line
  do
    echo ipxact_drop/$line
    initial_name=`grep -m 1 crr ipxact_drop/$line | sed -e 's/<spirit:name>//' | sed -e 's/<\/spirit:name>//' `
    final_name="${line%.*}"
    echo $initial_name
    echo $final_name
    sed -i "s+${initial_name}+${final_name}+" ipxact_drop/$line  
done < file_names.log

echo " ***** SCRIPT RUN FINISHED *****"

