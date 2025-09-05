unset n
while read -r user work codename; do
  echo $user $work $codename
  : $[n++]
done <connectedclients.now
sed "1 $n d" connectedclients.now

