"""
For importing currently available Dockerfile specifications.
"""

import glob
import os

specdir_env_name = "DOCKFMAKER_SPECS"
# Filename suffix for files containing format specifications.
dockerspec_filename_suffix = ".docker_spec"
containers_wconda_filename = "containers_wconda.txt"

special_cond_separator = ";"

# Where most basic commands for Docker are found.
base_dockerfile_cmd_file = "base_dockerfile_commands.txt"


def available_specification_dirs(dockerspec_dirs=None):
    """
    Directories where Dockerfile specifications can be found.
    """
    if dockerspec_dirs is not None:
        return dockerspec_dirs
    output_dirs = [os.getcwd()]
    if specdir_env_name in os.environ:
        output_dirs += os.environ[specdir_env_name].split(":")
    output_dirs.append(os.path.dirname(__file__))
    return output_dirs


def available_dockers(dockerspec_dirs=None):
    """
    Docker specification files available by default.
    """
    output = []
    for d in available_specification_dirs(dockerspec_dirs):
        output += [
            spec_file[:-4] for spec_file in glob.glob(d + "/*" + dockerspec_filename_suffix)
        ]


def find_spec_file(filename, dockerspec_dirs=None, find_all=False):
    if find_all:
        output = []
    for dockspec_dir in available_specification_dirs(dockerspec_dirs):
        cur_name = dockspec_dir + "/" + filename
        if os.path.isfile(cur_name):
            if find_all:
                output.append(cur_name)
            else:
                return cur_name
    if find_all:
        return output
    raise Exception(f"File not found: {filename}")


def dockerspec_filename(docker_name, dockerspec_dirs=None):
    """
    Docker specification file corresponding to a Docker file to be created.
    """
    return find_spec_file(docker_name + dockerspec_filename_suffix, dockerspec_dirs)


def get_list_wconda(dockerspec_dirs=None):
    output = []
    for total_filename in find_spec_file(
        containers_wconda_filename, dockerspec_dirs, find_all=True
    ):
        output += [l.strip() for l in open(total_filename, "r").readlines()]
    return output


def get_base_dockerfile_commands(dockerspec_dirs=None):
    total_filename = find_spec_file(base_dockerfile_cmd_file, dockerspec_dirs)
    return [l.strip() for l in open(total_filename, "r").readlines()]
