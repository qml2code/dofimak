"""
Flags used to specify what should be included into the docker file. Flags after 'PARENT' are written in order in which commands are added to Dockerfile:
PARENT      include everything from another `docker_spec` file. "Circular parenting" does not crash the code.
FROM        which Docker to start from ("child" supercedes "parent")
APT         packages that should be installed with `apt-get`.
CONDAV      version of CONDA to be installed (currently does not work, perhaps I don't know enough about conda). Entry in the "child" takes priority over the entry in the parent.
PYTHONV     version of python; "child" takes priority.
PIP         packages installed with `pip`. Additional necessary flags can be put with ${special_cond_separator}.
PIPLAST     packages that should be additionally installed with PIP (e.g. qml's setup.py requires numpy to run, producing bug if put into the 'PIP' section).
CONDA       packages that should be installed with conda; provided in '${package name}${special_cond_separator}${conda channel}' format.
PYTHONPATH  python modules installed by copy to PYTHONPATH.
"""
import os
import platform
import shutil
import subprocess
from tempfile import TemporaryDirectory

import click

from .passwd_checked_pip import (
    create_passwd_checked_pip_install,
    passwd_checked_pip_install_scrname,
)
from .specifications import dockerspec_filename, get_base_dockerfile_commands, get_list_wconda

special_cond_separator = ";"

parent_flag = "PARENT"
pythonpath_flag = "PYTHONPATH"
apt_flag = "APT"
from_flag = "FROM"
private_git_flag = "PRIVATE_GIT"
pip_flag = "PIP"
piplast_flag = "PIPLAST"

dependency_specifiers = [
    from_flag,
    apt_flag,
    "CONDAV",
    "PYTHONV",
    private_git_flag,
    "CONDA",
    pip_flag,
    piplast_flag,
    pythonpath_flag,
]

# Command for normal shell operation.
login_shell_command = 'SHELL ["/bin/bash", "--login", "-c"]'


# Command for updating conda.
conda_update_command = "RUN conda update -n base conda"
conda_installation_script_name = "Miniconda3-latest-Linux-x86_64.sh"
internal_installation_files = "/installation_files"
internal_conda_dir = "/opt/conda"
internal_script_storage = "/misc_scripts"

dockerfile_name = "Dockerfile"

linux_safe_removal = "wipe"
macos_safe_removal = "gwipe"


def get_safe_removal_command():
    match platform.system():
        case "Linux":
            return linux_safe_removal
        case "Darwin":
            return macos_safe_removal
        case "Windows":
            return None


class BinaryUnavailable(Exception):
    pass


no_safe_removal_warning = f"""No utility for safe removal found (`{linux_safe_removal}` for Linux, `{macos_safe_removal}` for MacOS), making it impossible to wipe Dockerfile after it had been used. The Dockerfile in question will contain your private information and thus should be removed. If you are aware of the risks use the `--nowipe` flag to run the command without safely removing the Dockerfile."""


def bin_available(exec_name):
    return shutil.which(exec_name) is not None


def check_bin_availability(exec_name, error_line=None):
    if (exec_name is None) or (not bin_available(exec_name)):
        if error_line is None:
            error_line = f"Command not found: {exec_name}"
        raise BinaryUnavailable(error_line)


def check_login_kwargs(all_dependencies, nowipe=False):
    if private_git_flag not in all_dependencies:
        return {}, False
    if not nowipe:
        safe_removal = get_safe_removal_command()
        check_bin_availability(safe_removal, error_line=no_safe_removal_warning)
    print(
        "Please enter your github.com credentials. (**WARNING**: they will appear in the prepared `Dockerfile`, make sure it's wiped afterwards!)"
    )
    login = click.prompt("account name")
    passwd = click.prompt("password", hide_input=True)
    return {"login": login, "passwd": passwd}, True


def conda_installation_lines(temporary_folder):
    """
    If we need to install conda inside a Docker container we add these lines to the Dockerfile script.
    """
    # Solution based on https://fabiorosado.dev/blog/install-conda-in-docker/
    subprocess.run(
        [
            "wget",
            "--quiet",
            "https://repo.anaconda.com/miniconda/" + conda_installation_script_name,
            "-O",
            temporary_folder + "/" + conda_installation_script_name,
        ]
    )
    internal_conda_install = internal_installation_files + "/" + conda_installation_script_name
    output = [
        "COPY "
        + temporary_folder
        + "/"
        + conda_installation_script_name
        + " "
        + internal_conda_install
    ]
    output += [
        "RUN chmod +x " + internal_conda_install,
        "RUN " + internal_conda_install + " -b -p " + internal_conda_dir,
        "ENV PATH=" + internal_conda_dir + "/bin:$PATH",
    ]
    return output


def get_from_dep_lines(dep_list):
    return ["FROM " + dep_list[0]]


