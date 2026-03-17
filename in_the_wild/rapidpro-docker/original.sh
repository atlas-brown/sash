#!/bin/bash

HOSTNAME=$(grep HOSTNAME .env | awk -F '=' '{printf $2}')
RP_CONTAINERS=$(docker container ls | grep ${HOSTNAME} | awk '{printf $2"\n" }' | sort | uniq | awk -F ':' '{print $1":"$2" "}')
if [[ $RP_CONTAINERS == "" ]]; then
  echo "There are no Rapidpro containers with hostname: ${HOSTNAME}"
  exit 1
fi

echo "Stopping Rapidpro docker containers..."
if ! docker-compose down; then
  echo "Failed docker-compose down"
fi

echo "Removing Rapidpro docker images..."
docker rmi $(docker images -q -f dangling=true) &> /dev/null
yes | docker system prune &> /dev/null

echo "Removing database volume..."
rm -rf $(pwd)/data

echo "Removing Nginx locations..."
rm /etc/nginx/upstream/${HOSTNAME}.conf
service nginx restart
