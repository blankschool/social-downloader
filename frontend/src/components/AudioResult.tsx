import { FileText, Copy, Download } from 'lucide-react';
import { motion } from 'motion/react';
import { useState } from 'react';

interface AudioResultProps {
  transcription: string;
}

export function AudioResult({ transcription }: AudioResultProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(transcription);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (error) {
      console.error('Failed to copy:', error);
    }
  };

  const handleDownloadText = () => {
    const blob = new Blob([transcription], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `transcricao_${Date.now()}.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
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
          <FileText className="w-4 h-4 text-muted-foreground" />
          <h3 className="font-serif text-[15px] font-normal tracking-tight">Transcrição de Áudio</h3>
        </div>
        <div className="flex items-center gap-2">
          <motion.button
            onClick={handleCopy}
            className="px-3 py-1.5 rounded-lg text-xs bg-muted text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1.5"
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
          >
            <Copy className="w-3.5 h-3.5" />
            {copied ? 'Copiado!' : 'Copiar'}
          </motion.button>
          <motion.button
            onClick={handleDownloadText}
            className="px-3 py-1.5 rounded-lg text-xs bg-primary text-primary-foreground hover:bg-primary/90 transition-colors flex items-center gap-1.5"
            whileHover={{ scale: 1.03 }}
            whileTap={{ scale: 0.97 }}
          >
            <Download className="w-3.5 h-3.5" />
            Baixar .txt
          </motion.button>
        </div>
      </div>

      <div className="rounded-lg border border-border bg-background px-4 py-3">
        <p className="text-sm text-muted-foreground leading-relaxed whitespace-pre-wrap">
          {transcription}
        </p>
      </div>
    </motion.div>
  );
}
