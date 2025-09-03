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

# pip install playwright
# NODE_OPTIONS='' PLAYWRIGHT_BROWSERS_PATH=0  playwright install chromium
# NODE_OPTIONS='' playwright install-deps
