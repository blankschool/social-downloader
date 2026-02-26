# Arquitetura do Sistema - Social Media Transcription

## 📋 Regras de Download por Plataforma

### Instagram
- **Reels**: Cobalt self-hosted (`http://localhost:9000`)
- **Carousel**: gallery-dl com cookies (`www.instagram.com_cookies.txt`)
- **Posts**: gallery-dl com cookies
- **Stories**: gallery-dl com cookies

### TikTok
- **Vídeos**: Cobalt self-hosted (`http://localhost:9000`)

### YouTube
- **Vídeos**: yt-dlp com otimização (já funciona)
- **Áudio**: yt-dlp

### Transcrição
- **Áudio**: yt-dlp para extração
- **Vídeo**: Whisper API (OpenAI)
- **Imagem**: GPT-4o-mini (OpenAI)

### Outras Plataformas
- **Twitter/X**: Cobalt self-hosted
- **Facebook**: Cobalt self-hosted
- **Reddit**: Cobalt self-hosted
- **Todas as outras**: Cobalt self-hosted

## 🔧 Configuração Atual

### Backend
- **Porta**: 8000
- **Processo**: supervisor (social-media-transcription)
- **Logs**: `/var/log/social-media-transcription.log`

### Cobalt Self-Hosted
- **Container**: cobalt-api
- **Versão**: 10.9.4
- **URL**: `http://localhost:9000`
- **Auto-restart**: Sim

### Frontend
- **Build**: Vite + React + TypeScript
- **Dist**: `/opt/social-media-transcription/frontend/dist`
- **URL**: `https://savedown.ngrok.app`

### Nginx
- **Config**: `/etc/nginx/sites-available/social-media-transcription`
- **Frontend**: `/` → `frontend/dist`
- **API**: `/api/*` → `http://127.0.0.1:8000/*` (com regex rewrite)

## 📝 Notas Importantes

1. **Instagram Cookies**: Necessário para gallery-dl funcionar com carousel/posts/stories
2. **Cobalt Fallback**: Se Cobalt falhar, usa yt-dlp automaticamente
3. **YouTube Otimizado**: Já tem otimização funcionando, manter como está
4. **Transcrição de Áudio**: Sempre usa yt-dlp para extração

## 🚀 Deploy

- **VPS**: 76.13.165.5
- **User**: root
- **Path**: `/opt/social-media-transcription`
- **Ngrok**: `savedown.ngrok.app`
