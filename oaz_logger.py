"""
OAZ Smart Image Bank - Sistema de Logging Centralizado
========================================================
Este módulo fornece logging padronizado para integração com RPA Monitor.

Formato de log:
[YYYY-MM-DD HH:MM:SS.mmm] [LEVEL] [MODULE] [EVENT] message

Níveis:
- INFO: Eventos normais do sistema
- DEBUG: Informações detalhadas para debugging
- WARN: Avisos que não impedem execução
- ERROR: Erros que precisam de atenção
- SUCCESS: Eventos concluídos com sucesso

Eventos:
- START: Início de uma operação
- END: Fim de uma operação
- PROGRESS: Progresso durante operação
- ACTION: Ação do usuário (clique, navegação)
- DATA: Operação de dados (CRUD)
- AUTH: Autenticação
- UPLOAD: Upload de arquivos
- BATCH: Processamento em lote
- IMPORT: Importação de dados
- API: Chamada de API
"""

import sys
import logging
from datetime import datetime
from functools import wraps
from flask import request, g
import traceback

# Configurar logging básico do Python
logging.basicConfig(
    level=logging.DEBUG,
    format='%(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Logger principal
logger = logging.getLogger('OAZ')
logger.setLevel(logging.DEBUG)

# Cores ANSI para console (opcional)
COLORS = {
    'INFO': '\033[94m',      # Azul
    'DEBUG': '\033[90m',     # Cinza
    'WARN': '\033[93m',      # Amarelo
    'ERROR': '\033[91m',     # Vermelho
    'SUCCESS': '\033[92m',   # Verde
    'RESET': '\033[0m'       # Reset
}

# Desabilitar cores se não for terminal
USE_COLORS = sys.stdout.isatty()

def _format_timestamp():
    """Retorna timestamp no formato padrão."""
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

def _colorize(level, text):
    """Adiciona cor ao texto se disponível."""
    if USE_COLORS and level in COLORS:
        return f"{COLORS[level]}{text}{COLORS['RESET']}"
    return text

def _log(level, module, event, message, extra=None):
    """Função interna de logging."""
    timestamp = _format_timestamp()
    
    # Formatar linha principal
    log_line = f"[{timestamp}] [{level:7}] [{module:15}] [{event:10}] {message}"
    
    # Adicionar detalhes extras se existirem
    if extra:
        if isinstance(extra, dict):
            details = ' | '.join([f"{k}={v}" for k, v in extra.items()])
            log_line += f" | {details}"
        else:
            log_line += f" | {extra}"
    
    # Colorir e imprimir
    colored_line = _colorize(level, log_line)
    print(colored_line, flush=True)
    
    return log_line

# ============================================
# FUNÇÕES DE LOGGING POR NÍVEL
# ============================================

def info(module, event, message, **extra):
    """Log de informação geral."""
    return _log('INFO', module, event, message, extra if extra else None)

def debug(module, event, message, **extra):
    """Log de debug detalhado."""
    return _log('DEBUG', module, event, message, extra if extra else None)

def warn(module, event, message, **extra):
    """Log de aviso."""
    return _log('WARN', module, event, message, extra if extra else None)

def error(module, event, message, **extra):
    """Log de erro."""
    return _log('ERROR', module, event, message, extra if extra else None)

def success(module, event, message, **extra):
    """Log de sucesso."""
    return _log('SUCCESS', module, event, message, extra if extra else None)

# ============================================
# FUNÇÕES DE LOGGING POR CONTEXTO
# ============================================

def log_start(module, operation, **extra):
    """Log de início de operação."""
    return info(module, 'START', f"Iniciando: {operation}", **extra)

def log_end(module, operation, **extra):
    """Log de fim de operação."""
    return success(module, 'END', f"Finalizado: {operation}", **extra)

def log_progress(module, operation, current, total, **extra):
    """Log de progresso."""
    percent = round((current / total) * 100, 1) if total > 0 else 0
    return info(module, 'PROGRESS', f"{operation}: {current}/{total} ({percent}%)", **extra)

def log_action(module, action, **extra):
    """Log de ação do usuário."""
    return info(module, 'ACTION', action, **extra)

def log_error(module, operation, error_msg, **extra):
    """Log de erro com detalhes."""
    return error(module, 'ERROR', f"{operation}: {error_msg}", **extra)

def log_data(module, operation, entity, **extra):
    """Log de operação de dados."""
    return info(module, 'DATA', f"{operation}: {entity}", **extra)

# ============================================
# DECORADORES PARA ROTAS FLASK
# ============================================

def log_route(module):
    """
    Decorator para logging automático de rotas Flask.
    Registra entrada e saída de cada rota.
    
    Uso:
        @app.route('/dashboard')
        @log_route('DASHBOARD')
        def dashboard():
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Obter informações da requisição
            method = request.method
            path = request.path
            user = getattr(g, 'user', None)
            user_info = f"user={user.username}" if user else "user=anônimo"
            
            # Log de entrada
            info(module, 'ACTION', f"Acessando rota", method=method, path=path, user=user_info)
            
            try:
                # Executar função
                result = f(*args, **kwargs)
                
                # Log de sucesso
                debug(module, 'END', f"Rota concluída", path=path)
                
                return result
            except Exception as e:
                # Log de erro
                error(module, 'ERROR', f"Erro na rota: {str(e)}", path=path, traceback=traceback.format_exc()[-500:])
                raise
        
        return decorated_function
    return decorator

def log_operation(module, operation_name):
    """
    Decorator para logging de operações longas.
    Registra início, fim e tempo de execução.
    
    Uso:
        @log_operation('BATCH', 'Processamento de lote')
        def process_batch(batch_id):
            ...
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            start_time = datetime.now()
            
            # Log de início
            log_start(module, operation_name)
            
            try:
                # Executar função
                result = f(*args, **kwargs)
                
                # Calcular duração
                duration = (datetime.now() - start_time).total_seconds()
                
                # Log de sucesso
                log_end(module, operation_name, duration=f"{duration:.2f}s")
                
                return result
            except Exception as e:
                # Calcular duração até erro
                duration = (datetime.now() - start_time).total_seconds()
                
                # Log de erro
                log_error(module, operation_name, str(e), duration=f"{duration:.2f}s")
                raise
        
        return decorated_function
    return decorator

# ============================================
# MÓDULOS PRÉ-DEFINIDOS
# ============================================

class OAZModules:
    """Constantes para nomes de módulos."""
    AUTH = 'AUTH'
    DASHBOARD = 'DASHBOARD'
    CARTEIRA = 'CARTEIRA'
    BATCH = 'BATCH'
    UPLOAD = 'UPLOAD'
    CATALOG = 'CATALOG'
    BRANDS = 'BRANDS'
    COLLECTIONS = 'COLLECTIONS'
    PRODUCTS = 'PRODUCTS'
    IMAGES = 'IMAGES'
    REPORTS = 'REPORTS'
    API = 'API'
    DATABASE = 'DATABASE'
    STORAGE = 'STORAGE'
    AI = 'AI'
    SYSTEM = 'SYSTEM'

# Alias para módulos
M = OAZModules

# ============================================
# LOGGING ESPECÍFICO POR ÁREA
# ============================================

class AuthLogger:
    """Logger específico para autenticação."""
    
    @staticmethod
    def login_attempt(username):
        return info(M.AUTH, 'ACTION', f"Tentativa de login", username=username)
    
    @staticmethod
    def login_success(username, user_id):
        return success(M.AUTH, 'SUCCESS', f"Login realizado com sucesso", username=username, user_id=user_id)
    
    @staticmethod
    def login_failed(username, reason):
        return warn(M.AUTH, 'WARN', f"Falha no login", username=username, reason=reason)
    
    @staticmethod
    def logout(username):
        return info(M.AUTH, 'ACTION', f"Logout realizado", username=username)
    
    @staticmethod
    def access_denied(username, path):
        return warn(M.AUTH, 'WARN', f"Acesso negado", username=username, path=path)

class BatchLogger:
    """Logger específico para processamento em lote."""
    
    @staticmethod
    def batch_created(batch_id, name, total_files):
        return info(M.BATCH, 'START', f"Batch criado", batch_id=batch_id, nome=name, arquivos=total_files)
    
    @staticmethod
    def batch_started(batch_id, total_files):
        return info(M.BATCH, 'START', f"Processamento iniciado", batch_id=batch_id, total=total_files)
    
    @staticmethod
    def batch_progress(batch_id, processed, total, success_count, error_count):
        percent = round((processed / total) * 100, 1) if total > 0 else 0
        return info(M.BATCH, 'PROGRESS', f"Batch #{batch_id}: {processed}/{total} ({percent}%)", sucesso=success_count, erros=error_count)
    
    @staticmethod
    def batch_completed(batch_id, success_count, error_count, duration):
        return success(M.BATCH, 'END', f"Batch #{batch_id} concluído", sucesso=success_count, erros=error_count, duracao=f"{duration:.1f}s")
    
    @staticmethod
    def batch_error(batch_id, error_msg):
        return error(M.BATCH, 'ERROR', f"Erro no batch #{batch_id}", erro=error_msg)
    
    @staticmethod
    def file_processing(batch_id, filename, sku):
        return debug(M.BATCH, 'PROGRESS', f"Processando arquivo", batch_id=batch_id, arquivo=filename, sku=sku)
    
    @staticmethod
    def file_success(batch_id, filename, sku, matched=False):
        match_status = "COM match" if matched else "SEM match"
        return debug(M.BATCH, 'SUCCESS', f"Arquivo processado ({match_status})", batch_id=batch_id, sku=sku)
    
    @staticmethod
    def file_error(batch_id, filename, error_msg):
        return error(M.BATCH, 'ERROR', f"Erro ao processar arquivo", batch_id=batch_id, arquivo=filename, erro=error_msg)

class UploadLogger:
    """Logger específico para uploads."""
    
    @staticmethod
    def upload_started(batch_id, filename, size):
        size_mb = round(size / (1024 * 1024), 2) if size else 0
        return info(M.UPLOAD, 'START', f"Upload iniciado", batch_id=batch_id, arquivo=filename, tamanho=f"{size_mb}MB")
    
    @staticmethod
    def upload_progress(batch_id, received, total):
        percent = round((received / total) * 100, 1) if total > 0 else 0
        return debug(M.UPLOAD, 'PROGRESS', f"Upload: {received}/{total} arquivos ({percent}%)", batch_id=batch_id)
    
    @staticmethod
    def upload_completed(batch_id, total_files):
        return success(M.UPLOAD, 'END', f"Upload concluído", batch_id=batch_id, arquivos=total_files)
    
    @staticmethod
    def upload_error(batch_id, filename, error_msg):
        return error(M.UPLOAD, 'ERROR', f"Erro no upload", batch_id=batch_id, arquivo=filename, erro=error_msg)

class CarteiraLogger:
    """Logger específico para Carteira de Compras."""
    
    @staticmethod
    def import_started(filename, sheet_name=None):
        return info(M.CARTEIRA, 'START', f"Importação iniciada", arquivo=filename, aba=sheet_name or "todas")
    
    @staticmethod
    def import_progress(processed, total, sheet_name=None):
        percent = round((processed / total) * 100, 1) if total > 0 else 0
        return info(M.CARTEIRA, 'PROGRESS', f"Importação: {processed}/{total} ({percent}%)", aba=sheet_name)
    
    @staticmethod
    def import_completed(total_items, sheets_count, lote_id):
        return success(M.CARTEIRA, 'END', f"Importação concluída", itens=total_items, abas=sheets_count, lote=lote_id)
    
    @staticmethod
    def import_error(error_msg, line=None):
        return error(M.CARTEIRA, 'ERROR', f"Erro na importação", erro=error_msg, linha=line)
    
    @staticmethod
    def sku_created(sku, colecao):
        return debug(M.CARTEIRA, 'DATA', f"SKU criado", sku=sku, colecao=colecao)
    
    @staticmethod
    def sku_updated(sku, colecao):
        return debug(M.CARTEIRA, 'DATA', f"SKU atualizado", sku=sku, colecao=colecao)
    
    @staticmethod
    def reconciliation_started():
        return info(M.CARTEIRA, 'START', f"Reconciliação iniciada")
    
    @staticmethod
    def reconciliation_completed(count):
        return success(M.CARTEIRA, 'END', f"Reconciliação concluída", imagens_reconciliadas=count)

class CatalogLogger:
    """Logger específico para catálogo de imagens."""
    
    @staticmethod
    def page_accessed(page, filters=None):
        return info(M.CATALOG, 'ACTION', f"Catálogo acessado", pagina=page, filtros=filters)
    
    @staticmethod
    def image_viewed(image_id, sku):
        return debug(M.CATALOG, 'ACTION', f"Imagem visualizada", image_id=image_id, sku=sku)
    
    @staticmethod
    def image_approved(image_id, sku, user):
        return info(M.CATALOG, 'DATA', f"Imagem aprovada", image_id=image_id, sku=sku, usuario=user)
    
    @staticmethod
    def image_rejected(image_id, sku, user, reason=None):
        return info(M.CATALOG, 'DATA', f"Imagem rejeitada", image_id=image_id, sku=sku, usuario=user, motivo=reason)

class CRUDLogger:
    """Logger genérico para operações CRUD."""
    
    @staticmethod
    def created(entity_type, entity_id, name=None):
        return info(M.DATABASE, 'DATA', f"{entity_type} criado", id=entity_id, nome=name)
    
    @staticmethod
    def updated(entity_type, entity_id, fields=None):
        return info(M.DATABASE, 'DATA', f"{entity_type} atualizado", id=entity_id, campos=fields)
    
    @staticmethod
    def deleted(entity_type, entity_id, name=None):
        return warn(M.DATABASE, 'DATA', f"{entity_type} excluído", id=entity_id, nome=name)
    
    @staticmethod
    def listed(entity_type, count, filters=None):
        return debug(M.DATABASE, 'DATA', f"{entity_type} listados", quantidade=count, filtros=filters)

class NavigationLogger:
    """Logger para navegação no sistema."""
    
    @staticmethod
    def page_enter(page_name, user=None):
        return info(M.SYSTEM, 'ACTION', f"Entrando na página: {page_name}", usuario=user)
    
    @staticmethod
    def tab_switch(tab_name, user=None):
        return debug(M.SYSTEM, 'ACTION', f"Mudando para aba: {tab_name}", usuario=user)
    
    @staticmethod
    def button_click(button_name, page=None, user=None):
        return debug(M.SYSTEM, 'ACTION', f"Clique no botão: {button_name}", pagina=page, usuario=user)
    
    @staticmethod
    def modal_open(modal_name, page=None):
        return debug(M.SYSTEM, 'ACTION', f"Modal aberto: {modal_name}", pagina=page)
    
    @staticmethod
    def modal_close(modal_name, page=None):
        return debug(M.SYSTEM, 'ACTION', f"Modal fechado: {modal_name}", pagina=page)

# ============================================
# INSTÂNCIAS GLOBAIS
# ============================================

auth_log = AuthLogger()
batch_log = BatchLogger()
upload_log = UploadLogger()
carteira_log = CarteiraLogger()
catalog_log = CatalogLogger()
crud_log = CRUDLogger()
nav_log = NavigationLogger()

# ============================================
# FUNÇÕES DE CONVENIÊNCIA
# ============================================

def log_separator(title=None):
    """Imprime linha separadora no log."""
    sep = "=" * 60
    if title:
        print(f"\n{sep}\n{title.center(60)}\n{sep}", flush=True)
    else:
        print(sep, flush=True)

def log_section(title):
    """Imprime cabeçalho de seção no log."""
    timestamp = _format_timestamp()
    print(f"\n[{timestamp}] {'=' * 20} {title} {'=' * 20}", flush=True)

# ============================================
# INICIALIZAÇÃO
# ============================================

def init_logging():
    """Inicializa o sistema de logging."""
    log_separator("OAZ SMART IMAGE BANK - SISTEMA DE LOGGING")
    info(M.SYSTEM, 'START', "Sistema de logging inicializado")
    info(M.SYSTEM, 'INFO', f"Cores: {'habilitadas' if USE_COLORS else 'desabilitadas'}")
    return True

# Auto-inicialização ao importar
init_logging()
