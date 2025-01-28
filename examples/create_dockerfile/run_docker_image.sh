#!/bin/bash

docker run -v "$(pwd):/rundir" qml2_docker:1.0 python test_qml2_run.py
