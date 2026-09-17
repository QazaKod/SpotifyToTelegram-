import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# Загружаем .env
load_dotenv()

BASE_DIR = Path(__file__).parent.resolve()
TEMP_DIR = BASE_DIR / "temp"
DATA_DIR = BASE_DIR / "data"

# Создаем необходимые директории
TEMP_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Токены и секреты
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
SPOTIFY_SP_DC = os.getenv("SPOTIFY_SP_DC", "").strip()
OWNER_TELEGRAM_ID = os.getenv("OWNER_TELEGRAM_ID", "867551644").strip()
WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()

# База данных
DATABASE_URL = f"sqlite+aiosqlite:///{DATA_DIR / 'bot.db'}"

# Настройки загрузок
MAX_CONCURRENT_DOWNLOADS = 3
AUDIO_BITRATE = "256"  # Оптимальный битрейт для быстрой конвертации без потери качества (исходник YT ~160k)
DURATION_TOLERANCE = 12  # Допустимая погрешность длительности в секундах

# Автообнаружение FFmpeg (особенно на Windows через WinGet)
def setup_ffmpeg():
    if shutil.which("ffmpeg"):
        return True

    # Проверяем типовой путь WinGet
    winget_packages = Path(os.path.expanduser(r"~\AppData\Local\Microsoft\WinGet\Packages"))
    if winget_packages.exists():
        ffmpeg_bins = list(winget_packages.glob("**/ffmpeg.exe"))
        if ffmpeg_bins:
            ffmpeg_dir = ffmpeg_bins[0].parent
            os.environ["PATH"] = str(ffmpeg_dir) + os.pathsep + os.environ.get("PATH", "")
            return True

    return False

setup_ffmpeg()

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin").strip()
