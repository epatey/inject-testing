#!/usr/bin/env python3
"""
================================================================================
PORTABLE PLAYWRIGHT PYINSTALLER BUILD SCRIPT
================================================================================

PURPOSE:
This script uses PyInstaller to create a fully self-contained, portable executable
from a Python application that uses Playwright and a headless browser. It solves
the problem of bundling Chromium dependencies with the application to ensure it
can run on different Linux systems without requiring users to install Playwright
or Chromium separately.

WHY MANUAL DEPENDENCY COLLECTION IS REQUIRED:
PyInstaller automatically handles Python module dependencies and their C extensions,
but it does NOT analyze or bundle dependencies of standalone binaries included via
--add-binary. When we include Chromium's headless_shell executable, PyInstaller
treats it as a data file and doesn't discover its shared library dependencies. This
script manually uses ldd to find these dependencies and explicitly bundles them,
which PyInstaller cannot do automatically.

WORKFLOW:
1. Install Chromium into the Playwright package directory (not user home)
2. Locate the chromium-headless-shell binary that Playwright uses for Chromium
3. Use ldd to discover all shared library dependencies
4. Explicitly add NSS and WebGL libraries that may be loaded dynamically
5. Bundle everything into a single executable with PyInstaller

OUTPUT:
A single executable file in dist/main that contains:
- Embedded python interpreter
- The python application code
- The Playwright library
- Chromium browser (headless_shell)
- All necessary shared libraries
- NSS security libraries
- WebGL libraries

COMPATIBILITY:
- Requires same or newer glibc version as build system (core glibc libraries are
  excluded to maintain ABI compatibility)
- For true cross-distribution compatibility, run StaticX on the output
"""

import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# Import playwright to find its installation directory
import playwright  # type: ignore

# Directory where this build script is located
SCRIPT_DIR = Path(__file__).parent.resolve()

# Temporary directory where collected libraries will be staged before bundling
BUILD_LIBS = SCRIPT_DIR / "build_libs"

# The main Python script that will be the entry point of the executable
ENTRYPOINT = SCRIPT_DIR / "main.py"


def main() -> None:
    """
    Main orchestration function that runs the complete build process.

    This function coordinates all steps in sequence:
    1. Verify PyInstaller is available
    2. Install Chromium into the package directory
    3. Find the headless_shell binary
    4. Collect all required libraries
    5. Build the final executable with PyInstaller

    The result is a portable executable that includes everything needed
    to run Playwright with Chromium on any compatible Linux system.
    """
    # Verify build environment
    _ensure_pyinstaller_available()

    # Install browser into package (not user home)
    headless_shell = _install_chromium_headless_shell()

    # Collect and stage all extra dependencies/libraries
    if BUILD_LIBS.exists():
        shutil.rmtree(BUILD_LIBS)
    BUILD_LIBS.mkdir(parents=True, exist_ok=True)

    _stage_libraries(_ldd_deps(headless_shell), "ldd dependencies")
    _stage_libraries(_nss_deps(), "NSS dependencies")
    _stage_libraries(_webgl_deps(), "WebGL dependencies")

    # Each library needs a --add-binary argument in the format "source:dest"
    # The :lib suffix tells PyInstaller to place these in a lib/ subdirectory
    add_binary_args = [f"--add-binary={str(f)}:lib" for f in BUILD_LIBS.glob("*")]

    _build_executable(add_binary_args)


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    return subprocess.run(
        cmd, cwd=cwd, env=env, text=True, capture_output=True, check=True
    ).stdout


def _ensure_pyinstaller_available() -> None:
    try:
        # Try to run PyInstaller as a module to check if it's available
        _run([sys.executable, "-m", "PyInstaller", "--version"])
    except RuntimeError as e:
        # Provide helpful error message with installation command
        raise RuntimeError(
            "PyInstaller not found in this Python environment. "
            f"Install it with:\n  {sys.executable} -m pip install pyinstaller"
        ) from e


def _install_chromium_headless_shell() -> Path:
    """
    Install Chromium browser into the Playwright package directory.

    By setting PLAYWRIGHT_BROWSERS_PATH=0, we tell Playwright to install
    browsers into its package directory instead of the user's home directory.
    This is crucial for creating a portable executable - the browser files
    will be included when PyInstaller bundles the playwright package.

    Without this step, Playwright would look for browsers in ~/.cache/ms-playwright
    at runtime, which wouldn't exist on target systems.
    """
    # Copy current environment and override browser path
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = "0"  # "0" means use package directory

    print("[1/4] Ensuring Chromium is installed into the Playwright package path")

    # Run playwright install command with modified environment
    _run(
        [sys.executable, "-m", "playwright", "install", "chromium-headless-shell"],
        env=env,
    )

    return _find_chromium_headless_shell()


