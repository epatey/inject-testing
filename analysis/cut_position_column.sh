#!/bin/bash 

# remove the position column from a pyi-archive_viewer output
# usage: ./cut_position_column.sh main-linux-arm64.txt

cut -d, -f2- $1
