#!bin/bash
wordcount=0
numbercount=0
while read line; dp
  for word in $line; do
    $wordcount = $wordcount + 1
    echo word >> /words/wordsfile.txt
  done
  for number in $line; do
    $numbercount = $numbercount + 1
    echo number >> /numbers/numbersfile.txt
  done
  echo $wordcount " WORDS, " $numberscount " NUMBERS"
done

