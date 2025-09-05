#!/bin/sh
cd $directory/

dat_file=$(find "$directory" -name '*.dat' -exec basename {} \;)    #find *.dat file
chmod 700 $directory/$dat_file  #changing its permission to be copied

cp $directory/$dat_file $second_dir/$dat_file           #copying .dat file

