#!/bin/bash

# backup_inputs.sh
#
# This script mirrors the benchmark input data from the specified base URL.
# It downloads and maintains an up-to-date local copy of all relevant files.
#
# Usage: ./backup_inputs.sh

set -euo pipefail

URL_BASE="https://atlas.cs.brown.edu/data"
TARGET_DIR="./data"

mkdir -p "$TARGET_DIR"

download() {
    local url="$1"
    local rel_path="${url#"$BASE_URL/"}"
    local dest_path="$DEST/$rel_path"

    mkdir -p "$(dirname "$dest_path")"
    if [[ -f "$dest_path" ]]; then
        echo "[SKIP] $rel_path"
    else
        echo "[GET ] $rel_path"
        wget --no-check-certificate "$url" -O "$dest_path"
    fi
}

# -----------------------------
# analytics dataset
# -----------------------------

LOG_ANALYSIS_DIR="$TARGET_DIR/log-analysis"

download "$URL_BASE/log-analysis/routeviews.mrt" "$LOG_ANALYSIS_DIR/routeviews.mrt"

for size in min small full; do
    download "$URL_BASE/log-analysis/ray_tracing_${size}.tar.gz" "$LOG_ANALYSIS_DIR/ray_tracing_${size}.tar.gz"
    download "$URL_BASE/log-analysis/port_scan_${size}.tar.gz" "$LOG_ANALYSIS_DIR/port_scan_${size}.tar.gz"
done

download "$URL_BASE/pcaps.zip" "$TARGET_DIR/pcaps.zip"
download "$URL_BASE/pcaps_large.zip" "$TARGET_DIR/pcaps_large.zip"

download "$URL_BASE/nginx.zip" "$TARGET_DIR/nginx.zip"
download "$URL_BASE/log-analysis/web-server-access-logs.zip" "$LOG_ANALYSIS_DIR/nginx_large.zip"

# -----------------------------
# bio dataset
# -----------------------------

BIO_DIR="$TARGET_DIR/bio"
TERASEQ_DIR="$TARGET_DIR/teraseq"
DATA_DIR="$TARGET_DIR/data"
mkdir -p "$BIO_DIR/large" "$TERASEQ_DIR/full" "$DATA_DIR"

BIO_LARGE_SAMPLES=(
    CHS HG00408
    CHS HG00476
    CHS HG00690
    PJL HG02605
    PJL HG02729
    PJL HG02787
    CEU NA12155
    CEU NA12546
    CEU NA12707
    CEU NA12818
)

for sample in "${BIO_MEDIUM_SAMPLES[@]}"; do
    download "$BASE_URL/bio/large/${sample}.bam"
done

BIO_MEDIUM_SAMPLES=(
    HG00614 HG00421 HG00559
    HG01941 HG01942 HG02491
    NA10852 NA12828 NA12843
    NA18500 NA18924 NA19160
    NA19172 NA19189 NA19236
)

for sample in "${BIO_MEDIUM_SAMPLES[@]}"; do
    download "$BASE_URL/bio/medium/${sample}.bam"
done

TERA_SAMPLES=(
    hsa.dRNASeq.HeLa.polyA.1
    hsa.dRNASeq.HeLa.polyA.REL5.1
    hsa.dRNASeq.HeLa.polyA.PNK.REL5.1
    hsa.dRNASeq.HeLa.polyA.CIP.decap.REL5.long.1
    hsa.dRNASeq.HeLa.polyA.decap.REL5.long.1
    hsa.dRNASeq.HeLa.polyA.REL5.long.1
    hsa.dRNASeq.HeLa.polyA.REL5OH.long.1
)

for s in "${TERA_SAMPLES[@]}"; do
    download "$BASE_URL/teraseq/full/$s/fastq/reads.1.fastq.gz"
done


download "$BASE_URL/teraseq/full/data/SILVA_132_LSURef_tax_silva_trunc.fasta.gz"
download "$BASE_URL/teraseq/full/data/SILVA_132_SSURef_Nr99_tax_silva_trunc.fasta.gz"

download "$BASE_URL/teraseq/full/data/Homo_sapiens.GRCh38.dna_sm.primary_assembly.fa.gz"
download "$BASE_URL/teraseq/full/data/Homo_sapiens.GRCh38.91.gtf.gz"

download "$BASE_URL/teraseq/full/data/Mus_musculus.GRCm38.dna.primary_assembly.fa.gz"
download "$BASE_URL/teraseq/full/data/Mus_musculus.GRCm38.97.gtf.gz"

download "$BASE_URL/teraseq/full/data/hg38-tRNAs.tar.gz"

download "$BASE_URL/teraseq/full/data/atlas.clusters.hg38.2-0.bed.gz"

download "$BASE_URL/teraseq/full/data/UCSC2ensembl.txt"

download "$BASE_URL/teraseq/full/data/epitheloid-carcinoma-cell-line--HelaS3-ENCODE-biol_rep1.CNhs12325.10815-111B5.hg19.ctss.bed.gz"
download "$BASE_URL/teraseq/full/data/epitheloid-carcinoma-cell-line--HelaS3-ENCODE-biol_rep2.CNhs12326.10816-111B6.hg19.ctss.bed.gz"
download "$BASE_URL/teraseq/full/data/epitheloid-carcinoma-cell-line--HelaS3-ENCODE-biol_rep3.CNhs12327.10817-111B7.hg19.ctss.bed.gz"

