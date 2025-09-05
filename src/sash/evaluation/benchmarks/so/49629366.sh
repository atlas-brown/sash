#!/bin/sh
for sample in /path/*.sorted.bam
do
dir="/pathto/hisat2_output"
dir2="/pathto/folder"
base=`basename $sample '.sorted.bam'`
"stringtie -p 8 -G gencode.v27.primary_assembly.annotation_nochr.gtf -o ${dir2}/stringtie_output/${base}/${base}_GRCh38.gtf -l ${dir2}/stringtie_output/${base}/${base} ${dir}/${base}.sorted.bam; ls ${dir2}/stringtie_output/*/*_GRCh38.gtf > mergelist.txt; stringtie --merge -p 8 -G gencode.v27.primary_assembly.annotation_nochr.gtf -o ${dir2}/stringtie_output/stringtie_merged.gtf mergelist.txt"
done

