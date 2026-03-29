# Social Media Transcription App - Complete Setup Guide

A fast, self-hosted application for downloading and transcribing media from YouTube, Instagram, TikTok, and X (Twitter). Uses Cobalt API for ultra-fast downloads with automatic fallback to yt-dlp.

## 📋 Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Self-Hosted Cobalt Setup](#self-hosted-cobalt-setup)
4. [Configuration](#configuration)
5. [Running the Application](#running-the-application)
6. [Accessing the App](#accessing-the-app)
7. [Features](#features)
8. [Troubleshooting](#troubleshooting)

## 🎯 Prerequisites

Before starting, ensure you have:

- **Python 3.10+** installed
- **Node.js 18+** and npm installed
- **Docker Desktop** installed and running
- **Git** installed
- **macOS** (instructions are for macOS, but can be adapted for Linux/Windows)

## 📦 Installation

### 1. Clone and Navigate to Project

```bash
cd /path/to/social-media-transcription
```

### 2. Set Up Python Virtual Environment

```bash
# Create virtual environment
python3 -m venv venv

# Activate virtual environment
source venv/bin/activate

# Install Python dependencies
pip install -r requirements.txt
```

### 3. Set Up Frontend Dependencies

```bash
cd frontend
npm install
cd ..
```

### 4. Verify Binaries

Ensure `yt-dlp` and `ffmpeg` are in the `bin/` directory:

```bash
ls -lh bin/
# Should show:
# - ffmpeg
# - yt-dlp
```

If missing, download:
- **yt-dlp**: `wget https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -O bin/yt-dlp && chmod +x bin/yt-dlp`
- **ffmpeg**: Install via Homebrew: `brew install ffmpeg` then copy to `bin/`

## 🐳 Self-Hosted Cobalt Setup

Cobalt provides ultra-fast downloads (2-3s vs 30-50s). This setup runs Cobalt in Docker.

### 1. Start Cobalt Docker Container

```bash
# Stop and remove any existing container
docker stop cobalt 2>/dev/null || true
docker rm cobalt 2>/dev/null || true

# Start Cobalt with self-hosted configuration
docker run -d \
  -p 9000:9000 \
  --name cobalt \
  -e API_URL=http://localhost:9000 \
  ghcr.io/imputnet/cobalt:latest
```

### 2. Verify Cobalt is Running

```bash
# Check container status
docker ps | grep cobalt

# Test Cobalt API
curl http://localhost:9000/ | python3 -m json.tool

# Check logs
docker logs cobalt
```

You should see:
- Container status: `Up X minutes`
- API response with version info
- Logs showing: `[✓] internal tunnel handler running`

### 3. Keep Cobalt Running

Cobalt needs to stay running. To restart if it stops:

```bash
docker start cobalt
```

To view logs:
```bash
docker logs -f cobalt
```

## ⚙️ Configuration

### Environment Variables

The app uses `config/.env` for configuration. Create it if missing:

```bash
cat > config/.env << 'EOF'
API_KEY=cI4cA4Xvml2O0TCXdxRDuhHdY1251G34gso3VdHfDIc
PORT=8000
HOST=0.0.0.0
YT_DLP_PATH=./bin/yt-dlp
FFMPEG_PATH=./bin/ffmpeg

# Cobalt Configuration (Self-Hosted)
COBALT_INSTANCE=self_hosted
COBALT_SELF_HOSTED_URL=http://localhost:9000
COBALT_DEFAULT_QUALITY=max
COBALT_TIMEOUT=60
ENABLE_YTDLP_FALLBACK=true
EOF
```

### Frontend Configuration

Create `frontend/.env.local`:

```bash
cat > frontend/.env.local << 'EOF'
VITE_API_BASE_URL=https://savedown.ngrok.app
VITE_API_KEY=cI4cA4Xvml2O0TCXdxRDuhHdY1251G34gso3VdHfDIc
EOF
```

**Note**: Replace `savedown.ngrok.app` with your ngrok domain if using ngrok.

### Cookies (Optional)

For better download success rates, add cookies to `config/cookies/`:
- `www.tiktok.com_cookies.txt`
- `www.instagram.com_cookies.txt`
- `www.twitter.com_cookies.txt`

Export cookies using browser extensions like "Get cookies.txt".

## 🚀 Running the Application

### 1. Start Backend Server

```bash
# Activate virtual environment
source venv/bin/activate

# Start backend
python backend/main.py
```

The backend will start on `http://localhost:8000`

**In production**: Run in background:
```bash
python backend/main.py > logs/output.log 2> logs/error.log &
```

### 2. Start Frontend Development Server

Open a new terminal:

```bash
cd frontend
npm run dev
```

The frontend will start on `http://localhost:5173`

**For production build**:
```bash
cd frontend
npm run build
# Serve dist/ folder with a web server
```

### 3. Access via ngrok (Optional)

To access from the internet using your custom domain:

```bash
# Make sure ngrok is configured
ngrok config check

# Start ngrok tunnel
ngrok http 8000 --domain=savedown.ngrok.app
```

Update `frontend/.env.local` with your ngrok URL.

## 🌐 Accessing the App

1. **Local**: Open `http://localhost:5173` in your browser
2. **Via ngrok**: Open `https://savedown.ngrok.app` (or your ngrok domain)

## ✨ Features

### Download Types

- **Video Downloads**: Ultra-fast via Cobalt API (2-3s), fallback to yt-dlp (30-50s)
- **Image Carousels**: Instagram posts with transcription
- **Audio Transcription**: Automatic transcription using OpenAI Whisper

### Supported Platforms

- ✅ YouTube (Cobalt + yt-dlp fallback)
- ✅ Instagram (Cobalt for videos, gallery-dl for images)
- ✅ TikTok (tikwm.com API with yt-dlp fallback)
- ✅ X/Twitter (Cobalt)

### Keyboard Shortcuts

- `Cmd/Ctrl + V`: Paste URL from clipboard
- `Cmd/Ctrl + Shift + V`: Paste and immediately download
- `Enter`: Trigger download
- `Esc`: Clear input and results

### Optimizations

- **Multi-threaded downloads**: Uses aria2c for 16 parallel connections
- **Concurrent fragments**: Downloads 16 fragments simultaneously
- **Pre-merged formats**: Prioritizes formats that don't need merging (faster)
- **Smart fallback**: Automatically uses yt-dlp if Cobalt fails

### Quality Settings

- **Default**: Maximum quality (1080p+ when available)
- **Configurable**: Change quality in Settings panel (max, 1080, 720, 480)

## 🔧 Troubleshooting

### Cobalt Returns Empty Files

**Symptom**: Downloads show 0 bytes or fail

**Solution**: The app automatically falls back to yt-dlp. Check logs:
```bash
tail -f logs/backend.log
```

Look for: `🔄 Falling back to yt-dlp due to empty content`

### Cobalt Container Not Starting

**Symptom**: `docker ps` shows cobalt as stopped

**Solution**:
```bash
# Check logs
docker logs cobalt

# Restart container
docker start cobalt

# If still failing, recreate:
docker stop cobalt
docker rm cobalt
docker run -d -p 9000:9000 --name cobalt -e API_URL=http://localhost:9000 ghcr.io/imputnet/cobalt:latest
```

### Backend Won't Start (Port Already in Use)

**Symptom**: `ERROR: [Errno 48] address already in use`

**Solution**:
```bash
# Find and kill process using port 8000
lsof -ti:8000 | xargs kill -9

# Or change port in config/.env:
# PORT=8001
```

### Frontend Shows White Screen

**Symptom**: Blank page when opening app

**Solution**:
1. Check browser console (F12) for errors
2. Verify frontend is running: `curl http://localhost:5173`
3. Rebuild frontend: `cd frontend && npm run build`

### Downloads Are Slow

**Symptom**: YouTube downloads take >60 seconds

**Solution**:
- Check if aria2c is installed: `which aria2c`
- Install if missing: `brew install aria2`
- Verify Cobalt is running: `docker ps | grep cobalt`
- Check network connection speed

### TikTok Downloads Fail

**Symptom**: TikTok URLs fail to download

**Solution**:
- App automatically falls back to tikwm.com API
- If still failing, check if tikwm.com is accessible
- Verify cookies are configured in `config/cookies/www.tiktok.com_cookies.txt`

### Instagram Downloads Fail

**Symptom**: Instagram URLs return errors

**Solution**:
- Add Instagram cookies: `config/cookies/www.instagram.com_cookies.txt`
- Verify Cobalt is running
- Check if URL is a post, reel, or story (post requires different endpoint)

## 📊 Performance Benchmarks

| Platform | Method | Time | Quality |
|----------|--------|------|---------|
| YouTube | Cobalt | ~1.7s | Max |
| YouTube | yt-dlp fallback | ~38s | Max |
| Instagram | Cobalt | ~2.4s | Max |
| TikTok | tikwm.com | ~6-7s | Max |

## 🔐 Security Notes

- API key is configured in `.env` files - keep these private
- Backend uses API key authentication via `X-API-Key` header
- Cookies are stored locally and not shared
- ngrok tunnels are encrypted (HTTPS)

## 📝 Development Notes

### File Structure

```
social-media-transcription/
├── backend/           # Python FastAPI backend
│   ├── main.py       # Main API server
│   ├── lib/          # Cobalt client
│   └── config/       # Configuration
├── frontend/         # React/Vite frontend
│   └── src/          # Source files
├── bin/              # yt-dlp, ffmpeg binaries
├── config/           # Environment config
│   └── cookies/      # Platform cookies
├── cobalt-main/      # Cobalt submodule (Docker)
└── logs/             # Application logs
```

### Updating Dependencies

**Python**:
```bash
source venv/bin/activate
pip install -r requirements.txt --upgrade
```

**Node.js**:
```bash
cd frontend
npm update
```

**Cobalt**:
```bash
docker pull ghcr.io/imputnet/cobalt:latest
docker stop cobalt
docker rm cobalt
# Then restart with docker run command from Setup section
```

## 🆘 Getting Help

1. Check logs: `tail -f logs/backend.log` and `tail -f logs/error.log`
2. Verify all services are running:
   - Backend: `curl http://localhost:8000/health`
   - Frontend: `curl http://localhost:5173`
   - Cobalt: `curl http://localhost:9000/`
3. Check Docker: `docker ps` and `docker logs cobalt`

## 🎉 You're All Set!

Your Social Media Transcription App is now configured and ready to use. Start downloading and transcribing media at lightning speed!

**Quick Start Command Summary**:
```bash
# Terminal 1: Cobalt
docker run -d -p 9000:9000 --name cobalt -e API_URL=http://localhost:9000 ghcr.io/imputnet/cobalt:latest

# Terminal 2: Backend
source venv/bin/activate && python backend/main.py

# Terminal 3: Frontend
cd frontend && npm run dev
```

Then open `http://localhost:5173` in your browser!
