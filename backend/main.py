from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Security,
    UploadFile,
    status,
    Query,
    Response,
)
from fastapi.security import APIKeyHeader
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, HttpUrl
import subprocess
import shutil
import os
from pathlib import Path
from typing import Literal, Optional
import logging
from datetime import datetime
from dotenv import load_dotenv
import sys
from time import perf_counter
from urllib.parse import urlparse
from io import BytesIO
import tempfile
from starlette.background import BackgroundTask
from openai import OpenAI
import base64
import mimetypes
import re
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
import requests

# Configuração de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Diretórios base
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT_DIR / "config"
COOKIES_DIR = CONFIG_DIR / "cookies"
FRONTEND_DIR = ROOT_DIR / "frontend"
FRONTEND_BUILD_DIR = FRONTEND_DIR / "dist"
# Use temporary directory for downloads (auto-cleanup)
DOWNLOADS_DIR = Path(tempfile.gettempdir()) / "n8n-download-bridge"

# Configurações
load_dotenv(ROOT_DIR / ".env")
load_dotenv(CONFIG_DIR / ".env", override=False)

API_KEY = os.getenv("API_KEY", "")
if not API_KEY:
    raise RuntimeError("API_KEY não configurada. Defina API_KEY no arquivo .env")
DEFAULT_TRANSCRIBE_PROMPT = (
    "Atue como um transcritor de documentos. Analise a imagem fornecida e "
    "transcreva todo o texto visível exatamente como ele aparece."
)
# Prompt em inglês para frames de vídeo - modelos vision funcionam melhor em inglês para OCR
VIDEO_FRAME_PROMPT = (
    "Extract ALL visible text from this video frame. Include: overlays, captions, subtitles, "
    "words in boxes or bubbles, titles, hashtags, labels, and any text on screen. "
    "Return ONLY the raw text, exactly as shown. Do not describe or explain."
)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_AUDIO_MODEL = os.getenv("OPENAI_AUDIO_MODEL", "whisper-1")
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
# Para frames de vídeo: gpt-4o tem OCR melhor. Defina OPENAI_VIDEO_FRAME_MODEL=gpt-4o se quiser
OPENAI_VIDEO_FRAME_MODEL = os.getenv("OPENAI_VIDEO_FRAME_MODEL", OPENAI_VISION_MODEL)

# Cookies
COOKIES_CANDIDATES = [
    COOKIES_DIR / "cookies.txt",
    ROOT_DIR / "cookies.txt",  # compatibilidade com estrutura antiga
]
PRIMARY_COOKIES_FILE = COOKIES_CANDIDATES[0]
DOMAIN_COOKIE_MAP: dict[str, list[Path]] = {
    "tiktok.com": [
        COOKIES_DIR / "www.tiktok.com_cookies.txt",
        ROOT_DIR / "www.tiktok.com_cookies.txt",
    ],
    "instagram.com": [
        COOKIES_DIR / "www.instagram.com_cookies.txt",
        ROOT_DIR / "www.instagram.com_cookies.txt",
    ],
}
ALT_COOKIES_FILES = [p for paths in DOMAIN_COOKIE_MAP.values() for p in paths]
_cookies_cache: dict[Path, tuple[float, Optional[list[str]]]] = {}


def get_cookies_args(url: Optional[str] = None) -> list[str]:
    """
    Retorna os argumentos de cookies para yt-dlp/gallery-dl.
    Usa arquivos de cookies (compatível com VPS sem navegador instalado).
    Prioriza cookies específicos por domínio quando disponíveis.
    """
    candidates: list[Path] = []
    lower_host = ""
    if url:
        try:
            lower_host = urlparse(url).netloc.lower()
        except Exception:
            lower_host = url.lower()

    # Prioriza cookies específicos por domínio quando houver
    for domain, cookie_paths in DOMAIN_COOKIE_MAP.items():
        if domain in lower_host:
            for cookie_path in cookie_paths:
                if cookie_path not in candidates:
                    candidates.append(cookie_path)

    # Fallbacks genéricos (não usa cookies de outros domínios)
    for p in COOKIES_CANDIDATES:
        if p not in candidates:
            candidates.append(p)

    for file_path in candidates:
        args = _cached_cookie_args(file_path)
        if args:
            return args
    return []


def _cached_cookie_args(file_path: Path) -> Optional[list[str]]:
    """Lê cookies com cache por mtime para evitar I/O a cada requisição."""
    try:
        if not file_path.exists():
            _cookies_cache.pop(file_path, None)
            return None

        stat = file_path.stat()
        mtime = stat.st_mtime
        cached = _cookies_cache.get(file_path)
        if cached and cached[0] == mtime:
            return cached[1] or None

        if stat.st_size == 0:
            _cookies_cache[file_path] = (mtime, None)
            logger.info(f"{file_path.name} está vazio; ignorando.")
            return None

        with file_path.open("r", encoding="utf-8", errors="ignore") as f:
            head = f.read(200)
            if "HTTP Cookie File" not in head and "# Netscape" not in head:
                logger.warning(f"{file_path.name} não parece Netscape; ignorando.")
                _cookies_cache[file_path] = (mtime, None)
                return None

        args = ["--cookies", str(file_path)]
        _cookies_cache[file_path] = (mtime, args)
        logger.info(f"Usando cookies de {file_path.name}")
        return args
    except Exception as exc:
        _cookies_cache.pop(file_path, None)
        logger.warning(f"Não foi possível ler {file_path.name}, ignorando: {exc}")
        return None


_yt_dlp_path_cache: Optional[str] = None
_gallery_dl_path_cache: Optional[str] = None
_ffmpeg_path_cache: Optional[str] = None
_openai_client: Optional[OpenAI] = None


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def get_ffmpeg_location() -> Optional[str]:
    """
    Resolve o caminho do ffmpeg.
    Prioridade:
    1) FFMPEG_PATH (arquivo ou diretório)
    2) bin/ffmpeg dentro do projeto
    3) ffmpeg do PATH
    """
    global _ffmpeg_path_cache
    if _ffmpeg_path_cache is not None:
        return _ffmpeg_path_cache or None

    env_path = os.getenv("FFMPEG_PATH")
    if env_path:
        candidate = Path(env_path)
        if candidate.is_dir():
            ffmpeg_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            dir_ffmpeg = candidate / ffmpeg_name
            if _is_executable(dir_ffmpeg):
                _ffmpeg_path_cache = str(candidate)
                logger.info(f"ffmpeg selecionado via FFMPEG_PATH (dir): {_ffmpeg_path_cache}")
                return _ffmpeg_path_cache
        if _is_executable(candidate):
            _ffmpeg_path_cache = str(candidate)
            logger.info(f"ffmpeg selecionado via FFMPEG_PATH: {_ffmpeg_path_cache}")
            return _ffmpeg_path_cache
        logger.warning(f"FFMPEG_PATH configurado, mas inválido: {env_path}")

    local_bin = ROOT_DIR / "bin"
    ffmpeg_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    local_ffmpeg = local_bin / ffmpeg_name
    if _is_executable(local_ffmpeg):
        _ffmpeg_path_cache = str(local_bin)
        logger.info(f"ffmpeg selecionado no projeto: {_ffmpeg_path_cache}")
        return _ffmpeg_path_cache

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        _ffmpeg_path_cache = system_ffmpeg
        logger.info(f"ffmpeg encontrado no PATH: {_ffmpeg_path_cache}")
        return _ffmpeg_path_cache

    _ffmpeg_path_cache = ""
    logger.warning("ffmpeg não encontrado; conversões podem falhar.")
    return None


def get_ffmpeg_location_arg() -> list[str]:
    location = get_ffmpeg_location()
    if location:
        return ["--ffmpeg-location", location]
    return []


def choose_yt_dlp_binary_for_url(url: str) -> str:
    """
    Para TikTok, usa ./bin/yt-dlp se disponível (tem curl_cffi para impersonation).
    Para outras URLs, usa o binário do sistema (Homebrew/PATH).
    """
    lower = url.lower()
    if "tiktok.com" in lower:
        alt_bin = str(ROOT_DIR / "bin" / "yt-dlp")
        if _is_executable(Path(alt_bin)):
            return alt_bin
    return get_yt_dlp_binary()


def get_yt_dlp_binary() -> str:
    """
    Resolve binário do yt-dlp priorizando Homebrew/PATH.
    Ordem:
    1) YT_DLP_PATH (override explícito)
    2) yt-dlp do PATH (Homebrew)
    3) Binário do venv (pip)
    4) Repo local yt-dlp-master (download do GitHub) como fallback
    """
    global _yt_dlp_path_cache
    if _yt_dlp_path_cache:
        return _yt_dlp_path_cache

    env_path = os.getenv("YT_DLP_PATH")
    if env_path:
        env_candidate = Path(env_path)
        if _is_executable(env_candidate):
            _yt_dlp_path_cache = str(env_candidate)
            logger.info(f"yt-dlp selecionado via YT_DLP_PATH: {_yt_dlp_path_cache}")
            return _yt_dlp_path_cache
        logger.warning(f"YT_DLP_PATH configurado, mas não executável: {env_path}")

    candidates: list[str] = []

    # Priorizar yt-dlp do PATH (Homebrew)
    path_bin = shutil.which("yt-dlp")
    if path_bin:
        candidates.append(path_bin)

    venv_bin = Path(sys.executable).parent / "yt-dlp"
    if _is_executable(venv_bin):
        candidates.append(str(venv_bin))

    local_repo = ROOT_DIR / "yt-dlp-master"
    for name in ("yt-dlp.sh", "yt-dlp"):
        candidate = local_repo / name
        if _is_executable(candidate):
            candidates.append(str(candidate))

    for path in candidates:
        _yt_dlp_path_cache = path
        logger.info(f"yt-dlp selecionado: {_yt_dlp_path_cache}")
        return _yt_dlp_path_cache

    _yt_dlp_path_cache = "yt-dlp"
    logger.info("yt-dlp selecionado: yt-dlp (PATH)")
    return _yt_dlp_path_cache


def get_gallery_dl_binary() -> str:
    """
    Resolve binário do gallery-dl priorizando a instalação estável (pip/venv).
    Ordem:
    1) GALLERY_DL_PATH (override explícito)
    2) Binário do venv (pip)
    3) gallery-dl do PATH
    4) Repo local gallery-dl-master/bin/gallery-dl como fallback
    """
    global _gallery_dl_path_cache
    if _gallery_dl_path_cache:
        return _gallery_dl_path_cache

    env_path = os.getenv("GALLERY_DL_PATH")
    if env_path:
        env_candidate = Path(env_path)
        if _is_executable(env_candidate):
            _gallery_dl_path_cache = str(env_candidate)
            logger.info(f"gallery-dl selecionado via GALLERY_DL_PATH: {_gallery_dl_path_cache}")
            return _gallery_dl_path_cache
        logger.warning(f"GALLERY_DL_PATH configurado, mas não executável: {env_path}")

    candidates: list[str] = []

    venv_bin = Path(sys.executable).parent / "gallery-dl"
    if _is_executable(venv_bin):
        candidates.append(str(venv_bin))

    path_bin = shutil.which("gallery-dl")
    if path_bin:
        candidates.append(path_bin)

    local_repo = ROOT_DIR / "gallery-dl-master"
    for name in ("bin/gallery-dl", "gallery-dl"):
        candidate = local_repo / name
        if _is_executable(candidate):
            candidates.append(str(candidate))

    for path in candidates:
        _gallery_dl_path_cache = path
        logger.info(f"gallery-dl selecionado: {_gallery_dl_path_cache}")
        return _gallery_dl_path_cache

    _gallery_dl_path_cache = "gallery-dl"
    logger.info("gallery-dl selecionado: gallery-dl (PATH)")
    return _gallery_dl_path_cache


