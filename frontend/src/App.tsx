import { useState, useRef, useEffect } from 'react';
import { useTheme } from 'next-themes';
import { Download, Image, Mic, Moon, Sun, Monitor, CircleCheck, UploadCloud, FileIcon, X as XIcon } from 'lucide-react';
import { motion, AnimatePresence } from 'motion/react';
import * as RadioGroupPrimitive from '@radix-ui/react-radio-group';
import { cn } from './components/ui/utils';
import { AppSettings } from './components/SettingsPanel';
import { VideoResult } from './components/VideoResult';
import { ImageCarouselResult } from './components/ImageCarouselResult';
import { AudioResult } from './components/AudioResult';
import { apiFetch } from './lib/api';
import {
  detectPlatform,
  isCarouselUrl,
  triggerBrowserDownload,
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

const modeOptions = [
  {
    value: 'video' as const,
    icon: Download,
    label: 'Baixar Vídeo',
    description: 'Salva o arquivo completo',
  },
  {
    value: 'images' as const,
    icon: Image,
    label: 'Ler Carrossel',
    description: 'Extrai texto das imagens',
  },
  {
    value: 'audio' as const,
    icon: Mic,
    label: 'Transcrever Áudio',
    description: 'Converte fala em texto',
  },
];

export default function App() {
  const { theme, setTheme } = useTheme();

  const [url, setUrl] = useState('');
  const [downloadType, setDownloadType] = useState<'video' | 'images' | 'audio'>('video');
  const [sourceMode, setSourceMode] = useState<'url' | 'upload'>('url');
  const [uploadType, setUploadType] = useState<'image-text' | 'audio-text'>('image-text');
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [modeWarning, setModeWarning] = useState<{ message: string; suggestedMode: 'video' | 'audio' } | null>(null);
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

  useEffect(() => {
    const savedSettings = localStorage.getItem('app_settings');
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings);
        setSettings((prev) => ({ ...prev, ...parsed }));
      } catch (err) {
        console.error('Failed to load settings:', err);
      }
    }
  }, []);

  // Auto-focus input on mount
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Warn when mode is incompatible with pasted URL
  useEffect(() => {
    if (!url) { setModeWarning(null); return; }
    const platform = detectPlatform(url);
    if (downloadType === 'images' && platform !== 'instagram') {
      setModeWarning({ message: 'Ler Carrossel só funciona com posts do Instagram.', suggestedMode: 'video' });
    } else if (downloadType === 'images' && platform === 'instagram' && url.includes('/reel/')) {
      setModeWarning({ message: 'Este link é um Reel, não um carrossel.', suggestedMode: 'video' });
    } else {
      setModeWarning(null);
    }
  }, [url, downloadType]);

  // Auto-download when video result is ready
  useEffect(() => {
    if (result && result.type === 'video') {
      triggerBrowserDownload(result.blob, result.filename);
    }
  }, [result]);

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round((bytes / Math.pow(k, i)) * 100) / 100 + ' ' + sizes[i];
  };

  const handleKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && url && !isLoading) {
      handleDownload();
    }
  };

  const handleDownloadVideo = async (url: string) => {
    const platform = detectPlatform(url);

    const endpoint = `/download/binary?url=${encodeURIComponent(url)}&format=mp4&quality=max`;

    try {
      const response = await apiFetch(endpoint, { method: 'POST' });

      if (!response.ok) {
        const errorText = await response.text();
        let message = errorText || 'Erro ao baixar vídeo';
        try {
          const errorJson = JSON.parse(errorText);
          if (errorJson?.detail) {
            message =
              typeof errorJson.detail === 'string'
                ? errorJson.detail
                : JSON.stringify(errorJson.detail);
          }
        } catch (_) {
          /* not JSON */
        }
        throw new Error(message);
      }

      const directDownloadHeader = response.headers.get('X-Direct-Download');
      if (directDownloadHeader === 'true') {
        const jsonData = await response.json();
        const directUrl = jsonData.direct_url;
        const responsePlatform = response.headers.get('X-Platform') || '';
        const filename = jsonData.filename || '';

        if (responsePlatform === 'tiktok') {
          try {
            const videoResponse = await fetch(directUrl);
            if (!videoResponse.ok) throw new Error('Failed to download video from TikTok CDN');
            const blob = await videoResponse.blob();
            const blobUrl = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = blobUrl;
            a.download = filename || 'tiktok_video.mp4';
            a.style.display = 'none';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            setTimeout(() => URL.revokeObjectURL(blobUrl), 100);
            return null;
          } catch (error) {
            console.error('TikTok download error:', error);
            window.open(directUrl, '_blank');
            return null;
          }
        }

        window.open(directUrl, '_blank');
        return null;
      }

      const contentLength = response.headers.get('Content-Length');
      const totalBytes = contentLength ? parseInt(contentLength, 10) : 0;
      if (totalBytes > 0) console.log(`Receiving ${formatFileSize(totalBytes)} from server...`);

      const blob = await response.blob();
      if (blob.size === 0) throw new Error('Download falhou: arquivo vazio. Tente novamente.');

      const filename = getFilenameFromResponse(response, `${platform}_${Date.now()}.mp4`);
      const fileSize = formatFileSize(blob.size);

      return { blob, filename, platform, fileSize };
    } catch (err: any) {
      if (err.name === 'AbortError') {
        throw new Error('Download expirou. O arquivo pode ser muito grande. Tente novamente.');
      }
      const detectedPlatform = detectPlatform(url);
      if (
        detectedPlatform === 'tiktok' &&
        (err instanceof TypeError || err.message.includes('fetch'))
      ) {
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
    if (!data.items || !Array.isArray(data.items)) throw new Error('Resposta inválida da API');

    return data.items
      .filter((item: any) => item.url)
      .map((item: any) => {
        const isVideo = !!item.is_video;
        const filename =
          item.filename ||
          item.file ||
          (isVideo ? `instagram_video_${item.index}.mp4` : `instagram_image_${item.index}.jpg`);
        return { url: item.url, transcription: item.text || '', filename, isVideo };
      });
  };

  const handleTranscribeAudio = async (url: string) => {
    const language =
      settings.transcriptionLanguage === 'auto' ? undefined : settings.transcriptionLanguage;
    const response = await apiFetch('/transcribe/video', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, format: settings.audioFormat, language }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      throw new Error(errorText || 'Erro ao transcrever áudio');
    }

    const data = await response.json();
    return data.transcript || 'Transcrição não disponível';
  };

  const handleUpload = async () => {
    if (!uploadFile) return;
    setIsLoading(true);
    setError(null);
    setResult(null);

    const formData = new FormData();
    formData.append('file', uploadFile);

    try {
      if (uploadType === 'image-text') {
        const response = await apiFetch('/transcribe/image', { method: 'POST', body: formData });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        setResult({ type: 'audio', transcription: data.text || 'Nenhum texto encontrado.' });
      } else {
        const response = await apiFetch('/transcribe/upload-audio', { method: 'POST', body: formData });
        if (!response.ok) throw new Error(await response.text());
        const data = await response.json();
        setResult({ type: 'audio', transcription: data.transcript || 'Nenhum texto encontrado.' });
      }
    } catch (err) {
      setError(formatErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  const handleFileDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file) setUploadFile(file);
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) setUploadFile(file);
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const handleSettingsChange = (newSettings: AppSettings) => {
    setSettings(newSettings);
  };

  const startProgressSimulation = (platform: string, type: 'video' | 'images' | 'audio') => {
    if (progressIntervalRef.current) clearInterval(progressIntervalRef.current);
    setProgress(0);

    const estimatedTimes: Record<string, number> = {
      youtube: type === 'audio' ? 20 : 120,
      tiktok: 4,
      instagram: type === 'images' ? 8 : 5,
      twitter: 8,
      unknown: 10,
    };

    const totalTime = estimatedTimes[platform] || estimatedTimes['unknown'];
    const updateInterval = 100;
    const totalSteps = (totalTime * 1000) / updateInterval;
    let currentStep = 0;

    progressIntervalRef.current = window.setInterval(() => {
      currentStep++;
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
        if (videoResult === null) return;
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
        if (!isCarouselUrl(url)) {
          throw new Error(
            'Esta URL não é um post do Instagram. Use a opção "Baixar Vídeo" para reels ou stories.'
          );
        }
        const images = await handleDownloadCarousel(url);
        if (images.length === 0) throw new Error('Nenhuma imagem encontrada no carrossel');
        setResult({ type: 'carousel', images });
      } else if (downloadType === 'audio') {
        const transcription = await handleTranscribeAudio(url);
        setResult({ type: 'audio', transcription });
      }
    } catch (err) {
      setError(formatErrorMessage(err));
      console.error('Download error:', err);
    } finally {
      stopProgressSimulation();
      setIsLoading(false);
    }
  };

  useEffect(() => {
    handleDownloadRef.current = handleDownload;
  }, [handleDownload]);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'v' && !e.shiftKey && !isLoading) {
        e.preventDefault();
        inputRef.current?.focus();
        navigator.clipboard
          .readText()
          .then((text) => {
            if (
              text &&
              (text.includes('instagram.com') ||
                text.includes('youtube.com') ||
                text.includes('tiktok.com') ||
                text.includes('twitter.com') ||
                text.includes('x.com') ||
                text.includes('youtu.be'))
            ) {
              setUrl(text);
            }
          })
          .catch((err) => console.error('Failed to read clipboard:', err));
      }

      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key === 'V' && !isLoading) {
        e.preventDefault();
        navigator.clipboard
          .readText()
          .then((text) => {
            if (
              text &&
              (text.includes('instagram.com') ||
                text.includes('youtube.com') ||
                text.includes('tiktok.com') ||
                text.includes('twitter.com') ||
                text.includes('x.com') ||
                text.includes('youtu.be'))
            ) {
              setUrl(text);
              setTimeout(() => {
                if (!isLoading && text && handleDownloadRef.current) {
                  handleDownloadRef.current();
                }
              }, 100);
            }
          })
          .catch((err) => console.error('Failed to read clipboard:', err));
      }

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
          await new Promise((resolve) => setTimeout(resolve, 500));
        } catch (err) {
          console.error('Error downloading image:', err);
        }
      }
    }
  };

  return (
    <div className="min-h-screen bg-background text-foreground flex flex-col transition-colors duration-300">
      {/* Header */}
      <header className="border-b border-border py-4">
        <div className="max-w-xl mx-auto px-4">
          <div className="flex items-center justify-between">
            <div>
              <h1 className="font-serif text-[22px] font-normal tracking-tight">
                Social Downloader
              </h1>
            </div>
            <div className="flex items-center gap-1">
              <div className="flex items-center rounded-md border border-border bg-muted p-0.5 gap-0.5">
                {([
                  { value: 'light', icon: <Sun className="size-3.5" /> },
                  { value: 'system', icon: <Monitor className="size-3.5" /> },
                  { value: 'dark', icon: <Moon className="size-3.5" /> },
                ] as const).map((opt) => (
                  <button
                    key={opt.value}
                    onClick={() => setTheme(opt.value)}
                    className={`flex items-center justify-center size-6 rounded transition-all cursor-pointer ${
                      theme === opt.value
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    }`}
                    title={opt.value === 'light' ? 'Claro' : opt.value === 'dark' ? 'Escuro' : 'Sistema'}
                  >
                    {opt.icon}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 px-4 py-10">
        <div className="max-w-xl mx-auto flex flex-col gap-6">

          {/* Source mode toggle: URL | Upload */}
          <motion.div
            className="flex"
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5, delay: 0.05 }}
          >
            <div className="flex items-center rounded-md border border-border bg-muted p-0.5 gap-0.5">
              {(['url', 'upload'] as const).map((mode) => (
                <button
                  key={mode}
                  onClick={() => { setSourceMode(mode); setResult(null); setError(null); }}
                  className={`px-3 py-1 rounded text-xs transition-all cursor-pointer ${
                    sourceMode === mode
                      ? 'bg-background text-foreground shadow-sm'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {mode === 'url' ? 'Link' : 'Upload'}
                </button>
              ))}
            </div>
          </motion.div>

          {/* Mode Selector — Radio Cards (URL mode only) */}
          {sourceMode === 'url' && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.1 }}
            >
              <RadioGroupPrimitive.Root
                value={downloadType}
                onValueChange={(val) => setDownloadType(val as 'video' | 'images' | 'audio')}
                className="grid grid-cols-3 gap-3"
              >
                {modeOptions.map((option) => {
                  const Icon = option.icon;
                  return (
                    <RadioGroupPrimitive.Item
                      key={option.value}
                      value={option.value}
                      className={cn(
                        'group relative rounded-lg px-4 py-4 text-start ring-1 ring-border transition-all cursor-pointer focus:outline-none',
                        'hover:bg-muted/50',
                        'data-[state=checked]:ring-2 data-[state=checked]:ring-primary data-[state=checked]:bg-primary/5'
                      )}
                    >
                      <CircleCheck className="absolute top-0 right-0 h-5 w-5 translate-x-1/2 -translate-y-1/2 fill-foreground stroke-background group-data-[state=unchecked]:hidden" />
                      <Icon className="mb-3 w-4 h-4 text-muted-foreground" />
                      <span className="block text-sm font-medium tracking-tight">{option.label}</span>
                      <p className="text-xs text-muted-foreground mt-1 leading-snug">
                        {option.description}
                      </p>
                    </RadioGroupPrimitive.Item>
                  );
                })}
              </RadioGroupPrimitive.Root>
            </motion.div>
          )}

          {/* Upload mode — function selector (2 options) */}
          {sourceMode === 'upload' && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.1 }}
            >
              <RadioGroupPrimitive.Root
                value={uploadType}
                onValueChange={(val) => { setUploadType(val as 'image-text' | 'audio-text'); setUploadFile(null); }}
                className="grid grid-cols-2 gap-3"
              >
                {([
                  { value: 'image-text' as const, icon: Image, label: 'Extrair Texto', description: 'Lê o texto de uma imagem' },
                  { value: 'audio-text' as const, icon: Mic, label: 'Transcrever Áudio', description: 'Áudio ou vídeo local' },
                ]).map((option) => {
                  const Icon = option.icon;
                  return (
                    <RadioGroupPrimitive.Item
                      key={option.value}
                      value={option.value}
                      className={cn(
                        'group relative rounded-lg px-4 py-4 text-start ring-1 ring-border transition-all cursor-pointer focus:outline-none',
                        'hover:bg-muted/50',
                        'data-[state=checked]:ring-2 data-[state=checked]:ring-primary data-[state=checked]:bg-primary/5'
                      )}
                    >
                      <CircleCheck className="absolute top-0 right-0 h-5 w-5 translate-x-1/2 -translate-y-1/2 fill-foreground stroke-background group-data-[state=unchecked]:hidden" />
                      <Icon className="mb-3 w-4 h-4 text-muted-foreground" />
                      <span className="block text-sm font-medium tracking-tight">{option.label}</span>
                      <p className="text-xs text-muted-foreground mt-1 leading-snug">{option.description}</p>
                    </RadioGroupPrimitive.Item>
                  );
                })}
              </RadioGroupPrimitive.Root>
            </motion.div>
          )}

          {/* Mode/URL compatibility warning */}
          <AnimatePresence>
            {sourceMode === 'url' && modeWarning && (
              <motion.div
                className="flex items-center justify-between gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 px-4 py-3"
                initial={{ opacity: 0, y: -8 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -8 }}
              >
                <p className="text-xs text-amber-700 dark:text-amber-400">{modeWarning.message}</p>
                <button
                  onClick={() => { setDownloadType(modeWarning.suggestedMode); setModeWarning(null); }}
                  className="shrink-0 text-xs font-medium text-amber-700 dark:text-amber-400 underline underline-offset-2 hover:opacity-70 transition-opacity"
                >
                  Usar {modeWarning.suggestedMode === 'video' ? 'Baixar Vídeo' : 'Transcrever Áudio'}
                </button>
              </motion.div>
            )}
          </AnimatePresence>

          {/* URL Input Card */}
          {sourceMode === 'url' && (
            <motion.div
              className="rounded-lg border border-border bg-card px-6 py-5 space-y-4"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.2 }}
            >
              <div className="space-y-2">
                <motion.input
                  ref={inputRef}
                  type="text"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  onKeyPress={handleKeyPress}
                  placeholder="Cole o link aqui..."
                  className="w-full px-4 py-3 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring text-foreground placeholder:text-muted-foreground transition-colors"
                  whileFocus={{ scale: 1.005 }}
                  disabled={isLoading}
                />
              </div>

              <motion.button
                onClick={handleDownload}
                disabled={!url || isLoading}
                className="w-full py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
                whileHover={url && !isLoading ? { scale: 1.01 } : {}}
                whileTap={url && !isLoading ? { scale: 0.99 } : {}}
              >
                {isLoading ? (
                  <>
                    <motion.div
                      className="w-4 h-4 border-2 border-current border-t-transparent rounded-full"
                      animate={{ rotate: 360 }}
                      transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                    />
                    Processando...
                  </>
                ) : (
                  <>
                    <Download className="w-4 h-4" />
                    {downloadType === 'video' && 'Baixar Vídeo'}
                    {downloadType === 'images' && 'Extrair Textos do Carrossel'}
                    {downloadType === 'audio' && 'Transcrever Áudio'}
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
                    className="space-y-1.5"
                  >
                    <div className="w-full h-1.5 rounded-full overflow-hidden bg-muted">
                      <motion.div
                        className="h-full bg-primary"
                        initial={{ width: 0 }}
                        animate={{ width: `${progress}%` }}
                        transition={{ duration: 0.3, ease: 'easeOut' }}
                      />
                    </div>
                    <p className="text-xs text-center text-muted-foreground">
                      {progress < 95 ? `${Math.round(progress)}%` : 'Finalizando...'}
                    </p>
                  </motion.div>
                )}
              </AnimatePresence>
            </motion.div>
          )}

          {/* Upload Input Card */}
          {sourceMode === 'upload' && (
            <motion.div
              className="rounded-lg border border-border bg-card px-6 py-5 space-y-4"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.5, delay: 0.2 }}
            >
              {/* Dropzone */}
              <div
                onClick={() => !uploadFile && fileRef.current?.click()}
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleFileDrop}
                className={`relative h-[88px] rounded-lg border-2 border-dashed flex items-center justify-center gap-3 transition-colors ${
                  dragOver
                    ? 'border-foreground/40 bg-muted/40'
                    : uploadFile
                      ? 'border-border bg-muted/20 cursor-default'
                      : 'border-border/60 hover:border-muted-foreground/40 hover:bg-muted/20 cursor-pointer'
                }`}
              >
                <input
                  ref={fileRef}
                  type="file"
                  accept={uploadType === 'image-text' ? 'image/*' : 'audio/*,video/*'}
                  onChange={handleFileChange}
                  className="hidden"
                />

                {uploadFile ? (
                  <div className="flex items-center gap-2.5 px-4 w-full">
                    <FileIcon className="size-5 text-muted-foreground/60 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{uploadFile.name}</p>
                      <p className="text-[11px] text-muted-foreground font-light">{formatSize(uploadFile.size)}</p>
                    </div>
                    <button
                      onClick={(e) => { e.stopPropagation(); setUploadFile(null); if (fileRef.current) fileRef.current.value = ''; }}
                      className="flex-shrink-0 text-muted-foreground hover:text-foreground transition-colors p-1 rounded hover:bg-muted"
                    >
                      <XIcon className="size-3.5" />
                    </button>
                  </div>
                ) : (
                  <div className="flex flex-col items-center gap-1 text-center px-4">
                    <UploadCloud className="size-5 text-muted-foreground/50" />
                    <p className="text-xs text-muted-foreground">
                      Arraste ou{' '}
                      <span className="text-foreground underline underline-offset-2">escolha um arquivo</span>
                    </p>
                    <p className="text-[10px] text-muted-foreground/50">
                      {uploadType === 'image-text' ? 'PNG, JPG, WEBP, etc.' : 'MP4, MOV, MP3, WAV, etc.'}
                    </p>
                  </div>
                )}
              </div>

              <motion.button
                onClick={handleUpload}
                disabled={!uploadFile || isLoading}
                className="w-full py-3 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2 bg-primary text-primary-foreground hover:bg-primary/90 disabled:opacity-40 disabled:cursor-not-allowed"
                whileHover={uploadFile && !isLoading ? { scale: 1.01 } : {}}
                whileTap={uploadFile && !isLoading ? { scale: 0.99 } : {}}
              >
                {isLoading ? (
                  <>
                    <motion.div
                      className="w-4 h-4 border-2 border-current border-t-transparent rounded-full"
                      animate={{ rotate: 360 }}
                      transition={{ duration: 1, repeat: Infinity, ease: 'linear' }}
                    />
                    Processando...
                  </>
                ) : (
                  <>
                    {uploadType === 'image-text' ? <Image className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
                    {uploadType === 'image-text' ? 'Extrair Texto' : 'Transcrever Áudio'}
                  </>
                )}
              </motion.button>
            </motion.div>
          )}

          {/* Error Display */}
          <AnimatePresence>
            {error && (
              <motion.div
                className="border border-destructive/30 bg-destructive/10 rounded-lg px-4 py-3"
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
              >
                <p className="text-sm text-destructive">{error}</p>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Results */}
          {result && result.type === 'video' && (
            <VideoResult
              filename={result.filename}
              platform={result.platform}
              onDownload={handleVideoDownload}
              blob={result.blob}
            />
          )}

          {result && result.type === 'carousel' && (
            <ImageCarouselResult
              images={result.images}
              onDownloadImage={handleImageDownload}
              onDownloadAll={handleDownloadAllImages}
            />
          )}

          {result && result.type === 'audio' && (
            <AudioResult transcription={result.transcription} />
          )}

        </div>
      </main>

    </div>
  );
}
