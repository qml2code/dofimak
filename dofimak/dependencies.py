"""
TODO: Two options that were present originally and could be revived on request:
1. Flags specifying that some pip packages must be installed after others (previously known by `PIPLAST` flag, might be re-introduced by, e.g. `PIP@1`, `PIP@2`, etc. flags)
2. `PYTHONPATH` flag specifying that a package should be installed by downloading it into a directory in `$PYTHONPATH`. Presently abandoned as all repos we care about have a normal `pyproject.toml` now.
3. `CONDAV` flag specifying conda version; we mostly switched to dockers with pre-installed condas anyway.
"""
import subprocess
from typing import List

from packaging.requirements import Requirement

from .specifications import dockerspec_filename

parent_flag = "PARENT"
apt_flag = "APT"
from_flag = "FROM"
private_git_flag = "PRIVATE_GIT"
conda_flag = "CONDA"
yml_flag = "YML"
pip_flag = "PIP"

dependency_specifiers = [
    from_flag,
    apt_flag,
    private_git_flag,
    conda_flag,
    yml_flag,
    pip_flag,
]


class Dependency:
    def __init__(self, str_definition):
        self.dep_name = str_definition

    def get_identifier(self):
        return self.dep_name

    def __eq__(self, other_dep):
        return (
            type(other_dep) is type(self) and self.get_identifier() == other_dep.get_identifier()
        )

    def merge_sanity_check(self, other_dep):
        assert self == other_dep, "Tried merging non-matching dependencies!"

    def merge(self, other_dep):
        self.merge_sanity_check(other_dep)


class FROMDependency(Dependency):
    """
    'Base' Docker in the 'FROM' statement.
    """

    def __init__(self, str_definition):
        self.dep_name, self.tag = str_definition.split(":")

    def merge(self, other_dep):
        raise Exception("Multiple 'FROM' statements encountered!")

    def install_str(self):
        return self.dep_name + ":" + self.tag


class APTDependency(Dependency):
    """
    Linux APT package.
    """


class PRIVATE_GIT(Dependency):
    pass


class CondaEnvDependency(Dependency):
    def __init__(self, str_definition):
        """
        Dependency in the conda environment.
        """
        self.req = Requirement(str_definition)
        self.package_name = self.req.name
        self.specifier = self.req.specifier

    def get_identifier(self):
        return self.package_name

    def merge(self, other_dep):
        self.merge_sanity_check(other_dep)
        self.specifier = self.specifier & other_dep.specifier

    def name_wspecifier(self):
        output = self.package_name
        if self.specifier is not None:
            output += str(self.specifier)
        return output


# for dependencies installable by Conda.
special_cond_separator = ";"


class CondaDependency(CondaEnvDependency):
    def __init__(self, str_definition):
        """
        Package to be installed by Conda.
        """
        def_spl = str_definition.split(special_cond_separator)
        super().__init__(def_spl[0])
        if len(def_spl) > 1:
            self.channel_name = def_spl[1]
        else:
            self.channel_name = None
        if len(def_spl) > 2:
            self.solver_name = def_spl[2]
        else:
            self.solver_name = None


class CondaChannelDependency(CondaDependency):
    def __init__(self, channel_name):
        self.channel_name = channel_name
        self.solver_name = None
        self.package_name = None

    def __eq__(self, other_dep):
        assert isinstance(other_dep, type(self))
        return self.channel_name == other_dep.channel_name

    def merge(self, other_dep):
        Dependency.merge(self, other_dep)


# for dependencies installable by pip
def alternative_github_url(repo_url):
    # TODO: add more options here as needed.
    specifier = repo_url.split(".com")[1]
    if ("https://github.com" in repo_url) or ("https://git@github.com" in repo_url):
        return "git@github.com:" + specifier
    elif "git+ssh" in repo_url:
        return "https://github.com/" + specifier
    raise Exception("Unknown URL type")