def download_tiktok_audio_via_tikwm(url: str, output_dir: Path, audio_format: str = "mp3") -> Path:
    """
    Baixa vídeo TikTok via tikwm.com e extrai o áudio usando ffmpeg.
    Retorna o caminho do arquivo de áudio.
    Optimized with reduced timeouts.
    """
    logger.info(f"🎵 Baixando TikTok e extraindo áudio via tikwm.com: {url}")

    try:
        # Primeiro, baixar o vídeo via tikwm
        video_result = download_tiktok_via_tikwm(url, output_dir)
        video_path = Path(video_result["file_path"])

        # Gerar nome do arquivo de áudio
        audio_filename = video_path.stem + f".{audio_format}"
        audio_path = output_dir / audio_filename

        # Extrair áudio usando ffmpeg
        ffmpeg_bin = resolve_ffmpeg_binary()
        cmd = [
            ffmpeg_bin,
            "-i", str(video_path),
            "-vn",  # sem vídeo
            "-acodec", "libmp3lame" if audio_format == "mp3" else "copy",
            "-y",  # sobrescrever
            str(audio_path)
        ]

        logger.info(f"Extraindo áudio com ffmpeg: {' '.join(cmd)}")
        subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True)  # Reduced from 120s to 60s

        # Remover arquivo de vídeo temporário
        video_path.unlink(missing_ok=True)
        logger.info(f"✅ Áudio extraído com sucesso via tikwm.com: {audio_path}")

        return audio_path

    except subprocess.CalledProcessError as e:
        logger.error(f"Erro ao extrair áudio com ffmpeg: {e.stderr}")
        raise Exception(f"Falha ao extrair áudio: {str(e)}")
    except Exception as e:
        logger.error(f"Erro ao baixar TikTok e extrair áudio: {e}")
        raise


