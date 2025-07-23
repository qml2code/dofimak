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
tmp_repo_src = "/tmp_repo_src"

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


def check_login_kwargs(all_dependencies, nowipe=False, dockerfolder=False):
    if (private_git_flag not in all_dependencies) or dockerfolder:
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


def combined_run(command_list):
    return ["RUN " + " &&\\\n    ".join(command_list)]


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
    output += combined_run(
        [
            f"chmod +x {internal_conda_install}",
            internal_conda_install + " -b -p " + internal_conda_dir,
        ]
    ) + [
        "ENV PATH=" + internal_conda_dir + "/bin:$PATH",
    ]
    return output


def get_from_dep_lines(dep_list):
    return ["FROM " + dep_list[0]]


def get_apt_dep_lines(dep_list, **kwargs):
    if not dep_list:
        return []
    l = "apt-get install -y"
    for dep in dep_list:
        l += " " + dep
    return combined_run(["apt-get update", l])


def get_local_dependencies(docker_name, dockerspec_dirs=None, cwd=None):
    spec_filename = dockerspec_filename(docker_name, dockerspec_dirs=dockerspec_dirs, cwd=cwd)
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


def divide_conda_dep_str(dep_str: str):
    dep_spl = dep_str.split(special_cond_separator)
    package_name = dep_spl[0]
    if len(dep_spl) > 1:
        channel_name = dep_spl[1]
    else:
        channel_name = None
    if len(dep_spl) > 2:
        solver_name = dep_spl[2]
    else:
        solver_name = None
    return package_name, channel_name, solver_name


def get_conda_separate_package_installation(dep_list, no_conda_tos=False):
    if no_conda_tos:
        commands = []
    else:
        commands = ["conda tos accept"]
        added_channels = []
    for dep in dep_list:
        package_name, channel_name, solver_name = divide_conda_dep_str(dep)
        channel_args = ""
        if channel_name is not None:
            channel_args = "-c " + channel_name + " "
            if (not no_conda_tos) and (channel_name not in added_channels):
                added_channels.append(channel_name)
                # make sure TOS are accepted for newly added channels
                commands += [
                    "conda config --append channels " + channel_name,
                    "conda tos accept --override-channels --channel " + channel_name,
                ]
        if solver_name is not None:
            channel_args += f"--solver={solver_name} "
        commands.append("conda install " + channel_args + package_name)
    return commands


def get_conda_package_installation(dep_list, no_conda_tos=False):
    installation_groups = {}
    if not no_conda_tos:
        added_channels = []
    for dep in dep_list:
        package_name, channel_name, solver_name = divide_conda_dep_str(dep)
        installation_group = (channel_name, solver_name)
        if installation_group in installation_groups:
            installation_groups[installation_group].append(package_name)
        else:
            installation_groups[installation_group] = [package_name]
        if (
            (not no_conda_tos)
            and (channel_name is not None)
            and (channel_name not in added_channels)
        ):
            added_channels.append(channel_name)
    if no_conda_tos:
        commands = []
    else:
        commands = [
            "conda config --append channels " + " ".join(added_channels),
            "conda tos accept",
        ]
    for (channel_name, solver_name), package_names in installation_groups.items():
        command = "conda install"
        if channel_name is not None:
            command += f" -c {channel_name}"
        if solver_name is not None:
            command += f" --solver={solver_name}"
        commands.append(f"{command} " + " ".join(package_names))
    return commands


def get_conda_dep_lines(dep_list, no_conda_tos=False, separate_conda_deps=False, **kwargs):
    if separate_conda_deps:
        commands = get_conda_separate_package_installation(dep_list, no_conda_tos=no_conda_tos)
    else:
        commands = get_conda_package_installation(dep_list, no_conda_tos=no_conda_tos)
    return combined_run(commands)


def extract_github_info(dep):
    if "git+" in dep:
        cut_dep = dep.split("git+")[1]
    else:
        cut_dep = dep
    if "@" in cut_dep:
        url, branch = cut_dep.split("@")
    else:
        url = cut_dep
        branch = None
    repo_name = url.split("/")[-1].split(".")[0]

    return repo_name, url, branch


