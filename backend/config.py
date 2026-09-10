import os
from pathlib import Path
from urllib.parse import unquote
from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# data.go.kr은 Encoding된 키와 Decoding된 키를 둘 다 발급하는데, 어느 쪽을 붙여넣어도
# httpx가 쿼리스트링을 만들 때 이중 인코딩되지 않도록 여기서 한 번 디코딩해둔다.
MOLIT_SERVICE_KEY = unquote(os.getenv("MOLIT_SERVICE_KEY", "").strip())

DB_PATH = ROOT_DIR / "backend" / "cache.sqlite3"
LEGAL_DONG_CSV = ROOT_DIR / "backend" / "data" / "legal_dong_codes.csv"

def has_molit_key() -> bool:
    return bool(MOLIT_SERVICE_KEY)
