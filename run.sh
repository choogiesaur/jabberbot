#!/bin/bash

INSTALLDIR=/opt/jabberbot
VENVDIR=$INSTALLDIR/env

cd $INSTALLDIR
source $VENVDIR/bin/activate
python $INSTALLDIR/join_muc.py
