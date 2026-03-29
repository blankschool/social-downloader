import { Download } from 'lucide-react';
import { motion } from 'motion/react';

interface VideoResultProps {
  filename: string;
  platform: string;
  onDownload: () => void;
  blob?: Blob;
}

const platformLabels: Record<string, string> = {
  youtube: 'YouTube',
  instagram: 'Instagram',
  tiktok: 'TikTok',
  twitter: 'X',
};

export function VideoResult({ filename, platform, onDownload, blob }: VideoResultProps) {
  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
  };

  const fileSize = blob ? formatFileSize(blob.size) : null;
  const label = platformLabels[platform] || platform;

  return (
    <motion.div
      className="rounded-lg border border-border bg-card px-6 py-5"
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div className="flex items-center justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-1.5">
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
              {label}
            </span>
          </div>
          <p className="font-serif text-[15px] font-normal tracking-tight truncate">{filename}</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            {fileSize ? `${fileSize} · Pronto para download` : 'Pronto para download'}
          </p>
        </div>
        <motion.button
          onClick={onDownload}
          className="shrink-0 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm flex items-center gap-2 hover:bg-primary/90 transition-colors"
          whileHover={{ scale: 1.03 }}
          whileTap={{ scale: 0.97 }}
        >
          <Download className="w-4 h-4" />
          Baixar
        </motion.button>
      </div>
    </motion.div>
  );
}
