import sys

from dockerfilemaker import prepare_dockerfile

if len(sys.argv) > 1:
    docker_name = sys.argv[1]
else:
    docker_name = "qml2_docker"

prepare_dockerfile(docker_name, dockerspec_dir="../../dockerfilemaker/specifications")
