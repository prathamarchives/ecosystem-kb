#!/bin/sh
set -e
case "$1" in
  worker) exec kb worker ;;
  *) exec "$@" ;;
esac
