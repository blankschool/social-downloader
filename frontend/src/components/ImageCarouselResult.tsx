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
  isDark: boolean;
}

export function ImageCarouselResult({
  images,
  onDownloadImage,
  onDownloadAll,
  isDark,
}: ImageCarouselResultProps) {
  const [copied, setCopied] = useState(false);

  const handleCopyAllTexts = async () => {
    // Combine all transcriptions with separators
    const allTexts = images
      .map((img, idx) => {
        if (!img.transcription) return null;
        return `=== Imagem ${idx + 1} ===\n${img.transcription}`;
      })
      .filter(Boolean)
      .join('\n\n');

    if (!allTexts) {
      return;
    }

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
      className={`${
        isDark ? 'bg-[#1a1a1a] border-[#2a2a2a]' : 'bg-white border-[#e0e0e0]'
      } border rounded-xl p-6 space-y-4`}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ImageIcon className={`w-5 h-5 ${isDark ? 'text-[#6a6a6a]' : 'text-[#999999]'}`} />
          <h3 className="text-lg font-semibold">
            Carrossel do Instagram ({images.length} {images.length === 1 ? 'item' : 'itens'})
          </h3>
        </div>
        <div className="flex items-center gap-2">
          {/* Copy All Texts Button */}
          {images.some(img => img.transcription) && (
            <motion.button
              onClick={handleCopyAllTexts}
              className={`px-4 py-2 rounded-lg text-sm transition-colors flex items-center gap-2 ${
                copied
                  ? 'bg-green-600 text-white'
                  : isDark
                  ? 'bg-[#3a3a3a] text-white hover:bg-[#4a4a4a]'
                  : 'bg-[#e0e0e0] text-[#1a1a1a] hover:bg-[#d0d0d0]'
              }`}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              {copied ? (
                <>
                  <Check className="w-4 h-4" />
                  Copiado!
                </>
              ) : (
                <>
                  <Copy className="w-4 h-4" />
                  Copiar Textos
                </>
              )}
            </motion.button>
          )}
          {/* Download All Button */}
          {images.length > 1 && (
            <motion.button
              onClick={onDownloadAll}
              className={`px-4 py-2 rounded-lg text-sm transition-colors ${
                isDark
                  ? 'bg-[#4a4a4a] text-white hover:bg-[#5a5a5a]'
                  : 'bg-[#1a1a1a] text-white hover:bg-[#2a2a2a]'
              }`}
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
            >
              Baixar Todas
            </motion.button>
          )}
        </div>
      </div>

      <div className="space-y-6">
        {images.map((image, idx) => (
          <motion.div
            key={idx}
            className={`p-4 ${
              isDark ? 'bg-[#0f0f0f] border-[#2a2a2a]' : 'bg-[#f5f5f5] border-[#e0e0e0]'
            } border rounded-lg space-y-3`}
            initial={{ opacity: 0, x: -20 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: idx * 0.1 }}
          >
            <div className="flex items-start gap-4">
              <div className="flex-1">
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
            </div>

            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <FileText className={`w-4 h-4 ${isDark ? 'text-[#6a6a6a]' : 'text-[#999999]'}`} />
                <span className="text-sm font-medium">
                  {image.isVideo ? 'Texto extraído dos frames:' : 'Texto extraído:'}
                </span>
              </div>
              <p
                className={`text-sm ${
                  isDark ? 'text-[#a0a0a0]' : 'text-[#666666]'
                } leading-relaxed whitespace-pre-wrap`}
              >
                {image.transcription || (
                  <span className={isDark ? 'text-[#6a6a6a] italic' : 'text-[#888888] italic'}>
                    {image.isVideo
                      ? 'Nenhum texto detectado nos frames do vídeo.'
                      : 'Nenhum texto na imagem.'}
                  </span>
                )}
              </p>
            </div>

            <motion.button
              onClick={() => onDownloadImage(image.url, image.filename)}
              className={`w-full py-2 rounded-lg transition-colors flex items-center justify-center gap-2 ${
                isDark
                  ? 'bg-[#252525] hover:bg-[#2a2a2a]'
                  : 'bg-[#e0e0e0] hover:bg-[#d0d0d0]'
              }`}
              whileHover={{ scale: 1.02 }}
              whileTap={{ scale: 0.98 }}
            >
              <Download className="w-4 h-4" />
              Baixar {image.isVideo ? 'Vídeo' : 'Imagem'} {idx + 1}
            </motion.button>
          </motion.div>
        ))}
      </div>
    </motion.div>
  );
}
