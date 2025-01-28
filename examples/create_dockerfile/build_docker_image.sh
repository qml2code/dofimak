#!/bin/bash

rm -f Dockerfile

python make_docker_file.py qml2_docker

docker image build -t qml2_docker:1.0 .
