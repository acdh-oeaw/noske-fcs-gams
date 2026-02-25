#!/bin/bash
#
# Extracts existing verticals and config files from the acdhch/noske-fcs-gams-dse-static docker image
#

if [ ! -d data ] ; then
    mkdir data
fi
docker pull acdhch/noske-fcs-gams-dse-static:latest
docker save acdhch/noske-fcs-gams-dse-static:latest > tmp.tar
for i in `docker image inspect acdhch/noske-fcs-gams-dse-static:latest | grep -A 6 RootFS | grep sha256 | sed -e 's/ *"sha256://' -e 's/".*//'`; do
    tar -xf tmp.tar --transform 's/.*\///' blobs/sha256/$i
    tar -xf $i -C data --transform 's/.*\/registry\///' --wildcards var/lib/manatee/registry/* 2>/dev/null || true
    rm $i
done
rm tmp.tar