class PIPDependency(CondaEnvDependency):
    def __init__(self, str_definition, pythonpath_install=False):
        """
        Package installed by PIP from git URL.
        """
        super().__init__(str_definition)
        self.url = self.req.url
        self.is_github_link = self.url is not None
        if self.is_github_link:
            def_spl = self.url.split("@")
            last_field = def_spl[-1]
            if "/" in last_field:
                self.branch = None
            else:
                self.branch = last_field
                self.url = "@".join(def_spl[:-1])
        else:
            self.branch = None

        self.pythonpath_install = pythonpath_install

    def merge(self, other_dep):
        self.merge_sanity_check(other_dep)
        assert self.url == other_dep.url, f"Package URLs do not match for {self.package_name}"
        if self.branch is None:
            self.branch = other_dep.branch
        else:
            assert (
                other_dep.branch is None or other_dep.branch == self.branch
            ), f"Repo branches do not match for {self.package_name}"

    def install_str(self):
        if self.is_github_link:
            output = self.package_name + "@" + self.url
            if self.branch is not None:
                output += "@" + self.branch
            return output
        return self.name_wspecifier()

    def install_str_wtoken(self):
        if "git+ssh" in self.url:
            return alternative_github_url(self.url)
        elif "https://github.com" in self.url:
            return self.url
        raise Exception("Unknown URL type.")


# name of file used to store *.yml for the environment.
default_yml_file = "base.yml"


class YMLDependency(Dependency):
    def __init__(self):
        self.textlines = None

    def merge(self):
        raise Exception("Multiple YML files?")

    def get_from_docker(self, docker_tag):
        result = subprocess.run(
            ["docker", "run", docker_tag, "conda", "env", "export"], capture_output=True
        )
        self.textlines = result.stdout.decode().split("\n")

    def clear_packages(self, package_names):
        assert self.textlines is not None
        line_id = 0
        while line_id != len(self.textlines):
            line = self.textlines[line_id]
            lspl = line.split()
            if len(lspl) > 1 and lspl[0] == "-":
                lspl2 = lspl[1].split("=")
                if lspl2[0] in package_names:
                    del self.textlines[line_id]
                    continue
            line_id += 1

    def dump_to_yml(self, yml_file=default_yml_file):
        f = open(yml_file, "w")
        print("\n".join(self.textlines), file=f)
        f.close()


dependency_constructors = {
    apt_flag: APTDependency,
    from_flag: FROMDependency,
    private_git_flag: PRIVATE_GIT,
    conda_flag: CondaDependency,
    yml_flag: YMLDependency,
    pip_flag: PIPDependency,
}


class DependenciesSublist(list):
    """
    Containing finalized dependencies of a single type.
    """

    def add_dependencies(self, dep_list: List[Dependency]):
        for dep in dep_list:
            if dep in self:
                new_dep_id = self.index(dep)
                self[new_dep_id].merge(dep)
            else:
                self.append(dep)


class Dependencies(dict):
    """
    Class containing all finalized dependencies of the Docker.
    """

    def contains_private(self):
        return private_git_flag in self

    def contains_from(self):
        return from_flag in self

    def from_docker(self):
        if self.contains_from():
            dep_sublist = self[from_flag]
            assert len(dep_sublist) == 1
            return dep_sublist[0]
        return None

    def add_dependencies(self, flag, dep_list):
        if flag not in self:
            self[flag] = DependenciesSublist()
        self[flag].add_dependencies(dep_list)

    def add_dependencies_from_strs(self, flag, str_def_list):
        constructor = dependency_constructors[flag]
        new_deps = [constructor(str_def) for str_def in str_def_list]
        self.add_dependencies(flag, new_deps)

    def import_yml_from_docker(self, docker_tag):
        yml_dep = YMLDependency()
        yml_dep.get_from_docker(docker_tag)
        self.add_dependencies(yml_flag, [yml_dep])


# for reading dependencies from a file


def get_local_dependencies(
    dependencies: Dependencies, parents, docker_name, dockerspec_dirs=None, cwd=None
):
    spec_filename = dockerspec_filename(docker_name, dockerspec_dirs=dockerspec_dirs, cwd=cwd)
    processed_lines = open(spec_filename, "r").readlines()
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
        added_str_dependencies = lspl[1:]
        if flag == parent_flag:
            for str_dep in added_str_dependencies:
                if str_dep not in parents:
                    parents.append(str_dep)
        dependencies.add_dependencies_from_strs(flag, added_str_dependencies)


def get_all_dependencies(docker_name, dockerspec_dirs=None, cwd=None):
    dependencies = Dependencies()
    parents = [docker_name]
    cur_imported_id = 0
    while cur_imported_id != len(parents):
        imported_docker_name = parents[cur_imported_id]
        get_local_dependencies(
            dependencies, parents, imported_docker_name, dockerspec_dirs=dockerspec_dirs, cwd=cwd
        )
        cur_imported_id += 1
    return dependencies
