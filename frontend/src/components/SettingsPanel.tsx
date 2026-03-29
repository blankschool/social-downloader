import { X } from 'lucide-react';
import { motion } from 'motion/react';
import { useState, useEffect } from 'react';

export interface AppSettings {
  videoQuality: string;
  audioFormat: string;
  transcriptionLanguage: string;
}

interface SettingsPanelProps {
  onClose: () => void;
  onSettingsChange?: (settings: AppSettings) => void;
}

const DEFAULT_SETTINGS: AppSettings = {
  videoQuality: 'max',
  audioFormat: 'mp3',
  transcriptionLanguage: 'auto',
};

export function SettingsPanel({ onClose, onSettingsChange }: SettingsPanelProps) {
  const [settings, setSettings] = useState<AppSettings>(DEFAULT_SETTINGS);

  useEffect(() => {
    const savedSettings = localStorage.getItem('app_settings');
    if (savedSettings) {
      try {
        const parsed = JSON.parse(savedSettings);
        setSettings({ ...DEFAULT_SETTINGS, ...parsed });
      } catch (err) {
        console.error('Failed to parse saved settings:', err);
      }
    }
  }, []);

  const updateSetting = <K extends keyof AppSettings>(key: K, value: AppSettings[K]) => {
    const newSettings = { ...settings, [key]: value };
    setSettings(newSettings);
    localStorage.setItem('app_settings', JSON.stringify(newSettings));
    onSettingsChange?.(newSettings);
  };

  return (
    <motion.div
      className="rounded-lg border border-border bg-card px-6 py-5 space-y-5"
      initial={{ opacity: 0, y: -20, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -20, scale: 0.95 }}
      transition={{ duration: 0.3 }}
    >
      <div className="flex items-center justify-between">
        <h3 className="font-serif text-[15px] font-normal tracking-tight">Configurações</h3>
        <motion.button
          onClick={onClose}
          className="p-1 hover:bg-muted rounded transition-colors text-muted-foreground hover:text-foreground"
          aria-label="Fechar configurações"
          whileHover={{ scale: 1.1, rotate: 90 }}
          whileTap={{ scale: 0.9 }}
        >
          <X className="w-4 h-4" />
        </motion.button>
      </div>

      <div className="space-y-4">
        <motion.div
          className="space-y-1.5"
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.1 }}
        >
          <label className="text-xs font-medium text-muted-foreground">Qualidade do Vídeo</label>
          <select
            value={settings.videoQuality}
            onChange={(e) => updateSetting('videoQuality', e.target.value)}
            className="w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
          >
            <option value="max">Máxima (padrão)</option>
            <option value="1080">1080p</option>
            <option value="720">720p</option>
            <option value="480">480p</option>
          </select>
        </motion.div>

        <motion.div
          className="space-y-1.5"
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.15 }}
        >
          <label className="text-xs font-medium text-muted-foreground">Formato de Áudio</label>
          <select
            value={settings.audioFormat}
            onChange={(e) => updateSetting('audioFormat', e.target.value)}
            className="w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
          >
            <option value="mp3">MP3</option>
            <option value="wav">WAV</option>
            <option value="m4a">M4A</option>
          </select>
        </motion.div>

        <motion.div
          className="space-y-1.5"
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.2 }}
        >
          <label className="text-xs font-medium text-muted-foreground">Idioma da Transcrição</label>
          <select
            value={settings.transcriptionLanguage}
            onChange={(e) => updateSetting('transcriptionLanguage', e.target.value)}
            className="w-full px-3 py-2 text-sm bg-background border border-border rounded-lg focus:outline-none focus:ring-1 focus:ring-ring text-foreground"
          >
            <option value="auto">Detectar automaticamente</option>
            <option value="pt">Português</option>
            <option value="en">Inglês</option>
            <option value="es">Espanhol</option>
            <option value="fr">Francês</option>
            <option value="de">Alemão</option>
            <option value="it">Italiano</option>
            <option value="ja">Japonês</option>
            <option value="ko">Coreano</option>
            <option value="zh">Chinês</option>
          </select>
        </motion.div>

        <motion.div
          className="space-y-3 pt-1"
          initial={{ opacity: 0, x: -20 }}
          animate={{ opacity: 1, x: 0 }}
          transition={{ delay: 0.25 }}
        >
          <label className="flex items-center justify-between cursor-pointer gap-3">
            <span className="text-xs text-muted-foreground">Incluir áudio nos downloads de vídeo</span>
            <input type="checkbox" defaultChecked className="w-4 h-4 accent-foreground" />
          </label>

          <label className="flex items-center justify-between cursor-pointer gap-3">
            <span className="text-xs text-muted-foreground">Transcrever áudio automaticamente</span>
            <input type="checkbox" className="w-4 h-4 accent-foreground" />
          </label>

          <label className="flex items-center justify-between cursor-pointer gap-3">
            <span className="text-xs text-muted-foreground">Baixar todas as imagens do carrossel</span>
            <input type="checkbox" defaultChecked className="w-4 h-4 accent-foreground" />
          </label>
        </motion.div>
      </div>

      <div className="pt-2 border-t border-border">
        <p className="text-xs text-muted-foreground/60">
          As configurações são salvas localmente no seu navegador
        </p>
      </div>
    </motion.div>
  );
}
