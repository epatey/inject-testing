#!/usr/bin/env python3
"""
================================================================================
PORTABLE PLAYWRIGHT PYINSTALLER BUILD SCRIPT
================================================================================

PURPOSE:
This script creates a fully self-contained, portable executable from a Playwright-based
Python application using PyInstaller. It solves the complex problem of bundling Chromium
browser dependencies with the application to ensure it can run on different Linux systems
without requiring users to install Playwright or Chromium separately.

KEY CHALLENGES ADDRESSED:
1. Browser Dependency Management: Playwright normally downloads browsers to a user's home
   directory, but this script embeds Chromium directly into the package.
2. Shared Library Dependencies: Chromium requires numerous system libraries that may not
   be present on target systems. This script identifies and bundles these libraries.
3. Cross-Distribution Compatibility: By bundling most libraries (except core glibc), 
   the resulting executable can run on various Linux distributions.
4. NSS (Network Security Services) Libraries: These security libraries are dynamically
   loaded by Chromium at runtime and must be explicitly included.

WORKFLOW:
1. Install Chromium into the Playwright package directory (not user home)
2. Locate the headless_shell binary that Playwright uses for Chromium
3. Use ldd to discover all shared library dependencies
4. Explicitly add NSS libraries that may be loaded dynamically
5. Bundle everything into a single executable with PyInstaller

OUTPUT:
A single executable file in dist/main that contains:
- Your Python application code
- The Playwright library
- Chromium browser (headless_shell)
- All necessary shared libraries
- NSS security libraries

COMPATIBILITY:
- Architecture-agnostic (works on x86_64, ARM64, etc.)
- Distribution-agnostic (tested on Ubuntu, Debian, Alpine, etc.)
- Excludes core glibc libraries to maintain ABI compatibility with host system

Usage:
  .venv/bin/python build.py    # to prefer your project venv
  python build.py              # to use whatever python is on PATH
================================================================================
"""

import glob
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Set

# ================================================================================
# CONFIGURATION CONSTANTS
# ================================================================================

# Directory where this build script is located
SCRIPT_DIR = Path(__file__).parent.resolve()

# Temporary directory where collected libraries will be staged before bundling
BUILD_LIBS = SCRIPT_DIR / "build_libs"

# The main Python script that will be the entry point of the executable
ENTRYPOINT = SCRIPT_DIR / "main.py"

# Core system libraries that should NOT be bundled
# These are provided by the host OS and bundling them would break compatibility
# The dynamic linker (ld-linux) and core C libraries must match the host system
LDD_EXCLUDES = (
    "ld-linux",      # Dynamic linker/loader - must match host kernel
    "libc.so",       # Core C library - defines system ABI
    "libm.so",       # Math library - part of core glibc
    "libpthread.so", # POSIX threads - part of core glibc
    "libdl.so",      # Dynamic loading - part of core glibc
    "librt.so",      # Real-time extensions - part of core glibc
)

# NSS (Network Security Services) libraries required by Chromium
# These are often loaded dynamically at runtime using dlopen(), so they
# don't always appear in ldd output. We must explicitly include them.
# These handle SSL/TLS, certificates, and cryptographic operations.
NSS_NAMES = [
    "libsoftokn3.so",      # Software token implementation for NSS
    "libsoftokn3.chk",     # Checksum file for libsoftokn3
    "libnss3.so",          # Main NSS library
    "libnssutil3.so",      # NSS utility functions
    "libsmime3.so",        # S/MIME cryptographic functions
    "libssl3.so",          # SSL/TLS protocol implementation
    "libnssckbi.so",       # Built-in root certificates (CRITICAL for HTTPS)
    "libnspr4.so",         # Netscape Portable Runtime (NSS dependency)
    "libplc4.so",          # NSPR library for classic I/O
    "libplds4.so",         # NSPR library for data structures
    "libfreebl3.so",       # Freebl cryptographic library
    "libfreeblpriv3.so",   # Private Freebl functions
]


def run(cmd: List[str], cwd: Optional[Path] = None, env: Optional[dict] = None) -> str:
    """
    Execute a shell command and return its output.
    
    This is a wrapper around subprocess.run that:
    - Captures both stdout and stderr
    - Raises an exception with detailed error info if the command fails
    - Returns stdout as a string for successful commands
    
    Args:
        cmd: List of command arguments (first element is the executable)
        cwd: Working directory for the command (optional)
        env: Environment variables for the command (optional)
    
    Returns:
        The stdout output of the command as a string
    
    Raises:
        RuntimeError: If the command exits with non-zero status, includes
                     the command, stdout, and stderr in the error message
    """
    res = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    if res.returncode != 0:
        # Provide detailed error information for debugging
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\nstdout:\n{res.stdout}\nstderr:\n{res.stderr}"
        )
    return res.stdout


