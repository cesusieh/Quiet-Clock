// SPDX-License-Identifier: MIT
import Adw from 'gi://Adw';
import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

export default class QuietIslandPreferences extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const page = new Adw.PreferencesPage({title: 'Quiet Island', icon_name: 'preferences-system-time-symbolic'});
        window.add(page);
        const appearance = new Adw.PreferencesGroup({title: 'Barra superior',
            description: 'Hora em 24 horas no centro do monitor principal. Três menus independentes.'});
        page.add(appearance);
        for (const [key, title, subtitle] of [
            ['show-music', 'Música à esquerda', 'Aparece quando há um player tocando ou pausado.'],
            ['show-cover', 'Capa no menu de música', 'Capa de 68 px ou ícone do sistema.'],
            ['show-progress', 'Progresso e busca', 'Avançar só quando o player permitir.'],
            ['show-visualizer', 'Visualizador de áudio', 'Níveis reais do serviço local; sem animação inventada.'],
            ['show-ai', 'Atividade de IA à direita', 'Aparece somente quando há sessões conectadas.'],
            ['show-project', 'Projetos nos detalhes', 'Usa somente o nome de projeto informado pela integração.'],
            ['notify-completion', 'Aviso de conclusão', 'Respeita Não Perturbe e as configurações de notificações.'],
        ]) {
            const row = new Adw.SwitchRow({title, subtitle});
            settings.bind(key, row, 'active', 0);
            appearance.add(row);
        }
        const group = new Adw.PreferencesGroup({title: 'Integrações locais',
            description: 'Codex, Claude Code e Hermes precisam dos hooks do projeto. Sem integração: sem conexão. Não são lidos prompts ou respostas.'});
        page.add(group);
        group.add(new Adw.ActionRow({title: 'Primeiro protótipo · 0.1.0',
            subtitle: 'Abrir terminal, tokens, custos e outros módulos permanecem no backlog.'}));
    }
}
