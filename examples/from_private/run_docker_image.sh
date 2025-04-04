#!/bin/bash

scrdir=../create_dockerfile/

docker run -v "$(pwd)/$scrdir:/rundir" qml2-dev_docker:1.0 python test_qml2_run.py
