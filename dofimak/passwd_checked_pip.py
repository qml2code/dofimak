import inspect

passwd_checked_pip_install_scrname = "passwd_checked_pip.py"


def passwd_checked_pip_install(package_links, login=None, passwd=None, website="github.com"):
    import pexpect
    from pexpect.exceptions import EOF

    child = pexpect.spawn(f"pip install {' '.join(package_links)}")

    while True:
        try:
            child.expect(f"Username for 'https://{website}':")
            assert login is not None, f"ABORTING: Requires {website} username!"
            child.sendline(login)
            child.expect(f"Password for 'https://{login}@{website}':")
            assert passwd is not None, f"ABORTING: Requires {website} password!"
            child.sendline(passwd)
        except EOF:
            break
    print(child.before.decode())


def cmd_passwd_checked():
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("packages", nargs="*")
    p.add_argument("--login", type=str)
    p.add_argument("--passwd", type=str)

    args = p.parse_args()
    passwd_checked_pip_install(args.packages, login=args.login, passwd=args.passwd)


def create_passwd_checked_pip_install(output_dir="."):
    source1 = inspect.getsource(passwd_checked_pip_install)
    source2 = inspect.getsource(cmd_passwd_checked)
    with open(output_dir + "/" + passwd_checked_pip_install_scrname, "w") as f:
        print(
            f"""
{source1}

{source2}

cmd_passwd_checked()
""",
            file=f,
        )