def get_apt_dep_lines(dep_list, **kwargs):
    if not dep_list:
        return []
    l = "RUN apt-get install -y"
    for dep in dep_list:
        l += " " + dep
    return ["RUN apt-get update", l]


def get_local_dependencies(docker_name, dockerspec_dirs=None):
    spec_filename = dockerspec_filename(docker_name, dockerspec_dirs=dockerspec_dirs)
    processed_lines = open(spec_filename, "r").readlines()
    output = {}
    for l in processed_lines:
        lspl = l.split()
        flag = lspl[0]
        if not flag:
            continue
        if flag[0] == "#":
            continue
        assert (flag in dependency_specifiers) or (
            flag == parent_flag
        ), f"Dependency flag not found: {flag}"
        if flag not in output:
            output[flag] = []
        output[flag] += lspl[1:]
    return output


def get_conda_dep_lines(dep_list, **kwargs):
    output = []
    for dep in dep_list:
        dep_spl = dep.split(special_cond_separator)
        package_name = dep_spl[0]
        channel_args = ""
        if len(dep_spl) > 1:
            channel_name = dep_spl[1]
            if channel_name:
                channel_args = "-c " + channel_name + " "
        if len(dep_spl) > 2:
            solver_name = dep_spl[2]
            if solver_name:
                channel_args += f"--solver={solver_name} "
        output.append("RUN conda install " + channel_args + package_name)
    return output


def pip_install_line(comp, login=None, passwd=None, **other_kwargs):
    is_private = (login is not None) and (passwd is not None)
    l = "RUN "
    if is_private:
        l += f"python {internal_script_storage}/{passwd_checked_pip_install_scrname}"
    else:
        l += "pip install"
    for c in comp:
        l += " " + c
    if is_private:
        l += f" --login {login} --passwd {passwd}"
    return l


def get_pip_dep_lines(dep_list, **kwargs):
    no_special_flags = []
    wspecial_flags = []
    for dep in dep_list:
        if special_cond_separator in dep:
            wspecial_flags.append(dep)
        else:
            no_special_flags.append(dep)
    lines = []
    if no_special_flags:
        lines.append(pip_install_line(no_special_flags, **kwargs))
    if wspecial_flags:
        for dep in wspecial_flags:
            dep_spl = dep.split(special_cond_separator)
            lines.append(pip_install_line(dep_spl, **kwargs))
    return lines


def get_module_imported_dir(module_name, **kwargs):
    initfile_command = "import " + module_name + "; print(" + module_name + ".__file__)"
    init_file = subprocess.run(
        ["python", "-c", initfile_command], capture_output=True
    ).stdout.decode("utf-8")
    return os.path.dirname(init_file)


def get_pythonpath_dep_lines(dep_list, temp_module_copy_dir):
    output = []
    for dep in dep_list:
        destination = "/extra_modules/" + dep
        output.append("COPY " + temp_module_copy_dir + "/" + dep + " " + destination)
        if not os.path.isfile(get_module_imported_dir(dep) + "/__init__.py"):
            output.append("ENV PYTHONPATH " + destination + ":$PYTHONPATH")
    return output


# TODO does not work for some reason.
def get_conda_version_specification(dep_list):
    return ["RUN conda install anaconda=" + dep_list[0]]


def get_python_version_specification(dep_list):
    return ["RUN conda install python=" + dep_list[0]]


def get_private_git_dep_lines(dummy_arg, temp_dir=".", **kwargs):
    assert len(dummy_arg) == 0
    create_passwd_checked_pip_install(output_dir=temp_dir)
    return [
        f"COPY {temp_dir}/{passwd_checked_pip_install_scrname} {internal_script_storage}/{passwd_checked_pip_install_scrname}",
        pip_install_line(["pexpect"]),
    ]


dependency_line_dict = {
    from_flag: get_from_dep_lines,
    apt_flag: get_apt_dep_lines,
    "CONDA": get_conda_dep_lines,
    pip_flag: get_pip_dep_lines,
    piplast_flag: get_pip_dep_lines,
    pythonpath_flag: get_pythonpath_dep_lines,
    "CONDAV": get_conda_version_specification,
    "PYTHONV": get_python_version_specification,
    private_git_flag: get_private_git_dep_lines,
}


def get_all_dependencies(docker_name, dockerspec_dirs=None):
    cur_imported_id = 0
    dep_dict = {parent_flag: [docker_name]}
    while cur_imported_id != len(dep_dict[parent_flag]):
        to_add = get_local_dependencies(
            dep_dict[parent_flag][cur_imported_id], dockerspec_dirs=dockerspec_dirs
        )
        for dep_type, dep_list in to_add.items():
            if dep_type not in dep_dict:
                dep_dict[dep_type] = []
            for dep in dep_list:
                if dep not in dep_dict[dep_type]:
                    dep_dict[dep_type].append(dep)
        cur_imported_id += 1
    del dep_dict[parent_flag]
    return dep_dict