def _find_chromium_headless_shell() -> Path:
    """
    Locate the headless_shell binary within the Playwright package.

    The binary is typically located at:
    playwright/driver/package/.local-browsers/chromium-*/chrome-linux/headless_shell

    Returns:
        Path to the headless_shell executable

    Raises:
        FileNotFoundError: If headless_shell cannot be located, suggesting
                          that Chromium installation may have failed
    """
    print("[2/4] Locating headless_shell used by Playwright")

    # Get the playwright package directory
    pkg = Path(playwright.__file__).parent

    # Search recursively for headless_shell in the driver/package subdirectory
    # This is where Playwright stores downloaded browser binaries
    for p in (pkg / "driver" / "package").rglob("headless_shell"):
        # Verify it's an executable file (not a directory or symlink to nowhere)
        if p.is_file() and os.access(p, os.X_OK):
            print(f"Using headless_shell: {p}")
            return p

    # If we get here, something went wrong with browser installation
    raise FileNotFoundError(
        "Could not locate headless_shell. Ensure 'playwright install chromium' succeeds."
    )


def _parse_ldd_paths(ldd_output: str) -> list[Path]:
    """
    Parse the output of the ldd command to extract library paths.

    ldd output format examples:
    - Normal library: libX11.so.6 => /lib/x86_64-linux-gnu/libX11.so.6 (0x00007f...)
    - Virtual library: linux-vdso.so.1 (0x00007fff...)
    - Not found: libmissing.so => not found

    This function:
    1. Extracts the absolute paths from "=>" mappings
    2. Filters out core system libraries that shouldn't be bundled
    3. Returns unique paths as a set

    Args:
        ldd_output: Raw output from the ldd command

    Returns:
        Set of Path objects for libraries that should be bundled
    """
    # Core system libraries that should NOT be bundled
    # These are provided by the host OS and bundling them would break compatibility
    # The dynamic linker (ld-linux) and core C libraries must match the host system
    LDD_EXCLUDES = (
        "ld-linux",  # Dynamic linker/loader - must match host kernel
        "libc.so",  # Core C library - defines system ABI
        "libm.so",  # Math library - part of core glibc
        "libpthread.so",  # POSIX threads - part of core glibc
        "libdl.so",  # Dynamic loading - part of core glibc
        "librt.so",  # Real-time extensions - part of core glibc
    )

    return [
        Path(m.group(1))
        for line in ldd_output.splitlines()
        if "=>"
        in line  # Skip lines without "=>" (like linux-vdso or statically linked)
        for m in [re.search(r"=>\s+(\S+)", line)]  # Extract the path after "=>"
        if m
        and m.group(1).startswith("/")  # Skip non-absolute paths (like "not found")
        and not any(
            ex in m.group(1) for ex in LDD_EXCLUDES
        )  # Filter out core system libraries
    ]


def _ldd_deps(binary: Path) -> list[Path]:
    """
    Use ldd to discover all shared library dependencies of a binary.

    These are the libraries explicitly linked by headless_shell.

    Args:
        binary: Path to the executable to analyze

    Returns:
        Set of paths to required shared libraries (excluding core system libs)
    """
    print("[3/4] Collecting shared libraries via ldd")

    return _parse_ldd_paths(_run(["ldd", str(binary)]))


# Cache for ldconfig output to avoid multiple calls
_ldconfig_cache: dict[str, Path] | None = None


def _get_ldconfig_cache() -> dict[str, Path]:
    """
    Build and cache a dictionary of library names to paths from ldconfig output.

    This function runs ldconfig once and parses its output into a dictionary
    for fast lookups. The cache is stored globally to avoid repeated calls.

    Returns:
        Dictionary mapping library names to their file paths
    """
    global _ldconfig_cache

    if _ldconfig_cache is not None:
        return _ldconfig_cache

    _ldconfig_cache = {}

    # Check if ldconfig is available (might not be in minimal containers)
    if shutil.which("ldconfig"):
        try:
            # Get the library cache listing
            out = _run(["ldconfig", "-p"])

            for line in out.splitlines():
                # ldconfig -p format:
                # libX11.so.6 (libc6,x86-64) => /lib/x86_64-linux-gnu/libX11.so.6

                line = line.strip()
                if not line or "=>" not in line:
                    continue

                # Split on first space to get library name
                parts = line.split(" ", 1)
                if len(parts) < 2:
                    continue

                lib_name = parts[0]

                # Extract the path after "=>"
                m = re.search(r"=>\s+(\S+)$", line)
                if m:
                    p = Path(m.group(1))
                    # Verify the file actually exists and cache it
                    if p.exists():
                        _ldconfig_cache[lib_name] = p
        except Exception:
            # ldconfig might fail in some environments, continue with empty cache
            pass

    return _ldconfig_cache


def _webgl_deps() -> list[Path]:
    """
    Best-effort include graphics libraries

    libGLESv2 is needed for WebGL support but location varies by distribution
    """
    return [
        Path(gpath)
        for pattern in ("/usr/lib/*-linux-gnu/libGLESv2.so*",)
        for gpath in glob.glob(pattern)
        if Path(gpath).exists()
    ]


