#!/bin/bash
# https://stackoverflow.com/questions/65666474/how-can-i-stop-my-script-to-overwrite-existing-files
for i in $vct; do
            n=1
            mv "Aday $i.png" /home/eurydice/"Bulunur Bir Şeyler"/Dosyamsılar/Kazananlar/"Bahar $n.png" ; # n is redefined every iteration
            mv "Aday $i.jpg" /home/eurydice/"Bulunur Bir Şeyler"/Dosyamsılar/Kazananlar/"Bahar $n.jpg" ; # n is redefined every iteration
            n=$((n+1))
        done
