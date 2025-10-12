"""
Miscellaneous utilities
"""
import platform
import shutil

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
