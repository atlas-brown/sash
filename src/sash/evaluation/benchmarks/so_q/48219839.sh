#!/bin/sh
echo "OK, start pushing the Userdetails to  COUPA now..."
cd /usr/App/ss/outbound/usrdtl/
n=0


      until [ $n -ge 3 ] || [ ! -f /usr/App/ss/outbound/usrdtl/USERS_APPROVERS_*.csv ]
      do 
      if [ -f /usr/App/ss/outbound/usrdtl/USERS_APPROVERS_*.csv ] ;  
      then 
      pushFiles()
      else
      n=$[$n+1]
      sleep 60
      echo " trying " $n "times " 
      fi
      done

pushFiles()
{
echo "File present Now try SSH connection"
while [ $? -eq 0 ];
do
    echo $(date);
     scpg3 -v /usr/App/ss/outbound/usrdtl/USERS_APPROVERS_*.csv <sshHost>:/Incoming/Users/
     if [ $? -eq 0 ]; then
        echo "Successfull" 
        echo $(date);
        echo "Successfull" >> /usr/App/ss/UserApproverDetails.log
        exit 1;
        else
            echo $(date);
            echo "Failed" >> /usr/App/ss/UserApproverDetails.log
            echo "trying again to push file.."
            scpg3 -v /usr/App/sg/outbound/usrdtl/USERS_APPROVERS_*.csv <ssh Host>:/Incoming/Users/
            echo $(date);   
        exit 1;
    fi
done
}

