#!/bin/bash

    #Move victors of 'Seçme-Eleme' to 'Kazananlar'
    cd /home/eurydice/Bulunur\ Bir\ Şeyler/Dosyamsılar/Seçme-Eleme
    echo "Select victors"
    read vct
    for i in $vct; do
            n=1
            mv "Aday $i.png" /home/eurydice/"Bulunur Bir Şeyler"/Dosyamsılar/Kazananlar/"Bahar $n.png" ;
            mv "Aday $i.jpg" /home/eurydice/"Bulunur Bir Şeyler"/Dosyamsılar/Kazananlar/"Bahar $n.jpg" ;
            n=$((n+1))
        done
    #Now let's remove the rest
    rm /home/eurydice/Bulunur\ Bir\ Şeyler/Dosyamsılar/Seçme-Eleme/*
