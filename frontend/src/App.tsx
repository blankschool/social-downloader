import { useState, useRef, useEffect, useCallback } from 'react';
import { Download, Image, Mic, Settings, Info, Moon, Sun } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import { SettingsPanel, AppSettings } from './components/SettingsPanel';
import { Footer } from './components/Footer';
import { VideoResult } from './components/VideoResult';
import { ImageCarouselResult } from './components/ImageCarouselResult';
import { AudioResult } from './components/AudioResult';
import { apiFetch } from './lib/api';
import {
  detectPlatform,
  isCarouselUrl,
  triggerBrowserDownload,
  generateFilename,
  getFilenameFromResponse,
  formatErrorMessage,
  downloadFromUrl,
} from './lib/download';

interface VideoResultData {
  type: 'video';
  filename: string;
  platform: string;
  blob: Blob;
  fileSize: string;
}

interface ImageCarouselResultData {
  type: 'carousel';
  images: Array<{
    url: string;
    transcription: string;
    filename: string;
    isVideo?: boolean;
  }>;
}

interface AudioResultData {
  type: 'audio';
  transcription: string;
}

type ResultData = VideoResultData | ImageCarouselResultData | AudioResultData;

export default function App() {
  const [url, setUrl] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [downloadType, setDownloadType] = useState<'video' | 'images' | 'audio'>('video');
  const [theme, setTheme] = useState<'dark' | 'light'>('dark');
  const [isLoading, setIsLoading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [result, setResult] = useState<ResultData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settings, setSettings] = useState<AppSettings>({
    videoQuality: 'max',
    audioFormat: 'mp3',
    transcriptionLanguage: 'auto',
  });
  const inputRef = useRef<HTMLInputElement>(null);
  const handleDownloadRef = useRef<() => Promise<void>>();
  const progressIntervalRef = useRef<number | null>(null);

  // Load settings from localStorage on mount
  useEffect(() => {
    const savedSettings = localStorage.getItem('app_settings');
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings);
        setSettings({ ...settings, ...parsed });
      } catch (err) {
        console.error('Failed to load settings:', err);
      }
    }
  }, []);

  const toggleTheme = () => {
    setTheme(theme === 'dark' ? 'light' : 'dark');
  };

  const isDark = theme === 'dark';

  // Auto-focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Auto-download when video result is ready
  useEffect(() => {
    if (result && result.type === 'video') {
      // Automatically trigger download
      triggerBrowserDownload(result.blob, result.filename);
    }
  }, [result]);

  // Format file size helper
  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  };

  // Handle Enter key press
  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && url && !isLoading) {
      handleDownload();
    }
  };

  const handleDownloadVideo = async (url: string) => {
    const platform = detectPlatform(url);
    
    // Always use max quality
    const endpoint = `/download/binary?url=${encodeURIComponent(url)}&format=mp4&quality=max`;

    try {
      // No timeout for large file downloads (YouTube can be 300MB+)
      const response = await apiFetch(endpoint, {
        method: 'POST',
      });

      if (!response.ok) {
        // Read body once (body can only be consumed once - avoids "Body is disturbed or locked")
        const errorText = await response.text();
        let message = errorText || 'Erro ao baixar vídeo';
        try {
          const errorJson = JSON.parse(errorText);
          if (errorJson?.detail) {
            message = typeof errorJson.detail === 'string' ? errorJson.detail : JSON.stringify(errorJson.detail);
          }
        } catch (_) {
          /* not JSON, use raw text as message */
        }
        throw new Error(message);
      }

      // Check if response contains a direct download URL (Instagram/TikTok)
      const directDownloadHeader = response.headers.get('X-Direct-Download');
      if (directDownloadHeader === 'true') {
        const jsonData = await response.json();
        const directUrl = jsonData.direct_url;
        const platform = response.headers.get('X-Platform') || '';
        const filename = jsonData.filename || '';
        
        // TikTok: Auto-download via fetch + blob (avoids CORS issues with download attribute)
        if (platform === 'tiktok') {
          try {
            // Fetch the video from CDN
            const videoResponse = await fetch(directUrl);
            if (!videoResponse.ok) {
              throw new Error('Failed to download video from TikTok CDN');
            }
            
            // Convert to blob
            const blob = await videoResponse.blob();
            
            // Trigger download using blob URL
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            a.download = filename || 'tiktok_video.mp4';
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            
            // Clean up blob URL
            setTimeout(() => URL.revokeObjectURL(blobUrl), 100);
            
            return null; // Flow ends - download completed
          } catch (error) {
            console.error('TikTok download error:', error);
            // Fallback: open in new tab if fetch fails
            window.open(directUrl, '_blank');
            return null;
          }
        }
        
        // Instagram: Open in new tab (keeps current behavior)
        if (platform === 'instagram') {
          window.open(directUrl, '_blank');
          return null;
        }
        
        // Fallback for other platforms
        window.open(directUrl, '_blank');
        return null;
      }

      // Handle normal blob responses (YouTube/Twitter - server-side download)
      // Get Content-Length to show progress
      const contentLength = response.headers.get('Content-Length');
      const totalBytes = contentLength ? parseInt(contentLength, 10) : 0;
      
      if (totalBytes > 0) {
        console.log(`Receiving ${formatFileSize(totalBytes)} from server...`);
      }
      
      const blob = await response.blob();
      
      // Validate blob is not empty
      if (blob.size === 0) {
        throw new Error('Download falhou: arquivo vazio. Tente novamente.');
      }
      
      const filename = getFilenameFromResponse(response, `${platform}_${Date.now()}.mp4`);
      const fileSize = formatFileSize(blob.size);

      return { blob, filename, platform, fileSize };
    } catch (err: any) {
      // Handle timeout errors
      if (err.name === 'AbortError') {
        throw new Error('Download expirou. O arquivo pode ser muito grande. Tente novamente.');
      }
      
      // Show generic error for TikTok fetch errors
      const platform = detectPlatform(url);
      if (platform === 'tiktok' && (err instanceof TypeError || err.message.includes('fetch'))) {
        throw new Error('Não foi possível baixar. Tente novamente.');
      }
      throw err;
    }
  };

  const handleDownloadCarousel = async (url: string) => {
    const response = await apiFetch('/transcribe/instagram', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || 'Erro ao processar carrossel');
    }

    const data = await response.json();
    
    if (!data.items || !Array.isArray(data.items)) {
      throw new Error('Resposta inválida da API');
    }

    // Backend includes imagens e vídeos (vídeos são transcritos via frames extraídos)
    const images = data.items
      .filter((item: any) => item.url)
      .map((item: any) => {
        const isVideo = !!item.is_video;
        const filename = item.filename || item.file || (isVideo ? `instagram_video_${item.index}.mp4` : `instagram_image_${item.index}.jpg`);
        return {
          url: item.url,
          transcription: item.text || '',
          filename,
          isVideo,
        };
      });

    return images;
  };

  const handleTranscribeAudio = async (url: string) => {
    const language = settings.transcriptionLanguage === 'auto' ? undefined : settings.transcriptionLanguage;
    const response = await apiFetch('/transcribe/video', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ 
        url, 
        format: settings.audioFormat,
        language: language
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || 'Erro ao transcrever áudio');
    }

    const data = await response.json();
    return data.transcript || 'Transcrição não disponível';
  };

  const handleSettingsChange = (newSettings: AppSettings) => {
    setSettings(newSettings);
  };

  // Simulate progress based on platform estimated times
  const startProgressSimulation = (platform: string, type: 'video' | 'images' | 'audio') => {
    // Clear any existing progress interval
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current);
    }
    
    setProgress(0);
    
    // Estimated completion times (in seconds) per platform
    const estimatedTimes: Record<string, number> = {
      'youtube': type === 'audio' ? 20 : 120, // YouTube can be large (up to 2 minutes)
      'tiktok': 4,
      'instagram': type === 'images' ? 8 : 5,
      'twitter': 8,
      'unknown': 10,
    };
    
    const totalTime = estimatedTimes[platform] || estimatedTimes['unknown'];
    const updateInterval = 100; // Update every 100ms
    const totalSteps = (totalTime * 1000) / updateInterval;
    let currentStep = 0;
    
    progressIntervalRef.current = window.setInterval(() => {
      currentStep++;
      // Progress slows down as it approaches 95% (never reaches 100% until complete)
      const rawProgress = (currentStep / totalSteps) * 100;
      const easedProgress = 95 * (1 - Math.exp(-rawProgress / 30));
      setProgress(Math.min(easedProgress, 95));
    }, updateInterval);
  };
  
  const stopProgressSimulation = () => {
    if (progressIntervalRef.current) {
      clearInterval(progressIntervalRef.current);
      progressIntervalRef.current = null;
    }
    setProgress(100);
    // Reset progress after animation completes
    setTimeout(() => setProgress(0), 500);
  };

  const handleDownload = async () => {
    if (!url) return;

    const platform = detectPlatform(url);
    setIsLoading(true);
    setError(null);
    setResult(null);
    
    startProgressSimulation(platform, downloadType);

    try {
      if (downloadType === 'video') {
        const videoResult = await handleDownloadVideo(url);
        
        // If videoResult is null, it means browser was redirected to direct URL
        // No need to show result UI, download happens automatically
        if (videoResult === null) {
          return; // Exit early - browser is handling the download
        }
        
        if (!videoResult || !videoResult.blob || !videoResult.filename) {
          throw new Error('Erro ao obter dados do vídeo');
        }
        setResult({
          type: 'video',
          filename: videoResult.filename,
          platform: videoResult.platform,
          blob: videoResult.blob,
          fileSize: videoResult.fileSize,
        });
      } else if (downloadType === 'images') {
        // Check if it's an Instagram post/carousel
        if (!isCarouselUrl(url)) {
          throw new Error('Esta URL não é um post do Instagram. Use a opção "Vídeo" para reels ou stories.');
        }

        const images = await handleDownloadCarousel(url);
        
        if (images.length === 0) {
          throw new Error('Nenhuma imagem encontrada no carrossel');
        }

        setResult({
          type: 'carousel',
          images,
        });
      } else if (downloadType === 'audio') {
        const transcription = await handleTranscribeAudio(url);
        setResult({
          type: 'audio',
          transcription,
        });
      }
    } catch (err) {
      const errorMessage = formatErrorMessage(err);
      setError(errorMessage);
      console.error('Download error:', err);
    } finally {
      stopProgressSimulation();
      setIsLoading(false);
    }
  };

  // Keep handleDownload ref updated
  useEffect(() => {
    handleDownloadRef.current = handleDownload;
  }, [handleDownload]);

  // Keyboard shortcuts
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      // Cmd/Ctrl + V: Focus input and paste from clipboard
      if ((e.metaKey || e.ctrlKey) && e.key === 'v' && !e.shiftKey && !isLoading) {
        e.preventDefault();
        inputRef.current?.focus();
        navigator.clipboard.readText().then(text => {
          if (text && (text.includes('instagram.com') || text.includes('youtube.com') || 
              text.includes('tiktok.com') || text.includes('twitter.com') || text.includes('x.com') ||
              text.includes('youtu.be'))) {
            setUrl(text);
          }
        }).catch(err => {
          console.error('Failed to read clipboard:', err);
        });
      }
      
      // Cmd/Ctrl + Shift + V: Paste and download immediately
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'V' && !isLoading) {
        e.preventDefault();
        navigator.clipboard.readText().then(text => {
          if (text && (text.includes('instagram.com') || text.includes('youtube.com') || 
              text.includes('tiktok.com') || text.includes('twitter.com') || text.includes('x.com') ||
              text.includes('youtu.be'))) {
            setUrl(text);
            // Small delay to ensure state updates before download
            setTimeout(() => {
              if (!isLoading && text && handleDownloadRef.current) {
                handleDownloadRef.current();
              }
            }, 100);
          }
        }).catch(err => {
          console.error('Failed to read clipboard:', err);
        });
      }
      
      // Escape: Clear everything
      if (e.key === 'Escape') {
        setUrl('');
        setResult(null);
        setError(null);
        inputRef.current?.focus();
      }
    };
    
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isLoading]);

  const handleVideoDownload = () => {
    if (result && result.type === 'video') {
      triggerBrowserDownload(result.blob, result.filename);
    }
  };

  const handleImageDownload = async (imageUrl: string, filename: string) => {
    try {
      await downloadFromUrl(imageUrl, filename);
    } catch (err) {
      setError(formatErrorMessage(err));
    }
  };

  const handleDownloadAllImages = async () => {
    if (result && result.type === 'carousel') {
      for (const image of result.images) {
        try {
          await downloadFromUrl(image.url, image.filename);
          // Add a small delay between downloads to avoid overwhelming the browser
          await new Promise(resolve => setTimeout(resolve, 500));
        } catch (err) {
          console.error('Error downloading image:', err);
        }
      }
    }
  };

  return (
    <div className={`min-h-screen ${isDark ? 'bg-[#0f0f0f] text-white' : 'bg-white text-[#1a1a1a]'} flex flex-col transition-colors duration-300`}>
      {/* Header */}
      <header className={`border-b ${isDark ? 'border-[#2a2a2a]' : 'border-[#e0e0e0]'} py-6`}>
        <div className="max-w-3xl mx-auto px-4">
          <div className="flex items-center justify-end">
            <div className="flex items-center gap-2">
              <motion.button
                onClick={toggleTheme}
                className={`p-2 ${isDark ? 'hover:bg-[#1a1a1a]' : 'hover:bg-[#e8e8e8]'} rounded-lg transition-colors`}
                aria-label="Alternar tema"
                whileHover={{ scale: 1.05 }}
                whileTap={{ scale: 0.95 }}
              >
                <AnimatePresence mode="wait">
                  {isDark ? (
                    <motion.div
                      key="sun"
                      initial={{ rotate: -90, opacity: 0 }}
                      animate={{ rotate: 0, opacity: 1 }}
                      exit={{ rotate: 90, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                    >
                      <Sun className="w-5 h-5" />
                    </motion.div>
                  ) : (
                    <motion.div
                      key="moon"
                      initial={{ rotate: 90, opacity: 0 }}
                      animate={{ rotate: 0, opacity: 1 }}
                      exit={{ rotate: -90, opacity: 0 }}
                      transition={{ duration: 0.2 }}
                    >
                      <Moon className="w-5 h-5" />
                    </motion.div>
                  )}
                </AnimatePresence>
              </motion.button>
              <motion.button
                onClick={() => setShowSettings(!showSettings)}
                className={`p-2 ${isDark ? 'hover:bg-[#1a1a1a]' : 'hover:bg-[#e8e8e8]'} rounded-lg transition-colors`}
                aria-label="Configurações"
                whileHover={{ scale: 1.05, rotate: 90 }}
                whileTap={{ scale: 0.95 }}
              >
                <Settings className="w-5 h-5" />
              </motion.button>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 flex items-center justify-center px-4 py-12">
        <div className="max-w-3xl w-full space-y-8">
          {/* Intro Text */}
          <motion.div 
            className="text-center space-y-3"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.1 }}
          >
            <h2 className="text-4xl font-bold">Baixar e Transcrever</h2>
            <p className={`${isDark ? 'text-[#a0a0a0]' : 'text-[#666666]'} text-lg`}>
              Salve vídeos, imagens e transcreva áudios do Instagram, YouTube, TikTok e X
            </p>
          </motion.div>

          {/* Download Type Selector */}
          <motion.div 
            className="flex gap-3 justify-center"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.2 }}
          >
            <motion.button
              onClick={() => setDownloadType('video')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
                downloadType === 'video'
                  ? isDark ? 'bg-[#4a4a4a] text-white' : 'bg-[#1a1a1a] text-white'
                  : isDark ? 'bg-[#1a1a1a] text-[#a0a0a0] hover:bg-[#252525]' : 'bg-[#e8e8e8] text-[#666666] hover:bg-[#d8d8d8]'
              }`}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              <Download className="w-4 h-4" />
              Vídeo
            </motion.button>
            <motion.button
              onClick={() => setDownloadType('images')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
                downloadType === 'images'
                  ? isDark ? 'bg-[#4a4a4a] text-white' : 'bg-[#1a1a1a] text-white'
                  : isDark ? 'bg-[#1a1a1a] text-[#a0a0a0] hover:bg-[#252525]' : 'bg-[#e8e8e8] text-[#666666] hover:bg-[#d8d8d8]'
              }`}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              <Image className="w-4 h-4" />
              Imagens
            </motion.button>
            <motion.button
              onClick={() => setDownloadType('audio')}
              className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors ${
                downloadType === 'audio'
                  ? isDark ? 'bg-[#4a4a4a] text-white' : 'bg-[#1a1a1a] text-white'
                  : isDark ? 'bg-[#1a1a1a] text-[#a0a0a0] hover:bg-[#252525]' : 'bg-[#e8e8e8] text-[#666666] hover:bg-[#d8d8d8]'
              }`}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              <Mic className="w-4 h-4" />
              Áudio
            </motion.button>
          </motion.div>

          {/* URL Input and Download Button */}
          <motion.div 
            className="space-y-4"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.3 }}
          >
            <div className="relative space-y-2">
              <motion.input
                ref={inputRef}
                type="text"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Cole o link do Instagram, YouTube, TikTok ou X..."
                className={`w-full px-6 py-4 ${
                  isDark ? 'bg-[#1a1a1a] border-[#2a2a2a] text-white placeholder-[#6a6a6a] focus:border-[#4a4a4a]' : 'bg-white border-[#d0d0d0] text-[#1a1a1a] placeholder-[#999999] focus:border-[#999999]'
                } border rounded-xl focus:outline-none transition-colors`}
                whileFocus={{ scale: 1.01 }}
                disabled={isLoading}
              />
              <p className={`text-xs text-center ${isDark ? 'text-[#6a6a6a]' : 'text-[#999999]'}`}>
                Atalhos: <kbd className={`px-1.5 py-0.5 rounded text-xs ${isDark ? 'bg-[#2a2a2a]' : 'bg-[#e0e0e0]'}`}>⌘V</kbd> colar • <kbd className={`px-1.5 py-0.5 rounded text-xs ${isDark ? 'bg-[#2a2a2a]' : 'bg-[#e0e0e0]'}`}>Enter</kbd> baixar • <kbd className={`px-1.5 py-0.5 rounded text-xs ${isDark ? 'bg-[#2a2a2a]' : 'bg-[#e0e0e0]'}`}>⌘⇧V</kbd> colar e baixar • <kbd className={`px-1.5 py-0.5 rounded text-xs ${isDark ? 'bg-[#2a2a2a]' : 'bg-[#e0e0e0]'}`}>Esc</kbd> limpar
              </p>
            </div>

            <motion.button
              onClick={handleDownload}
              disabled={!url || isLoading}
              className={`w-full py-4 rounded-xl font-semibold transition-colors flex items-center justify-center gap-2 ${
                url && !isLoading
                  ? isDark ? 'bg-[#4a4a4a] text-white hover:bg-[#5a5a5a]' : 'bg-[#1a1a1a] text-white hover:bg-[#2a2a2a]'
                  : isDark ? 'bg-[#1a1a1a] text-[#4a4a4a] cursor-not-allowed' : 'bg-[#e0e0e0] text-[#999999] cursor-not-allowed'
              }`}
              whileHover={url && !isLoading ? { scale: 1.02 } : {}}
              whileTap={url && !isLoading ? { scale: 0.98 } : {}}
            >
              {isLoading ? (
                <>
                  <motion.div
                    className="w-5 h-5 border-2 border-current border-t-transparent rounded-full"
                    animate={{ rotate: 360 }}
                    transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                  />
                  Processando...
                </>
              ) : (
                <>
                  <Download className="w-5 h-5" />
                  {downloadType === 'video' && 'Baixar Vídeo'}
                  {downloadType === 'images' && 'Baixar e Transcrever Imagens'}
                  {downloadType === 'audio' && 'Baixar e Transcrever Áudio'}
                </>
              )}
            </motion.button>

            {/* Progress Bar */}
            <AnimatePresence>
              {isLoading && progress > 0 && (
                <motion.div
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  className="w-full mt-4"
                >
                  <div className={`w-full h-2 rounded-full overflow-hidden ${isDark ? 'bg-[#1a1a1a]' : 'bg-[#e0e0e0]'}`}>
                    <motion.div
                      className={`h-full ${isDark ? 'bg-[#4a4a4a]' : 'bg-[#1a1a1a]'}`}
                      initial={{ width: 0 }}
                      animate={{ width: `${progress}%` }}
                      transition={{ duration: 0.3, ease: 'easeOut' }}
                    />
                  </div>
                  <p className={`text-xs text-center mt-2 ${isDark ? 'text-[#6a6a6a]' : 'text-[#999999]'}`}>
                    {progress < 95 ? `Processando... ${Math.round(progress)}%` : 'Finalizando...'}
                  </p>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>

          {/* Error Display */}
          {error && (
            <motion.div
              className={`${
                isDark ? 'bg-red-900/20 border-red-900/50' : 'bg-red-100 border-red-300'
              } border rounded-xl p-4`}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <p className={`text-sm ${isDark ? 'text-red-400' : 'text-red-800'}`}>{error}</p>
            </motion.div>
          )}

          {/* Results Display */}
          {result && result.type === 'video' && (
            <motion.div
              className={`${
                isDark ? 'bg-[#1a1a1a] border-[#2a2a2a]' : 'bg-white border-[#e0e0e0]'
              } border rounded-xl p-6 text-center`}
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
            >
              <p className="text-lg font-medium mb-2">✅ Download iniciado!</p>
              <p className={`text-sm ${isDark ? 'text-[#a0a0a0]' : 'text-[#666666]'}`}>
                {result.filename}
              </p>
              <p className={`text-sm ${isDark ? 'text-[#8a8a8a]' : 'text-[#888888]'} mt-1`}>
                {result.fileSize}
              </p>
              <p className={`text-xs mt-2 ${isDark ? 'text-[#6a6a6a]' : 'text-[#999999]'}`}>
                O arquivo está sendo baixado automaticamente
              </p>
            </motion.div>
          )}

          {result && result.type === 'carousel' && (
            <ImageCarouselResult
              images={result.images}
              onDownloadImage={handleImageDownload}
              onDownloadAll={handleDownloadAllImages}
              isDark={isDark}
            />
          )}

          {result && result.type === 'audio' && (
            <AudioResult transcription={result.transcription} isDark={isDark} />
          )}

          {/* Info Box */}
          <motion.div 
            className={`${isDark ? 'bg-[#1a1a1a] border-[#2a2a2a]' : 'bg-white border-[#e0e0e0]'} border rounded-xl p-4`}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.6, delay: 0.4 }}
          >
            <div className="flex gap-3">
              <Info className={`w-5 h-5 ${isDark ? 'text-[#6a6a6a]' : 'text-[#999999]'} flex-shrink-0 mt-0.5`} />
              <div className="space-y-2">
                <p className={`text-sm ${isDark ? 'text-[#a0a0a0]' : 'text-[#666666]'}`}>
                  <strong className={isDark ? 'text-white' : 'text-[#1a1a1a]'}>Plataformas suportadas:</strong> Instagram (posts, reels, stories, carrosséis), 
                  YouTube (vídeos, shorts), TikTok (vídeos), X/Twitter (vídeos, imagens)
                </p>
                <p className={`text-sm ${isDark ? 'text-[#a0a0a0]' : 'text-[#666666]'}`}>
                  <strong className={isDark ? 'text-white' : 'text-[#1a1a1a]'}>Recursos:</strong> Downloads diretos, transcrição de carrossel de imagens, 
                  transcrição de áudio
                </p>
              </div>
            </div>
          </motion.div>

          {/* Settings Panel */}
          <AnimatePresence>
            {showSettings && (
              <SettingsPanel 
                isDark={isDark} 
                onClose={() => setShowSettings(false)}
                onSettingsChange={handleSettingsChange}
              />
            )}
          </AnimatePresence>
        </div>
      </main>

      {/* Footer */}
      <Footer isDark={isDark} />
    </div>
  );
}