def download_tiktok_via_tikwm(url: str, output_dir: Path) -> dict:
    """
    Download de TikTok usando a API gratuita do tikwm.com.
    OPTIMIZED for maximum speed: reduced timeouts, larger chunks, connection reuse.
    """
    from time import perf_counter
    t0 = perf_counter()
    
    # Use session for connection reuse (faster)
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Connection": "keep-alive",
        "Accept": "*/*",
        "Accept-Encoding": "gzip, deflate",
    })

    try:
        # Fast API call with reduced timeout
        api_url = f"https://www.tikwm.com/api/?url={url}"
        t1 = perf_counter()
        resp = session.get(api_url, timeout=8, verify=False)  # Faster: 8s timeout, skip SSL verification
        resp.raise_for_status()
        data = resp.json()
        t2 = perf_counter()
        logger.info(f"⚡ tikwm API responded in {(t2-t1)*1000:.0f}ms")

        if data.get("code") != 0:
            raise Exception(f"API tikwm retornou erro: {data.get('msg', 'Unknown error')}")

        video_data = data.get("data", {})

        # Get video URL (prefer no watermark)
        video_url = video_data.get("play") or video_data.get("wmplay")
        if not video_url:
            raise Exception("Nenhuma URL de vídeo encontrada na resposta do tikwm")

        # Generate filename
        video_id = video_data.get("id", "tiktok")
        author = video_data.get("author", {}).get("unique_id", "unknown")
        filename = f"tiktok_{author}_{video_id}.mp4"
        file_path = output_dir / filename

        # Fast video download with larger chunks
        t3 = perf_counter()
        video_resp = session.get(video_url, timeout=30, stream=True, verify=False)
        video_resp.raise_for_status()

        # Use 64KB chunks for faster I/O
        with open(file_path, "wb") as f:
            for chunk in video_resp.iter_content(chunk_size=65536):  # 64KB chunks
                if chunk:
                    f.write(chunk)

        t4 = perf_counter()
        file_size = file_path.stat().st_size
        size_mb = file_size / (1024 * 1024)
        total_time = t4 - t0
        speed_mbps = (size_mb * 8) / total_time if total_time > 0 else 0
        logger.info(f"⚡ TikTok downloaded: {size_mb:.2f}MB in {total_time:.2f}s ({speed_mbps:.1f} Mbps)")

        return {
            "success": True,
            "file_path": str(file_path),
            "file_size": get_file_size(file_path),
            "format": "mp4",
            "title": video_data.get("title", ""),
            "author": author,
            "source": "tikwm"
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de rede ao usar tikwm: {e}")
        raise Exception(f"Falha na API tikwm: {str(e)}")
    except Exception as e:
        logger.error(f"Erro ao usar tikwm: {e}")
        raise


def get_impersonate_args(url: str) -> list[str]:
    """
    Retorna args de impersonation para TikTok (Chrome-120).
    Se não for TikTok, retorna vazio.
    """
    lowered = url.lower()
    if "tiktok.com" not in lowered:
        return []
    return ["--impersonate", "Chrome-120"]


_impersonation_cache_by_path: dict[str, bool] = {}


def get_openai_client() -> OpenAI:
    """Retorna cliente OpenAI com cache."""
    global _openai_client
    if _openai_client:
        return _openai_client
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY não configurada")
    _openai_client = OpenAI(api_key=OPENAI_API_KEY)
    return _openai_client



def detectPlatform(url: str) -> str:
    """Helper function to detect platform from URL"""
    lower_url = url.lower()
    if "youtube.com" in lower_url or "youtu.be" in lower_url:
        return "youtube"
    if "tiktok.com" in lower_url:
        return "tiktok"
    if "instagram.com" in lower_url:
        return "instagram"
    if "twitter.com" in lower_url or "x.com" in lower_url:
        return "twitter"
    return "video"


def get_youtube_best_quality_args() -> list[str]:
    """
    Returns yt-dlp arguments for YouTube ALWAYS best quality.
    Use in all YouTube download paths (execute_ytdlp_optimized, stream_ytdlp, stream_ytdlp_merge, execute_ytdlp).
    - No player_client specified: let yt-dlp auto-select working client (tries multiple: android, ios, web, mweb)
    - Sort: resolution descending, then fps, so we get 4K/1080p first.
    - Format: best video + best audio (any codec: VP9, AV1, H.264), fallback to single best.
    """
    return [
        "-S", "res,fps",  # Prefer highest resolution, then highest fps
        "-f", "bv*+ba/bestvideo+bestaudio/best",
    ]


def download_via_ytdlp_fallback(url: str, audio_only: bool = False) -> dict:
    """
    Fallback to yt-dlp if Cobalt fails.
    OPTIMIZED for maximum download speed while maintaining max quality.
    Uses aria2c multi-threading when available.
    """
    from time import perf_counter
    t_start = perf_counter()
    platform = detectPlatform(url)
    logger.info(f"⚡ Using OPTIMIZED yt-dlp fallback for {platform}: {url}")
    
    # Use the existing yt-dlp infrastructure
    if audio_only:
        # Use audio extraction
        audio_path = download_audio_from_url(url, "mp3")
        with open(audio_path, "rb") as f:
            content = f.read()
        audio_path.unlink(missing_ok=True)
        
        t_end = perf_counter()
        logger.info(f"✅ Audio downloaded via yt-dlp fallback for {platform} in {(t_end - t_start) * 1000:.0f}ms")
        
        return {
            "blob": content,
            "filename": audio_path.name,
            "content_type": "audio/mpeg",
            "size": len(content)
        }
    else:
        # Use video download with optimizations
        result = execute_ytdlp_optimized(url, output_format="mp4")
        file_path = Path(result["file_path"])
        
        with open(file_path, "rb") as f:
            content = f.read()
        
        filename = file_path.name
        file_path.unlink(missing_ok=True)
        
        t_end = perf_counter()
        size_mb = len(content) / (1024 * 1024)
        speed_mbps = (size_mb * 8) / ((t_end - t_start) or 1)  # Avoid division by zero
        logger.info(f"✅ Video downloaded via yt-dlp fallback for {platform}: {size_mb:.2f}MB in {(t_end - t_start):.1f}s ({speed_mbps:.1f} Mbps)")
        
        return {
            "blob": content,
            "filename": filename,
            "content_type": "video/mp4",
            "size": len(content)
        }


def sanitize_filename(filename: str, max_length: int = 200) -> str:
    """
    Sanitize filename to be filesystem-safe and limit length.
    Removes invalid characters and truncates if too long.
    """
    # Remove invalid characters for filenames
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    
    # Remove leading/trailing spaces and dots
    filename = filename.strip('. ')
    
    # Truncate if too long (leave room for extension)
    if len(filename) > max_length:
        filename = filename[:max_length]
    
    # If empty after sanitization, use fallback
    if not filename:
        filename = "video"
    
    return filename


def extract_username_from_instagram_url(original_url: str) -> str:
    """
    Extract Instagram username from URL.
    Tries to find username in URL patterns, returns sanitized name or fallback.
    
    Examples:
        https://www.instagram.com/username/reel/ID → username
        https://www.instagram.com/reel/ID → instagram (fallback)
        https://www.instagram.com/p/ID → instagram (fallback)
    """
    # Pattern: /username/content_type/ID
    # Match format like: instagram.com/USERNAME/(reel|p|stories)/...
    match = re.search(r'instagram\.com/([^/]+)/(reel|p|stories|tv)/', original_url)
    if match:
        username = match.group(1)
        # Exclude reserved paths that aren't usernames
        if username not in ['reel', 'p', 'stories', 'tv', 'explore', 'accounts']:
            return sanitize_filename(username, max_length=50)
    
    # Try alternative pattern: /username/ at the end or followed by query params
    match = re.search(r'instagram\.com/([^/?#]+)/?(?:\?|#|$)', original_url)
    if match:
        username = match.group(1)
        if username not in ['reel', 'p', 'stories', 'tv', 'explore', 'accounts']:
            return sanitize_filename(username, max_length=50)
    
    # Fallback
    return "instagram"


def execute_ytdlp_optimized(url: str, output_format: str = "mp4") -> dict:
    """
    yt-dlp execution with BEST QUALITY (no aggressive optimizations).
    Simple and reliable download with best available formats.
    Uses uploader name or video title as filename.
    """
    t0 = perf_counter()
    platform = detectPlatform(url)
    
    # Use meaningful filenames: uploader name or video title
    # YouTube: %(title)s gives the video title
    # Instagram: %(uploader)s gives the account name
    # TikTok: %(uploader)s gives the username
    if platform == "youtube":
        # For YouTube, use video title as filename
        output_template = str(DOWNLOADS_DIR / "%(title)s.%(ext)s")
    else:
        # For other platforms, use uploader/username
        output_template = str(DOWNLOADS_DIR / "%(uploader)s_%(id)s.%(ext)s")
    
    cmd = [choose_yt_dlp_binary_for_url(url)]
    cmd.extend(get_cookies_args(url))
    cmd.extend(get_impersonate_args(url))
    cmd.extend(get_ffmpeg_location_arg())
    
    # YouTube: ALWAYS best quality, keep original codec (no re-encode = much faster)
    if platform == "youtube":
        cmd.extend(get_youtube_best_quality_args())
        cmd.extend([
            '--merge-output-format', 'mp4',  # merge to mp4 container
            '--remux-video', 'mp4',  # remux without re-encoding (fast)
        ])
    else:
        # Other platforms: H.264 + AAC for universal playback
        cmd.extend([
            '-f', 'bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',
            '--postprocessor-args', 'ffmpeg:-c:v libx264 -c:a aac',
            '--recode-video', 'mp4',
            '--merge-output-format', 'mp4',
        ])
    
    cmd.extend([
        '--no-check-certificate',
        '--no-playlist',
        '--restrict-filenames',
        '--trim-filenames', '200',
        '-o', output_template,
        '--progress',
        '--newline',
    ])
    
    cmd.append(url)
    logger.info(f"⚡ Executing optimized yt-dlp: {' '.join(cmd[:10])}...")
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 minute timeout for large files
        )
        
        t1 = perf_counter()
        
        if result.returncode != 0:
            logger.error(f"yt-dlp error: {result.stderr}")
            raise Exception(f"yt-dlp failed: {result.stderr[:500]}")
        
        # Find the downloaded file (search by pattern since filename is dynamic)
        downloaded_files = sorted(
            DOWNLOADS_DIR.glob("*.mp4"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        
        if not downloaded_files:
            raise Exception("No file downloaded")
        
        # Get the most recently modified file (the one we just downloaded)
        file_path = downloaded_files[0]
        file_size = file_path.stat().st_size
        size_mb = file_size / (1024 * 1024)
        download_time = t1 - t0
        speed_mbps = (size_mb * 8) / (download_time or 1)
        
        logger.info(f"⚡ Optimized download complete: {size_mb:.1f}MB in {download_time:.1f}s ({speed_mbps:.1f} Mbps)")
        logger.info(f"📁 Filename: {file_path.name}")
        
        return {
            "success": True,
            "file_path": str(file_path),
            "file_size": get_file_size(file_path),
            "format": file_path.suffix.lstrip('.'),
            "download_time": download_time,
            "speed_mbps": speed_mbps,
        }
        
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timed out after 600s")
        raise Exception("Download timed out")
    except Exception as e:
        logger.error(f"Optimized yt-dlp error: {e}")
        raise


def resolve_ffmpeg_binary() -> str:
    """Resolve o binário do ffmpeg para chamadas diretas."""
    location = get_ffmpeg_location()
    if location and Path(location).is_dir():
        name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        return str(Path(location) / name)
    return location or "ffmpeg"


def download_audio_from_url(url: str, audio_format: str = "mp3") -> Path:
    """Baixa apenas o áudio usando yt-dlp e retorna o caminho do arquivo."""

    # Para TikTok, tentar tikwm.com primeiro, mas fallback para yt-dlp se falhar
    if "tiktok" in url.lower():
        try:
            logger.info("TikTok detectado - tentando API tikwm.com para áudio")
            return download_tiktok_audio_via_tikwm(url, DOWNLOADS_DIR, audio_format)
        except Exception as e:
            logger.warning(f"tikwm.com áudio falhou: {e}. Usando yt-dlp como fallback.")

    t0 = perf_counter()
    platform = detectPlatform(url)
    
    # Use meaningful filenames based on platform
    if platform == "youtube":
        output_template = str(DOWNLOADS_DIR / "%(title)s.%(ext)s")
    else:
        output_template = str(DOWNLOADS_DIR / "%(uploader)s_%(id)s.%(ext)s")

    # Detectar YouTube para otimizações
    lower_url = url.lower()
    is_youtube = "youtube.com" in lower_url or "youtu.be" in lower_url

    cmd: list[str] = [choose_yt_dlp_binary_for_url(url)]
    cmd.extend(get_cookies_args(url))
    cmd.extend(get_impersonate_args(url))
    cmd.extend(get_ffmpeg_location_arg())

    # Adicionar otimizações do YouTube antes da extração de áudio
    if is_youtube:
        cmd.extend([
            '--extractor-args', 'youtube:player_client=android',  # Emular cliente Android
            '--http-chunk-size', '10M',  # Chunks de 10MB
        ])
        # Verificar se aria2c está disponível para multi-threading
        if shutil.which("aria2c"):
            cmd.extend([
                '--external-downloader', 'aria2c',
                '--external-downloader-args', '-x 16 -s 16 -k 1M',  # 16 conexões paralelas
            ])
            logger.info("YouTube áudio: Usando aria2c com 16 conexões paralelas")

    cmd.extend([
        "-x",
        "--audio-format",
        audio_format,
        "-o",
        output_template,
        "--no-playlist",
        "--restrict-filenames",  # ASCII-only filenames
        "--trim-filenames", "200",  # Limit filename length
        "--progress",
        "--newline"
    ])
    cmd.append(url)

    logger.info(f"Baixando áudio com yt-dlp: {' '.join(cmd)}")
    try:
        t1 = perf_counter()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=True
        )
        t2 = perf_counter()

        # Find the most recently downloaded audio file
        audio_extensions = [f"*.{audio_format}", "*.m4a", "*.mp3", "*.opus", "*.ogg"]
        downloaded_files = []
        for ext in audio_extensions:
            downloaded_files.extend(DOWNLOADS_DIR.glob(ext))
        
        if not downloaded_files:
            raise Exception("Áudio não encontrado após download")
        
        # Get the most recently modified file
        downloaded_files = sorted(downloaded_files, key=lambda p: p.stat().st_mtime, reverse=True)

        file_path = downloaded_files[0]
        logger.info(
            "yt-dlp áudio timing prep=%.1fms run=%.1fms",
            (t1 - t0) * 1000,
            (t2 - t1) * 1000,
        )
        logger.debug(result.stdout)
        return file_path
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Download de áudio timeout (5 minutos)")
    except subprocess.CalledProcessError as exc:
        logger.error(f"Erro ao baixar áudio: {exc.stderr}")
        error_msg = exc.stderr or str(exc)

        # Provide more user-friendly error messages
        if "Unable to extract" in error_msg or "Unable to download" in error_msg or "IP address is blocked" in error_msg or "Video not available" in error_msg:
            raise HTTPException(
                status_code=503,
                detail="Falha ao extrair vídeo. O site pode ter mudado ou bloqueado o acesso."
            )
        else:
            raise HTTPException(status_code=500, detail=f"Erro no download: {error_msg[:200]}")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


def extract_audio_from_upload(upload_path: Path, audio_format: str = "mp3") -> Path:
    """Extrai áudio de um arquivo local usando ffmpeg."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = DOWNLOADS_DIR / f"audio_extract_{timestamp}.{audio_format}"
    ffmpeg_bin = resolve_ffmpeg_binary()

    codec_args = {
        "mp3": ["-acodec", "libmp3lame"],
        "m4a": ["-acodec", "aac"],
        "wav": ["-acodec", "pcm_s16le", "-ar", "16000"],
    }.get(audio_format, ["-acodec", "libmp3lame"])

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(upload_path),
        "-vn",
        *codec_args,
        str(output_path),
    ]

    logger.info(f"Extraindo áudio via ffmpeg: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, capture_output=True, text=True, timeout=240, check=True)
        if not output_path.exists():
            raise Exception("Arquivo de áudio não gerado")
        return output_path
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Extração de áudio timeout (4 minutos)")
    except subprocess.CalledProcessError as exc:
        logger.error(f"Erro no ffmpeg: {exc.stderr}")
        raise HTTPException(status_code=500, detail="Falha ao extrair áudio")


def transcribe_audio_file(audio_path: Path, language: Optional[str] = None) -> str:
    """Transcreve áudio com Whisper."""
    try:
        with audio_path.open("rb") as f:
            result = get_openai_client().audio.transcriptions.create(
                model=OPENAI_AUDIO_MODEL,
                file=f,
                language=language,
                response_format="text"
            )
        if isinstance(result, str):
            return result
        text = getattr(result, "text", None)
        return text or str(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Erro ao transcrever áudio: {exc}")
        raise HTTPException(status_code=500, detail="Falha na transcrição de áudio")


def transcribe_image_bytes(
    data: bytes,
    mime_type: str = "image/png",
    prompt: Optional[str] = None,
    detail: Literal["low", "high", "auto"] = "auto",
    model: Optional[str] = None,
) -> str:
    """Transcreve texto de uma imagem usando modelo vision."""
    effective_prompt = prompt or DEFAULT_TRANSCRIBE_PROMPT
    model_to_use = model or OPENAI_VISION_MODEL
    try:
        b64 = base64.b64encode(data).decode()
        image_url_config: dict = {"url": f"data:{mime_type};base64,{b64}"}
        if detail != "auto":
            image_url_config["detail"] = detail
        res = get_openai_client().chat.completions.create(
            model=model_to_use,
            messages=[
                {
                    "role": "system",
                    "content": "Extract all visible text from the image. Return only the text, nothing else."
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": effective_prompt},
                        {"type": "image_url", "image_url": image_url_config},
                    ],
                },
            ],
            temperature=0,
            max_tokens=800,
        )
        return res.choices[0].message.content.strip()
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(f"Erro ao transcrever imagem: {exc}")
        raise HTTPException(status_code=500, detail="Falha na transcrição de imagem")


def get_video_duration_seconds(file_path: Path) -> float:
    """Obtém duração do vídeo em segundos via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(file_path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError) as e:
        logger.warning(f"ffprobe failed for {file_path}: {e}")
    return 0.0


def extract_video_frames_as_png(file_path: Path, num_frames: int = 1) -> list[bytes]:
    """
    Extrai frames do vídeo como PNG usando ffmpeg.
    Distribui os frames ao longo da duração (0%, 25%, 50%, 75%, 100%).
    Retorna lista de bytes PNG. Usa fallbacks se extração falhar.
    """
    duration = get_video_duration_seconds(file_path)
    if duration <= 0:
        duration = 5.0
        num_frames = 3
        timestamps = [0.0, 1.0, 2.0]
        logger.info(f"Duration unknown for {file_path.name}, extracting 3 frames at 0,1,2s")
    else:
        # Mais frames para capturar texto que aparece em momentos específicos
        num_frames = min(max(num_frames, 1), 12)
        timestamps = []
        for i in range(num_frames):
            t = (i / (num_frames - 1)) * duration if num_frames > 1 else 0
            timestamps.append(min(max(t, 0), max(0, duration - 0.05)))
    out_dir = Path(tempfile.mkdtemp(prefix="video_frames_"))
    frames: list[bytes] = []
    try:
        for i, ts in enumerate(timestamps):
            out_file = out_dir / f"frame_{i:02d}.png"
            # -ss antes de -i = seek rápido; -noautorotate evita problemas com metadata
            cmd = [
                "ffmpeg", "-y", "-noautorotate",
                "-ss", str(ts),
                "-i", str(file_path),
                "-vframes", "1",
                "-f", "image2",
                "-q:v", "2",
                str(out_file),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if result.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
                frames.append(out_file.read_bytes())
            else:
                # Fallback: tentar com -i antes de -ss (mais lento, mais preciso)
                stderr_preview = (result.stderr or "")[-300:] if result.stderr else ""
                logger.warning(f"ffmpeg frame {i} ts={ts}s failed, trying accurate seek: {stderr_preview}")
                cmd_fallback = [
                    "ffmpeg", "-y", "-noautorotate",
                    "-i", str(file_path),
                    "-ss", str(ts),
                    "-vframes", "1",
                    "-f", "image2",
                    str(out_file),
                ]
                result2 = subprocess.run(cmd_fallback, capture_output=True, text=True, timeout=90)
                if result2.returncode == 0 and out_file.exists() and out_file.stat().st_size > 0:
                    frames.append(out_file.read_bytes())
        if not frames:
            logger.warning(f"No frames extracted from {file_path} (tried {len(timestamps)} timestamps)")
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)
    return frames


def transcribe_video_frames(file_path: Path, prompt: Optional[str] = None) -> str:
    """
    Extrai frames do vídeo, transcreve cada um como imagem e junta os textos.
    Usa prompt específico para texto em vídeo (overlays, legendas, etc).
    """
    # Apenas o primeiro frame: muitos vídeos têm legendas que mudam; o frame inicial traz o conteúdo principal
    frames = extract_video_frames_as_png(file_path, num_frames=1)
    if not frames:
        logger.warning(f"Nenhum frame extraído de {file_path}")
        return ""
    effective_prompt = prompt or VIDEO_FRAME_PROMPT
    texts: list[str] = []
    seen: set[str] = set()
    for i, png_bytes in enumerate(frames):
        try:
            # gpt-4o + detail="high" para melhor OCR de texto em overlays
            text = transcribe_image_bytes(
                png_bytes,
                mime_type="image/png",
                prompt=effective_prompt,
                detail="high",
                model=OPENAI_VIDEO_FRAME_MODEL,
            )
            text = (text or "").strip()
            if text and text not in seen:
                seen.add(text)
                texts.append(text)
        except Exception as e:
            logger.warning(f"Erro ao transcrever frame {i} do vídeo {file_path.name}: {e}")
    result = "\n\n".join(texts).strip()
    logger.info(f"Video {file_path.name}: {len(frames)} frames, {len(texts)} texts extracted")
    return result


def clean_instagram_filename(url: str, username: str, index: int) -> str:
    """
    Gera filename limpo para imagens do Instagram a partir da URL do CDN.
    Remove query parameters e gera nome descritivo.
    
    Exemplos:
        https://.../.../image.jpg?param=value -> instagram_username_01.jpg
    """
    try:
        # Parse URL e remove query parameters
        from urllib.parse import urlparse
        parsed = urlparse(url)
        path = parsed.path
        
        # Extrair extensão do arquivo
        ext = '.jpg'  # Default
        if '.' in path:
            # Pegar a parte do path antes de qualquer query param
            clean_path = path.split('?')[0]
            if '.' in clean_path:
                base_ext = clean_path.rsplit('.', 1)[-1].lower()
                # Validar extensão
                if base_ext in ['jpg', 'jpeg', 'png', 'webp', 'gif', 'mp4', 'webm', 'mov', 'm4v']:
                    ext = f'.{base_ext}'
        
        # Gerar filename limpo: instagram_username_01.jpg ou .mp4
        filename = f"instagram_{username}_{index:02d}{ext}"
        return sanitize_filename(filename, max_length=100)
    except Exception as e:
        logger.warning(f"Failed to clean filename from {url}: {e}")
        return f"instagram_image_{index:02d}.jpg"


def transcribe_instagram_carousel(url: str, prompt: Optional[str]) -> list[dict]:
    """Baixa imagens do carrossel e transcreve cada uma em paralelo para maior velocidade."""
    t0 = perf_counter()
    
    # Extract username from URL for clean filenames
    username = extract_username_from_instagram_url(url)
    logger.info(f"📸 Extracting carousel from @{username}")
    
    # First, get direct URLs (for frontend display)
    direct_urls = execute_gallery_dl_urls(url)
    logger.info(f"📸 Found {len(direct_urls)} direct URLs")
    
    # Then download and transcribe
    result = execute_gallery_dl(url)
    t1 = perf_counter()
    logger.info(f"⏱️ gallery-dl download: {(t1 - t0) * 1000:.0f}ms")

    raw_download_dir = result.get("download_dir")
    download_dir = Path(raw_download_dir) if raw_download_dir else None
    files = result.get("files", [])
    if not files:
        raise HTTPException(status_code=404, detail="Nenhuma imagem encontrada")

    # Prepare tasks for parallel processing
    tasks = []
    for idx, info in enumerate(files, start=1):
        file_path = Path(info.get("path", ""))
        # Get corresponding direct URL (if available)
        direct_url = direct_urls[idx - 1] if idx <= len(direct_urls) else None
        
        if not file_path.exists():
            continue
        if file_path.stat().st_size > 25 * 1024 * 1024:
            tasks.append((idx, file_path, direct_url, None, "Arquivo maior que 25MB, ignorado"))
            continue

        mime, _ = mimetypes.guess_type(file_path.name)
        mime = mime or "image/png"

        # Skip video files - only transcribe images
        if mime.startswith("video/"):
            tasks.append((idx, file_path, direct_url, mime, "video"))
        elif mime.startswith("image/"):
            tasks.append((idx, file_path, direct_url, mime, "image"))
        else:
            tasks.append((idx, file_path, direct_url, mime, "unknown"))

    items = []
    try:
        # Process images in parallel using ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=5) as executor:
            def process_image(task_data):
                idx, file_path, direct_url, mime, task_type = task_data
                t_start = perf_counter()

                # Generate clean filename for download
                clean_filename = clean_instagram_filename(direct_url, username, idx)
                
                if task_type == "video":
                    # Extrai frames do vídeo, converte em PNG e transcreve os textos
                    text = transcribe_video_frames(file_path, prompt=prompt)
                    t_end = perf_counter()
                    logger.info(f"⏱️ Transcribed video (frames) {file_path.name}: {(t_end - t_start) * 1000:.0f}ms")
                    return {
                        "index": idx,
                        "file": file_path.name,
                        "url": direct_url,
                        "filename": clean_filename,
                        "is_video": True,
                        "text": text
                    }
                elif task_type == "image" or task_type == "unknown":
                    text = transcribe_image_bytes(file_path.read_bytes(), mime_type=mime, prompt=prompt)
                    t_end = perf_counter()
                    logger.info(f"⏱️ Transcribed {file_path.name}: {(t_end - t_start) * 1000:.0f}ms")
                    return {
                        "index": idx,
                        "file": file_path.name,
                        "url": direct_url,
                        "filename": clean_filename,  # Clean filename for download
                        "is_video": False,
                        "text": text
                    }
                else:
                    clean_filename = clean_instagram_filename(direct_url, username, idx)
                    return {
                        "index": idx,
                        "file": file_path.name,
                        "url": direct_url,
                        "filename": clean_filename,  # Clean filename for download
                        "error": task_type
                    }

            t2 = perf_counter()
            # Execute all transcriptions in parallel
            items = list(executor.map(process_image, tasks))
            t3 = perf_counter()
            logger.info(f"⏱️ Parallel transcription total: {(t3 - t2) * 1000:.0f}ms for {len(tasks)} items")
            logger.info(f"⏱️ Total carousel processing: {(t3 - t0) * 1000:.0f}ms")
    finally:
        if download_dir and download_dir.exists():
            shutil.rmtree(download_dir, ignore_errors=True)

    if not items:
        raise HTTPException(status_code=404, detail="Nenhum item processado")
    return items

# Criar diretórios essenciais
COOKIES_DIR.mkdir(parents=True, exist_ok=True)
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# FastAPI app
app = FastAPI(
    title="N8N Download Bridge API",
    description="API para download de vídeos e imagens via yt-dlp e gallery-dl",
    version="2.0.0"
)

# GZip compression for API responses (not for video/image files)
app.add_middleware(GZipMiddleware, minimum_size=1000)

# CORS para chamadas de browser (preflight/OPTIONS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-File-Size", "X-Tool-Used", "X-Format", "X-Total-Files", "X-Processing-Time-Ms"],
)

