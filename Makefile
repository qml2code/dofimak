# if in your environment "python" or "pip" are aliases for another command modify these
# lines accordingly.
python=python
pip=pip

all: install

dev-env:
	$(pip) install pre-commit

./.git/hooks/commit-msg: dev-env
	pre-commit install --hook-type commit-msg

./.git/hooks/pre-commit: dev-env
	pre-commit install

conventional-commits: ./.git/hooks/commit-msg

dev-setup: dev-env ./.git/hooks/pre-commit

review: dev-setup
	pre-commit run --all-files

install:
	$(pip) install .

clean:
	rm -Rf ./build
	rm -Rf ./dockerfilemaker.egg-info
