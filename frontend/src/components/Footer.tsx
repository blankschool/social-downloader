import { motion } from 'motion/react';

export function Footer() {
  return (
    <footer className="border-t border-border py-8">
      <div className="max-w-xl mx-auto px-4">
        <motion.div
          className="text-center text-xs text-muted-foreground"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.7 }}
        >
          <p>Baixe e transcreva conteúdo das redes sociais</p>
          <p className="mt-1">Suporta Instagram, YouTube, TikTok e X (Twitter)</p>
        </motion.div>
      </div>
    </footer>
  );
}
