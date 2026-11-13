#!/usr/bin/env bash
set -u

DIR="$(cd "$(dirname "$0")" && pwd)"
RUN="bash $DIR/one_run.sh"
P="${1:-n38}"

step() { echo "$(date +%H:%M:%S) $*"; }

for n in 100 200 300 400; do
  step "join $n"
  STUDENTS=$n $RUN lesson new "$P-join-$n"
done

step "lottery 300"
STUDENTS=300 ORDER_MODE=lottery $RUN lesson new "$P-lottery-300"
step "presence 300"
STUDENTS=300 PRESENCE=1 $RUN lesson new "$P-presence-300"

step "3 sessions x 100"
SESSIONS=3 STUDENTS=100 $RUN lesson new "$P-multi-3x100"
step "6 sessions x 100"
SESSIONS=6 STUDENTS=100 $RUN lesson new "$P-multi-6x100"
step "10 sessions x 60"
SESSIONS=10 STUDENTS=60 $RUN lesson new "$P-multi-10x60"

for n in 1 2 3; do
  step "salvo base $n"
  $RUN salvo base "$P-salvo-base-$n"
  step "salvo new $n"
  $RUN salvo new "$P-salvo-new-$n"
done
for n in 1 2 3; do
  step "ssesalvo new $n"
  $RUN ssesalvo new "$P-ssesalvo-new-$n"
done

step "sse lottery"
STUDENTS=200 SESSION_OPTIONS='{"order_mode":"lottery"}' $RUN ssemode new "$P-sse-lottery"
step "sse presence"
STUDENTS=200 PRESENCE=1 $RUN ssemode new "$P-sse-presence"
step "finished"
