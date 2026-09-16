import asyncio
import config

# Семафор для ограничения одновременных тяжелых операций (yt-dlp + ffmpeg)
download_semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_DOWNLOADS)