download "$BASE_URL/teraseq/full/data/hg19ToHg38.over.chain.gz"

for f in \
    Rep1-HeLaS3-NETCAGE-0_5M_CAC \
    Rep1-HeLaS3-NETCAGE-1M_AGT \
    Rep1-HeLaS3-NETCAGE-2M_GCG \
    Rep2-HeLaS3-NETCAGE-0_5M_TAC \
    Rep2-HeLaS3-NETCAGE-1M_ACG \
    Rep2-HeLaS3-NETCAGE-2M_GCT; do
    download "$BASE_URL/teraseq/full/data/NET-CAGE/${f}.ctss.bed.gz"
done

download "$BASE_URL/teraseq/full/data/meth/encodeCcreHela.bed"

download "$BASE_URL/teraseq/TERA-Seq_manuscript/data/SIRV_Set1_Sequences_170612a.tar"

download "$BASE_URL/teraseq/TERA-Seq_manuscript/adapter/data/trimming.tsv"

# -----------------------------
# covid dataset
# -----------------------------
download "${BASE_URL}/covid-mts/in_small.csv.gz"
download "${BASE_URL}/covid-mts/in_full.csv.gz"

# -----------------------------
# file-mod dataset
# -----------------------------
download "${BASE_URL}/pcaps.zip"
download "${BASE_URL}/pcaps_large.zip"

# WAV inputs (one master archive)
download "${BASE_URL}/wav.zip"

# JPG inputs – size‑specific archives
download "${BASE_URL}/small/jpg.zip"
download "${BASE_URL}/full/jpg.zip"

# -----------------------------
# inference dataset
# -----------------------------

# 1) files hosted on atlas.cs.brown.edu
download "${BASE_URL}/models.zip"
download "${BASE_URL}/pl-06-P_F-A_N-20250401T083751Z-001.zip"   # small set
download "${BASE_URL}/pl-01-PFW-20250401T083800Z-001.zip"       # full set

GROUP_URL="https://atlas-group.cs.brown.edu/data"

download_grp() {                               # helper for the second host
    local url="$1"
    local rel_path="${url#"$GROUP_URL/"}"      # strip the new base
    local dest_path="$DEST/$rel_path"
    mkdir -p "$(dirname "$dest_path")"
    if [[ -f "$dest_path" ]]; then
        echo "[SKIP] $rel_path"
    else
        echo "[GET ] $rel_path"
        wget --no-check-certificate "$url" -O "$dest_path"
    fi
}

download_grp "${GROUP_URL}/small/jpg.zip"
download_grp "${GROUP_URL}/llm/playlist_small.tar.gz"
download_grp "${GROUP_URL}/full/jpg.zip"
download_grp "${GROUP_URL}/llm/playlist_full.tar.gz"

# -----------------------------
# nlp dataset
# -----------------------------

# Book & link files
download "${BASE_URL}/gutenberg/books.txt"
download "${BASE_URL}/gutenberg/8/0/0/8001/8001.txt"          # genesis
download "${BASE_URL}/gutenberg/3/3/4/2/33420/33420-0.txt"    # exodus

# Gutenberg project archives used by the script
download "${BASE_URL}/nlp/pg-small.tar.gz"
download "${BASE_URL}/nlp/pg.tar.gz"

# -----------------------------
# oneliners dataset
# -----------------------------

# Common dummy source files
download "${BASE_URL}/dummy/1M.txt"
download "${BASE_URL}/dummy/dict.txt"
download "${BASE_URL}/dummy/all_cmds.txt"

# Size‑specific chess archives
download "${BASE_URL}/oneliners/chessdata_small.tar.gz"
download "${BASE_URL}/oneliners/chessdata_min.tar.gz"
download "${BASE_URL}/oneliners/chessdata.tar.gz"

# -----------------------------
# pkg dataset
# -----------------------------

download "${BASE_URL}/aurpkg/packages"
download "${BASE_URL}/prog-inf/node_modules.tar.gz"

# -----------------------------
# repl dataset (Chromium repo snapshot)
# -----------------------------

download "${BASE_URL}/git/chromium.tar.gz"

# -----------------------------
# weather dataset
# -----------------------------

download "${BASE_URL}/max-temp/temperatures.small.tar.gz"
download "${BASE_URL}/max-temp/temperatures.full.tar.gz"

# -----------------------------
# web‑index dataset
# -----------------------------

# Common stop‑word list
download "${BASE_URL}/web-index/stopwords.txt"

# Min / “input_small” variant
download "${BASE_URL}/wikipedia/input_small/articles.tar.gz"
download "${BASE_URL}/wikipedia/input_small/index.txt"

# Small (1 GB) variant
download "${BASE_URL}/wikipedia/wikipedia1g.tar.gz"
download "${BASE_URL}/wikipedia/index1g.txt"

# Full (10 GB) variant
download "${BASE_URL}/wikipedia/wikipedia10g.tar.gz"
download "${BASE_URL}/wikipedia/index10g.txt"