#!/bin/bash --posix

SUITE_DIR="$(realpath "$(dirname "$0")")"
export SUITE_DIR

export TIMEFORMAT=%R
cd "$SUITE_DIR" || exit 1

selected_scripts=""

while [ $# -gt 0 ]; do
    case "$1" in
        --small)
            export ENTRIES=3000
            export IN="$SUITE_DIR/inputs/pg-small"
            shift
            ;;
        --min)
            export ENTRIES=1
            export IN="$SUITE_DIR/inputs/pg-min"
            shift
            ;;
        --full)
            export ENTRIES=115916
            export IN="$SUITE_DIR/inputs/pg"
            shift
            ;;
        -s|--scripts)
            shift
            while [ $# -gt 0 ] && [ "$(echo "$1" | cut -c1)" != "-" ]; do
                if [ -z "$selected_scripts" ]; then
                    selected_scripts="$1"
                else
                    selected_scripts="$selected_scripts $1"
                fi
                shift
            done
            ;;
        *)
            shift
            ;;
    esac
done

# Set defaults if not set
if [ -z "$ENTRIES" ]; then
    export ENTRIES=115916
    export IN="$SUITE_DIR/inputs/pg"
fi

KOALA_SHELL=${KOALA_SHELL:-bash}
export BENCHMARK_CATEGORY="nlp"

mkdir -p "outputs"

# Define the script names in a single variable
script_names="syllable_words_1
syllable_words_2
letter_words
bigrams_appear_twice
bigrams
compare_exodus_genesis
count_consonant_seq
count_morphs
count_trigrams
count_vowel_seq
count_words
find_anagrams
merge_upper
sort
sort_words_by_folding
sort_words_by_num_of_syllables
sort_words_by_rhyming
trigram_rec
uppercase_by_token
uppercase_by_type
verses_2om_3om_2instances
vowel_sequencies_gr_1K
words_no_vowels"

mkdir -p "outputs"

export LC_ALL=C

should_run() {
    script_name=$1
    if [ -z "$selected_scripts" ]; then
        return 0
    fi
    for selected in $selected_scripts; do
        if [ "$selected" = "$script_name" ]; then
            return 0
        fi
    done
    return 1
}

# Loop through each script name from the variable
while IFS= read -r script; do
    if should_run "$script"; then
        script_file="./scripts/$script.sh"
        output_dir="./outputs/$script/"

        mkdir -p "$output_dir"

        BENCHMARK_SCRIPT="$(realpath "$script_file")"
        export BENCHMARK_SCRIPT
        echo "$script"
        $KOALA_SHELL "$script_file" "$output_dir"
        echo "$?"
    fi
done <<< "$script_names"