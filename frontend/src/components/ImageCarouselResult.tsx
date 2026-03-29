import { Download, FileText, Image as ImageIcon, Copy, Check } from 'lucide-react';
import { motion } from 'motion/react';
import { useState } from 'react';

interface CarouselImage {
  url: string;
  transcription: string;
  filename: string;
  isVideo?: boolean;
}

interface ImageCarouselResultProps {
  images: CarouselImage[];
  onDownloadImage: (url: string, filename: string) => void;
  onDownloadAll: () => void;
}

export function ImageCarouselResult({
  images,
  onDownloadImage,
  onDownloadAll,
}: ImageCarouselResultProps) {
  const [copied, setCopied] = useState(false);

  const handleCopyAllTexts = async () => {
    const allTexts = images
      .map((img, idx) => {
        if (!img.transcription) return null;
        return `=== Imagem ${idx + 1} ===\n${img.transcription}`;
      })
      .filter(Boolean)
      .join('\n\n');

    if (!allTexts) return;

    try {
      await navigator.clipboard.writeText(allTexts);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  };

  return (
    <motion.div
      className="rounded-lg border border-border bg-card px-6 py-5 space-y-4"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ImageIcon className="w-4 h-4 text-muted-foreground" />
          <h3 className="font-serif text-[15px] font-normal tracking-tight">
            Carrossel do Instagram{' '}
            <span className="text-muted-foreground font-sans text-xs font-normal">
              ({images.length} {images.length === 1 ? 'item' : 'itens'})
            </span>
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {images.some(img => img.transcription) && (
            <motion.button
              onClick={handleCopyAllTexts}
              className={`px-3 py-1.5 rounded-lg text-xs flex items-center gap-1.5 transition-colors ${
                copied
                  ? 'bg-emerald-600 text-white'
                  : 'bg-muted text-muted-foreground hover:text-foreground'
              }`}
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
            >
              {copied ? (
                <><Check className="w-3.5 h-3.5" />Copiado!</>
              ) : (
                <><Copy className="w-3.5 h-3.5" />Copiar Textos</>
              )}
            </motion.button>
          )}
          {images.length > 1 && (
            <motion.button
              onClick={onDownloadAll}
              className="px-3 py-1.5 rounded-lg text-xs bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
              whileHover={{ scale: 1.03 }}
              whileTap={{ scale: 0.97 }}
            >
              Baixar Todas
            </motion.button>
          )}
        </div>
      </div>

      <div className="space-y-4">
        {images.map((image, idx) => (
          <motion.div
            key={idx}
            className="rounded-lg border border-border bg-background px-4 py-4 space-y-3"
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: idx * 0.1 }}
          >
            <div>
              {image.isVideo ? (
                <video
                  src={image.url}
                  controls
                  className="w-full rounded-lg object-cover max-h-96"
                  preload="metadata"
                  playsInline
                />
              ) : (
                <img
                  src={image.url}
                  alt={`Imagem ${idx + 1}`}
                  className="w-full rounded-lg object-cover max-h-96"
                  loading="lazy"
                />
              )}
            </div>

            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <FileText className="w-3.5 h-3.5 text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">
                  {image.isVideo ? 'Texto extraído dos frames:' : 'Texto extraído:'}
                </span>
              </div>
              <p className="text-sm text-muted-foreground leading-relaxed whitespace-pre-wrap">
                {image.transcription || (
                  <span className="italic">
                    {image.isVideo
                      ? 'Nenhum texto detectado nos frames do vídeo.'
                      : 'Nenhum texto na imagem.'}
                  </span>
                )}
              </p>
            </div>

            <motion.button
              onClick={() => onDownloadImage(image.url, image.filename)}
              className="w-full py-2 rounded-lg text-xs bg-muted text-muted-foreground hover:text-foreground transition-colors flex items-center justify-center gap-1.5"
              whileHover={{ scale: 1.01 }}
              whileTap={{ scale: 0.99 }}
            >
              <Download className="w-3.5 h-3.5" />
              Baixar {image.isVideo ? 'Vídeo' : 'Imagem'} {idx + 1}
            </motion.button>
          </motion.div>
        ))}
      </div>
    </motion.div>
  );
}
