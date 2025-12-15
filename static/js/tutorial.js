class TutorialManager {
    constructor() {
        this.isActive = localStorage.getItem('tutorialMode') === 'true';
        this.modal = null;
        this.init();
    }
    
    init() {
        this.createModal();
        this.setupEventListeners();
        this.updateToggleButton();
    }
    
    createModal() {
        const modalHTML = `
        <div id="tutorialModal" class="tutorial-modal" style="display:none;">
            <div class="tutorial-modal-content">
                <div class="tutorial-modal-header">
                    <span class="tutorial-modal-icon"><i class="fas fa-info-circle"></i></span>
                    <h4 id="tutorialModalTitle"></h4>
                    <span class="tutorial-modal-close">&times;</span>
                </div>
                <p id="tutorialModalDesc"></p>
                <div class="tutorial-modal-footer">
                    <span class="tutorial-hint"><i class="fas fa-lightbulb"></i> Clique fora ou no X para fechar</span>
                </div>
            </div>
        </div>`;
        document.body.insertAdjacentHTML('beforeend', modalHTML);
        this.modal = document.getElementById('tutorialModal');
        this.modal.querySelector('.tutorial-modal-close').onclick = () => this.hideModal();
        this.modal.onclick = (e) => {
            if (e.target === this.modal) this.hideModal();
        };
    }
    
    setupEventListeners() {
        document.addEventListener('click', (e) => this.handleInteraction(e), true);
        document.addEventListener('submit', (e) => this.handleInteraction(e), true);
        document.addEventListener('change', (e) => this.handleInteraction(e), true);
        document.addEventListener('focus', (e) => this.handleFocus(e), true);
    }
    
    handleInteraction(e) {
        if (!this.isActive) return;
        
        const target = e.target.closest('button, a, input, select, textarea, [onclick], label, form, .card, .stat-card, .nav-item');
        if (!target) return;
        
        if (target.closest('#tutorialModal, #tutorialToggle, .tutorial-excluded')) return;
        
        e.preventDefault();
        e.stopPropagation();
        e.stopImmediatePropagation();
        
        const info = this.getElementInfo(target);
        this.showModal(info.title, info.desc);
    }
    
    handleFocus(e) {
        if (!this.isActive) return;
        const target = e.target;
        if (target.matches('input, select, textarea, [contenteditable]')) {
            if (!target.closest('#tutorialModal, .tutorial-excluded')) {
                e.preventDefault();
                target.blur();
            }
        }
    }
    
    getElementInfo(el) {
        let title = el.getAttribute('data-tutorial-title');
        let desc = el.getAttribute('data-tutorial-desc');
        
        if (title && desc) return { title, desc };
        
        title = title || this.generateTitle(el);
        desc = desc || this.generateDescription(el);
        
        return { title, desc };
    }
    
    generateTitle(el) {
        const text = el.textContent?.trim()?.substring(0, 50);
        if (text && text.length > 2 && text.length < 40) return text;
        
        const tooltip = el.getAttribute('title') || el.getAttribute('data-bs-original-title');
        if (tooltip) return tooltip;
        
        const icon = el.querySelector('i[class*="fa-"]');
        if (icon) {
            const iconClass = [...icon.classList].find(c => c.startsWith('fa-') && c !== 'fa-fw');
            if (iconClass) return this.iconToTitle(iconClass);
        }
        
        if (el.tagName === 'INPUT') {
            const types = {
                'text': 'Campo de Texto',
                'email': 'Campo de E-mail',
                'password': 'Campo de Senha',
                'number': 'Campo Numérico',
                'file': 'Seleção de Arquivo',
                'date': 'Seleção de Data',
                'checkbox': 'Caixa de Seleção',
                'radio': 'Opção de Seleção',
                'search': 'Campo de Busca'
            };
            return types[el.type] || 'Campo de Entrada';
        }
        if (el.tagName === 'SELECT') return 'Lista de Seleção';
        if (el.tagName === 'BUTTON') return 'Botão';
        if (el.tagName === 'FORM') return 'Formulário';
        if (el.classList.contains('card') || el.classList.contains('stat-card')) return 'Card de Informação';
        
        return 'Elemento Interativo';
    }
    
    generateDescription(el) {
        if (el.placeholder) return `Digite aqui: ${el.placeholder}`;
        
        if (el.getAttribute('aria-label')) return el.getAttribute('aria-label');
        
        const label = document.querySelector(`label[for="${el.id}"]`);
        if (label) return `Preencha o campo: ${label.textContent.trim()}`;
        
        if (el.tagName === 'A' && el.href) {
            if (el.href.includes('/edit')) return 'Abre o formulário de edição deste item';
            if (el.href.includes('/delete')) return 'Remove este item do sistema';
            if (el.href.includes('/view') || el.href.includes('/show') || el.href.includes('/detail')) return 'Visualiza os detalhes completos';
            if (el.href.includes('/new') || el.href.includes('/create')) return 'Abre o formulário para criar um novo item';
            if (el.href.includes('/upload')) return 'Abre a tela de upload de arquivos';
            if (el.href.includes('/catalog')) return 'Acessa a biblioteca de imagens';
            if (el.href.includes('/dashboard')) return 'Volta para o painel principal';
            if (el.href.includes('/batch')) return 'Gerencia uploads em lote';
            if (el.href.includes('/carteira')) return 'Acessa a carteira de compras';
            if (el.href.includes('/collection')) return 'Gerencia coleções';
            if (el.href.includes('/brand')) return 'Gerencia marcas';
            if (el.href.includes('/report')) return 'Acessa relatórios do sistema';
            if (el.href.includes('/logout')) return 'Encerra sua sessão no sistema';
            return 'Navega para outra página do sistema';
        }
        
        if (el.type === 'submit') return 'Confirma e envia as informações do formulário';
        if (el.type === 'file') return 'Clique para selecionar arquivos do seu computador';
        if (el.type === 'checkbox') return 'Marque ou desmarque esta opção';
        if (el.type === 'search') return 'Digite para buscar no sistema';
        
        if (el.classList.contains('stat-card') || el.classList.contains('card')) {
            return 'Card com informações e métricas do sistema';
        }
        
        if (el.classList.contains('nav-item')) {
            return 'Menu de navegação - acessa diferentes áreas do sistema';
        }
        
        return 'Clique para interagir com este elemento';
    }
    
    iconToTitle(iconClass) {
        const map = {
            'fa-eye': 'Visualizar',
            'fa-edit': 'Editar',
            'fa-pen': 'Editar',
            'fa-pencil': 'Editar',
            'fa-trash': 'Excluir',
            'fa-trash-alt': 'Excluir',
            'fa-plus': 'Adicionar Novo',
            'fa-plus-circle': 'Adicionar Novo',
            'fa-save': 'Salvar',
            'fa-download': 'Download',
            'fa-upload': 'Upload',
            'fa-cloud-upload-alt': 'Upload em Lote',
            'fa-search': 'Pesquisar',
            'fa-filter': 'Filtrar',
            'fa-refresh': 'Atualizar',
            'fa-sync': 'Sincronizar',
            'fa-redo': 'Reprocessar',
            'fa-file-pdf': 'Documento PDF',
            'fa-file-excel': 'Planilha Excel',
            'fa-file-csv': 'Arquivo CSV',
            'fa-camera': 'Captura de Tela',
            'fa-image': 'Imagem',
            'fa-images': 'Galeria de Imagens',
            'fa-th-large': 'Painel Principal',
            'fa-robot': 'Análise por IA',
            'fa-box': 'Produtos',
            'fa-shopping-cart': 'Carteira de Compras',
            'fa-folder': 'Coleções',
            'fa-tag': 'Marcas',
            'fa-clipboard-check': 'Auditoria',
            'fa-file-alt': 'Relatórios',
            'fa-network-wired': 'Integrações',
            'fa-question-circle': 'Ajuda / Tutorial',
            'fa-sign-out-alt': 'Sair do Sistema',
            'fa-cog': 'Configurações',
            'fa-bell': 'Notificações',
            'fa-check': 'Confirmar',
            'fa-times': 'Cancelar / Fechar',
            'fa-play': 'Iniciar',
            'fa-pause': 'Pausar',
            'fa-stop': 'Parar',
            'fa-gem': 'OAZ Banco de Imagens'
        };
        const result = map[iconClass];
        if (result) return result;
        return iconClass.replace('fa-', '').replace(/-/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
    }
    
    toggle() {
        this.isActive = !this.isActive;
        localStorage.setItem('tutorialMode', this.isActive);
        this.updateToggleButton();
        this.showToggleNotification();
        
        if (this.isActive) {
            document.querySelectorAll('input, textarea, select').forEach(el => el.blur());
        }
    }
    
    updateToggleButton() {
        const btn = document.getElementById('tutorialToggle');
        if (btn) {
            btn.classList.toggle('active', this.isActive);
            btn.innerHTML = this.isActive 
                ? '<i class="fas fa-graduation-cap"></i> Tutorial ATIVO'
                : '<i class="far fa-question-circle"></i> Tutorial';
        }
    }
    
    showToggleNotification() {
        const existing = document.querySelector('.tutorial-notification');
        if (existing) existing.remove();
        
        const notification = document.createElement('div');
        notification.className = 'tutorial-notification';
        notification.innerHTML = this.isActive 
            ? '<i class="fas fa-graduation-cap"></i> Modo Tutorial ATIVADO - Clique em qualquer elemento para ver sua descrição'
            : '<i class="fas fa-check"></i> Modo Tutorial desativado - Navegação normal restaurada';
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.classList.add('fade-out');
            setTimeout(() => notification.remove(), 300);
        }, 3000);
    }
    
    showModal(title, desc) {
        document.getElementById('tutorialModalTitle').textContent = title;
        document.getElementById('tutorialModalDesc').textContent = desc;
        this.modal.style.display = 'flex';
    }
    
    hideModal() {
        this.modal.style.display = 'none';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.tutorialManager = new TutorialManager();
});
