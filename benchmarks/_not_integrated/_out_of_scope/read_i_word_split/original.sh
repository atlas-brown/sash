#!/bin/sh

state="CA"
city="Los Angeles"
while true; do
    read -e -p "State: " -i $state state
    read -e -p "City: " -i $city city # city will word split
    # and turn into 'read -e -p "City: " -i Los Angeles city'
    # echo $Angeles --> "Los"
    # echo $city --> unbound
    echo "Your state: $state"
    echo "Your city: $city"
done
