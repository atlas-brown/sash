#!/bin/sh

Show_Error() {
        echo -e "\033[31m ERROR \033[0m $1"
}

if [ "$1" = "-help" ]
then
    echo "<input> - input file name or input URL"
else


for i in "$@"
do
case $i in
    -l=*|--ddloc=*)
    DDLOC="${i#*=}"
    shift # past argument=value
    ;;
    -l|--ddloc)
    shift # past argument
    DDLOC="$1"
    shift # past value
    ;;
    *)
          # unknown option
    ;;
esac
done

if [ -z "$DDLOC" ]
then
       Show_Error 'File is missing. Use -l or --ddloc.'
else
echo "Location::: $DDLOC"
fi
fi