def pip_install_line(comp, login=None, passwd=None, **other_kwargs):
    is_private = (login is not None) and (passwd is not None)
    if is_private:
        l = f"python {internal_script_storage}/{passwd_checked_pip_install_scrname}"
    else:
        l = "pip install"
    for c in comp:
        l += " " + c
    if is_private:
        l += f" --login {login} --passwd {passwd}"
    return l


def is_github_link(dep):
    return "git+https" in dep


def extract_pyproject_github_deps(cloned_dir):
    pyproject_toml_filename = cloned_dir + "/pyproject.toml"
    if not os.path.isfile(pyproject_toml_filename):
        return []
    subprocess.run(["pyprojectsort", pyproject_toml_filename])
    new_lines = []
    new_deps = []
    reading_dependencies = False
    for line in open(pyproject_toml_filename, "r").readlines():
        if reading_dependencies:
            dep = line.strip().split(",")[0]
            if is_github_link(dep):
                new_deps.append(dep[1:-1])
                continue
            if dep == "]":
                reading_dependencies = False
        else:
            if line == "dependencies = [\n":
                reading_dependencies = True
        new_lines.append(line)
    with open(pyproject_toml_filename, "w") as f:
        print("".join(new_lines), file=f)
    return new_deps


def github_link_from_url(repo_url):
    # TODO: add more options here as needed.
    if "https://github.com" in repo_url:
        specifier = repo_url.split(".com")[1]
        return "git@github.com:" + specifier
    else:
        return repo_url


def get_clone_command(repo_url, cloned_dir, branch=None):
    clone_command = ["git", "clone"]
    if branch is not None:
        clone_command += ["--branch", branch]
    return clone_command + [repo_url, cloned_dir]


def clone_repo(repo_url, cloned_dir, branch=None):
    repo_git_url = github_link_from_url(repo_url)
    for url in [repo_git_url, repo_url]:
        print("Attempting to clone:", url)
        command = get_clone_command(url, cloned_dir, branch=branch)
        completed_process = subprocess.run(command)
        if completed_process.returncode == 0:
            return
    raise Exception(
        f"FAILED CLONING THE REPO: {repo_url}\nCONSIDER CHECKING YOUR ACCESS PRIVILEGES!"
    )


def process_pip_github_dep(dep, dep_list, git_repo_branches, temp_dir, cloned_repos):
    repo_name, url, branch = extract_github_info(dep)
    cloned_dir = temp_dir + "/" + repo_name
    if url in git_repo_branches:
        assert git_repo_branches[url] == branch, "Branch mismatch in a repo dependency!"
        return
    else:
        git_repo_branches[url] = branch
    clone_repo(url, cloned_dir, branch=branch)
    new_repo_deps = extract_pyproject_github_deps(cloned_dir)
    for new_repo_dep in new_repo_deps:
        dep_list.append(new_repo_dep)
    cloned_repos.append(repo_name)
    return f"{tmp_repo_src}/{repo_name}"


def pip_predownload_adjusted_list(dep_list, temp_dir):
    assert temp_dir is not None
    dep_id = 0
    revised_dep_list = []
    git_repo_branches = {}
    cloned_repos = []
    while dep_id != len(dep_list):
        dep = dep_list[dep_id]
        if is_github_link(dep):
            dep = process_pip_github_dep(dep, dep_list, git_repo_branches, temp_dir, cloned_repos)
        if dep is not None:
            revised_dep_list.append(dep)
        dep_id += 1
    return revised_dep_list, cloned_repos