def _nss_deps() -> list[Path]:
    """
    Locate the NSS (Network Security Services) Libraries.

    These security libraries are dynamically loaded by Chromium at runtime for HTTPS
    support and must be explicitly included.
    """
    # NSS (Network Security Services) libraries handle SSL/TLS, certificates, and
    # cryptographic operations and are required by Chromium. These are often loaded
    # dynamically at runtime using dlopen(), so they don't always appear in ldd output.
    NSS_NAMES = [
        "libsoftokn3.so",  # Software token implementation for NSS
        "libsoftokn3.chk",  # Checksum file for libsoftokn3
        "libnss3.so",  # Main NSS library
        "libnssutil3.so",  # NSS utility functions
        "libsmime3.so",  # S/MIME cryptographic functions
        "libssl3.so",  # SSL/TLS protocol implementation
        "libnssckbi.so",  # Built-in root certificates (CRITICAL for HTTPS)
        "libnspr4.so",  # Netscape Portable Runtime (NSS dependency)
        "libplc4.so",  # NSPR library for classic I/O
        "libplds4.so",  # NSPR library for data structures
        "libfreebl3.so",  # Freebl cryptographic library
        "libfreeblpriv3.so",  # Private Freebl functions
    ]

    return [path for name in NSS_NAMES if (path := _find_nss_lib(name))]


def _find_nss_lib(name: str) -> Path | None:
    """
    Find an NSS library by name, trying multiple strategies.

    NSS libraries are critical for HTTPS support but may be installed
    in various locations depending on the distribution. This function:
    1. First tries the fast ldconfig cache lookup
    2. Falls back to filesystem search in common locations

    Args:
        name: NSS library filename (e.g., "libnssckbi.so")

    Returns:
        Path to the library if found, None otherwise.
        Any returned Path is guaranteed to exist at the time of return.
    """
    # Strategy 1: Try ldconfig cache (fastest)
    if path := _get_ldconfig_cache().get(name):
        return path

    # Strategy 2: Search common library directories
    # Different distributions use different layouts:
    # - Debian/Ubuntu: /usr/lib/x86_64-linux-gnu/
    # - Fedora/RHEL: /usr/lib64/
    # - Alpine: /usr/lib/
    for root in ("/usr/lib", "/lib"):
        # rglob searches recursively, handling all subdirectory structures
        for candidate in Path(root).rglob(name):
            if candidate.is_file():
                return candidate

    return None


def _stage_dependency(src: Path, dest_dir: Path) -> None:
    """
    Copy a dependency file (typically a lib) to the destination directory, resolving symlinks.

    Many libraries are symlinks (e.g., libfoo.so -> libfoo.so.1.2.3).
    This function follows symlinks to copy the actual file content, ensuring the
    bundled library is complete and functional.

    Args:
        src: Source library path (may be a symlink)
        dest_dir: Destination directory for the copy
    """
    # Ensure destination directory exists
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Keep the original filename in the destination
    target = dest_dir / src.name

    # follow_symlinks=True ensures we copy the actual file content,
    # not just create another symlink
    shutil.copy2(src, target, follow_symlinks=True)


def _stage_libraries(dependencies: Iterable[Path], description: str) -> None:
    """
    Stage multiple libraries to the BUILD_LIBS directory with error handling.

    Args:
        libraries: Iterable of library paths to stage
        description: Optional description for error messages
    """
    print(f"\nStaging {description} dependencies")
    for dependency in dependencies:
        try:
            _stage_dependency(dependency, BUILD_LIBS)
            print(f"\t{dependency}")
        except OSError as e:
            # Some libraries might be inaccessible, continue with others
            print(f"WARN: failed to copy {dependency}: {e}")


def _build_executable(extra_binaries: list[str]) -> None:
    """
    Execute PyInstaller to create the final executable.

    The resulting executable will self-extract to a temporary directory
    at runtime and set up the library paths appropriately.

    Args:
        add_bin_args: List of --add-binary arguments for shared libraries
    """
    print("[4/4] Building PyInstaller onefile binary")

    # Set environment to use package-local browser
    env = os.environ.copy()
    env["PLAYWRIGHT_BROWSERS_PATH"] = "0"

    # Construct the full PyInstaller command
    cmd = (
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--onefile",  # Single executable output
            "--strip",
            "--optimize",
            "2",
            "--collect-all",  # Collect all files from the playwright package
            "playwright",  # Package name for --collect-all
            "--copy-metadata=playwright",  # Include playwright metadata
            "--exclude-module",
            "tkinter",
            "--exclude-module",
            "test",
            "--exclude-module",
            "unittest",
            "--exclude-module",
            "pdb",
        ]
        + extra_binaries  # --add-binary arguments
        + [str(ENTRYPOINT.name)]  # the main Python script to bundle
    )

    print("# PyInstaller command:")
    print("\n".join(cmd))

    # Run PyInstaller in the script directory so relative paths work correctly
    _run(cmd, cwd=SCRIPT_DIR, env=env)

    print("Build complete: dist/main")


if __name__ == "__main__":
    # Entry point when running as a script
    main()
