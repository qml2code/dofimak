#!/bin/bash
# NOTE: Running the script is NOT required for running build_docker_image.sh. It was created to demonstrate the intermediate files that are created

rm -f Dockerfile

dofimak --dockerfile qml2-dev_docker