# Security
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

# Frontend estático (UI) - prioriza build do Vite (frontend/dist)
if FRONTEND_BUILD_DIR.exists():
    app.mount("/ui", StaticFiles(directory=FRONTEND_BUILD_DIR, html=True), name="ui")
elif FRONTEND_DIR.exists():
    app.mount("/ui", StaticFiles(directory=FRONTEND_DIR, html=True), name="ui")
else:
    logger.warning("Frontend directory não encontrado; rota /ui desabilitada.")


# Models
class DownloadRequest(BaseModel):
    url: HttpUrl
    tool: Literal["yt-dlp", "gallery-dl"]
    format: Optional[Literal["mp4", "webm", "best"]] = "mp4"
    quality: Optional[str] = "best"


class DownloadResponse(BaseModel):
    success: bool
    message: str
    file_path: Optional[str] = None
    file_size: Optional[str] = None
    direct_url: Optional[str] = None
    thumbnail_url: Optional[str] = None
    tool_used: str
    format: Optional[str] = None
    direct_urls: Optional[list[str]] = None  # para respostas que trazem várias URLs


class AudioDownloadRequest(BaseModel):
    url: HttpUrl
    format: Optional[Literal["mp3", "m4a", "wav"]] = "mp3"
    language: Optional[str] = None


class InstagramTranscribeRequest(BaseModel):
    url: HttpUrl
    prompt: Optional[str] = None  # ignorado; usamos prompt padrão


# Dependency para validar API Key
async def validate_api_key(api_key: str = Security(api_key_header)):
    if api_key != API_KEY:
        logger.warning(f"Tentativa de acesso com API Key inválida: {api_key[:10]}...")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="API Key inválida"
        )
    return api_key


def get_file_size(file_path: Path) -> str:
    """Retorna o tamanho do arquivo em formato legível"""
    size = file_path.stat().st_size
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024.0:
            return f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}TB"


def cleanup_path(path: Path):
    """Remove arquivo ou diretório de forma segura (para BackgroundTask)"""
    try:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
                logger.info(f"Cleaned up directory: {path}")
            else:
                path.unlink(missing_ok=True)
                logger.info(f"Cleaned up file: {path}")
    except Exception as e:
        logger.warning(f"Failed to cleanup {path}: {e}")


