"""Start the admin page on 127.0.0.1 only: uv run python -m admin"""

import uvicorn

from admin.app import BRANCH

if __name__ == "__main__":
    print(
        f"\n  KidNews admin -> http://127.0.0.1:8001   (database: {BRANCH.upper()})\n"
    )
    uvicorn.run("admin.app:app", host="127.0.0.1", port=8001, log_level="warning")
