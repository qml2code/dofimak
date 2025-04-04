# dofimak

**Do**cker**fi**le**mak**er (**dofimak**) is an utility for hassle-free dockerfile making.

# :wrench: Installation

Clone the repository

```bash
git clone git@github.com:qml2code/dofimak.git
```
If [GNU make](https://www.gnu.org/software/make/) is installed use

```bash
make install
```
Otherwise, if `pip` is installed use

```bash
pip install .
```

# :red_circle: **USER WARKING**

During installation of private `git` repositories the package asks for account names and passwords for the repositories' retrieval. These data is used to contruct `Dockerfile`, which the package removes automatically with the `wipe` command. Make sure that `Dockerfile` is deleted afterwards!

# :computer: Environment variables

- DOCKFMAKER_SPECS - colon-separated list of directories where Dockerfile specifications are held. The package automatically adds to those the current directory and `dofimak/specifications` package directory.