def ensure_pyinstaller_available() -> None:
    """
    Verify that PyInstaller is installed in the current Python environment.
    
    This function attempts to run PyInstaller's version command to confirm
    it's available. If PyInstaller is not found, it raises an error with
    instructions on how to install it.
    
    This check is performed early to fail fast if the build environment
    is not properly configured.
    
    Raises:
        RuntimeError: If PyInstaller is not installed, includes installation instructions
    """
    try:
        # Try to run PyInstaller as a module to check if it's available
        run([sys.executable, "-m", "PyInstaller", "--version"])
    except RuntimeError as e:
        # Provide helpful error message with installation command
        raise RuntimeError(
            "PyInstaller not found in this Python environment. "
            f"Install it with:\n  {sys.executable} -m pip install pyinstaller"
        ) from e


def playwright_install_chromium() -> None:
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
    run([sys.executable, "-m", "playwright", "install", "chromium"], env=env)


def find_headless_shell() -> Path:
    """
    Locate the headless_shell binary within the Playwright package.
    
    headless_shell is Chromium's headless-only binary that Playwright uses
    for browser automation. It's smaller than the full Chrome binary and
    designed for server/automation use cases.
    
    The binary is typically located at:
    playwright/driver/package/.local-browsers/chromium-*/chrome-linux/headless_shell
    
    Returns:
        Path to the headless_shell executable
    
    Raises:
        FileNotFoundError: If headless_shell cannot be located, suggesting
                          that Chromium installation may have failed
    """
    print("[2/4] Locating headless_shell used by Playwright")
    
    # Import playwright to find its installation directory
    import playwright  # type: ignore
    
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


def parse_ldd_paths(ldd_output: str) -> Set[Path]:
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
    paths: Set[Path] = set()
    
    for line in ldd_output.splitlines():
        # Skip lines without "=>" (like linux-vdso or statically linked)
        if "=>" not in line:
            continue
        
        # Extract the path after "=>"
        # Regex captures the non-whitespace path after "=> "
        m = re.search(r"=>\s+(\S+)", line)
        if not m:
            continue
        
        candidate = m.group(1)
        
        # Skip non-absolute paths (like "not found")
        if not candidate.startswith("/"):
            continue
        
        # Filter out core system libraries that must not be bundled
        # These libraries define the system ABI and must match the host
        if any(ex in candidate for ex in LDD_EXCLUDES):
            continue
        
        paths.add(Path(candidate))
    
    return paths


def ldd_deps(binary: Path) -> Set[Path]:
    """
    Use ldd to discover all shared library dependencies of a binary.
    
    ldd (List Dynamic Dependencies) shows all shared libraries that a binary
    links against. This is architecture-agnostic - it will work correctly
    whether running on x86_64, ARM64, or other architectures.
    
    Args:
        binary: Path to the executable to analyze
    
    Returns:
        Set of paths to required shared libraries (excluding core system libs)
    """
    print("[3/4] Collecting shared libraries via ldd (arch-agnostic)")
    
    # Run ldd on the binary
    out = run(["ldd", str(binary)])
    
    # Parse the output to extract library paths
    return parse_ldd_paths(out)


def ldconfig_lookup(name: str) -> Optional[Path]:
    """
    Look up a library by name using the ldconfig cache.
    
    ldconfig maintains a cache of shared libraries on the system.
    Using 'ldconfig -p' prints this cache, which is faster than
    searching the filesystem and respects the system's library
    configuration (ld.so.conf).
    
    Args:
        name: Library filename to search for (e.g., "libnss3.so")
    
    Returns:
        Path to the library if found, None otherwise
    """
    # Check if ldconfig is available (might not be in minimal containers)
    if shutil.which("ldconfig"):
        try:
            # Get the library cache listing
            out = run(["ldconfig", "-p"])
            
            for line in out.splitlines():
                # ldconfig -p format:
                # libX11.so.6 (libc6,x86-64) => /lib/x86_64-linux-gnu/libX11.so.6
                
                # Check if this line is for our library (must start with name + space)
                if not line.strip().startswith(name + " "):
                    continue
                
                # Extract the path after "=>"
                m = re.search(r"=>\s+(\S+)$", line.strip())
                if m:
                    p = Path(m.group(1))
                    # Verify the file actually exists
                    if p.exists():
                        return p
        except Exception:
            # ldconfig might fail in some environments, continue with fallback
            pass
    return None


def find_nss_lib(name: str) -> Optional[Path]:
    """
    Find an NSS library by name, trying multiple strategies.
    
    NSS libraries are critical for HTTPS support but may be installed
    in various locations depending on the distribution. This function:
    1. First tries the fast ldconfig cache lookup
    2. Falls back to filesystem search in common locations
    
    Args:
        name: NSS library filename (e.g., "libnssckbi.so")
    
    Returns:
        Path to the library if found, None otherwise
    """
    # Strategy 1: Try ldconfig cache (fastest)
    p = ldconfig_lookup(name)
    if p:
        return p
    
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