def get_pip_dep_lines(dep_list, dockerfolder=False, temp_dir=None, **kwargs):
    if dockerfolder:
        dep_list, cloned_repos = pip_predownload_adjusted_list(dep_list, temp_dir)
    no_special_flags = []
    wspecial_flags = []
    for dep in dep_list:
        if special_cond_separator in dep:
            wspecial_flags.append(dep)
        else:
            no_special_flags.append(dep)
    lines = []
    if no_special_flags:
        lines.append(pip_install_line(no_special_flags, dockerfolder=dockerfolder, **kwargs))
    if wspecial_flags:
        for dep in wspecial_flags:
            dep_spl = dep.split(special_cond_separator)
            lines.append(pip_install_line(dep_spl, **kwargs))
    if dockerfolder:
        lines += [f"rm -Rf {tmp_repo_src}"]
        copy_commands = [
            f"COPY {temp_dir}/{cloned_repo} {tmp_repo_src}/{cloned_repo}"
            for cloned_repo in cloned_repos
        ]

    commands = combined_run(lines)
    if dockerfolder:
        commands = copy_commands + commands
    return commands


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
def get_conda_version_specification(dep_list, **kwargs):
    return ["RUN conda install anaconda=" + dep_list[0]]


def get_python_version_specification(dep_list, no_conda_tos=False, **kwargs):
    commands = ["conda install python=" + dep_list[0]]
    if not no_conda_tos:
        # NOTE: conda TOS still need to be accepted after the command because it deletes TOS acceptance files
        commands = ["conda tos accept"] + commands
    return combined_run(commands)


def get_private_git_dep_lines(dummy_arg, temp_dir=".", dockerfolder=False, **kwargs):
    if dockerfolder:
        return []
    assert len(dummy_arg) == 0
    create_passwd_checked_pip_install(output_dir=temp_dir)
    return [
        f"COPY {temp_dir}/{passwd_checked_pip_install_scrname} {internal_script_storage}/{passwd_checked_pip_install_scrname}"
    ] + combined_run([pip_install_line(["pexpect"])])


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