def execute_ytdlp(url: str, download_file: bool = True, output_format: str = "mp4") -> dict:
    """Executa yt-dlp e retorna informações do download"""

    # Para TikTok, tentar tikwm.com primeiro, mas fallback para yt-dlp se falhar
    if "tiktok" in url.lower() and download_file:
        try:
            logger.info("TikTok detectado - tentando API tikwm.com")
            return download_tiktok_via_tikwm(url, DOWNLOADS_DIR)
        except Exception as e:
            logger.warning(f"tikwm.com falhou: {e}. Usando yt-dlp como fallback.")

    t0 = perf_counter()
    platform = detectPlatform(url)
    
    # Use meaningful filenames based on platform
    if download_file:
        if platform == "youtube":
            output_template = str(DOWNLOADS_DIR / "%(title)s.%(ext)s")
        else:
            output_template = str(DOWNLOADS_DIR / "%(uploader)s_%(id)s.%(ext)s")
    else:
        # For URL extraction, timestamp is fine
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_template = str(DOWNLOADS_DIR / f"video_{timestamp}.%(ext)s")

    # Comando base
    cmd = [choose_yt_dlp_binary_for_url(url)]

    # Adicionar cookies se existir
    cookies_args = get_cookies_args(url)
    cmd.extend(cookies_args)
    t1 = perf_counter()
    # Adicionar impersonation se necessário
    cmd.extend(get_impersonate_args(url))
    lower_url = url.lower()
    is_tiktok = "tiktok.com" in lower_url
    is_youtube = "youtube.com" in lower_url or "youtu.be" in lower_url

    if download_file:
        # Configurar formato de acordo com a preferência
        if output_format == "mp4":
            if is_tiktok:
                # Preferir stream progressivo (sem merge) para acelerar
                cmd.extend([
                    "-f",
                    "bv*[ext=mp4][protocol!*=dash][protocol!*=m3u8][acodec!=none]/"
                    "b[ext=mp4]/best"
                ])
            elif is_youtube:
                # YouTube: ALWAYS best quality (same as execute_ytdlp_optimized)
                cmd.extend(get_youtube_best_quality_args())
                cmd.extend([
                    '--concurrent-fragments', '16',
                    '--buffer-size', '32K',
                    '--http-chunk-size', '10M',
                    '--retries', '3',
                    '--fragment-retries', '3',
                    '--no-check-certificate',
                ])
                if shutil.which("aria2c"):
                    cmd.extend([
                        '--external-downloader', 'aria2c',
                        '--external-downloader-args',
                        'aria2c:-x 16 -s 16 -k 2M --min-split-size=1M --max-connection-per-server=16 --enable-http-pipelining=true',
                    ])
                    logger.info("⚡ YouTube: aria2c + best quality (res,fps)")
                else:
                    logger.info("⚡ YouTube: best quality (res,fps)")
            else:
                # Preferir h264/aac para compatibilidade ampla (Safari/iOS)
                cmd.extend([
                    '-f',
                    'bv*[vcodec^=avc1][ext=mp4]+ba[ext=m4a]/'
                    'bv*[vcodec^=h264][ext=mp4]+ba[ext=m4a]/'
                    'b[ext=mp4]',
                    '--merge-output-format', 'mp4',
                    '--remux-video', 'mp4'
                ])
        elif output_format == "webm":
            cmd.extend(['-f', 'bestvideo[ext=webm]+bestaudio[ext=webm]/best[ext=webm]/best'])
        else:  # best
            cmd.extend(['-f', 'best'])

        if is_tiktok:
            cmd.extend(["--concurrent-fragments", "8"])
        cmd.extend([
            '-o', output_template,
            '--no-playlist',
            '--restrict-filenames',  # ASCII-only filenames
            '--trim-filenames', '200',  # Limit filename length
            '--progress',
            '--newline',
            '--no-warnings'
        ])
    else:
        # Modo URL direta: retorna melhor URL disponível (pode ser m3u8/MP4)
        if is_tiktok:
            cmd.extend([
                '--skip-download',
                '--no-warnings',
                '--print', 'thumbnail',
                '--print', 'url',
                '--concurrent-fragments', '8',
            ])
        else:
            cmd.extend([
                '--skip-download',
                '--print', 'thumbnail',
                '--print', 'url',
            ])

    cmd.append(str(url))

    logger.info(f"Executando: {' '.join(cmd)}")

    t2 = perf_counter()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=True
        )
        t3 = perf_counter()

        if download_file:
            # Procurar arquivo baixado (search by most recent since filename is dynamic)
            downloaded_files = sorted(
                DOWNLOADS_DIR.glob("*.mp4"),
                key=lambda p: p.stat().st_mtime,
                reverse=True
            )
            if downloaded_files:
                file_path = downloaded_files[0]
                t4 = perf_counter()
                logger.info(
                    "yt-dlp timing cookies=%.1fms prep=%.1fms run=%.1fms post=%.1fms",
                    (t1 - t0) * 1000,  # resolução de cookies
                    (t2 - t1) * 1000,  # montagem de comando
                    (t3 - t2) * 1000,  # execução yt-dlp
                    (t4 - t3) * 1000,  # pós-processamento
                )
                return {
                    "success": True,
                    "file_path": str(file_path),
                    "file_size": get_file_size(file_path),
                    "output": result.stdout,
                    "format": file_path.suffix[1:]  # Remove o ponto da extensão
                }
            else:
                raise Exception("Arquivo não encontrado após download")
        else:
            # Retornar URL direta + thumbnail (quando disponível)
            lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            thumbnail_url = None
            direct_urls: list[str] = []
            if not lines:
                raise Exception("Nenhuma URL retornada pelo yt-dlp")

            if len(lines) >= 2:
                thumb = lines[0]
                if thumb.lower() not in ("na", "none"):
                    thumbnail_url = thumb
                direct_urls = [line for line in lines[1:] if line.lower() not in ("na", "none")]
            else:
                if lines[0].lower() not in ("na", "none"):
                    direct_urls = [lines[0]]

            direct_url = direct_urls[0] if direct_urls else None
            t4 = perf_counter()
            logger.info(
                "yt-dlp timing cookies=%.1fms prep=%.1fms run=%.1fms post=%.1fms",
                (t1 - t0) * 1000,  # resolução de cookies
                (t2 - t1) * 1000,  # montagem de comando
                (t3 - t2) * 1000,  # execução yt-dlp
                (t4 - t3) * 1000,  # pós-processamento
            )
            return {
                "success": True,
                "direct_url": direct_url,
                "direct_urls": direct_urls,
                "thumbnail_url": thumbnail_url,
                "output": result.stdout
            }

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Download timeout (5 minutos)")
    except subprocess.CalledProcessError as e:
        logger.error(f"Erro no yt-dlp: {e.stderr}")
        error_msg = e.stderr or str(e)

        # Provide more user-friendly error messages
        if "Unable to extract" in error_msg or "Unable to download" in error_msg or "IP address is blocked" in error_msg or "Video not available" in error_msg:
            if "instagram" in url.lower():
                raise HTTPException(
                    status_code=503,
                    detail="Instagram bloqueou o download. Verifique se a conta é privada ou tente novamente mais tarde."
                )
            elif "youtube" in url.lower():
                raise HTTPException(
                    status_code=503,
                    detail="YouTube bloqueou o download. Tente novamente em alguns minutos."
                )
            else:
                raise HTTPException(
                    status_code=503,
                    detail="Falha ao extrair vídeo. O site pode ter mudado ou bloqueado o acesso."
                )
        elif "Private video" in error_msg or "private" in error_msg.lower():
            raise HTTPException(
                status_code=403,
                detail="Vídeo privado. Não é possível baixar vídeos privados."
            )
        elif "not available" in error_msg.lower():
            raise HTTPException(
                status_code=404,
                detail="Vídeo não disponível ou foi removido."
            )
        else:
            raise HTTPException(status_code=500, detail=f"Erro no download: {error_msg[:200]}")
    except Exception as e:
        logger.error(f"Erro inesperado: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def stream_ytdlp(url: str, output_format: str = "mp4") -> dict:
    """
    Executa yt-dlp mandando saída para stdout (sem gravar em disco).
    Retorna bytes do arquivo e metadados básicos.
    """
    t0 = perf_counter()

    cmd = [choose_yt_dlp_binary_for_url(url)]
    cmd.extend(get_cookies_args(url))
    cmd.extend(get_impersonate_args(url))
    lower_url = url.lower()
    is_tiktok = "tiktok.com" in lower_url
    is_youtube = "youtube.com" in lower_url or "youtu.be" in lower_url

    # YouTube: always best quality (same logic as execute_ytdlp_optimized)
    if is_youtube:
        cmd.extend(get_youtube_best_quality_args())
        fmt = None  # already added by helper
    elif is_tiktok:
        fmt = (
            "bv*[ext=mp4][protocol!*=dash][protocol!*=m3u8][acodec!=none]/"
            "b[ext=mp4]/best"
        )
    elif output_format == "mp4":
        fmt = (
            "best[ext=mp4][vcodec!=none][acodec!=none][protocol!*=m3u8]/"
            "best[ext=mp4][vcodec!=none][acodec!=none]/"
            "best[ext=mp4][protocol!*=m3u8]/"
            "best[protocol!*=m3u8]"
        )
    elif output_format == "webm":
        fmt = (
            "best[ext=webm][vcodec!=none][acodec!=none][protocol!*=m3u8]/"
            "best[ext=webm][vcodec!=none][acodec!=none]/"
            "best[ext=webm][protocol!*=m3u8]/"
            "best[protocol!*=m3u8]"
        )
    else:
        fmt = "best"

    if fmt is not None:
        cmd.extend(["-f", fmt])
    cmd.extend([
        "-o", "-",
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        "--no-progress",
    ])

    if is_tiktok:
        cmd.extend(["--concurrent-fragments", "8"])

    cmd.append(str(url))
    logger.info(f"Streaming yt-dlp: {' '.join(cmd)}")

    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=600
        )
        data = result.stdout or b""
        if not data:
            raise HTTPException(status_code=500, detail="The downloaded stream is empty")
        # Verificação rápida de header MP4/WebM para evitar enviar lixo
        if output_format == "mp4" and b"ftyp" not in data[:128]:
            raise HTTPException(status_code=500, detail="Stream não é MP4 válido (faltando ftyp); tente /download/binary")
        if output_format == "webm" and not (data.startswith(b"\x1aE\xdf\xa3") or b"webm" in data[:128].lower()):
            raise HTTPException(status_code=500, detail="Stream não é WebM válido; tente /download/binary")
        mime = "video/mp4" if output_format == "mp4" else "video/webm"
        t1 = perf_counter()
        logger.info("yt-dlp stream timing total=%.1fms", (t1 - t0) * 1000)
        return {
            "data": data,
            "mime": mime,
            "size": len(data),
            "format": output_format
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Streaming timeout (10 minutos)")
    except subprocess.CalledProcessError as e:
        logger.error(f"Erro no yt-dlp (stream): {e.stderr}")
        raise HTTPException(status_code=500, detail=f"Erro no yt-dlp: {e.stderr.decode(errors='ignore')}")
    except Exception as e:
        logger.error(f"Erro inesperado (stream): {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def stream_ytdlp_merge(url: str, output_format: str = "mp4") -> dict:
    """
    Baixa melhor qualidade (vídeo+áudio separados) e mescla em arquivo temporário.
    Remove o arquivo após o streaming (via BackgroundTask).
    """
    t0 = perf_counter()

    # caminho temporário controlado
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{output_format}")
    tmp_path = Path(tmp.name)
    tmp.close()

    cmd = [choose_yt_dlp_binary_for_url(url)]
    cmd.extend(get_cookies_args(url))
    cmd.extend(get_impersonate_args(url))
    lower_url = url.lower()
    is_youtube = "youtube.com" in lower_url or "youtu.be" in lower_url
    if is_youtube:
        cmd.extend(get_youtube_best_quality_args())
    cmd.extend([
        "-f", "bv*+ba/bestvideo+bestaudio/best",
        "--merge-output-format", output_format,
        "-o", str(tmp_path.with_suffix(".%(ext)s")),
        "--no-playlist",
        "--newline",
    ])
    cmd.append(str(url))

    logger.info(f"Streaming (merge) yt-dlp: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            timeout=900
        )
        # localizar arquivo gerado (pode ter extensão real diferente)
        generated = list(tmp_path.parent.glob(f"{tmp_path.stem}.*"))
        if not generated:
            raise HTTPException(status_code=500, detail="Arquivo mesclado não encontrado")
        file_path = generated[0]
        mime = "video/mp4" if output_format == "mp4" else "video/webm"
        size = file_path.stat().st_size
        if size == 0:
            raise HTTPException(status_code=500, detail="Arquivo mesclado está vazio")
        t1 = perf_counter()
        logger.info("yt-dlp merge timing total=%.1fms", (t1 - t0) * 1000)
        return {
            "file_path": file_path,
            "mime": mime,
            "size": size,
            "format": output_format
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Merge timeout (15 minutos)")
    except subprocess.CalledProcessError as e:
        logger.error(f"Erro no yt-dlp (merge stream): {e.stderr}")
        raise HTTPException(status_code=500, detail=f"Erro no yt-dlp: {e.stderr.decode(errors='ignore')}")
    except Exception as e:
        logger.error(f"Erro inesperado (merge stream): {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def execute_gallery_dl(url: str) -> dict:
    """Executa gallery-dl e retorna informações do download"""

    t0 = perf_counter()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = DOWNLOADS_DIR / f"gallery_{timestamp}"
    output_dir.mkdir(exist_ok=True)

    cmd = [get_gallery_dl_binary()]

    # Adicionar cookies se existir
    cookies_args = get_cookies_args(url)
    cmd.extend(cookies_args)
    t1 = perf_counter()

    cmd.extend([
        "-d", str(output_dir),
        "--write-metadata",
        str(url)
    ])

    logger.info(f"Executando: {' '.join(cmd)}")

    t2 = perf_counter()
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=True
        )
        t3 = perf_counter()

        # Procurar arquivos baixados com seus metadados
        file_with_metadata = []
        for f in output_dir.rglob("*"):
            if not f.is_file() or f.name.endswith('.json'):
                continue

            # Procurar arquivo de metadata correspondente
            metadata_file = f.with_suffix(f.suffix + '.json')
            num = None
            if metadata_file.exists():
                try:
                    with open(metadata_file, 'r') as mf:
                        meta = json.load(mf)
                        # Instagram usa 'num' para indicar a posição no carrossel
                        num = meta.get('num', meta.get('count', meta.get('position')))
                except Exception as e:
                    logger.warning(f"Erro ao ler metadata de {f.name}: {e}")

            file_with_metadata.append((f, num))

        # Ordenar: primeiro por 'num' (se disponível), depois por nome natural
        def sort_key(item):
            path, num = item
            if num is not None:
                return (0, num, path.name)
            # Natural sort para arquivos sem metadata
            parts = re.split(r'(\d+)', path.name)
            natural_parts = [int(part) if part.isdigit() else part for part in parts]
            return (1, 0, natural_parts)

        file_with_metadata.sort(key=sort_key)
        downloaded_files = [path for path, _ in file_with_metadata]

        if downloaded_files:
            files_info = [
                {
                    "path": str(f),
                    "size": get_file_size(f),
                    "name": f.name
                }
                for f in downloaded_files
            ]

            t4 = perf_counter()
            logger.info(
                "gallery-dl timing cookies=%.1fms prep=%.1fms run=%.1fms post=%.1fms",
                (t1 - t0) * 1000,  # resolução de cookies
                (t2 - t1) * 1000,  # montagem de comando
                (t3 - t2) * 1000,  # execução gallery-dl
                (t4 - t3) * 1000,  # pós-processamento
            )
            return {
                "success": True,
                "files": files_info,
                "output": result.stdout,
                "download_dir": str(output_dir)
            }
        else:
            raise Exception("Nenhum arquivo encontrado após download")

    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Download timeout (5 minutos)")
    except subprocess.CalledProcessError as e:
        logger.error(f"Erro no gallery-dl: {e.stderr}")
        raise HTTPException(status_code=500, detail=f"Erro no gallery-dl: {e.stderr}")
    except Exception as e:
        logger.error(f"Erro inesperado: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def execute_gallery_dl_urls(url: str) -> list[str]:
    """Retorna URLs diretas usando gallery-dl sem baixar arquivos"""
    cmd = [get_gallery_dl_binary(), "-g"]
    cmd.extend(get_cookies_args(url))
    cmd.append(str(url))

    logger.info(f"Executando (URLs apenas): {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            check=True
        )
        urls: list[str] = []

        def _looks_like_media(u: str) -> bool:
            try:
                parsed = urlparse(u)
                host = parsed.netloc.lower()
                path = parsed.path.lower()
            except Exception:
                return True

            ext = Path(path).suffix.lstrip(".").lower()
            blocked_ext = {"json", "txt", "html", "xml"}
            allowed_ext = {
                "mp4", "webm", "mov", "m4v", "mp3", "m4a", "aac",
                "jpg", "jpeg", "png", "gif", "webp", "m3u8", "mpd"
            }

            if ext in blocked_ext:
                return False
            if ext in allowed_ext:
                return True

            # Instagram: ignore URLs que não são CDN de mídia
            if "instagram.com" in host and not any(cdn in host for cdn in ["cdninstagram.com", "fbcdn.net", "fna.fbcdn.net"]):
                return False

            # TikTok: se não tem extensão e não parece arquivo, provavelmente é página HTML
            if "tiktok.com" in host and not ext:
                return False

            return True

        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            # Skip ytdl: prefixed lines (not direct URLs)
            if line.startswith("ytdl:"):
                continue

            # gallery-dl às vezes retorna linhas com prefixo "| " ou texto extra;
            # extrai a primeira URL http(s) válida para evitar gerar paths inválidos no frontend
            if line.startswith("|"):
                line = line.lstrip("|").strip()

            match = re.search(r"https?://\S+", line)
            if not match:
                continue

            candidate = match.group(0).rstrip("|,\"'")
            if candidate.startswith(("http://", "https://")) and candidate not in urls and _looks_like_media(candidate):
                urls.append(candidate)

        if not urls:
            raise HTTPException(status_code=404, detail="Nenhuma URL retornada pelo gallery-dl")
        return urls
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=408, detail="Timeout ao obter URLs (3 minutos)")
    except subprocess.CalledProcessError as e:
        logger.error(f"Erro no gallery-dl (URLs): {e.stderr}")
        raise HTTPException(status_code=500, detail=f"Erro no gallery-dl: {e.stderr}")
    except Exception as e:
        logger.error(f"Erro inesperado (URLs): {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


def zip_directory(source_dir: Path) -> Path:
    """Compacta um diretório em ZIP e retorna o caminho do arquivo"""
    zip_name = DOWNLOADS_DIR / f"{source_dir.name}.zip"
    try:
        if zip_name.exists():
            zip_name.unlink()
        shutil.make_archive(zip_name.with_suffix(''), 'zip', root_dir=source_dir)
        return zip_name
    except Exception as e:
        logger.error(f"Erro ao zipar diretório {source_dir}: {e}")
        raise HTTPException(status_code=500, detail="Falha ao gerar ZIP")


# Endpoints
@app.get("/")
async def root():
    # Redireciona para a UI quando ela estiver presente
    if FRONTEND_DIR.exists():
        return RedirectResponse(url="/ui", status_code=307)
    return {
        "name": "N8N Download Bridge API",
        "version": "2.0.0",
        "status": "online",
        "endpoints": {
            "/health": "Health check",
            "/download": "Download e retorna JSON com info do arquivo",
            "/download/binary": "Download e retorna arquivo binário direto",
            "/download/url": "Retorna URL direta sem fazer download"
        }
    }


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "yt-dlp": subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True).stdout.strip(),
        "gallery-dl": subprocess.run(["gallery-dl", "--version"], capture_output=True, text=True).stdout.strip(),
        "cookies_file_exists": any(path.exists() for path in COOKIES_CANDIDATES + ALT_COOKIES_FILES),
        "downloads_dir": str(DOWNLOADS_DIR),
        "cookies_dir": str(COOKIES_DIR)
    }


@app.get("/youtube/formats")
async def get_youtube_formats(
    url: str = Query(..., description="YouTube video URL"),
    api_key: str = Security(validate_api_key)
):
    """
    Get all available formats for a YouTube video without downloading.
    Returns video and audio quality options with file sizes.
    """
    import json
    logger.info(f"📋 Fetching formats for: {url}")
    
    # #region agent log
    try:
        with open(str(ROOT_DIR / ".cursor" / "debug.log"), 'a') as f:
            f.write(json.dumps({"location":"main.py:1824","message":"get_youtube_formats entry","data":{"url":url[:100]},"timestamp":int(__import__('time').time()*1000),"sessionId":"debug-session","runId":"run1","hypothesisId":"D,E"})+"\n")
    except: pass
    # #endregion
    
    try:
        cmd = [choose_yt_dlp_binary_for_url(url)]
        cmd.extend(get_cookies_args(url))
        cmd.extend([
            '-F',  # List all formats
            '--no-warnings',
            '--no-playlist',  # Don't process playlists, only the single video
            '--playlist-end', '1',  # Safety: only process first item if playlist detected
            url
        ])
        
        # #region agent log
        try:
            with open(str(ROOT_DIR / ".cursor" / "debug.log"), 'a') as f:
                f.write(json.dumps({"location":"main.py:1844","message":"Before subprocess.run","data":{"cmd":' '.join(cmd[:6])+"..."},"timestamp":int(__import__('time').time()*1000),"sessionId":"debug-session","runId":"run2","hypothesisId":"E"})+"\n")
        except: pass
        # #endregion
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,  # Reduced to 30 seconds since we're not processing playlists
            check=True
        )
        
        # #region agent log
        try:
            with open(str(ROOT_DIR / ".cursor" / "debug.log"), 'a') as f:
                f.write(json.dumps({"location":"main.py:1852","message":"After subprocess.run","data":{"returncode":result.returncode,"stdoutLength":len(result.stdout),"stderrLength":len(result.stderr)},"timestamp":int(__import__('time').time()*1000),"sessionId":"debug-session","runId":"run1","hypothesisId":"E"})+"\n")
        except: pass
        # #endregion
        
        # Parse yt-dlp format output
        formats = []
        lines = result.stdout.split('\n')
        
        for line in lines:
            # Skip header and empty lines
            if not line.strip() or 'format code' in line.lower() or line.startswith('-'):
                continue
                
            # Parse format line
            parts = line.split()
            if len(parts) < 3:
                continue
                
            format_id = parts[0]
            ext = parts[1]
            
            # Extract resolution and filesize
            resolution = 'audio only' if 'audio only' in line else ''
            filesize = ''
            note = ''
            
            for i, part in enumerate(parts):
                if 'x' in part and part.replace('x', '').isdigit():
                    resolution = part
                elif 'MiB' in part or 'KiB' in part or 'GiB' in part:
                    # Clean up the filesize
                    if i > 0:
                        size_num = parts[i-1].lstrip('|~≈')
                        if size_num.replace('.', '').isdigit():
                            filesize = f"~{size_num} {part}"
                        else:
                            filesize = f"~{part}"
                    else:
                        filesize = f"~{part}"
                elif part.endswith('p') and part[:-1].isdigit():
                    resolution = part
            
            # Quality labels
            if 'audio only' in line:
                note = 'Áudio'
            elif '2160' in line or '4k' in line.lower():
                note = '4K Ultra HD'
            elif '1440' in line:
                note = '2K Quad HD'
            elif '1080' in line:
                note = 'Full HD 1080p'
            elif '720' in line:
                note = 'HD 720p'
            elif '480' in line:
                note = 'SD 480p'
            elif '360' in line:
                note = 'SD 360p'
            
            if note:  # Only include formats we can label
                formats.append({
                    'format_id': format_id,
                    'ext': ext,
                    'resolution': resolution,
                    'filesize': filesize,
                    'note': note
                })
        
        logger.info(f"✅ Found {len(formats)} formats")
        
        # #region agent log
        try:
            with open(str(ROOT_DIR / ".cursor" / "debug.log"), 'a') as f:
                f.write(json.dumps({"location":"main.py:1915","message":"Before return response","data":{"formatsCount":len(formats)},"timestamp":int(__import__('time').time()*1000),"sessionId":"debug-session","runId":"run1","hypothesisId":"G"})+"\n")
        except: pass
        # #endregion
        
        return {
            'success': True,
            'formats': formats
        }
        
    except subprocess.TimeoutExpired as e:
        # #region agent log
        try:
            with open(str(ROOT_DIR / ".cursor" / "debug.log"), 'a') as f:
                f.write(json.dumps({"location":"main.py:1922","message":"TimeoutExpired","data":{"error":str(e)},"timestamp":int(__import__('time').time()*1000),"sessionId":"debug-session","runId":"run1","hypothesisId":"E"})+"\n")
        except: pass
        # #endregion
        raise HTTPException(status_code=408, detail="Timeout ao buscar formatos")
    except subprocess.CalledProcessError as e:
        # #region agent log
        try:
            with open(str(ROOT_DIR / ".cursor" / "debug.log"), 'a') as f:
                f.write(json.dumps({"location":"main.py:1927","message":"CalledProcessError","data":{"returncode":e.returncode,"stderr":e.stderr[:200] if e.stderr else None},"timestamp":int(__import__('time').time()*1000),"sessionId":"debug-session","runId":"run1","hypothesisId":"E"})+"\n")
        except: pass
        # #endregion
        logger.error(f"Erro ao buscar formatos: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao buscar formatos: {str(e)}")
    except Exception as e:
        # #region agent log
        try:
            with open(str(ROOT_DIR / ".cursor" / "debug.log"), 'a') as f:
                f.write(json.dumps({"location":"main.py:1932","message":"General Exception","data":{"errorType":type(e).__name__,"errorMessage":str(e)[:200]},"timestamp":int(__import__('time').time()*1000),"sessionId":"debug-session","runId":"run1","hypothesisId":"A,E"})+"\n")
        except: pass
        # #endregion
        logger.error(f"Erro ao buscar formatos: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Erro ao buscar formatos: {str(e)}")


@app.post("/download", response_model=DownloadResponse)
async def download_json(
    request: DownloadRequest,
    api_key: str = Security(validate_api_key)
):
    """
    Faz download e retorna JSON com informações do arquivo.
    Ideal para: processos que precisam de metadados do arquivo.
    """
    logger.info(f"Download JSON request: {request.url} usando {request.tool}")

    try:
        if request.tool == "yt-dlp":
            result = execute_ytdlp(str(request.url), download_file=True, output_format=request.format)
            return DownloadResponse(
                success=True,
                message="Download concluído com sucesso",
                file_path=result.get("file_path"),
                file_size=result.get("file_size"),
                tool_used="yt-dlp",
                format=result.get("format")
            )
        else:
            result = execute_gallery_dl(str(request.url))
            first_file = result["files"][0] if result["files"] else {}
            return DownloadResponse(
                success=True,
                message=f"Download concluído: {len(result['files'])} arquivo(s)",
                file_path=first_file.get("path"),
                file_size=first_file.get("size"),
                tool_used="gallery-dl"
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no download: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/download/binary")
async def download_binary(
    url: str = Query(..., description="URL do vídeo/imagem"),
    format: Literal["mp4", "webm", "best"] = Query(default="mp4", description="Formato do vídeo"),
    quality: str = Query(default="max", description="Qualidade do vídeo (max, 1080, 720, 480)"),
    api_key: str = Security(validate_api_key)
):
    """
    Universal download endpoint for all platforms:
    - Instagram: Returns JSON with direct URL
    - TikTok: Returns JSON with direct URL  
    - YouTube: Downloads and returns file
    - Twitter/X: Downloads and returns file
    """
    logger.info(f"🚀 Binary download request: {url} formato={format} qualidade={quality}")
    
    t_start = perf_counter()
    platform = detectPlatform(url)
    
    try:
        # Instagram: Try gallery-dl first, fallback to yt-dlp
        if platform == "instagram":
            logger.info("📸 Instagram detected - trying gallery-dl first")
            try:
                # Extract direct URLs only (fast, no download)
                urls = execute_gallery_dl_urls(url)
                
                if not urls:
                    raise Exception("No media URLs found from gallery-dl")
                
                # Get first URL (for reels/posts with single video)
                direct_url = urls[0]
                
                # Extract username from original URL
                username = extract_username_from_instagram_url(url)
                
                t_end = perf_counter()
                total_ms = int((t_end - t_start) * 1000)
                logger.info(f"✅ Instagram direct URL via gallery-dl in {total_ms}ms")
                
                # Return JSON with direct URL for frontend redirect
                return Response(
                    content=json.dumps({
                        "direct_url": direct_url,
                        "platform": platform,
                        "username": username
                    }),
                    media_type="application/json",
                    headers={
                        "X-Direct-Download": "true",
                        "X-Platform": platform,
                        "X-Processing-Time-Ms": str(total_ms)
                    }
                )
                
            except Exception as e:
                # Fallback to yt-dlp for Instagram
                logger.warning(f"⚠️ gallery-dl failed ({e}), trying yt-dlp for Instagram")
                try:
                    result = execute_ytdlp_optimized(url, output_format="mp4")
                    file_path = Path(result["file_path"])
                    
                    with open(file_path, "rb") as f:
                        content = f.read()
                    
                    filename = file_path.name
                    file_path.unlink(missing_ok=True)
                    
                    t_end = perf_counter()
                    total_ms = int((t_end - t_start) * 1000)
                    logger.info(f"✅ Instagram downloaded via yt-dlp in {total_ms}ms, size: {len(content)} bytes")
                    
                    return Response(
                        content=content,
                        media_type="video/mp4",
                        headers={
                            "Content-Disposition": f'attachment; filename="{filename}"',
                            "X-Tool-Used": "yt-dlp",
                            "X-Processing-Time-Ms": str(total_ms),
                            "X-File-Size": str(len(content))
                        }
                    )
                except Exception as e2:
                    logger.error(f"yt-dlp also failed for Instagram: {e2}")
                    raise HTTPException(
                        status_code=503,
                        detail="Não foi possível baixar este conteúdo do Instagram. Verifique se a conta não é privada ou tente novamente."
                    )
        
        # TikTok: Try tikwm API first, fallback to yt-dlp
        elif platform == "tiktok":
            logger.info("🎵 TikTok detected - trying tikwm API first")
            
            try:
                # Use tikwm to get video metadata
                api_url = f"https://www.tikwm.com/api/?url={url}"
                headers = {
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                    "Connection": "keep-alive",
                }
                resp = requests.get(api_url, headers=headers, timeout=8, verify=False)
                resp.raise_for_status()
                data = resp.json()
                
                if data.get("code") != 0:
                    raise Exception(f"tikwm error: {data.get('msg', 'Unknown error')}")
                
                video_data = data.get("data", {})
                direct_url = video_data.get("hdplay") or video_data.get("play") or video_data.get("wmplay")
                
                if not direct_url:
                    raise Exception("No video URL found from tikwm")
                
                author = video_data.get("author", {}).get("unique_id", "user")
                video_id = video_data.get("id", "video")
                filename = f"tiktok_{author}_{video_id}.mp4"
                
                t_end = perf_counter()
                total_ms = int((t_end - t_start) * 1000)
                logger.info(f"✅ TikTok direct URL via tikwm: {direct_url[:100]}...")
                
                return Response(
                    content=json.dumps({
                        "direct_url": direct_url,
                        "platform": platform,
                        "filename": filename
                    }),
                    media_type="application/json",
                    headers={
                        "X-Direct-Download": "true",
                        "X-Platform": platform,
                        "X-Processing-Time-Ms": str(total_ms)
                    }
                )
            except Exception as e:
                # Fallback to yt-dlp
                logger.warning(f"⚠️ tikwm failed ({e}), using yt-dlp fallback")
                result = execute_ytdlp_optimized(url, output_format="mp4")
                file_path = Path(result["file_path"])
                
                with open(file_path, "rb") as f:
                    content = f.read()
                
                filename = file_path.name
                file_path.unlink(missing_ok=True)
                
                t_end = perf_counter()
                total_ms = int((t_end - t_start) * 1000)
                logger.info(f"✅ TikTok downloaded via yt-dlp in {total_ms}ms, size: {len(content)} bytes")
                
                return Response(
                    content=content,
                    media_type="video/mp4",
                    headers={
                        "Content-Disposition": f'attachment; filename="{filename}"',
                        "X-Tool-Used": "yt-dlp",
                        "X-Processing-Time-Ms": str(total_ms),
                        "X-File-Size": str(len(content))
                    }
                )
        
        # YouTube: Download with yt-dlp (best quality) and send complete file
        elif platform == "youtube":
            logger.info("▶️  YouTube detected - downloading with best quality")
            result = execute_ytdlp_optimized(url, output_format="mp4")
            file_path = Path(result["file_path"])
            
            # Read entire file
            with open(file_path, "rb") as f:
                content = f.read()
            
            filename = file_path.name
            file_path.unlink(missing_ok=True)
            
            t_end = perf_counter()
            total_ms = int((t_end - t_start) * 1000)
            logger.info(f"✅ YouTube download completed in {total_ms}ms, size: {len(content)} bytes")
            
            return Response(
                content=content,
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "X-Tool-Used": "yt-dlp-best-quality",
                    "X-Processing-Time-Ms": str(total_ms),
                    "X-File-Size": str(len(content))
                }
            )
        
        # Twitter/X: Download with yt-dlp and send file
        elif platform == "twitter":
            logger.info("🐦 Twitter/X detected - downloading with yt-dlp")
            result = execute_ytdlp_optimized(url, output_format="mp4")
            file_path = Path(result["file_path"])
            
            # Read entire file
            with open(file_path, "rb") as f:
                content = f.read()
            
            filename = file_path.name
            file_path.unlink(missing_ok=True)
            
            t_end = perf_counter()
            total_ms = int((t_end - t_start) * 1000)
            logger.info(f"✅ Twitter download completed in {total_ms}ms, size: {len(content)} bytes")
            
            return Response(
                content=content,
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "X-Tool-Used": "yt-dlp",
                    "X-Processing-Time-Ms": str(total_ms),
                    "X-File-Size": str(len(content))
                }
            )
        
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported platform: {platform}")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no binary stream: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))




