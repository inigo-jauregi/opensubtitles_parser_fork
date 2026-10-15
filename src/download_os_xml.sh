#!/bin/bash
set -e
# Downloads all required ${raw} OpenSubtitles data
TGTS=(
    "de"
    "fr"
    "ru"
)

SRC=en
raw=raw_os/OpenSubtitles

for TGT in "${TGTS[@]}"; do

  lgs=($SRC $TGT)
  IFS=$'\n' lgs=($(sort <<<"${lgs[*]}"))
  unset IFS
  pairname="${lgs[0]}-${lgs[1]}"
  echo $pairname

  mkdir -p $raw/$pairname $raw/xml
  for lang in $TGT; do
    if [ ! -d $raw/xml/$lang ]; then
      if [ ! -f $raw/xml/$lang.zip ]; then
        wget "https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/xml/$lang.zip" -O $raw/xml/$lang.zip
      fi
      unzip -n $raw/xml/$lang.zip -d $raw && rm $raw/xml/$lang.zip
    fi
  done

  if [ ! -f $raw/"$pairname"/"$pairname".xml.gz ]; then
    wget "https://object.pouta.csc.fi/OPUS-OpenSubtitles/v2024/xml/$pairname.xml.gz" -O $raw/$pairname/$pairname.xml.gz
    zcat $raw/$pairname/$pairname.xml.gz > $raw/$pairname/$pairname.xml
    rm $raw/$pairname/$pairname.xml.gz
  fi
done