def copy_unique(src: Path, dest_dir: Path) -> None:
    """
    Copy a library file to the destination directory, resolving symlinks.
    
    Many libraries are symlinks (e.g., libfoo.so -> libfoo.so.1.2.3).
    This function follows symlinks to copy the actual file content,
    ensuring the bundled library is complete and functional.
    
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


def stage_libs(headless_shell: Path) -> List[str]:
    """
    Collect all required libraries and prepare PyInstaller arguments.
    
    This is the core function that:
    1. Identifies all shared libraries needed by headless_shell
    2. Explicitly adds NSS libraries (for HTTPS support)
    3. Optionally includes graphics libraries (for WebGL support)
    4. Stages all libraries in a temporary directory
    5. Generates PyInstaller --add-binary arguments
    
    Args:
        headless_shell: Path to the Chromium headless_shell binary
    
    Returns:
        List of PyInstaller --add-binary arguments for including libraries
    """
    # Clean and recreate the staging directory
    if BUILD_LIBS.exists():
        shutil.rmtree(BUILD_LIBS)
    BUILD_LIBS.mkdir(parents=True, exist_ok=True)

    # Step 1: Collect libraries identified by ldd
    # These are the libraries explicitly linked by headless_shell
    deps = ldd_deps(headless_shell)
    for lib in sorted(deps):
        try:
            copy_unique(lib, BUILD_LIBS)
        except Exception as e:
            # Some libraries might be inaccessible, continue with others
            print(f"WARN: failed to copy {lib}: {e}")

    # Step 2: Add NSS libraries explicitly
    # These are dynamically loaded at runtime for HTTPS support
    # Without these, HTTPS connections will fail with certificate errors
    for name in NSS_NAMES:
        p = find_nss_lib(name)
        if p and p.exists():
            print(f"Copying {p} to {BUILD_LIBS}")
            try:
                copy_unique(p, BUILD_LIBS)
            except Exception as e:
                print(f"WARN: failed to copy {p}: {e}")
        else:
            # Not all NSS components are required on all systems
            print(f"WARN: NSS component not found: {name}")

    # Step 3: Best-effort include graphics libraries
    # libGLESv2 is needed for WebGL support but location varies by distribution
    for pattern in ("/usr/lib/*-linux-gnu/libGLESv2.so*",):
        for gpath in glob.glob(pattern):
            gp = Path(gpath)
            if gp.exists():
                try:
                    copy_unique(gp, BUILD_LIBS)
                except Exception as e:
                    # Graphics libraries are optional, don't fail the build
                    print(f"WARN: failed to copy {gp}: {e}")

    # Step 4: Generate PyInstaller arguments
    # Each library needs a --add-binary argument in the format "source:dest"
    # The :lib suffix tells PyInstaller to place these in a lib/ subdirectory
    add_bin_args: List[str] = []
    for f in sorted(BUILD_LIBS.glob("*")):
        # Format: --add-binary /path/to/lib.so:lib
        # This bundles the library and makes it available at runtime in lib/
        add_bin_args.extend(["--add-binary", f"{str(f)}:lib"])
    
    return add_bin_args


def build_pyinstaller(add_bin_args: Iterable[str]) -> None:
    """
    Execute PyInstaller to create the final executable.
    
    This function runs PyInstaller with carefully chosen options:
    - --onefile: Create a single executable file (not a directory)
    - --noupx: Don't use UPX compression (can cause issues with some libraries)
    - --collect-all playwright: Include all playwright package files
    - --copy-metadata=playwright: Include package metadata (version info, etc.)
    - --add-binary: Include all collected shared libraries
    
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
            "--onefile",        # Single executable output
            "--noupx",          # Disable UPX compression (improves compatibility)
            "--collect-all",    # Collect all files from the playwright package
            "playwright",       # Package name for --collect-all
            "--copy-metadata=playwright",  # Include playwright metadata
        ]
        + list(add_bin_args)    # Add all the --add-binary arguments for libraries
        + [
            "-F",               # Shorthand for --onefile (redundant but explicit)
            str(ENTRYPOINT.name),  # The main Python script to bundle
        ]
    )

    # Run PyInstaller in the script directory so relative paths work correctly
    run(cmd, cwd=SCRIPT_DIR, env=env)
    
    print("Build complete: dist/main")


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
    # Step 1: Verify build environment
    ensure_pyinstaller_available()
    
    # Step 2: Install browser into package (not user home)
    playwright_install_chromium()
    
    # Step 3: Locate the browser binary
    headless_shell = find_headless_shell()
    
    # Step 4: Collect and stage all required libraries
    add_bin_args = stage_libs(headless_shell)
    
    # Step 5: Build the final executable
    build_pyinstaller(add_bin_args)


if __name__ == "__main__":
    # Entry point when running as a script
    main()