def get_all_dependencies(docker_name, dockerspec_dirs=None, cwd=None):
    cur_imported_id = 0
    dep_dict = {parent_flag: [docker_name]}
    while cur_imported_id != len(dep_dict[parent_flag]):
        to_add = get_local_dependencies(
            dep_dict[parent_flag][cur_imported_id], dockerspec_dirs=dockerspec_dirs, cwd=cwd
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


def check_dependency_consistency(all_dependencies, temp_dir=".", nowipe=False, dockerfolder=False):
    kwargs, is_private = check_login_kwargs(
        all_dependencies, nowipe=nowipe, dockerfolder=dockerfolder
    )
    if is_private:
        kwargs["temp_dir"] = temp_dir
    pip_deps = get_deps(all_dependencies, pip_flag)
    piplast_deps = get_deps(all_dependencies, pip_flag)
    if pip_deps or piplast_deps:
        if apt_flag not in all_dependencies:
            all_dependencies[apt_flag] = []
        if (
            (contains_git_repos(pip_deps) or contains_git_repos(piplast_deps))
            and ("git" not in all_dependencies[apt_flag])
            and (not dockerfolder)
        ):
            all_dependencies[apt_flag].append("git")
    return kwargs, is_private


def docker_contains_conda(docker_name, conda_present=False, dockerspec_dirs=None, cwd=None):
    if conda_present:
        return False
    notag_name = docker_name.split(":")[0]
    return notag_name not in get_list_wconda(dockerspec_dirs=dockerspec_dirs, cwd=cwd)


def get_dockerfile_lines_deps(
    docker_name,
    dockerspec_dirs=None,
    conda_updated=False,
    nowipe=False,
    no_conda_tos=False,
    dockerfolder=False,
    separate_conda_deps=False,
    cwd=None,
    conda_present=False,
):
    # Temporary directory where necessary files will be dumped.
    if dockerfolder:
        temp_dir = "prerequisites"
        os.mkdir(temp_dir)
    else:
        temp_dir_obj = TemporaryDirectory(dir=".", delete=False)
        temp_dir = os.path.basename(temp_dir_obj.name)
    # Docker-specific dependencies.
    all_dependencies = get_all_dependencies(docker_name, dockerspec_dirs=dockerspec_dirs, cwd=cwd)
    kwargs, is_private = check_dependency_consistency(
        all_dependencies, temp_dir=temp_dir, nowipe=nowipe, dockerfolder=dockerfolder
    )
    kwargs = {
        **kwargs,
        "no_conda_tos": no_conda_tos,
        "separate_conda_deps": separate_conda_deps,
        "dockerfolder": dockerfolder,
        "temp_dir": temp_dir,
    }
    if from_flag not in all_dependencies:
        raise Exception("Need a base Docker.")
    output = get_from_dep_lines(all_dependencies[from_flag])
    output += get_base_dockerfile_commands(dockerspec_dirs=dockerspec_dirs)

    # Commands run once we set up apt-installable components.
    post_apt_commands = [login_shell_command]
    if docker_contains_conda(
        all_dependencies[from_flag][0],
        dockerspec_dirs=dockerspec_dirs,
        cwd=cwd,
        conda_present=conda_present,
    ):
        post_apt_commands += conda_installation_lines(temp_dir)

    # K.Karan 2025.07.18: TBH I am now not sure why I implemented `conda_updated` option. Also, does not work with latest Conda releases.
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


def prepare_dockerfile(
    docker_name,
    dockerspec_dirs=None,
    nowipe=False,
    no_conda_tos=False,
    dockerfolder=False,
    separate_conda_deps=False,
    cwd=None,
    conda_present=False,
):
    dlines, copy_reqs, temp_dir, private = get_dockerfile_lines_deps(
        docker_name,
        dockerspec_dirs=dockerspec_dirs,
        nowipe=nowipe,
        no_conda_tos=no_conda_tos,
        dockerfolder=dockerfolder,
        separate_conda_deps=separate_conda_deps,
        cwd=cwd,
        conda_present=conda_present,
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


def prepare_image(
    docker_name,
    dockerspec_dirs=None,
    docker_tag=None,
    nowipe=False,
    verbose=False,
    no_conda_tos=False,
    separate_conda_deps=False,
    conda_present=False,
):
    check_bin_availability("docker")
    temp_dir, is_private = prepare_dockerfile(
        docker_name,
        dockerspec_dirs=dockerspec_dirs,
        nowipe=nowipe,
        no_conda_tos=no_conda_tos,
        separate_conda_deps=separate_conda_deps,
        conda_present=conda_present,
    )
    docker_build_command = ["docker", "image", "build"]
    if docker_tag is None:
        docker_tag = f"{docker_name}:1.0"
    docker_build_command += ["-t", docker_tag]
    if verbose:
        docker_build_command += ["--progress", "plain"]
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
@click.option("--dockerfolder", is_flag=True)
@click.option("--nowipe", is_flag=True)
@click.option("--verbose", is_flag=True)
@click.option("--no_conda_tos", is_flag=True)
@click.option("--separate_conda_deps", is_flag=True)
@click.option("--conda_present", is_flag=True)
def main(
    docker_name,
    tag,
    dockerfile,
    dockerfolder,
    nowipe,
    verbose,
    no_conda_tos,
    separate_conda_deps,
    conda_present,
):
    common_kwargs = {
        "no_conda_tos": no_conda_tos,
        "separate_conda_deps": separate_conda_deps,
        "conda_present": conda_present,
    }
    if dockerfile:
        assert not dockerfolder, "--dockerfile and --dockerfolder flags cannot be used together"
        prepare_dockerfile(docker_name, nowipe=True, dockerfolder=dockerfolder, **common_kwargs)
        return
    if dockerfolder:
        cwd = os.getcwd()
        assert not os.path.isdir(
            docker_name
        ), f"{docker_name} directory already exists, clear it to proceed."
        os.mkdir(docker_name)
        os.chdir(docker_name)
        prepare_dockerfile(docker_name, dockerfolder=dockerfolder, cwd=cwd, **common_kwargs)
        os.chdir("..")
        return
    prepare_image(docker_name, docker_tag=tag, nowipe=nowipe, verbose=verbose, **common_kwargs)
