"""
TODO: revise & update this comment

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
# TODO check temp_dir deletion
import os
import shutil
import subprocess
from tempfile import TemporaryDirectory

import click
import tomli
import tomli_w

from .dependencies import (
    APTDependency,
    CondaDependency,
    Dependencies,
    DependenciesSublist,
    PIPDependency,
    alternative_github_url,
    apt_flag,
    conda_flag,
    dependency_specifiers,
    from_flag,
    get_all_dependencies,
    pip_flag,
    private_git_flag,
)
from .passwd_checked_pip import (
    create_passwd_checked_pip_install,
    passwd_checked_pip_install_scrname,
)
from .specifications import get_base_dockerfile_commands, get_list_wconda
from .utils import check_bin_availability, get_safe_removal_command, no_safe_removal_warning

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


def check_login_kwargs(all_dependencies: Dependencies, nowipe=False, dockerfolder=False):
    if (not all_dependencies.contains_private()) or dockerfolder:
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


def get_from_dep_lines(dep_list: Dependencies):
    return ["FROM " + dep_list[from_flag][0].install_str()]


def get_apt_dep_lines(dep_list: Dependencies[APTDependency], **kwargs):
    if not dep_list:
        return []
    l = "apt-get install -y"
    for dep in dep_list:
        l += " " + dep.dep_name
    return combined_run(["apt-get update", l])


def get_conda_separate_package_installation(
    dep_list: DependenciesSublist[CondaDependency], no_conda_tos=False
):
    if no_conda_tos:
        commands = []
    else:
        commands = ["conda tos accept"]
        added_channels = []
    for dep in dep_list:
        assert isinstance(dep, CondaDependency)
        channel_args = ""
        if dep.channel_name is not None:
            channel_args = "-c " + dep.channel_name + " "
            if (not no_conda_tos) and (dep.channel_name not in added_channels):
                added_channels.append(dep.channel_name)
                # make sure TOS are accepted for newly added channels
                commands += [
                    "conda config --append channels " + dep.channel_name,
                    "conda tos accept --override-channels --channel " + dep.channel_name,
                ]
        if dep.solver_name is not None:
            channel_args += f"--solver={dep.solver_name} "
        commands.append("conda install " + channel_args + dep.name_wspecifier())
    return commands


def get_conda_package_installation(
    dep_list: DependenciesSublist[CondaDependency], no_conda_tos=False
):
    installation_groups = {}
    if not no_conda_tos:
        added_channels = []
    for dep in dep_list:
        assert isinstance(dep, CondaDependency)
        installation_group = (dep.channel_name, dep.solver_name)
        if installation_group in installation_groups:
            installation_groups[installation_group].append(dep.name_wspecifier())
        else:
            installation_groups[installation_group] = [dep.name_wspecifier()]
        if (
            (not no_conda_tos)
            and (dep.channel_name is not None)
            and (dep.channel_name not in added_channels)
        ):
            added_channels.append(dep.channel_name)
    if no_conda_tos:
        commands = []
    else:
        commands = [
            "conda config --append channels " + " ".join(added_channels),
            "conda tos accept",
        ]
    for (channel_name, solver_name), names_wspecifiers in installation_groups.items():
        command = "conda install"
        if channel_name is not None:
            command += f" -c {channel_name}"
        if solver_name is not None:
            command += f" --solver={solver_name}"
        commands.append(f"{command} " + " ".join(names_wspecifiers))
    return commands


def get_conda_dep_lines(
    dep_list: DependenciesSublist[CondaDependency],
    no_conda_tos=False,
    separate_conda_deps=False,
    **kwargs,
):
    if separate_conda_deps:
        commands = get_conda_separate_package_installation(dep_list, no_conda_tos=no_conda_tos)
    else:
        commands = get_conda_package_installation(dep_list, no_conda_tos=no_conda_tos)
    return combined_run(commands)


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


def extract_pyproject_github_deps(cloned_dir):
    pyproject_toml_filename = cloned_dir + "/pyproject.toml"
    if not os.path.isfile(pyproject_toml_filename):
        return []
    pyproject_import = tomli.loads(open(pyproject_toml_filename, "r").read())

    all_deps = pyproject_import["project"]["dependencies"]

    new_git_deps = []

    dep_id = 0
    while dep_id != len(all_deps):
        dep = PIPDependency(all_deps[dep_id])
        if dep.is_github_link:
            new_git_deps.append(dep)
            del all_deps[dep_id]
        else:
            dep_id += 1
    pyproject_import["project"]["dependencies"] = all_deps

    new_pyproject_text = tomli_w.dumps(pyproject_import)
    with open(pyproject_toml_filename, "w") as f:
        print(new_pyproject_text, file=f)

    return new_git_deps


def get_clone_command(repo_url, cloned_folder, branch=None):
    clone_command = ["git", "clone"]
    if branch is not None:
        clone_command += ["--branch", branch]
    return clone_command + [repo_url, cloned_folder]


def clone_repo(repo_url, cloned_folder, branch=None):
    alt_repo_url = alternative_github_url(repo_url)
    for url in [repo_url, alt_repo_url]:
        print("Attempting to clone:", url)
        command = get_clone_command(url, cloned_folder, branch=branch)
        completed_process = subprocess.run(command)
        if completed_process.returncode == 0:
            return
    raise Exception(
        f"FAILED CLONING THE REPO: {repo_url}\nCONSIDER CHECKING YOUR ACCESS PRIVILEGES!"
    )


def process_pip_cloned_github_dep(dep: PIPDependency, temp_dir):
    cloned_folder = f"{temp_dir}/{dep.package_name}"
    clone_repo(dep.url, cloned_folder, branch=dep.branch)
    new_repo_deps = extract_pyproject_github_deps(cloned_folder)
    return f"{tmp_repo_src}/{dep.package_name}", cloned_folder, new_repo_deps


def get_pip_dep_lines(
    dep_list: DependenciesSublist[PIPDependency], dockerfolder=False, temp_dir=None, **kwargs
):
    install_strings = []
    if dockerfolder:
        copy_folder_tuples = []
    dep_id = 0
    while dep_id != len(dep_list):
        dep = dep_list[dep_id]
        if dep.is_github_link:
            if dockerfolder:
                dep_install_str, cloned_folder, new_repo_deps = process_pip_cloned_github_dep(
                    dep, temp_dir
                )
                copy_folder_tuple = (dep_install_str, cloned_folder)
                assert copy_folder_tuple not in copy_folder_tuples
                copy_folder_tuples.append((cloned_folder, dep_install_str))
                dep_list.add_dependencies(new_repo_deps)
            else:
                dep_install_str = dep.install_str_wtoken()
        else:
            dep_install_str = dep.install_str()
        install_strings.append(dep_install_str)
        dep_id += 1
    lines = [pip_install_line(install_strings, **kwargs)]
    if dockerfolder:
        lines += [f"rm -Rf {tmp_repo_src}"]
        copy_commands = [
            f"COPY {cloned_folder} {dep_install_str}"
            for cloned_folder, dep_install_str in copy_folder_tuples
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
    conda_flag: get_conda_dep_lines,
    pip_flag: get_pip_dep_lines,
    private_git_flag: get_private_git_dep_lines,
}


def contains_git_repos(dependency_list: DependenciesSublist[PIPDependency]):
    for dep in dependency_list:
        if dep.url is not None:
            return True
    return False


def get_deps(all_dependencies, flag):
    if flag in all_dependencies:
        return all_dependencies[flag]
    else:
        return []


def check_dependency_consistency(
    all_dependencies: Dependencies, temp_dir=".", nowipe=False, dockerfolder=False
):
    kwargs, is_private = check_login_kwargs(
        all_dependencies, nowipe=nowipe, dockerfolder=dockerfolder
    )
    if is_private:
        kwargs["temp_dir"] = temp_dir
    pip_deps = get_deps(all_dependencies, pip_flag)
    if pip_deps:
        if contains_git_repos(pip_deps) and (not dockerfolder):
            all_dependencies.add_dependencies_from_strs(apt_flag, ["git"])
    return kwargs, is_private


def docker_contains_conda(
    all_dependencies: Dependencies, conda_present=False, dockerspec_dirs=None, cwd=None
):
    if conda_present:
        return False
    notag_name = all_dependencies.from_docker().dep_name
    return notag_name not in get_list_wconda(dockerspec_dirs=dockerspec_dirs, cwd=cwd)


def get_dockerfile_lines_deps(
    all_dependencies: Dependencies,
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
    if not all_dependencies.contains_from():
        raise Exception("Need a base Docker.")
    output = get_from_dep_lines(all_dependencies)
    output += get_base_dockerfile_commands(dockerspec_dirs=dockerspec_dirs)

    # Commands run once we set up apt-installable components.
    post_apt_commands = [login_shell_command]
    if docker_contains_conda(
        all_dependencies,
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

    for dep_spec in dependency_specifiers[1:]:
        if dep_spec not in all_dependencies:
            continue
        output += dependency_line_dict[dep_spec](all_dependencies[dep_spec], **kwargs)
        if dep_spec == apt_flag:
            output += post_apt_commands

    return output, temp_dir, is_private


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
    all_dependencies = get_all_dependencies(docker_name, dockerspec_dirs=dockerspec_dirs, cwd=cwd)
    dlines, temp_dir, private = get_dockerfile_lines_deps(
        all_dependencies,
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
