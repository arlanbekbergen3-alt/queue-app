#!/usr/bin/env bash
cd "$(dirname "$0")"
OUT=results/series
date -Iseconds > $OUT/series.started
for scenario in salvo peak; do
  for n in 1 2 3; do
    bash one_run.sh $scenario base $scenario-base-$n > /dev/null 2>&1
    bash one_run.sh $scenario new $scenario-new-$n > /dev/null 2>&1
  done
done
for scenario in ssesalvo sse; do
  for n in 1 2 3; do
    bash one_run.sh $scenario new $scenario-new-$n > /dev/null 2>&1
  done
done
date -Iseconds > $OUT/series.finished