@app.options("/download/binary")
async def options_download_binary():
    """Permite preflight CORS do navegador para rota binária."""
    return Response(status_code=200)


@app.post("/download/url", response_model=DownloadResponse)
async def download_url(
    payload: DownloadRequest,
    api_key: str = Security(validate_api_key)
):
    """
    Retorna a URL direta do vídeo sem fazer download.
    Apenas funciona com yt-dlp.
    Ideal para: quando você quer apenas o link direto do vídeo.
    """
    if payload.tool != "yt-dlp":
        raise HTTPException(
            status_code=400,
            detail="Endpoint /download/url apenas suporta yt-dlp"
        )

    logger.info(f"Download URL request: {payload.url}")

    try:
        fmt = payload.format or "mp4"
        result = execute_ytdlp(str(payload.url), download_file=False, output_format=fmt)
        direct_url = result.get("direct_url")
        thumbnail_url = result.get("thumbnail_url")
        return DownloadResponse(
            success=True,
            message="URL obtida com sucesso",
            direct_url=direct_url,
            thumbnail_url=thumbnail_url,
            tool_used="yt-dlp",
            format=fmt
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro ao obter URL: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/download/gallery/zip")
async def download_gallery_zip(
    request: DownloadRequest,
    api_key: str = Security(validate_api_key)
):
    """Baixa todos os itens do carrossel/galeria, gera ZIP e retorna o arquivo"""
    if request.tool != "gallery-dl":
        raise HTTPException(status_code=400, detail="Use tool=gallery-dl para este endpoint")

    logger.info(f"Download Gallery ZIP: {request.url}")
    result = execute_gallery_dl(str(request.url))
    download_dir = Path(result["download_dir"])
    zip_path = zip_directory(download_dir)

    # Cleanup both zip and download directory after response
    async def cleanup_both():
        cleanup_path(zip_path)
        cleanup_path(download_dir)

    return FileResponse(
        path=zip_path,
        media_type="application/zip",
        filename=zip_path.name,
        headers={
            "X-File-Size": get_file_size(zip_path),
            "X-Tool-Used": "gallery-dl",
            "X-Total-Files": str(len(result.get("files", [])))
        },
        background=BackgroundTask(cleanup_both)
    )


@app.post("/download/gallery/urls", response_model=DownloadResponse)
async def download_gallery_urls(
    request: DownloadRequest,
    api_key: str = Security(validate_api_key)
):
    """Retorna URLs diretas das imagens do carrossel/galeria sem baixar nada"""
    if request.tool != "gallery-dl":
        raise HTTPException(status_code=400, detail="Use tool=gallery-dl para este endpoint")

    logger.info(f"Gallery URLs request: {request.url}")
    urls = execute_gallery_dl_urls(str(request.url))

    return DownloadResponse(
        success=True,
        message=f"{len(urls)} URL(s) obtidas com sucesso",
        tool_used="gallery-dl",
        direct_urls=urls
    )


@app.post("/convert/hls")
async def convert_hls_to_mp4(
    url: HttpUrl = Query(..., description="URL HLS (.m3u8)"),
    api_key: str = Security(validate_api_key)
):
    """
    Converte um link HLS (.m3u8) em MP4 usando yt-dlp+ffmpeg.
    Ideal para casos onde só há stream HLS disponível.
    """
    logger.info(f"Convert HLS request: {url}")
    try:
        result = execute_ytdlp(str(url), download_file=True, output_format="mp4")
        file_path = Path(result["file_path"])
        return FileResponse(
            path=file_path,
            media_type="video/mp4",
            filename=file_path.name,
            headers={
                "X-File-Size": result["file_size"],
                "X-Tool-Used": "yt-dlp",
                "X-Format": result.get("format", "mp4")
            },
            background=BackgroundTask(cleanup_path, file_path)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro na conversão HLS: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/download/stream")
async def download_stream(
    url: str = Query(..., description="URL do vídeo"),
    format: Literal["mp4", "webm", "best"] = Query(default="mp4"),
    allow_merge: bool = Query(default=False, description="Permite mesclar vídeo+áudio em temp file para melhor qualidade"),
    api_key: str = Security(validate_api_key)
):
    """
    Faz download e retorna o arquivo via streaming (sem salvar no disco).
    Suporta apenas yt-dlp. Se allow_merge=true, baixa melhor qualidade (merge) e apaga o temp após envio.
    """
    logger.info(f"Download Stream request: {url} fmt={format}")
    try:
        if allow_merge:
            merged = stream_ytdlp_merge(url, output_format=format if format != "best" else "mp4")
            headers = {
                "X-Tool-Used": "yt-dlp",
                "X-Format": merged.get("format", format),
                "X-File-Size": str(merged.get("size", 0)),
                "Content-Disposition": f'attachment; filename="video_merge.{merged.get("format", format)}"'
            }
            return StreamingResponse(
                merged["file_path"].open("rb"),
                media_type=merged["mime"],
                headers=headers,
                background=BackgroundTask(merged["file_path"].unlink, missing_ok=True)
            )
        else:
            result = stream_ytdlp(url, output_format=format)
            headers = {
                "X-Tool-Used": "yt-dlp",
                "X-Format": result.get("format", format),
                "X-File-Size": str(result.get("size", 0)),
                "Content-Disposition": f'attachment; filename="video_stream.{result.get("format", format)}"'
            }
            return StreamingResponse(
                BytesIO(result["data"]),
                media_type=result["mime"],
                headers=headers
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Erro no stream: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/audio/extract")
async def extract_audio(
    request: AudioDownloadRequest,
    api_key: str = Security(validate_api_key)
):
    """Baixa apenas o áudio de um vídeo e retorna o arquivo."""
    audio_path = download_audio_from_url(str(request.url), request.format or "mp3")
    media_type = {
        "mp3": "audio/mpeg",
        "m4a": "audio/mp4",
        "wav": "audio/wav"
    }.get(audio_path.suffix.lstrip("."), "application/octet-stream")

    return FileResponse(
        path=audio_path,
        filename=audio_path.name,
        media_type=media_type,
        headers={
            "X-Format": audio_path.suffix.lstrip("."),
            "X-File-Size": get_file_size(audio_path),
            "X-Tool-Used": "yt-dlp"
        },
        background=BackgroundTask(audio_path.unlink, missing_ok=True)
    )


@app.post("/transcribe/video")
async def transcribe_video(
    request: AudioDownloadRequest,
    api_key: str = Security(validate_api_key)
):
    """
    Baixa o áudio do vídeo via yt-dlp e envia para o Whisper.
    Retorna o texto transcrito.
    """
    audio_path = None
    try:
        # Download audio via yt-dlp
        logger.info(f"Downloading audio via yt-dlp for transcription: {request.url}")
        result = download_via_ytdlp_fallback(str(request.url), audio_only=True)
        
        # Save to temporary file for Whisper
        audio_path = DOWNLOADS_DIR / result["filename"]
        with open(audio_path, "wb") as f:
            f.write(result["blob"])
        
        # Transcribe with Whisper
        transcript = transcribe_audio_file(audio_path, language=request.language)
        
        return {
            "success": True,
            "message": "Transcrição concluída",
            "transcript": transcript,
            "format": audio_path.suffix.lstrip("."),
            "file_size": get_file_size(audio_path)
        }
    finally:
        if audio_path and audio_path.exists():
            audio_path.unlink(missing_ok=True)


@app.post("/transcribe/image")
async def transcribe_image(
    file: UploadFile = File(...),
    prompt: Optional[str] = Form(None),
    api_key: str = Security(validate_api_key)
):
    """Extrai texto de uma imagem usando modelo de visão."""
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Imagem vazia")

    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo de imagem muito grande")

    mime_type = file.content_type or "image/png"
    text = transcribe_image_bytes(data, mime_type=mime_type, prompt=prompt)

    return {
        "success": True,
        "message": "Texto extraído com sucesso",
        "text": text,
        "mime": mime_type
    }


@app.post("/transcribe/upload-audio")
async def transcribe_upload_audio(
    file: UploadFile = File(...),
    language: Optional[str] = Form(None),
    api_key: str = Security(validate_api_key)
):
    """Transcreve áudio/vídeo enviado como arquivo local usando Whisper."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="Arquivo inválido")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio")

    if len(data) > 500 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Arquivo muito grande (máx 500 MB)")

    suffix = Path(file.filename).suffix or ".mp4"
    tmp_input = Path(tempfile.mktemp(suffix=suffix))
    try:
        tmp_input.write_bytes(data)
        audio_path = extract_audio_from_upload(tmp_input)
        try:
            transcript = transcribe_audio_file(audio_path, language=language or None)
        finally:
            audio_path.unlink(missing_ok=True)
    finally:
        tmp_input.unlink(missing_ok=True)

    return {"success": True, "transcript": transcript}


@app.post("/transcribe/instagram")
async def transcribe_instagram(
    request: InstagramTranscribeRequest,
    api_key: str = Security(validate_api_key)
):
    """
    Extrai texto das imagens de um post/carrossel do Instagram.
    Usa gallery-dl para baixar imagens e vision da OpenAI para transcrição.
    MACROBENCHMARK: Tempo total do endpoint incluindo download + transcrição paralela.
    """
    t_start = perf_counter()
    items = transcribe_instagram_carousel(str(request.url), request.prompt or DEFAULT_TRANSCRIBE_PROMPT)
    t_end = perf_counter()

    total_ms = int((t_end - t_start) * 1000)
    logger.info(f"🏁 MACROBENCHMARK /transcribe/instagram: {total_ms}ms total")

    return Response(
        content=json.dumps({
            "success": True,
            "message": f"{len(items)} imagem(ns) processada(s)",
            "items": items,
            "performance": {
                "total_ms": total_ms,
                "avg_per_item_ms": total_ms // len(items) if items else 0
            }
        }),
        media_type="application/json",
        headers={
            "X-Processing-Time-Ms": str(total_ms),
            "X-Items-Processed": str(len(items))
        }
    )


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    host = os.getenv("HOST", "0.0.0.0")
    uvicorn.run(app, host=host, port=port)