def contains_git_repos(dependency_list):
    for dep in dependency_list:
        if (len(dep) > 3) and (dep[:3] == "git"):
            return True
    return False


def get_deps(all_dependencies, flag):
    if flag in all_dependencies:
        return all_dependencies[flag]
    else:
        return []


def check_dependency_consistency(all_dependencies, temp_dir=".", nowipe=False):
    kwargs, is_private = check_login_kwargs(all_dependencies, nowipe=nowipe)
    if is_private:
        kwargs["temp_dir"] = temp_dir
    pip_deps = get_deps(all_dependencies, pip_flag)
    piplast_deps = get_deps(all_dependencies, pip_flag)
    if pip_deps or piplast_deps:
        if apt_flag not in all_dependencies:
            all_dependencies[apt_flag] = []
        if (contains_git_repos(pip_deps) or contains_git_repos(piplast_deps)) and (
            "git" not in all_dependencies[apt_flag]
        ):
            all_dependencies[apt_flag].append("git")
    return kwargs, is_private


def get_dockerfile_lines_deps(docker_name, dockerspec_dirs=None, conda_updated=True, nowipe=False):
    # Temporary directory where necessary files will be dumped.
    temp_dir_obj = TemporaryDirectory(dir=".", delete=False)
    temp_dir = os.path.basename(temp_dir_obj.name)
    # Docker-specific dependencies.
    all_dependencies = get_all_dependencies(docker_name, dockerspec_dirs=dockerspec_dirs)
    kwargs, is_private = check_dependency_consistency(
        all_dependencies, temp_dir=temp_dir, nowipe=nowipe
    )
    if from_flag not in all_dependencies:
        raise Exception("Need a base Docker.")
    output = get_from_dep_lines(all_dependencies[from_flag])
    output += get_base_dockerfile_commands(dockerspec_dirs=dockerspec_dirs)

    # Commands run once we set up apt-installable components.
    post_apt_commands = [login_shell_command]
    if all_dependencies[from_flag][0] not in get_list_wconda():
        post_apt_commands += conda_installation_lines(temp_dir)

    if conda_updated:
        post_apt_commands.append(conda_update_command)

    if apt_flag not in all_dependencies:
        output += post_apt_commands

    for dep_spec in dependency_specifiers[1:-1]:
        if dep_spec not in all_dependencies:
            continue
        output += dependency_line_dict[dep_spec](all_dependencies[dep_spec], **kwargs)
        if dep_spec == apt_flag:
            output += post_apt_commands

    if pythonpath_flag in all_dependencies:
        copy_reqs = all_dependencies[pythonpath_flag]
        output += dependency_line_dict[pythonpath_flag](
            all_dependencies[pythonpath_flag], temp_dir
        )
    else:
        copy_reqs = []
        temp_dir = None
    return output, copy_reqs, temp_dir, is_private


def prepare_dockerfile(docker_name, dockerspec_dirs=None, nowipe=False):
    dlines, copy_reqs, temp_dir, private = get_dockerfile_lines_deps(
        docker_name, dockerspec_dirs=dockerspec_dirs, nowipe=nowipe
    )
    output = open(dockerfile_name, "w")
    for l in dlines:
        print(l, file=output)
    output.close()
    if temp_dir is not None:
        for copy_req in copy_reqs:
            subprocess.run(["cp", "-r", get_module_imported_dir(copy_req), temp_dir])
    return temp_dir, private


def attempt_safe_removal(dockerfile_name):
    safe_removal = get_safe_removal_command()
    check_bin_availability(safe_removal)
    subprocess.run([safe_removal, dockerfile_name])


def prepare_image(docker_name, dockerspec_dirs=None, docker_tag=None, nowipe=False):
    check_bin_availability("docker")
    temp_dir, is_private = prepare_dockerfile(
        docker_name, dockerspec_dirs=dockerspec_dirs, nowipe=nowipe
    )
    docker_build_command = ["docker", "image", "build"]
    if docker_tag is None:
        docker_tag = f"{docker_name}:1.0"
    docker_build_command += ["-t", docker_tag]
    docker_build_command.append(".")
    print("CREATING THE DOCKER")
    subprocess.run(docker_build_command)
    print("CLEANING UP.")
    if is_private:
        if nowipe:
            print("WARNING: Dockerfile contains private data, but not wiped!")
        else:
            attempt_safe_removal(dockerfile_name)
    else:
        os.remove(dockerfile_name)
    if temp_dir is not None:
        shutil.rmtree(temp_dir)


@click.command()
@click.argument("docker_name")
@click.option("--tag", default=None)
@click.option("--dockerfile", is_flag=True)
@click.option("--nowipe", is_flag=True)
def main(docker_name, tag, dockerfile, nowipe):
    if dockerfile:
        prepare_dockerfile(docker_name, nowipe=True)
        return
    prepare_image(docker_name, docker_tag=tag, nowipe=nowipe)
