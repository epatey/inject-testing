#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import os
import sys
from pathlib import Path
import tempfile

tmp_dir = Path(tempfile.mkdtemp())  # guaranteed writable
user_data_dir = tmp_dir / "chromium_profile"
user_data_dir.mkdir()

print(f"hoping to use {user_data_dir=}")

# When running as a PyInstaller onefile binary, all bundled shared libs are extracted
# under sys._MEIPASS. Ensure the dynamic linker can find them by prepending that
# lib directory to LD_LIBRARY_PATH before launching Chromium.
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
  meipass = Path(getattr(sys, "_MEIPASS"))
  lib_dir = meipass / "lib"
  existing_ld = os.environ.get("LD_LIBRARY_PATH", "")
  new_ld = f"{lib_dir}:{existing_ld}" if existing_ld else str(lib_dir)
  os.environ["LD_LIBRARY_PATH"] = new_ld
  # Hint Playwright to use packaged browsers and skip host validation inside minimal containers
  os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")
  os.environ.setdefault("PLAYWRIGHT_SKIP_VALIDATE_HOST_REQUIREMENTS", "1")


def main():
  print("in main")
  try:

    os.environ['DEBUG'] = 'pw:api,pw:browser*'
    # os.environ['PWDEBUG'] = '1'  # This will slow down execution but provide more details

    with sync_playwright() as p:
        print("with pw")

        browser = p.chromium.launch(headless=True)
        # browser = p.chromium.launch_persistent_context(
        #     user_data_dir=str(user_data_dir),
        #     headless=True,
        #     args=["--no-sandbox", "--password-store=basic"]
        # )
        
        page = browser.new_page()
        page.goto("https://playwright.dev/")
        h2_text = page.locator("h1").first.text_content()
        print(f"First H2: {h2_text}")
        browser.close()
  except Exception as ex:
    print(f"caught {ex}")
    raise

if __name__ == "__main__":
    main()
