import os
import io
import csv
import mimetypes
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, flash, request, Response, make_response, jsonify, stream_with_context
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from openai import OpenAI
from dotenv import load_dotenv
import json
import base64
from PIL import Image as PILImage
import unicodedata

# SharePoint client (Microsoft Graph)
from sharepoint_client import (
    build_sharepoint_client_from_env,
    get_brand_name_from_path,
    get_collection_name_from_path,
    get_collection_and_subfolder_from_path,
    get_sharepoint_env,
    parse_sku_variants,
)
# Sistema de Logging OAZ (local)
from oaz_logger import (
    info, debug, warn, error, success,
    log_start, log_end, log_progress, log_action, log_error, log_data,
    log_route, log_operation, log_separator, log_section,
    auth_log, batch_log, upload_log, carteira_log, catalog_log, crud_log, nav_log,
    M  # Módulos
)

# RPA Monitor Client (monitoramento externo via WebSocket)
try:
    from rpa_monitor_client import auto_setup_rpa_monitor, rpa_log
    RPA_MONITOR_AVAILABLE = True
except ImportError:
    RPA_MONITOR_AVAILABLE = False
    rpa_log = None

# Load environment variables
load_dotenv()

# Inicializar RPA Monitor se disponível (usa variáveis de ambiente automaticamente)
# Variáveis esperadas: RPA_MONITOR_ID, RPA_MONITOR_HOST, RPA_MONITOR_TRANSPORT, RPA_MONITOR_REGION
if RPA_MONITOR_AVAILABLE:
    try:
        auto_setup_rpa_monitor()
        rpa_id = os.environ.get('RPA_MONITOR_ID', 'N/A')
        rpa_host = os.environ.get('RPA_MONITOR_HOST', 'N/A')
        print(f"[RPA Monitor] Conectado: {rpa_id} -> {rpa_host}")
    except Exception as e:
        print(f"[RPA Monitor] Erro ao conectar: {e}")
        RPA_MONITOR_AVAILABLE = False

# Funções helper para RPA logging (envia para servidor externo)
def rpa_info(msg, regiao="geral"):
    """Log INFO para RPA Monitor externo"""
    if RPA_MONITOR_AVAILABLE and rpa_log:
        try:
            rpa_log.info(msg)
        except Exception:
            pass

def rpa_warn(msg, regiao="geral"):
    """Log WARNING para RPA Monitor externo"""
    if RPA_MONITOR_AVAILABLE and rpa_log:
        try:
            rpa_log.warn(msg)
        except Exception:
            pass

def rpa_error(msg, exc=None, regiao="geral", take_screenshot=True):
    """Log ERROR para RPA Monitor externo com screenshot automático"""
    if RPA_MONITOR_AVAILABLE and rpa_log:
        try:
            rpa_log.error(msg, exc=exc, regiao=regiao)
            if take_screenshot:
                import time
                rpa_log.screenshot(
                    filename=f"error_{int(time.time())}.png",
                    regiao=regiao,
                )
        except Exception:
            pass

def rpa_screenshot(regiao="manual"):
    """Captura screenshot para RPA Monitor externo"""
    if RPA_MONITOR_AVAILABLE and rpa_log:
        try:
            import time
            rpa_log.screenshot(
                filename=f"screen_{int(time.time())}.png",
                regiao=regiao,
            )
        except Exception:
            pass

# Configuration
class Config:
    SECRET_KEY = os.environ.get('FLASK_SECRET_KEY') or 'dev-secret-key-oaz-img'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///oaz_img.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_recycle": 300,
        "pool_pre_ping": True,
        "pool_size": 20,
        "max_overflow": 30,
        "pool_timeout": 60,
    }
    UPLOAD_FOLDER = 'static/uploads'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'zip'}
    MAX_BATCH_WORKERS = 5  # Número de threads para processamento paralelo
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024 * 1024  # 3GB - suporta uploads grandes
    STORAGE_BACKEND = os.environ.get('STORAGE_BACKEND', 'bucket').lower()

app = Flask(__name__)
app.config.from_object(Config)

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
migrate = Migrate(app, db)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

_sharepoint_client = None
_sharepoint_index_cache = {"index": None, "created_at": None}
_sharepoint_index_ttl_minutes = int(os.environ.get("SHAREPOINT_INDEX_TTL_MINUTES", "30"))


def is_sharepoint_backend():
    return app.config.get('STORAGE_BACKEND', 'bucket') == 'sharepoint'


def get_sharepoint_client():
    global _sharepoint_client
    if _sharepoint_client is None:
        _sharepoint_client = build_sharepoint_client_from_env()
    return _sharepoint_client


def build_sharepoint_index(force_refresh=False, ttl_minutes=None):
    cache = _sharepoint_index_cache
    ttl_minutes = _sharepoint_index_ttl_minutes if ttl_minutes is None else ttl_minutes
    cache_exists = cache["index"] is not None and cache["created_at"]
    if cache["index"] is not None and not force_refresh and cache["created_at"]:
        age_minutes = (datetime.utcnow() - cache["created_at"]).total_seconds() / 60
        if age_minutes <= ttl_minutes:
            print(f"[SP] Usando índice em cache ({int(age_minutes)} min)")
            return cache["index"]
        print(f"[SP] Índice expirado ({int(age_minutes)} min), atualizando")

    if force_refresh:
        print("[SP] Reindexação forçada, gerando índice SharePoint (full)")
    elif not cache_exists:
        print("[SP] Índice não encontrado, executando full build_index")
    client = get_sharepoint_client()
    index = client.build_index()
    cache["index"] = index
    cache["created_at"] = datetime.utcnow()
    return index


def get_sharepoint_root_folder():
    return get_sharepoint_env()["root_folder"].strip('/')


def get_first_level_folder(parent_path):
    if not parent_path:
        return None
    root_folder = get_sharepoint_root_folder()
    marker = f":/{root_folder}"
    if marker in parent_path:
        relative = parent_path.split(marker, 1)[-1].lstrip('/')
    else:
        relative = parent_path.split('root:', 1)[-1].lstrip('/')
    if not relative:
        return None
    return relative.split('/', 1)[0]


def ensure_upload_enabled():
    if is_sharepoint_backend():
        message = 'Upload desativado; fonte é SharePoint.'
        if request.is_json or request.accept_mimetypes['application/json'] >= request.accept_mimetypes['text/html']:
            return jsonify({'error': message}), 403
        flash(message, 'info')
        return redirect(url_for('catalog'))
    return None


@app.before_request
def block_bucket_upload_routes():
    if not is_sharepoint_backend():
        return None
    if request.path.startswith('/batch') or request.path.startswith('/upload'):
        return ensure_upload_enabled()
    return None

# Error handler for large file uploads
@app.errorhandler(413)
@app.errorhandler(RequestEntityTooLarge)
def handle_file_too_large(e):
    flash('Arquivo muito grande! O limite máximo é 100MB por arquivo. Para lotes maiores, divida em arquivos ZIP menores.', 'error')
    return redirect(request.referrer or url_for('batch_list'))

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class SystemConfig(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)

class Brand(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship('Image', backref='brand_ref', lazy=True)

class Collection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    season = db.Column(db.String(50))  # Estação: Primavera/Verão, Outono/Inverno
    year = db.Column(db.Integer)  # Ano da coleção
    campanha = db.Column(db.String(100))  # Nome da campanha (legado)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship('Image', backref='collection', lazy=True)
    subcolecoes = db.relationship('Subcolecao', backref='colecao', lazy=True, cascade='all, delete-orphan')

class Subcolecao(db.Model):
    """Subcoleção/Campanha dentro de uma Coleção (Ex: Dia das Mães, Natal, Réveillon)"""
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False)
    slug = db.Column(db.String(100))  # Nome normalizado para busca
    tipo_campanha = db.Column(db.String(50))  # DDM, LANCAMENTO, COLECAO, PREVIEW, etc.
    data_inicio = db.Column(db.Date)  # Data início da campanha (opcional)
    data_fim = db.Column(db.Date)  # Data fim da campanha (opcional)
    colecao_id = db.Column(db.Integer, db.ForeignKey('collection.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Image(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    storage_path = db.Column(db.String(500))  # Path in Object Storage (e.g., /bucket/images/file.jpg)
    source_type = db.Column(db.String(30), default='sharepoint')
    sharepoint_drive_id = db.Column(db.String(255))
    sharepoint_item_id = db.Column(db.String(255))
    sharepoint_web_url = db.Column(db.Text)
    sharepoint_parent_path = db.Column(db.Text)
    sharepoint_file_name = db.Column(db.String(255))
    sharepoint_last_modified = db.Column(db.String(50))
    description = db.Column(db.Text)
    sku = db.Column(db.String(100))  # SKU completo do arquivo (ex: ABC123_01)
    sku_base = db.Column(db.String(100), index=True)  # SKU base para agrupamento (ex: ABC123)
    sequencia = db.Column(db.String(20))  # Sequência/ângulo (ex: 01, 02, A, B, FRENTE, COSTAS)
    brand_id = db.Column(db.Integer, db.ForeignKey('brand.id'))
    status = db.Column(db.String(20), default='Pendente')
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    shooting_date = db.Column(db.Date)
    photographer = db.Column(db.String(100))
    unique_code = db.Column(db.String(50), unique=True)
    collection_id = db.Column(db.Integer, db.ForeignKey('collection.id'))
    subcolecao_id = db.Column(db.Integer, db.ForeignKey('subcolecao.id'))  # Subcoleção/Campanha
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    tags = db.Column(db.Text)
    
    # Campos extras da Carteira (preenchidos no cruzamento SKU)
    nome_peca = db.Column(db.Text)  # Nome da peça vindo da Carteira (ex: "Jaqueta Reese")
    categoria = db.Column(db.String(100))  # GRUPO
    subcategoria = db.Column(db.String(100))  # SUBGRUPO
    tipo_peca = db.Column(db.String(50))  # TOP/BOTTOM/INTEIRO
    estilista = db.Column(db.String(255))
    origem = db.Column(db.String(50))  # NACIONAL/IMPORTADO
    referencia_estilo = db.Column(db.String(50))  # Código de estilo
    
    # AI-extracted attributes (legacy - mantido para retrocompatibilidade)
    ai_item_type = db.Column(db.String(100))
    ai_color = db.Column(db.String(50))
    ai_material = db.Column(db.String(100))
    ai_pattern = db.Column(db.String(50))
    ai_style = db.Column(db.String(50))
    
    # Relationship with individual items detected in image
    items = db.relationship('ImageItem', backref='image', lazy=True, cascade='all, delete-orphan')
    
    # Relationship with subcolecao
    subcolecao_rel = db.relationship('Subcolecao', backref='images', foreign_keys=[subcolecao_id])
    
    # Índices para agrupamento e busca
    __table_args__ = (
        db.Index('idx_image_sku_base', 'sku_base'),
        db.Index('idx_image_sku_base_collection', 'sku_base', 'collection_id'),
    )
    
    @property
    def image_url(self):
        """Returns the URL to access the image (Object Storage or local fallback)"""
        if self.source_type == 'sharepoint' and self.sharepoint_item_id and self.sharepoint_drive_id:
            try:
                return url_for('serve_sharepoint_image', image_id=self.id)
            except RuntimeError:
                return f"/sp/image/{self.id}"
        if self.storage_path:
            return self.storage_path
        return f"/static/uploads/{self.filename}"

class ImageItem(db.Model):
    """Representa uma peça individual detectada em uma imagem"""
    id = db.Column(db.Integer, primary_key=True)
    image_id = db.Column(db.Integer, db.ForeignKey('image.id'), nullable=False)
    item_order = db.Column(db.Integer, default=1)  # Ordem da peça na imagem
    
    # Descrição específica desta peça
    description = db.Column(db.Text)
    tags = db.Column(db.Text)  # JSON array
    
    # Atributos IA específicos desta peça
    ai_item_type = db.Column(db.String(100))
    ai_color = db.Column(db.String(50))
    ai_material = db.Column(db.String(100))
    ai_pattern = db.Column(db.String(50))
    ai_style = db.Column(db.String(50))
    
    # Referência visual (ex: "peça superior", "peça inferior", "acessório")
    position_ref = db.Column(db.String(50))

class ImageThumbnail(db.Model):
    """Armazena thumbnails das imagens para exibição rápida em tela"""
    id = db.Column(db.Integer, primary_key=True)
    image_id = db.Column(db.Integer, db.ForeignKey('image.id'), nullable=False, unique=True)
    thumbnail_data = db.Column(db.LargeBinary, nullable=False)  # Dados binários do thumbnail
    width = db.Column(db.Integer)  # Largura do thumbnail
    height = db.Column(db.Integer)  # Altura do thumbnail
    file_size = db.Column(db.Integer)  # Tamanho em bytes
    mime_type = db.Column(db.String(50), default='image/jpeg')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamento com Image
    image = db.relationship('Image', backref=db.backref('thumbnail', uselist=False))
    
    __table_args__ = (
        db.Index('idx_thumbnail_image_id', 'image_id'),
    )


def ensure_image_table_columns():
    """Adiciona colunas de SharePoint no SQLite sem Alembic."""
    try:
        result = db.session.execute("PRAGMA table_info(image)").fetchall()
    except Exception:
        return

    existing = {row[1] for row in result}
    columns = [
        ("source_type", "VARCHAR(30) DEFAULT 'sharepoint'"),
        ("sharepoint_drive_id", "VARCHAR(255)"),
        ("sharepoint_item_id", "VARCHAR(255)"),
        ("sharepoint_web_url", "TEXT"),
        ("sharepoint_parent_path", "TEXT"),
        ("sharepoint_file_name", "VARCHAR(255)"),
        ("sharepoint_last_modified", "VARCHAR(50)"),
    ]

    for name, ddl in columns:
        if name in existing:
            continue
        db.session.execute(f"ALTER TABLE image ADD COLUMN {name} {ddl}")
    db.session.commit()

class Produto(db.Model):
    """Modelo de Produto com SKU e atributos técnicos"""
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), unique=True, nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    cor = db.Column(db.String(50))
    categoria = db.Column(db.String(100))
    atributos_tecnicos = db.Column(db.Text)  # JSON com atributos extras
    
    # Relacionamentos
    marca_id = db.Column(db.Integer, db.ForeignKey('brand.id'))
    colecao_id = db.Column(db.Integer, db.ForeignKey('collection.id'))
    subcolecao_id = db.Column(db.Integer, db.ForeignKey('subcolecao.id'))  # Subcoleção/Campanha
    
    # Metadados
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Flags
    tem_foto = db.Column(db.Boolean, default=False)
    ativo = db.Column(db.Boolean, default=True)
    
    # Relationships
    marca = db.relationship('Brand', backref='produtos')
    colecao = db.relationship('Collection', backref='produtos')
    subcolecao = db.relationship('Subcolecao', backref='produtos')
    imagens = db.relationship('ImagemProduto', backref='produto', lazy=True, cascade='all, delete-orphan')
    historico_skus = db.relationship('HistoricoSKU', backref='produto', lazy=True)

class ImagemProduto(db.Model):
    """Associação entre Imagem e Produto (uma imagem pode ter vários produtos)"""
    id = db.Column(db.Integer, primary_key=True)
    imagem_id = db.Column(db.Integer, db.ForeignKey('image.id'), nullable=False)
    produto_id = db.Column(db.Integer, db.ForeignKey('produto.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamentos
    imagem = db.relationship('Image', backref='produtos_associados')

class HistoricoSKU(db.Model):
    """Histórico de alterações de SKU para rastreabilidade"""
    id = db.Column(db.Integer, primary_key=True)
    produto_id = db.Column(db.Integer, db.ForeignKey('produto.id'), nullable=False)
    sku_antigo = db.Column(db.String(50), nullable=False)
    sku_novo = db.Column(db.String(50), nullable=False)
    data_alteracao = db.Column(db.DateTime, default=datetime.utcnow)
    motivo = db.Column(db.String(255))
    usuario_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    
    usuario = db.relationship('User', backref='alteracoes_sku')

class CarteiraCompras(db.Model):
    """Carteira de compras importada para comparação com itens fotografados"""
    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(50), nullable=False)
    descricao = db.Column(db.String(255))
    cor = db.Column(db.String(100))
    categoria = db.Column(db.String(100))  # GRUPO (MALHA, TECIDO PLANO, etc)
    subcategoria = db.Column(db.String(100))  # SUBGRUPO (Blusa, Calça, Vestido, etc)
    colecao_nome = db.Column(db.String(100))  # ENTRADA (ex: INVERNO 2026)
    estilista = db.Column(db.String(255))
    shooting = db.Column(db.String(100))  # QUANDO
    observacoes = db.Column(db.Text)  # OBS
    origem = db.Column(db.String(50))  # NACIONAL / IMPORTADO
    quantidade = db.Column(db.Integer, default=1)
    
    # Novos campos para moda/acessórios/home
    tipo_carteira = db.Column(db.String(30), default='Moda')  # Moda, Acessórios, Home
    material = db.Column(db.String(100))  # GRUPO normalizado (material do tecido)
    tipo_peca = db.Column(db.String(100))  # SUBGRUPO normalizado (tipo de peça)
    posicao_peca = db.Column(db.String(50))  # TOP/BOTTOM/INTEIRO
    referencia_estilo = db.Column(db.String(50))  # REFERÊNCIA ESTILO (código interno)
    
    # Status
    status_foto = db.Column(db.String(30), default='Pendente')  # Pendente, Com Foto, Sem Foto
    okr = db.Column(db.String(20))  # Status de aprovação
    produto_id = db.Column(db.Integer, db.ForeignKey('produto.id'))  # Link com produto se existir
    
    # Relacionamentos com entidades auto-criadas
    colecao_id = db.Column(db.Integer, db.ForeignKey('collection.id'))  # Coleção associada
    subcolecao_id = db.Column(db.Integer, db.ForeignKey('subcolecao.id'))  # Subcoleção/Campanha
    marca_id = db.Column(db.Integer, db.ForeignKey('brand.id'))  # Marca associada
    
    # Metadados de importação
    data_importacao = db.Column(db.DateTime, default=datetime.utcnow)
    lote_importacao = db.Column(db.String(50))  # Identificador do lote de importação
    aba_origem = db.Column(db.String(50))  # Aba do Excel de origem
    
    # Relacionamentos
    produto = db.relationship('Produto', backref='itens_carteira')
    colecao = db.relationship('Collection', backref='itens_carteira')
    subcolecao = db.relationship('Subcolecao', backref='itens_carteira')
    marca = db.relationship('Brand', backref='itens_carteira')

class BatchUpload(db.Model):
    """Lote de upload de imagens para processamento em massa"""
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(255))
    total_arquivos = db.Column(db.Integer, default=0)
    processados = db.Column(db.Integer, default=0)
    sucesso = db.Column(db.Integer, default=0)
    falhas = db.Column(db.Integer, default=0)
    status = db.Column(db.String(30), default='Pendente')  # Pendente, Processando, Concluído, Erro
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime)
    finished_at = db.Column(db.DateTime)
    
    # Metadados
    usuario_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    colecao_id = db.Column(db.Integer, db.ForeignKey('collection.id'))
    marca_id = db.Column(db.Integer, db.ForeignKey('brand.id'))
    
    # Relacionamentos
    usuario = db.relationship('User', backref='batches')
    colecao = db.relationship('Collection', backref='batches')
    marca = db.relationship('Brand', backref='batches')
    items = db.relationship('BatchItem', backref='batch', lazy='dynamic', cascade='all, delete-orphan')
    
    @property
    def progresso(self):
        if self.total_arquivos == 0:
            return 0
        return round((self.processados / self.total_arquivos) * 100, 1)

class BatchItem(db.Model):
    """Item individual de um lote de upload - Sistema resiliente a falhas"""
    id = db.Column(db.Integer, primary_key=True)
    batch_id = db.Column(db.Integer, db.ForeignKey('batch_upload.id'), nullable=False)
    
    # Identificação
    sku = db.Column(db.String(100), nullable=False)  # Nome do arquivo = SKU
    filename_original = db.Column(db.String(255))
    file_size = db.Column(db.BigInteger)  # Tamanho do arquivo em bytes
    file_hash = db.Column(db.String(64))  # SHA256 para dedupe
    
    # FASE 1: Recepção (Cliente → Servidor)
    received_path = db.Column(db.String(500))  # Caminho temporário no servidor
    reception_status = db.Column(db.String(30), default='pending')  # pending, receiving, received, failed
    received_at = db.Column(db.DateTime)  # Quando foi recebido no servidor
    
    # FASE 2: Processamento (Servidor → Object Storage)
    processing_status = db.Column(db.String(30), default='pending')  # pending, processing, completed, failed, retry
    storage_path = db.Column(db.String(500))  # Caminho final no Object Storage
    image_id = db.Column(db.Integer, db.ForeignKey('image.id'))
    
    # Controle de erros e retries
    status = db.Column(db.String(30), default='Pendente')  # Pendente, Processando, Sucesso, Erro (legado)
    erro_mensagem = db.Column(db.Text)
    retry_count = db.Column(db.Integer, default=0)
    max_retries = db.Column(db.Integer, default=3)
    next_retry_at = db.Column(db.DateTime)
    last_error = db.Column(db.Text)
    tentativas = db.Column(db.Integer, default=0)  # Legado
    
    # Resultado da análise IA
    ai_description = db.Column(db.Text)
    ai_tags = db.Column(db.Text)  # JSON
    ai_attributes = db.Column(db.Text)  # JSON com todos os atributos
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    processed_at = db.Column(db.DateTime)
    
    # Heartbeat para detectar itens travados
    heartbeat_at = db.Column(db.DateTime)  # Última vez que o worker atualizou
    worker_id = db.Column(db.String(50))  # ID do worker processando
    
    # Relacionamento com imagem criada
    imagem = db.relationship('Image', backref='batch_item')
    
    # Índices para fila FIFO e busca rápida
    __table_args__ = (
        db.Index('idx_batch_item_sku', 'sku'),
        db.Index('idx_batch_item_status', 'status'),
        db.Index('idx_batch_item_batch_status', 'batch_id', 'status'),
        db.Index('idx_batch_item_processing', 'batch_id', 'processing_status'),
        db.Index('idx_batch_item_reception', 'batch_id', 'reception_status'),
        db.Index('idx_batch_item_retry', 'next_retry_at', 'processing_status'),
    )

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_openai_client():
    # First try Replit AI Integrations (no API key needed)
    base_url = os.getenv('AI_INTEGRATIONS_OPENAI_BASE_URL')
    ai_key = os.getenv('AI_INTEGRATIONS_OPENAI_API_KEY')
    if base_url and ai_key:
        return OpenAI(api_key=ai_key, base_url=base_url)
    
    # Fallback to user-configured API key in DB
    config = SystemConfig.query.filter_by(key='OPENAI_API_KEY').first()
    if config:
        return OpenAI(api_key=config.value)
    
    # Last fallback to env var
    api_key = os.getenv('OPENAI_API_KEY')
    if api_key:
        return OpenAI(api_key=api_key)
    
    return None

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')


def encode_image_bytes(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')

def analyze_image_with_ai(image_path):
    client = get_openai_client()
    if not client:
        return "AI Configuration missing. Please configure OpenAI API Key in Settings."
    
    try:
        base64_image = encode_image(image_path)
        
        prompt = """
        Você é um especialista em moda e têxteis analisando imagens para o banco de imagens OAZ (varejo de moda profissional).
        Sua análise deve ser EXTREMAMENTE PRECISA para catalogação e verificação de SKUs.
        
        RESPONDA TUDO EM PORTUGUÊS DO BRASIL.
        
        ═══════════════════════════════════════════════════════════════════
        1. TIPO DE ITEM - Identifique com precisão:
        ═══════════════════════════════════════════════════════════════════
        
        VESTIDOS (com modelagem):
        - Vestido Tubinho (justo, segue o corpo)
        - Vestido Evasê (cintura marcada, saia em A)
        - Vestido Ciganinha/Ombro a Ombro
        - Vestido Chemise (estilo camisa, com botões)
        - Vestido Transpassado (amarração lateral)
        - Vestido Tomara que Caia
        - Vestido Godê (saia rodada e ampla)
        - Vestido Peplum (babado na cintura)
        
        BLUSAS/TOPS:
        - Camiseta/T-shirt, Camisa Social, Regata, Top/Cropped
        - Blusa de Alcinha, Body, Blusa Ciganinha
        - Blusa de Renda/Laise, Bata, Polo
        
        CALÇAS (com modelagem):
        - Skinny, Flare/Boca de Sino, Pantalona/Wide Leg
        - Reta, Cargo, Jogger, Legging, Alfaiataria, Cigarrete
        
        SAIAS: Lápis, Evasê, Godê, Plissada, Envelope/Transpassada
        
        OUTROS: Blazer, Jaqueta, Cardigan, Suéter, Macacão, Shorts, Bermuda
        
        COMPRIMENTOS: Curto, Midi, Longo, Cropped, Mini, Maxi
        
        ═══════════════════════════════════════════════════════════════════
        2. COR - Seja específico com nuances:
        ═══════════════════════════════════════════════════════════════════
        Exemplos: Azul Marinho, Azul Royal, Azul Bebê, Vermelho Vinho, 
        Vermelho Ferrari, Rosa Blush, Rosa Pink, Preto, Branco Off-White,
        Bege Areia, Marrom Chocolate, Marrom Caramelo, Verde Militar,
        Verde Esmeralda, Cinza Chumbo, Cinza Mescla, Nude, Terracota
        
        ═══════════════════════════════════════════════════════════════════
        3. MATERIAL/TECIDO - ANÁLISE CRÍTICA (mais importante!)
        ═══════════════════════════════════════════════════════════════════
        
        ▼▼▼ TECIDOS TRANSPARENTES E DELICADOS (PRIORIDADE MÁXIMA) ▼▼▼
        
        ★★★ ANÁLISE DE MATERIAIS EM CAMADAS (MUITO IMPORTANTE!) ★★★
        
        Muitas peças têm MÚLTIPLOS materiais sobrepostos. Analise em DUAS ETAPAS:
        
        ETAPA 1 - IDENTIFIQUE A BASE (tecido que cobre o corpo):
        - Se há uma TELA/MALHA TRANSPARENTE cobrindo grande área = BASE é TULE
        - Sinais visuais: diferença no tom da pele entre áreas cobertas (mais escuras/acinzentadas) 
          e áreas descobertas (mãos, pescoço, rosto têm tom de pele natural)
        - Se a pele aparece "velada" ou com tom diferente = há uma camada de TULE por baixo
        
        ETAPA 2 - IDENTIFIQUE APLICAÇÕES/DETALHES (sobre a base):
        - Bordados, desenhos florais, arabescos = APLICAÇÃO de RENDA
        - Pedrarias, paetês = aplicações decorativas
        
        ★ REGRA DE NOMENCLATURA:
        - Base de tule + desenhos de renda = "Tule com renda aplicada" (NÃO apenas "Renda"!)
        - Base de tule + bordados = "Tule bordado"
        - Apenas malha transparente uniforme = "Tule"
        - Renda sem base transparente (autossustentada) = "Renda guipir"
        - Renda delicada com desenhos florais = "Renda chantilly"
        
        ⚠️ DICA VISUAL CRÍTICA:
        Se você observa que o TOM DA PELE é diferente entre:
        - Mãos/pescoço/rosto (tom natural, sem cobertura)
        - Braços/torso/pernas (tom mais escuro/velado)
        Isso indica que há uma CAMADA DE TULE como base, mesmo que tenha desenhos por cima!
        Neste caso, identifique como "Tule com renda" ou "Tule com detalhes de renda"
        
        TULE (base transparente):
        - Grade/rede transparente cobrindo grandes áreas do corpo
        - Cria efeito "véu" na pele - altera o tom visual
        
        RENDA (aplicação decorativa):
        - Desenhos ornamentais (flores, arabescos)
        - Geralmente aplicada SOBRE uma base (tule ou forro)
        
        ORGANZA:
        - Visual: Transparente, RÍGIDO, brilho acetinado
        - Estrutura: Armado, mantém forma, não é vazado como tule
        - Uso: Saias estruturadas, vestidos de festa
        
        CHIFFON:
        - Visual: Transparente, MUITO FLUIDO e esvoaçante
        - Estrutura: Leve, sem estrutura, cai naturalmente
        - Uso: Vestidos leves, blusas, lenços
        
        VOAL:
        - Visual: Semi-transparente, intermediário
        - Estrutura: Entre organza e chiffon
        
        ▼▼▼ FIBRAS NATURAIS (SEM BRILHO) ▼▼▼
        
        ALGODÃO:
        - Visual: OPACO, mate, sem brilho, trama uniforme
        - Toque: Macio, fresco, confortável
        - Amassa: Sim, moderadamente
        - Uso: Camisetas, calças, roupas básicas
        
        LINHO:
        - Visual: Textura RÚSTICA, aspecto AMASSADO natural
        - Toque: Fresco, levemente áspero
        - Amassa: MUITO (é característica do tecido)
        - Uso: Roupas de verão, peças elegantes
        
        SEDA:
        - Visual: Brilho LUXUOSO e NATURAL, muito lisa
        - Toque: Extremamente macio e suave
        - Caimento: Fluido e delicado
        - Uso: Vestidos de festa, blusas finas
        
        LÃ:
        - Visual: Textura peluda/felpuda, encorpado
        - Toque: Quente, pode ser áspero ou macio
        - Uso: Casacos, suéteres, inverno
        
        ▼▼▼ FIBRAS ARTIFICIAIS (de celulose) ▼▼▼
        
        VISCOSE:
        - Visual: Brilho SUTIL sedoso, OPACA (não transparente!)
        - Toque: Macio, fresco, similar ao algodão
        - Caimento: Fluido, molda o corpo
        - Amassa: Sim, facilmente
        - ATENÇÃO: Viscose NÃO é transparente! Se for transparente, é outro tecido!
        
        MODAL:
        - Visual: Similar à viscose, mais macio
        - Toque: Muito suave, sedoso
        - Uso: Roupas íntimas, camisetas premium
        
        ▼▼▼ FIBRAS SINTÉTICAS (derivadas de petróleo) ▼▼▼
        
        POLIÉSTER:
        - Visual: Brilho ARTIFICIAL e direto, aspecto "plástico"
        - Toque: Liso, levemente rígido
        - Amassa: NÃO amassa
        - Uso: Roupas esportivas, uniformes, misturas
        
        POLIAMIDA/NYLON:
        - Visual: Liso, brilhante, leve
        - Uso: Roupas esportivas, meias, lingerie
        
        ▼▼▼ MALHAS ▼▼▼
        
        TRICÔ:
        - Visual: Pontos ENTRELAÇADOS visíveis, textura artesanal
        - Uso: Suéteres, cardigans, vestidos de inverno
        
        MOLETOM:
        - Visual: Face externa lisa, interna felpuda
        - Uso: Moletons, casacos casuais
        
        RIBANA/CANELADA:
        - Visual: Nervuras VERTICAIS paralelas
        - Alta elasticidade
        - Uso: Punhos, golas, camisetas justas
        
        ▼▼▼ TECIDOS ESTRUTURADOS ▼▼▼
        
        JEANS/DENIM:
        - Visual: Diagonal característica, resistente
        - Cores: Azul índigo (claro/escuro), preto, branco
        
        SARJA:
        - Visual: Linhas DIAGONAIS bem visíveis (45°)
        - Uso: Calças, uniformes, shorts
        
        GABARDINE:
        - Visual: Liso com brilho sutil, muito estruturado
        - Uso: Alfaiataria, uniformes, blazers
        
        CREPE:
        - Visual: Textura GRANULADA, opaco, caimento pesado
        - Uso: Vestidos elegantes, alfaiataria
        
        CETIM/SATIN:
        - Visual: MUITO BRILHANTE, liso, escorregadio
        - Uso: Vestidos de festa, lingerie
        
        VELUDO:
        - Visual: Superfície PELUDA/felpuda, brilho característico
        - Uso: Vestidos de festa, blazers, inverno
        
        COURO/COURINO:
        - Visual: Liso, brilhante ou fosco, aspecto de pele
        - Uso: Jaquetas, calças, saias
        
        ═══════════════════════════════════════════════════════════════════
        4. ESTAMPA/PADRÃO:
        ═══════════════════════════════════════════════════════════════════
        Liso, Listrado (horizontal/vertical), Floral (grande/pequeno/delicado),
        Xadrez, Poá/Bolinhas, Geométrico, Animal Print (onça/zebra/cobra),
        Abstrato, Tie-Dye, Étnico, Tropical, Paisley, Camuflado
        
        ═══════════════════════════════════════════════════════════════════
        5. ESTILO (seja específico e baseado no USO da peça):
        ═══════════════════════════════════════════════════════════════════
        Analise o CONTEXTO DE USO da peça, não apenas sua aparência:
        
        - Festa/Gala: vestidos elegantes, peças brilhantes para eventos noturnos
        - Social/Trabalho: alfaiataria, camisas, peças formais para escritório
        - Dia a Dia/Urbano: peças práticas para uso cotidiano
        - Streetwear: estilo de rua, oversized, tênis, bonés
        - Minimalista: linhas limpas, cores neutras, sem ornamentos
        - Boho/Bohemian: fluido, étnico, franjas, estampas naturais
        - Esportivo/Athleisure: confortável, funcional, para exercícios ou lazer
        - Praia/Resort: leve, estampas tropicais, tecidos frescos
        - Vintage/Retrô: inspiração em décadas passadas
        - Balada/Noite: peças ousadas, recortes, transparências
        
        ⚠️ EVITE estilos genéricos. Use o contexto de USO real da peça.
        ⚠️ NÃO classifique como "Romântico" apenas por ter renda/tule - analise o contexto.
        
        ═══════════════════════════════════════════════════════════════════
        6. DESCRIÇÃO DETALHADA (CRÍTICO para verificação de SKU):
        ═══════════════════════════════════════════════════════════════════
        
        INCLUA OBRIGATORIAMENTE:
        □ Tipo exato e modelagem (tubinho, evasê, skinny, etc.)
        □ Comprimento preciso (curto, midi, longo, cropped, 7/8)
        □ Cintura (alta, média, baixa)
        □ Decote (V, redondo, quadrado, tomara que caia, ombro a ombro)
        □ Mangas (sem manga, curta, 3/4, longa, bufante, sino)
        □ Detalhes de design (botões, zíperes, bolsos, pregas, franzidos)
        □ Acabamentos (babados, rendas, bordados, recortes)
        □ Aviamentos visíveis (fivelas, argolas, ilhoses)
        □ Características ÚNICAS que diferenciam esta peça
        
        Exemplo de descrição boa:
        "Vestido midi evasê em crepe preto com decote V profundo, mangas 3/4 
        bufantes com elástico no punho, cintura marcada com cinto removível 
        de mesma cor com fivela dourada, saia fluida com comprimento abaixo 
        do joelho, fechamento por zíper invisível nas costas, forro completo."
        
        ═══════════════════════════════════════════════════════════════════
        7. DETECÇÃO DE MÚLTIPLAS PEÇAS:
        ═══════════════════════════════════════════════════════════════════
        
        IMPORTANTE: Analise a imagem e identifique TODAS as peças de roupa visíveis.
        
        - Se houver APENAS UMA peça: retorne um array "items" com 1 item
        - Se houver MÚLTIPLAS peças (ex: blusa + calça, vestido + bolsa): retorne cada uma separadamente
        - Máximo de 4 peças por imagem
        
        Para cada peça, identifique sua POSIÇÃO na imagem:
        - "Peça Superior" (blusas, camisas, tops, jaquetas)
        - "Peça Inferior" (calças, saias, shorts)
        - "Peça Única" (vestidos, macacões)
        - "Acessório" (bolsas, cintos, chapéus)
        - "Calçado" (sapatos, tênis, sandálias)
        
        ═══════════════════════════════════════════════════════════════════
        
        FORMATO DE RESPOSTA (JSON estrito com MÚLTIPLAS PEÇAS):
        {
            "item_count": 1,
            "items": [
                {
                    "position_ref": "Peça Superior/Inferior/Única/Acessório/Calçado",
                    "description": "Descrição ultra-detalhada desta peça...",
                    "attributes": {
                        "item_type": "Tipo + Modelagem + Comprimento",
                        "color": "Cor com nuance específica",
                        "material": "Material identificado com precisão",
                        "pattern": "Estampa ou Liso",
                        "style": "Estilo específico"
                    },
                    "seo_keywords": ["keyword1", "keyword2", "keyword3"]
                }
            ]
        }
        
        Se houver 2 peças (ex: blusa e calça):
        {
            "item_count": 2,
            "items": [
                {
                    "position_ref": "Peça Superior",
                    "description": "Descrição da blusa...",
                    "attributes": {...},
                    "seo_keywords": [...]
                },
                {
                    "position_ref": "Peça Inferior",
                    "description": "Descrição da calça...",
                    "attributes": {...},
                    "seo_keywords": [...]
                }
            ]
        }
        
        REGRAS PARA KEYWORDS:
        ✗ PROIBIDO: casual, moda casual, roupa feminina, moda, fashion, look, outfit, estilo
        ✓ USE: termos específicos do produto (material, cor, modelagem, detalhes)
        """
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{base64_image}"
                            },
                        },
                    ],
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=1200,
        )
        
        content = response.choices[0].message.content
        data = json.loads(content)
        
        # Tags genéricas a serem filtradas (não agregam valor)
        generic_tags = [
            'casual', 'moda casual', 'roupa feminina', 'roupa masculina',
            'moda feminina', 'moda masculina', 'vestuário', 'roupa',
            'fashion', 'moda', 'look', 'outfit', 'estilo casual'
        ]
        
        def filter_tags(attributes, keywords):
            """Filtra e gera tags a partir de atributos e keywords"""
            tags = []
            for key, value in attributes.items():
                if value and value.lower() != 'none' and value.lower() != 'n/a':
                    if value.lower() not in generic_tags:
                        tags.append(value)
            for keyword in keywords:
                if keyword.lower() not in generic_tags:
                    tags.append(keyword)
            return list(set(tags))
        
        # Novo formato com múltiplas peças
        if 'items' in data and isinstance(data['items'], list):
            items_data = []
            for idx, item in enumerate(data['items']):
                item_attributes = item.get('attributes', {})
                item_keywords = item.get('seo_keywords', [])
                item_tags = filter_tags(item_attributes, item_keywords)
                
                items_data.append({
                    'order': idx + 1,
                    'position_ref': item.get('position_ref', f'Peça {idx + 1}'),
                    'description': item.get('description', ''),
                    'tags': item_tags,
                    'attributes': item_attributes
                })
            
            # Retorna lista de itens
            return items_data
        
        # Fallback: formato antigo (uma peça só)
        description = data.get('description', '')
        attributes = data.get('attributes', {})
        keywords = data.get('seo_keywords', [])
        tags = filter_tags(attributes, keywords)
        
        # Retorna como lista com um item para compatibilidade
        return [{
            'order': 1,
            'position_ref': 'Peça Única',
            'description': description,
            'tags': tags,
            'attributes': attributes
        }]
        
    except Exception as e:
        print(f"AI Analysis Error: {e}")
        return f"Erro ao analisar imagem: {str(e)}"


def get_carteira_taxonomy():
    """Extrai taxonomia real da Carteira de Compras para padronização"""
    taxonomy = {
        'categorias': [],
        'subcategorias': [],
        'tipos_peca': [],
        'origens': ['NACIONAL', 'IMPORTADO'],
        'materiais': []
    }
    
    try:
        categorias = db.session.query(db.func.distinct(db.func.upper(CarteiraCompras.categoria))).filter(
            CarteiraCompras.categoria.isnot(None),
            CarteiraCompras.categoria != ''
        ).all()
        taxonomy['categorias'] = sorted([c[0].strip() for c in categorias if c[0]])
        
        subcategorias = db.session.query(db.func.distinct(db.func.upper(CarteiraCompras.subcategoria))).filter(
            CarteiraCompras.subcategoria.isnot(None),
            CarteiraCompras.subcategoria != ''
        ).all()
        taxonomy['subcategorias'] = sorted([s[0].strip() for s in subcategorias if s[0]])
        
        tipos = db.session.query(db.func.distinct(db.func.upper(CarteiraCompras.tipo_peca))).filter(
            CarteiraCompras.tipo_peca.isnot(None),
            CarteiraCompras.tipo_peca != ''
        ).all()
        taxonomy['tipos_peca'] = sorted([t[0].strip() for t in tipos if t[0]])
        
        materiais = db.session.query(db.func.distinct(db.func.upper(CarteiraCompras.material))).filter(
            CarteiraCompras.material.isnot(None),
            CarteiraCompras.material != ''
        ).all()
        taxonomy['materiais'] = sorted([m[0].strip() for m in materiais if m[0]])
        
    except Exception as e:
        print(f"[WARN] Error loading taxonomy: {e}")
    
    return taxonomy


def normalize_to_taxonomy(value, valid_values):
    """Normaliza um valor para corresponder à taxonomia da Carteira"""
    if not value or not valid_values:
        return value
    
    value_upper = value.upper().strip()
    
    for valid in valid_values:
        if valid.upper() == value_upper:
            return valid
    
    for valid in valid_values:
        if value_upper in valid.upper() or valid.upper() in value_upper:
            return valid
    
    return value.upper()


def analyze_image_with_context(image_path_or_url=None, sku=None, collection_id=None, brand_id=None, subcolecao_id=None, is_url=False, image_bytes=None):
    """
    Analisa imagem com contexto das carteiras de compras importadas.
    Busca produtos similares para dar contexto ao GPT.
    Aceita caminho de arquivo local ou URL da imagem.
    Retorna dados padronizados no formato da Carteira.
    """
    client = get_openai_client()
    if not client:
        return "AI Configuration missing. Please configure OpenAI API Key in Settings."
    
    try:
        taxonomy = get_carteira_taxonomy()
        context_products = []
        
        query = CarteiraCompras.query.filter(
            CarteiraCompras.descricao.isnot(None),
            CarteiraCompras.descricao != ''
        )
        
        if collection_id:
            query = query.filter_by(colecao_id=collection_id)
        if brand_id:
            query = query.filter_by(marca_id=brand_id)
        if subcolecao_id:
            query = query.filter_by(subcolecao_id=subcolecao_id)
        
        produtos_referencia = query.order_by(db.func.random()).limit(10).all()
        
        for prod in produtos_referencia:
            context_products.append({
                'sku': prod.sku,
                'descricao': prod.descricao,
                'categoria': prod.categoria,
                'cor': prod.cor,
                'material': prod.material,
                'tipo_peca': prod.tipo_peca,
                'subcategoria': prod.subcategoria,
                'origem': prod.origem
            })
        
        taxonomy_text = """
═══════════════════════════════════════════════════════════════════
TAXONOMIA OFICIAL (VOCÊ DEVE USAR APENAS ESTES VALORES):
═══════════════════════════════════════════════════════════════════
"""
        if taxonomy['categorias']:
            taxonomy_text += f"\nCATEGORIAS PERMITIDAS: {', '.join(taxonomy['categorias'])}"
        if taxonomy['tipos_peca']:
            taxonomy_text += f"\nTIPOS DE PEÇA PERMITIDOS: {', '.join(taxonomy['tipos_peca'])}"
        if taxonomy['origens']:
            taxonomy_text += f"\nORIGENS PERMITIDAS: {', '.join(taxonomy['origens'])}"
        if taxonomy['materiais']:
            taxonomy_text += f"\nMATERIAIS CONHECIDOS: {', '.join(taxonomy['materiais'][:20])}"
        
        context_text = ""
        if context_products:
            context_text = """

═══════════════════════════════════════════════════════════════════
EXEMPLOS DE PRODUTOS DO CATÁLOGO (referência de padrão):
═══════════════════════════════════════════════════════════════════
"""
            for i, p in enumerate(context_products[:5], 1):
                context_text += f"""
Produto {i}:
- Descrição: {p.get('descricao', 'N/A')}
- Categoria: {p.get('categoria', 'N/A')}
- Subcategoria: {p.get('subcategoria', 'N/A')}
- Tipo Peça: {p.get('tipo_peca', 'N/A')}
- Cor: {p.get('cor', 'N/A')}
- Material: {p.get('material', 'N/A')}
- Origem: {p.get('origem', 'N/A')}
"""
        
        if image_bytes is not None:
            base64_image = encode_image_bytes(image_bytes)
            image_content = {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            }
        elif is_url:
            image_content = {
                "type": "image_url",
                "image_url": {"url": image_path_or_url}
            }
        else:
            base64_image = encode_image(image_path_or_url)
            image_content = {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
            }
        
        prompt = f"""
        Você é um especialista em moda analisando imagens para o banco de imagens OAZ.
        
        {taxonomy_text}
        {context_text}
        
        RESPONDA TUDO EM PORTUGUÊS DO BRASIL.
        
        REGRA CRÍTICA: Você DEVE usar APENAS os valores listados na TAXONOMIA OFICIAL acima.
        Para campos como categoria, tipo_peca e origem, escolha EXATAMENTE um dos valores permitidos.
        
        Analise a imagem e preencha TODOS os campos abaixo:
        
        1. CATEGORIA: Escolha da lista de categorias permitidas (ex: JEANS, MALHA, TECIDO PLANO, TRICOT)
        2. TIPO DE PEÇA: Escolha da lista de tipos permitidos (ex: VESTIDO, BLUSA, CALÇA, SAIA, CAMISA)
        3. COR: Seja específico (Azul Marinho, Rosa Blush, Preto, Bege Areia)
        4. MATERIAL: Identifique o tecido (Algodão, Linho, Crepe, Malha, Jeans, Cetim)
        5. ESTAMPA: Liso, Listrado, Floral, Animal Print, Geométrico
        6. ESTILO: Festa, Social/Trabalho, Dia a Dia, Streetwear, Praia
        7. DESCRIÇÃO DETALHADA: Inclua modelagem, comprimento, decote, mangas, detalhes
        
        FORMATO DE RESPOSTA (JSON):
        {{
            "item_count": 1,
            "items": [
                {{
                    "position_ref": "Peça Superior/Inferior/Única",
                    "description": "Descrição ultra-detalhada...",
                    "carteira": {{
                        "categoria": "ESCOLHA DA LISTA DE CATEGORIAS",
                        "subcategoria": "ESCOLHA DA LISTA DE TIPOS DE PEÇA",
                        "tipo_peca": "ESCOLHA DA LISTA DE TIPOS DE PEÇA",
                        "origem": "NACIONAL ou IMPORTADO"
                    }},
                    "attributes": {{
                        "item_type": "Tipo + Modelagem detalhada",
                        "color": "Cor específica",
                        "material": "Material/Tecido",
                        "pattern": "Estampa ou Liso",
                        "style": "Estilo"
                    }},
                    "seo_keywords": ["keyword1", "keyword2", "keyword3"]
                }}
            ]
        }}
        
        NÃO use tags genéricas como "casual", "moda", "fashion", "look".
        
        IMPORTANTE: Foque na PEÇA PRINCIPAL da imagem. Se houver uma modelo usando a roupa, analise APENAS a roupa principal sendo mostrada.
        NÃO inclua peças secundárias como calças básicas ou acessórios comuns, a menos que sejam o foco da imagem.
        Na maioria dos casos, detecte apenas 1 peça principal.
        """
        
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        image_content,
                    ],
                }
            ],
            response_format={"type": "json_object"},
            max_tokens=1200,
        )
        
        content = response.choices[0].message.content
        data = json.loads(content)
        
        generic_tags = ['casual', 'moda casual', 'roupa feminina', 'fashion', 'moda', 'look', 'outfit', 
                        'elegante', 'moderno', 'feminino', 'masculino', 'roupa', 'peça', 'ideal para']
        
        def filter_tags(attributes, keywords, max_tags=5):
            """Filtra e simplifica tags para máximo de 5 tags curtas"""
            tags = []
            
            priority_keys = ['item_type', 'color', 'material', 'pattern']
            for key in priority_keys:
                value = attributes.get(key)
                if value and value.lower() not in ['none', 'n/a', 'liso']:
                    simple_value = value.split()[0] if len(value.split()) > 3 else value
                    if simple_value.lower() not in generic_tags and len(simple_value) < 30:
                        tags.append(simple_value)
            
            for keyword in keywords[:3]:
                if keyword.lower() not in generic_tags and len(keyword) < 20:
                    if keyword not in tags:
                        tags.append(keyword)
            
            unique_tags = []
            seen_lower = set()
            for tag in tags:
                tag_lower = tag.lower()
                if tag_lower not in seen_lower and len(tag) > 2:
                    seen_lower.add(tag_lower)
                    unique_tags.append(tag)
            
            return unique_tags[:max_tags]
        
        if 'items' in data and isinstance(data['items'], list):
            items_data = []
            for idx, item in enumerate(data['items']):
                item_attributes = item.get('attributes', {})
                item_keywords = item.get('seo_keywords', [])
                item_tags = filter_tags(item_attributes, item_keywords)
                
                carteira_data = item.get('carteira', {})
                normalized_carteira = {
                    'categoria': normalize_to_taxonomy(carteira_data.get('categoria'), taxonomy['categorias']),
                    'subcategoria': normalize_to_taxonomy(carteira_data.get('subcategoria'), taxonomy['tipos_peca']),
                    'tipo_peca': normalize_to_taxonomy(carteira_data.get('tipo_peca'), taxonomy['tipos_peca']),
                    'origem': normalize_to_taxonomy(carteira_data.get('origem'), taxonomy['origens'])
                }
                
                items_data.append({
                    'order': idx + 1,
                    'position_ref': item.get('position_ref', f'Peça {idx + 1}'),
                    'description': item.get('description', ''),
                    'tags': item_tags,
                    'attributes': item_attributes,
                    'carteira': normalized_carteira,
                    'analyzed_with_context': len(context_products) > 0
                })
            
            return items_data
        
        description = data.get('description', '')
        attributes = data.get('attributes', {})
        keywords = data.get('seo_keywords', [])
        tags = filter_tags(attributes, keywords)
        carteira_data = data.get('carteira', {})
        normalized_carteira = {
            'categoria': normalize_to_taxonomy(carteira_data.get('categoria'), taxonomy['categorias']),
            'subcategoria': normalize_to_taxonomy(carteira_data.get('subcategoria'), taxonomy['tipos_peca']),
            'tipo_peca': normalize_to_taxonomy(carteira_data.get('tipo_peca'), taxonomy['tipos_peca']),
            'origem': normalize_to_taxonomy(carteira_data.get('origem'), taxonomy['origens'])
        }
        
        return [{
            'order': 1,
            'position_ref': 'Peça Única',
            'description': description,
            'tags': tags,
            'attributes': attributes,
            'carteira': normalized_carteira,
            'analyzed_with_context': len(context_products) > 0
        }]
        
    except Exception as e:
        print(f"AI Context Analysis Error: {e}")
        return f"Erro ao analisar imagem: {str(e)}"


# Endpoint para receber logs do frontend
@app.route('/api/log', methods=['POST'])
def frontend_log():
    """Recebe logs do frontend (JavaScript) e exibe no console do servidor"""
    try:
        data = request.get_json()
        module = data.get('module', 'FRONTEND')
        action = data.get('action', 'ACTION')
        message = data.get('message', '')
        level = data.get('level', 'INFO').upper()
        extras = data.get('extras', {})
        
        if level == 'ERROR':
            error(M.SYSTEM, action, f"[JS] {message}", **extras)
            rpa_error(f"[JS] {action}: {message}", regiao="frontend")
        elif level == 'WARN':
            warn(M.SYSTEM, action, f"[JS] {message}", **extras)
            rpa_warn(f"[JS] {action}: {message}")
        elif level == 'SUCCESS':
            success(M.SYSTEM, action, f"[JS] {message}", **extras)
            rpa_info(f"[JS] {action}: {message}")
        elif level == 'DEBUG':
            debug(M.SYSTEM, action, f"[JS] {message}", **extras)
        else:
            info(M.SYSTEM, action, f"[JS] {message}", **extras)
            rpa_info(f"[JS] {action}: {message}")
        
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/analyze-single', methods=['POST'])
@login_required
def api_analyze_single():
    """Endpoint para análise individual de imagem via AJAX (usado no catálogo em lote)"""
    try:
        data = request.get_json()
        image_id = data.get('image_id')
        selected_fields = data.get('fields', ['descricao', 'tags', 'cor', 'tipo', 'material'])
        
        if not image_id:
            return jsonify({'success': False, 'error': 'ID da imagem não fornecido'}), 400
        
        image = Image.query.get(image_id)
        if not image:
            return jsonify({'success': False, 'error': 'Imagem não encontrada'}), 404
        
        rpa_info(f"[AI-BATCH] Analisando imagem ID:{image_id} | Campos: {selected_fields}")
        
        success, error_msg = analyze_single_image(image, selected_fields)
        
        if success:
            return jsonify({
                'success': True,
                'message': f'SKU {image.sku_base or image.sku or "sem SKU"} analisado',
                'image_id': image_id
            })
        else:
            return jsonify({
                'success': False,
                'error': error_msg or 'Erro desconhecido na análise',
                'image_id': image_id
            })
    
    except Exception as e:
        rpa_error(f"[AI-BATCH] Erro ao analisar imagem: {e}", exc=e)
        return jsonify({'success': False, 'error': str(e)}), 500


# Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        auth_log.login_attempt(username)
        rpa_info(f"[AUTH] Tentativa de login: {username}")
        user = User.query.filter_by(username=username).first()
        if user:
            password_check = user.check_password(password)
            if password_check:
                login_user(user)
                auth_log.login_success(username, user.id)
                rpa_info(f"[AUTH] Login bem-sucedido: {username} (ID: {user.id})")
                return redirect(url_for('dashboard'))
        auth_log.login_failed(username, "Usuário ou senha inválidos")
        rpa_warn(f"[AUTH] Login falhou: {username}")
        flash('Usuário ou senha inválidos')
    else:
        nav_log.page_enter("Login")
        rpa_info("[NAV] Página: Login")
    return render_template('auth/login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        info(M.AUTH, 'ACTION', f"Tentativa de registro", username=username, email=email)
        rpa_info(f"[AUTH] Tentativa de registro: {username}")
        
        if User.query.filter_by(username=username).first():
            warn(M.AUTH, 'WARN', f"Registro falhou - usuário já existe", username=username)
            rpa_warn(f"[AUTH] Registro falhou - usuário já existe: {username}")
            flash('Nome de usuário já existe')
            return redirect(url_for('register'))
            
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        success(M.AUTH, 'SUCCESS', f"Usuário registrado com sucesso", username=username, user_id=user.id)
        rpa_info(f"[AUTH] Usuário registrado: {username} (ID: {user.id})")
        login_user(user)
        return redirect(url_for('dashboard'))
    else:
        nav_log.page_enter("Registro")
        rpa_info("[NAV] Página: Registro")
    return render_template('auth/register.html')

@app.route('/logout')
@login_required
def logout():
    username = current_user.username if current_user else "desconhecido"
    auth_log.logout(username)
    rpa_info(f"[AUTH] Logout: {username}")
    logout_user()
    return redirect(url_for('login'))

@app.route('/storage/<path:object_path>')
def serve_storage_image(object_path):
    """Serve images from Object Storage"""
    try:
        from object_storage import object_storage
        from flask import Response
        
        file_bytes = object_storage.download_file(object_path)
        
        if file_bytes is None:
            print(f"[WARN] Image not found in storage: {object_path}")
            return "Image not found", 404
        
        ext = object_path.split('.')[-1].lower()
        content_types = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'gif': 'image/gif',
            'webp': 'image/webp'
        }
        content_type = content_types.get(ext, 'application/octet-stream')
        
        return Response(
            file_bytes,
            mimetype=content_type,
            headers={
                'Cache-Control': 'public, max-age=31536000'
            }
        )
    except Exception as e:
        print(f"[ERROR] Failed to serve storage image {object_path}: {e}")
        return "Image not found", 404


@app.route('/sp/image/<int:image_id>')
def serve_sharepoint_image(image_id):
    """Stream a SharePoint image through the app."""
    image = Image.query.get_or_404(image_id)
    if image.source_type != 'sharepoint':
        return "Image not found", 404

    try:
        client = get_sharepoint_client()
        metadata = client.get_metadata(image.sharepoint_drive_id, image.sharepoint_item_id)
        response = client.download_stream(image.sharepoint_drive_id, image.sharepoint_item_id)
        mime_type = metadata.get('mime_type') or mimetypes.guess_type(metadata.get('name', '') or '')[0] or 'application/octet-stream'

        return Response(
            stream_with_context(response.iter_content(chunk_size=8192)),
            mimetype=mime_type,
            headers={'Cache-Control': 'public, max-age=600'}
        )
    except Exception as e:
        print(f"[ERROR] Failed to stream SharePoint image {image_id}: {e}")
        return "Image not found", 404

def generate_thumbnail(image_data, max_width=300, quality=75):
    """Gera thumbnail a partir de dados de imagem
    
    Args:
        image_data: bytes da imagem original
        max_width: largura máxima do thumbnail (default 300px)
        quality: qualidade JPEG (default 75%)
    
    Returns:
        tuple: (thumbnail_bytes, width, height, file_size)
    """
    try:
        img = PILImage.open(io.BytesIO(image_data))
        
        if img.mode in ('RGBA', 'LA', 'P'):
            img = img.convert('RGB')
        
        original_width, original_height = img.size
        if original_width > max_width:
            ratio = max_width / original_width
            new_height = int(original_height * ratio)
            img = img.resize((max_width, new_height), PILImage.Resampling.LANCZOS)
        
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=quality, optimize=True)
        thumbnail_bytes = output.getvalue()
        
        width, height = img.size
        file_size = len(thumbnail_bytes)
        
        print(f"[THUMBNAIL] Gerado: {width}x{height}, {file_size/1024:.1f}KB (original: {original_width}x{original_height})")
        
        return thumbnail_bytes, width, height, file_size
        
    except Exception as e:
        print(f"[ERROR] Falha ao gerar thumbnail: {e}")
        return None, None, None, None

def save_thumbnail_for_image(image_id, image_data):
    """Salva thumbnail no banco de dados para uma imagem
    
    Args:
        image_id: ID da imagem
        image_data: bytes da imagem original
    
    Returns:
        ImageThumbnail ou None
    """
    try:
        existing = ImageThumbnail.query.filter_by(image_id=image_id).first()
        if existing:
            print(f"[THUMBNAIL] Já existe para imagem {image_id}, pulando...")
            return existing
        
        thumbnail_bytes, width, height, file_size = generate_thumbnail(image_data)
        
        if thumbnail_bytes is None:
            return None
        
        thumbnail = ImageThumbnail(
            image_id=image_id,
            thumbnail_data=thumbnail_bytes,
            width=width,
            height=height,
            file_size=file_size,
            mime_type='image/jpeg'
        )
        db.session.add(thumbnail)
        db.session.commit()
        
        print(f"[THUMBNAIL] Salvo para imagem {image_id}: {file_size/1024:.1f}KB")
        return thumbnail
        
    except Exception as e:
        print(f"[ERROR] Falha ao salvar thumbnail para imagem {image_id}: {e}")
        db.session.rollback()
        return None

@app.route('/thumbnail/<int:image_id>')
def serve_thumbnail(image_id):
    """Serve thumbnail de uma imagem do banco de dados"""
    try:
        thumbnail = ImageThumbnail.query.filter_by(image_id=image_id).first()
        
        if thumbnail and thumbnail.thumbnail_data:
            return Response(
                thumbnail.thumbnail_data,
                mimetype=thumbnail.mime_type or 'image/jpeg',
                headers={
                    'Cache-Control': 'public, max-age=31536000'
                }
            )
        
        image = Image.query.get(image_id)
        if image:
            if image.source_type == 'sharepoint' and image.sharepoint_drive_id and image.sharepoint_item_id:
                client = get_sharepoint_client()
                file_bytes = client.download_bytes(image.sharepoint_drive_id, image.sharepoint_item_id)
                if file_bytes:
                    save_thumbnail_for_image(image_id, file_bytes)
                    thumbnail = ImageThumbnail.query.filter_by(image_id=image_id).first()
                    if thumbnail:
                        return Response(
                            thumbnail.thumbnail_data,
                            mimetype='image/jpeg',
                            headers={'Cache-Control': 'public, max-age=31536000'}
                        )
            elif image.storage_path:
                from object_storage import object_storage
                file_bytes = object_storage.download_file(image.storage_path)
                if file_bytes:
                    save_thumbnail_for_image(image_id, file_bytes)
                    thumbnail = ImageThumbnail.query.filter_by(image_id=image_id).first()
                    if thumbnail:
                        return Response(
                            thumbnail.thumbnail_data,
                            mimetype='image/jpeg',
                            headers={'Cache-Control': 'public, max-age=31536000'}
                        )
        
        return Response(status=404)
        
    except Exception as e:
        print(f"[ERROR] Falha ao servir thumbnail {image_id}: {e}")
        return Response(status=404)

@app.route('/dashboard')
@login_required
def dashboard():
    nav_log.page_enter("Dashboard", user=current_user.username)
    rpa_info(f"[NAV] Dashboard - Usuário: {current_user.username}")
    total_images = Image.query.count()
    pending_images = Image.query.filter_by(status='Pendente').count()
    pending_ia_images = Image.query.filter_by(status='Pendente Análise IA').count()
    approved_images = Image.query.filter_by(status='Aprovado').count()
    rejected_images = Image.query.filter_by(status='Rejeitado').count()
    total_collections = Collection.query.count()
    total_brands = Brand.query.count()
    recent_images = Image.query.order_by(Image.upload_date.desc()).limit(5).all()
    debug(M.DASHBOARD, 'DATA', f"Métricas carregadas", imagens=total_images, pendentes=pending_images, colecoes=total_collections)
    
    pending_skus = db.session.query(db.func.count(db.func.distinct(Image.sku_base))).filter(Image.status == 'Pendente').scalar() or 0
    pending_ia_skus = db.session.query(db.func.count(db.func.distinct(Image.sku_base))).filter(Image.status == 'Pendente Análise IA').scalar() or 0
    total_skus = db.session.query(db.func.count(db.func.distinct(Image.sku_base))).scalar() or 0
    
    skus_pendentes_foto = []
    total_skus_carteira = 0
    total_skus_sem_foto = 0
    
    colecoes = Collection.query.all()
    for col in colecoes:
        total_col = db.session.query(db.func.count(db.func.distinct(CarteiraCompras.sku))).filter(
            CarteiraCompras.colecao_id == col.id
        ).scalar() or 0
        
        if total_col == 0:
            continue
        
        com_foto_real = db.session.query(db.func.count(db.func.distinct(CarteiraCompras.sku))).filter(
            CarteiraCompras.colecao_id == col.id,
            db.exists().where(
                db.and_(
                    Image.sku_base == CarteiraCompras.sku,
                    Image.collection_id == col.id
                )
            )
        ).scalar() or 0
        
        sem_foto_real = total_col - com_foto_real
        total_skus_carteira += total_col
        total_skus_sem_foto += sem_foto_real
        
        skus_pendentes_foto.append({
            'colecao': col.name,
            'total': total_col,
            'com_foto': com_foto_real,
            'sem_foto': sem_foto_real,
            'percentual': round((com_foto_real / total_col * 100) if total_col > 0 else 0, 1)
        })
    
    return render_template('dashboard/index.html',
                          total_images=total_images,
                          total_skus=total_skus,
                          pending_images=pending_images,
                          pending_skus=pending_skus,
                          pending_ia_images=pending_ia_images,
                          pending_ia_skus=pending_ia_skus,
                          approved_images=approved_images,
                          rejected_images=rejected_images,
                          total_collections=total_collections,
                          total_brands=total_brands,
                          recent_images=recent_images,
                          skus_pendentes_foto=skus_pendentes_foto,
                          total_skus_carteira=total_skus_carteira,
                          total_skus_sem_foto=total_skus_sem_foto)

@app.route('/catalog')
@login_required
def catalog():
    status_filter = request.args.getlist('status')
    collection_filter = request.args.get('collection_id')
    brand_filter = request.args.get('brand_id')
    search_query = request.args.get('q', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 20
    
    filters = {'status': status_filter, 'collection': collection_filter, 'brand': brand_filter, 'q': search_query}
    catalog_log.page_accessed(page, filters=filters)
    rpa_info(f"[NAV] Catálogo - Página {page} - Usuário: {current_user.username}")
    
    base_query = Image.query
    
    if status_filter:
        base_query = base_query.filter(Image.status.in_(status_filter))
    
    if collection_filter and collection_filter != 'all':
        base_query = base_query.filter_by(collection_id=collection_filter)
    
    if brand_filter and brand_filter != 'all':
        base_query = base_query.filter_by(brand_id=brand_filter)
    
    if search_query:
        search_term = f"%{search_query}%"
        base_query = base_query.filter(
            db.or_(
                Image.sku.ilike(search_term),
                Image.sku_base.ilike(search_term),
                Image.description.ilike(search_term),
                Image.original_name.ilike(search_term),
                Image.ai_item_type.ilike(search_term),
                Image.tags.ilike(search_term)
            )
        )
    
    sku_key_expr = db.func.coalesce(Image.sku_base, Image.sku, db.func.concat('no_sku_', Image.id.cast(db.String)))
    
    sku_subquery = base_query.with_entities(
        sku_key_expr.label('sku_key'),
        db.func.max(Image.upload_date).label('latest_upload')
    ).group_by(sku_key_expr).subquery()
    
    total_skus = db.session.query(db.func.count()).select_from(sku_subquery).scalar() or 0
    total_pages = max(1, (total_skus + per_page - 1) // per_page)
    
    page_skus_query = db.session.query(
        sku_subquery.c.sku_key
    ).order_by(sku_subquery.c.latest_upload.desc()).offset((page - 1) * per_page).limit(per_page).all()
    page_skus = [s[0] for s in page_skus_query]
    
    if page_skus:
        sku_key_filter = db.func.coalesce(Image.sku_base, Image.sku, db.func.concat('no_sku_', Image.id.cast(db.String)))
        page_images_query = Image.query.filter(sku_key_filter.in_(page_skus))
        if status_filter:
            page_images_query = page_images_query.filter(Image.status.in_(status_filter))
        if collection_filter and collection_filter != 'all':
            page_images_query = page_images_query.filter_by(collection_id=collection_filter)
        if brand_filter and brand_filter != 'all':
            page_images_query = page_images_query.filter_by(brand_id=brand_filter)
        if search_query:
            search_term = f"%{search_query}%"
            page_images_query = page_images_query.filter(
                db.or_(
                    Image.sku.ilike(search_term),
                    Image.sku_base.ilike(search_term),
                    Image.description.ilike(search_term),
                    Image.original_name.ilike(search_term),
                    Image.ai_item_type.ilike(search_term),
                    Image.tags.ilike(search_term)
                )
            )
        images_for_page = page_images_query.order_by(Image.sequencia, Image.upload_date.desc()).all()
    else:
        images_for_page = []
    
    sku_groups = {}
    for img in images_for_page:
        sku_key = img.sku_base or img.sku or f"no_sku_{img.id}"
        if sku_key not in sku_groups:
            try:
                tag_list = json.loads(img.tags) if img.tags else []
            except:
                tag_list = []
            sku_groups[sku_key] = {
                'sku_base': sku_key,
                'cover_image': img,
                'images': [],
                'brand': img.brand_ref.name if img.brand_ref else 'Sem Marca',
                'status': img.status,
                'tag_list': tag_list,
                'has_pending': False,
                'has_approved': False,
                'has_rejected': False
            }
        sku_groups[sku_key]['images'].append(img)
        if img.status == 'Pendente' or img.status == 'Pendente Análise IA':
            sku_groups[sku_key]['has_pending'] = True
        elif img.status == 'Aprovado':
            sku_groups[sku_key]['has_approved'] = True
        elif img.status == 'Rejeitado':
            sku_groups[sku_key]['has_rejected'] = True
    
    ordered_groups = []
    for sku in page_skus:
        if sku in sku_groups:
            ordered_groups.append(sku_groups[sku])
    
    collections = Collection.query.all()
    brands = Brand.query.order_by(Brand.name).all()
    
    return render_template('images/catalog.html', 
                          sku_groups=ordered_groups,
                          total_skus=total_skus,
                          page=page,
                          total_pages=total_pages,
                          per_page=per_page,
                          collections=collections, 
                          brands=brands, 
                          search_query=search_query,
                          storage_backend=app.config.get('STORAGE_BACKEND'))


@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    rpa_info(f"[NAV] Upload - Usuário: {current_user.username}")
    upload_guard = ensure_upload_enabled()
    if upload_guard:
        return upload_guard
    if request.method == 'POST':
        if 'image' not in request.files:
            flash('Nenhum arquivo enviado')
            return redirect(request.url)
        file = request.files['image']
        if file.filename == '':
            flash('Nenhum arquivo selecionado')
            return redirect(request.url)
        if file and file.filename.split('.')[-1].lower() in app.config['ALLOWED_EXTENSIONS']:
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            unique_filename = f"{timestamp}_{filename}"
            
            temp_file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(temp_file_path)
            
            ai_items = []
            try:
                ai_result = analyze_image_with_ai(temp_file_path)
                
                if isinstance(ai_result, str):
                    if ai_result.startswith("AI Configuration missing"):
                        print(f"[WARNING] AI not configured, using defaults")
                    else:
                        print(f"[ERROR] AI analysis failed: {ai_result}")
                    ai_items = []
                else:
                    ai_items = ai_result
            except Exception as e:
                print(f"[ERROR] Exception during AI analysis: {e}")
                ai_items = []
            
            storage_path = None
            try:
                from object_storage import object_storage
                with open(temp_file_path, 'rb') as f:
                    content_type = file.content_type or 'image/jpeg'
                    result = object_storage.upload_file(f, unique_filename, content_type)
                    storage_path = result['storage_path']
                    print(f"[INFO] Image uploaded to Object Storage: {storage_path}")
            except Exception as e:
                print(f"[ERROR] Object Storage upload failed: {e}")
                flash('Erro ao fazer upload para o armazenamento. Tente novamente.')
                return redirect(request.url)
            
            try:
                os.remove(temp_file_path)
                print(f"[INFO] Temporary file deleted: {temp_file_path}")
            except Exception as e:
                print(f"[WARNING] Could not delete temp file: {e}")
            
            import uuid
            unique_code = f"IMG-{uuid.uuid4().hex[:8].upper()}"
            
            first_item = ai_items[0] if ai_items else {}
            first_attrs = first_item.get('attributes', {}) if first_item else {}
            
            brand_id = request.form.get('brand_id')
            new_image = Image(
                filename=unique_filename,
                original_name=file.filename,
                storage_path=storage_path,
                collection_id=request.form.get('collection_id') if request.form.get('collection_id') else None,
                brand_id=int(brand_id) if brand_id else None,
                sku=request.form.get('sku'),
                photographer=request.form.get('photographer'),
                description=request.form.get('observations') or first_item.get('description', ''),
                tags=json.dumps(first_item.get('tags', [])) if first_item else json.dumps([]),
                ai_item_type=first_attrs.get('item_type'),
                ai_color=first_attrs.get('color'),
                ai_material=first_attrs.get('material'),
                ai_pattern=first_attrs.get('pattern'),
                ai_style=first_attrs.get('style'),
                uploader_id=current_user.id,
                unique_code=unique_code,
                status='Pendente'
            )
            db.session.add(new_image)
            db.session.flush()

            if sp_item.get('web_url'):
                rpa_info(f"[SHAREPOINT] Imagem sincronizada: {sku_full} -> {sp_item.get('web_url')}")
            
            for item_data in ai_items:
                attrs = item_data.get('attributes', {})
                new_item = ImageItem(
                    image_id=new_image.id,
                    item_order=item_data.get('order', 1),
                    position_ref=item_data.get('position_ref', 'Peça Única'),
                    description=item_data.get('description', ''),
                    tags=json.dumps(item_data.get('tags', [])),
                    ai_item_type=attrs.get('item_type'),
                    ai_color=attrs.get('color'),
                    ai_material=attrs.get('material'),
                    ai_pattern=attrs.get('pattern'),
                    ai_style=attrs.get('style')
                )
                db.session.add(new_item)
            
            db.session.commit()

            if is_sharepoint_backend():
                try:
                    rpa_info(f"[CROSS] Auto-cross iniciado para lote={lote_id}")
                    sync_result = run_sharepoint_cross_for_batch(lote_id, auto=True)
                    rpa_info(
                        "[CROSS] Auto-cross finalizado "
                        f"para lote={lote_id} | com_foto={sync_result.get('matched', 0)} "
                        f"| sem_foto={max(0, total_count - sync_result.get('matched', 0))}"
                    )
                except Exception as e:
                    rpa_error(f"[CROSS] Erro no auto-cross do lote {lote_id}: {str(e)}", exc=e, regiao="carteira")

            if is_sharepoint_backend():
                try:
                    rpa_info(f"[SHAREPOINT] Iniciando cruzamento automático do lote {lote_id}")
                    sync_result = run_sharepoint_cross_for_batch(lote_id, auto=True)
                    rpa_info(
                        "[SHAREPOINT] Cruzamento concluído "
                        f"(lote {lote_id}): {sync_result.get('matched', 0)} SKUs com foto, "
                        f"{sync_result.get('created', 0)} imagens criadas"
                    )
                except Exception as e:
                    rpa_error(f"[SHAREPOINT] Erro no cruzamento do lote {lote_id}: {str(e)}", exc=e, regiao="carteira")

            if is_sharepoint_backend():
                sync_result = sync_sharepoint_images_for_import(lote_id)
                rpa_info(f"[SHAREPOINT] Sync concluído (lote {lote_id}): {sync_result.get('created', 0)} imagens")
            
            item_count = len(ai_items)
            storage_info = " (Object Storage)" if storage_path else ""
            if item_count > 1:
                flash(f'Imagem enviada com sucesso{storage_info}. {item_count} peças detectadas e analisadas.')
            elif item_count == 1:
                flash(f'Imagem enviada com sucesso{storage_info}. Análise de IA concluída.')
            else:
                flash(f'Imagem enviada com sucesso{storage_info}. Configure a chave OpenAI em Configurações para análise automática.')
            return redirect(url_for('catalog'))
        else:
            flash('Formato de arquivo não permitido. Use: PNG, JPG, JPEG ou GIF')
            return redirect(request.url)
    collections = Collection.query.order_by(Collection.name).all()
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('images/upload.html', collections=collections, brands=brands, storage_backend=app.config.get('STORAGE_BACKEND'))


@app.route('/collections')
@login_required
def collections():
    nav_log.page_enter("Coleções", user=current_user.username)
    rpa_info(f"[NAV] Coleções - Usuário: {current_user.username}")
    search = request.args.get('search', '')
    season = request.args.get('season', '')
    year = request.args.get('year', '')
    
    query = Collection.query
    
    if search:
        query = query.filter(Collection.name.ilike(f'%{search}%'))
    if season:
        query = query.filter_by(season=season)
    if year:
        query = query.filter_by(year=int(year))
    
    collections = query.order_by(Collection.created_at.desc()).all()
    return render_template('collections/list.html', collections=collections)

@app.route('/collections/<int:id>')
@login_required
def collection_detail(id):
    """Página de detalhes da coleção com filtros de subcoleção"""
    collection = Collection.query.get_or_404(id)
    nav_log.page_enter(f"Coleção: {collection.name}", user=current_user.username)
    rpa_info(f"[NAV] Coleção: {collection.name} - Usuário: {current_user.username}")
    
    # Buscar subcoleções desta coleção
    subcolecoes = Subcolecao.query.filter_by(colecao_id=id).order_by(Subcolecao.nome).all()
    
    # Filtros
    subcolecao_id = request.args.get('subcolecao_id', type=int)
    status = request.args.get('status', '')
    search = request.args.get('search', '')
    
    # Query de imagens desta coleção
    query = Image.query.filter_by(collection_id=id)
    
    if subcolecao_id:
        query = query.filter_by(subcolecao_id=subcolecao_id)
    if status:
        query = query.filter_by(status=status)
    if search:
        query = query.filter(
            (Image.sku.ilike(f'%{search}%')) |
            (Image.description.ilike(f'%{search}%'))
        )
    
    images = query.order_by(Image.upload_date.desc()).all()
    
    # Estatísticas
    total_images = Image.query.filter_by(collection_id=id).count()
    stats = {
        'total': total_images,
        'pendentes': Image.query.filter_by(collection_id=id, status='Pendente').count(),
        'aprovadas': Image.query.filter_by(collection_id=id, status='Aprovado').count(),
        'rejeitadas': Image.query.filter_by(collection_id=id, status='Rejeitado').count(),
        'pendente_ia': Image.query.filter_by(collection_id=id, status='Pendente Análise IA').count(),
    }
    
    return render_template('collections/detail.html', 
                           collection=collection, 
                           subcolecoes=subcolecoes,
                           images=images,
                           stats=stats,
                           subcolecao_id=subcolecao_id)

@app.route('/collections/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_collection(id):
    collection = Collection.query.get_or_404(id)
    rpa_info(f"[NAV] Editar Coleção: {collection.name} - Usuário: {current_user.username}")
    
    if request.method == 'POST':
        collection.name = request.form.get('name')
        collection.description = request.form.get('description')
        collection.season = request.form.get('season')
        year = request.form.get('year')
        collection.year = int(year) if year else None
        collection.campanha = request.form.get('campanha')
        
        db.session.commit()
        rpa_info(f"[CRUD] Coleção atualizada: {collection.name} (ID: {id})")
        flash('Coleção atualizada com sucesso!')
        return redirect(url_for('collections'))
    
    current_year = datetime.now().year
    years = list(range(current_year - 2, current_year + 3))
    return render_template('collections/edit.html', collection=collection, years=years)

@app.route('/collections/<int:id>/delete', methods=['POST'])
@login_required
def delete_collection(id):
    collection = Collection.query.get_or_404(id)
    nome = collection.name
    rpa_info(f"[CRUD] Deletando coleção: {nome} (ID: {id})")
    db.session.delete(collection)
    db.session.commit()
    rpa_info(f"[CRUD] Coleção deletada: {nome}")
    flash('Coleção removida com sucesso!')
    return redirect(url_for('collections'))

def normalizar_sku(sku):
    """Normaliza SKU removendo zeros à esquerda de cada bloco para comparação"""
    if not sku:
        return ''
    sku = str(sku).strip().upper()
    partes = sku.split('.')
    normalizadas = []
    for parte in partes:
        if parte.isdigit():
            normalizadas.append(str(int(parte)))
        else:
            normalizadas.append(parte)
    return '.'.join(normalizadas)

def buscar_carteira_por_sku(sku_base):
    """Busca na Carteira com normalização de SKU"""
    if not sku_base:
        return None
    
    carteira = CarteiraCompras.query.filter_by(sku=sku_base).first()
    if carteira:
        return carteira
    
    sku_normalizado = normalizar_sku(sku_base)
    
    carteiras = CarteiraCompras.query.all()
    for c in carteiras:
        if normalizar_sku(c.sku) == sku_normalizado:
            return c
    
    return None

@app.route('/collections/<int:id>/reprocessar', methods=['POST'])
@login_required
def reprocessar_colecao(id):
    """Reprocessa todas as imagens de uma coleção, tentando match com Carteira"""
    collection = Collection.query.get_or_404(id)
    rpa_info(f"[BATCH] Reprocessando coleção: {collection.name} (ID: {id})")
    
    carteiras_mesma_colecao = {}
    carteiras_global = {}
    
    for c in CarteiraCompras.query.filter_by(colecao_id=id).all():
        sku_norm = normalizar_sku(c.sku)
        if sku_norm not in carteiras_mesma_colecao:
            carteiras_mesma_colecao[sku_norm] = c
    
    for c in CarteiraCompras.query.all():
        sku_norm = normalizar_sku(c.sku)
        if sku_norm not in carteiras_global:
            carteiras_global[sku_norm] = c
    
    images = Image.query.filter_by(collection_id=id).all()
    reprocessadas = 0
    matched = 0
    matched_mesma_colecao = 0
    
    for img in images:
        if not img.sku_base:
            continue
        
        sku_norm = normalizar_sku(img.sku_base)
        
        carteira = carteiras_mesma_colecao.get(sku_norm)
        if carteira:
            matched_mesma_colecao += 1
        else:
            carteira = carteiras_global.get(sku_norm)
        
        if carteira:
            img.nome_peca = carteira.descricao
            
            if carteira.categoria:
                img.categoria = carteira.categoria
            if carteira.subcategoria:
                img.subcategoria = carteira.subcategoria
            if carteira.tipo_peca:
                img.tipo_peca = carteira.tipo_peca
            if carteira.origem:
                img.origem = carteira.origem
            if carteira.estilista:
                img.estilista = carteira.estilista
            if carteira.referencia_estilo:
                img.referencia_estilo = carteira.referencia_estilo
            
            if carteira.colecao_id:
                img.collection_id = carteira.colecao_id
            if carteira.subcolecao_id:
                img.subcolecao_id = carteira.subcolecao_id
            if carteira.marca_id:
                img.brand_id = carteira.marca_id
            
            if img.status == 'Pendente Análise IA':
                img.status = 'Pendente'
            
            carteira.status_foto = 'Com Foto'
            matched += 1
        
        reprocessadas += 1
    
    db.session.commit()
    
    total_carteira_colecao = CarteiraCompras.query.filter_by(colecao_id=id).count()
    rpa_info(f"[BATCH] Reprocessamento concluído: {reprocessadas} imagens, {matched} matches")
    flash(f'Reprocessadas {reprocessadas} imagens. {matched} com match ({matched_mesma_colecao} da mesma coleção). Carteira desta coleção: {total_carteira_colecao} itens.')
    return redirect(url_for('collection_detail', id=id))

@app.route('/collections/delete-all', methods=['POST'])
@login_required
def delete_all_collections():
    """Deleta todas as coleções"""
    rpa_info("[CRUD] Iniciando deleção de TODAS as coleções")
    count = Collection.query.count()
    Collection.query.delete()
    db.session.commit()
    rpa_info(f"[CRUD] Todas coleções deletadas: {count} removidas")
    flash(f'{count} coleções removidas com sucesso!', 'success')
    return redirect(url_for('collections'))

@app.route('/collections/new', methods=['GET', 'POST'])
@login_required
def new_collection():
    nav_log.page_enter("Nova Coleção", user=current_user.username)
    rpa_info(f"[NAV] Nova Coleção - Usuário: {current_user.username}")
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        season = request.form.get('season')
        year = request.form.get('year')
        campanha = request.form.get('campanha')
        
        if not name:
            flash('Nome da coleção é obrigatório')
            return redirect(url_for('new_collection'))
            
        collection = Collection(
            name=name, 
            description=description,
            season=season,
            year=int(year) if year else None,
            campanha=campanha
        )
        db.session.add(collection)
        db.session.commit()
        crud_log.created("Coleção", collection.id, name)
        rpa_info(f"[CRUD] Coleção criada: {name} (ID: {collection.id})")
        
        flash('Coleção criada com sucesso!')
        return redirect(url_for('collections'))
    
    current_year = datetime.now().year
    years = list(range(current_year - 2, current_year + 3))
    return render_template('collections/new.html', years=years)

# ==================== SUBCOLEÇÕES / CAMPANHAS ====================

@app.route('/subcolecoes')
@login_required
def subcolecoes():
    rpa_info(f"[NAV] Subcoleções - Usuário: {current_user.username}")
    colecao_id = request.args.get('colecao_id', '')
    
    query = Subcolecao.query
    
    if colecao_id:
        query = query.filter_by(colecao_id=int(colecao_id))
    
    subcolecoes = query.order_by(Subcolecao.created_at.desc()).all()
    colecoes = Collection.query.order_by(Collection.name).all()
    
    return render_template('subcolecoes/list.html', subcolecoes=subcolecoes, colecoes=colecoes)

@app.route('/subcolecoes/new', methods=['GET', 'POST'])
@login_required
def new_subcolecao():
    rpa_info(f"[NAV] Nova Subcoleção - Usuário: {current_user.username}")
    # Pegar colecao_id da URL para pré-selecionar
    colecao_id_preselect = request.args.get('colecao_id', type=int)
    
    if request.method == 'POST':
        nome = request.form.get('nome', '').strip()
        colecao_id = request.form.get('colecao_id')
        tipo_campanha = request.form.get('tipo_campanha', '').strip() or None
        data_inicio = request.form.get('data_inicio')
        data_fim = request.form.get('data_fim')
        
        if not nome or not colecao_id:
            flash('Nome e Coleção são obrigatórios', 'error')
            return redirect(url_for('new_subcolecao', colecao_id=colecao_id_preselect))
        
        # Criar slug
        import re
        slug = re.sub(r'[^a-zA-Z0-9]', '_', nome.upper()).strip('_').lower()
        
        subcolecao = Subcolecao(
            nome=nome,
            slug=slug,
            colecao_id=int(colecao_id),
            tipo_campanha=tipo_campanha,
            data_inicio=datetime.strptime(data_inicio, '%Y-%m-%d').date() if data_inicio else None,
            data_fim=datetime.strptime(data_fim, '%Y-%m-%d').date() if data_fim else None
        )
        db.session.add(subcolecao)
        db.session.commit()
        rpa_info(f"[CRUD] Subcoleção criada: {nome} (ID: {subcolecao.id})")
        
        flash('Subcoleção criada com sucesso!', 'success')
        # Redirecionar para página de detalhes da coleção
        return redirect(url_for('collection_detail', id=int(colecao_id)))
    
    colecoes = Collection.query.order_by(Collection.name).all()
    return render_template('subcolecoes/new.html', colecoes=colecoes, colecao_id_preselect=colecao_id_preselect)

@app.route('/subcolecoes/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_subcolecao(id):
    subcolecao = Subcolecao.query.get_or_404(id)
    rpa_info(f"[NAV] Editar Subcoleção: {subcolecao.nome} - Usuário: {current_user.username}")
    colecao_id_original = subcolecao.colecao_id
    
    if request.method == 'POST':
        subcolecao.nome = request.form.get('nome', '').strip()
        colecao_id = request.form.get('colecao_id')
        subcolecao.colecao_id = int(colecao_id) if colecao_id else subcolecao.colecao_id
        subcolecao.tipo_campanha = request.form.get('tipo_campanha', '').strip() or None
        
        data_inicio = request.form.get('data_inicio')
        data_fim = request.form.get('data_fim')
        subcolecao.data_inicio = datetime.strptime(data_inicio, '%Y-%m-%d').date() if data_inicio else None
        subcolecao.data_fim = datetime.strptime(data_fim, '%Y-%m-%d').date() if data_fim else None
        
        # Atualizar slug
        import re
        subcolecao.slug = re.sub(r'[^a-zA-Z0-9]', '_', subcolecao.nome.upper()).strip('_').lower()
        
        db.session.commit()
        rpa_info(f"[CRUD] Subcoleção atualizada: {subcolecao.nome} (ID: {id})")
        flash('Subcoleção atualizada com sucesso!', 'success')
        # Redirecionar para página de detalhes da coleção
        return redirect(url_for('collection_detail', id=subcolecao.colecao_id))
    
    colecoes = Collection.query.order_by(Collection.name).all()
    return render_template('subcolecoes/edit.html', subcolecao=subcolecao, colecoes=colecoes)

@app.route('/subcolecoes/<int:id>/delete', methods=['POST'])
@login_required
def delete_subcolecao(id):
    subcolecao = Subcolecao.query.get_or_404(id)
    nome = subcolecao.nome
    colecao_id = subcolecao.colecao_id
    rpa_info(f"[CRUD] Deletando subcoleção: {nome} (ID: {id})")
    db.session.delete(subcolecao)
    db.session.commit()
    rpa_info(f"[CRUD] Subcoleção deletada: {nome}")
    flash('Subcoleção removida com sucesso!', 'success')
    return redirect(url_for('collection_detail', id=colecao_id))


@app.route('/image/<int:id>')
@login_required
def image_detail(id):
    image = Image.query.get_or_404(id)
    rpa_info(f"[NAV] Detalhe Imagem ID:{id} SKU:{image.sku or 'N/A'} - Usuário: {current_user.username}")
    try:
        image.tag_list = json.loads(image.tags) if image.tags else []
    except:
        image.tag_list = []
    
    for item in image.items:
        try:
            item.tag_list = json.loads(item.tags) if item.tags else []
        except:
            item.tag_list = []
    
    sku_key = image.sku_base or image.sku
    group_images = []
    if sku_key:
        group_images = Image.query.filter(
            db.or_(Image.sku_base == sku_key, Image.sku == sku_key)
        ).order_by(Image.sequencia, Image.upload_date).all()
    
    if len(group_images) <= 1:
        group_images = []
    
    current_index = 0
    for i, img in enumerate(group_images):
        if img.id == image.id:
            current_index = i
            break
    
    prev_image = group_images[current_index - 1] if group_images and current_index > 0 else None
    next_image = group_images[current_index + 1] if group_images and current_index < len(group_images) - 1 else None
    
    return render_template('images/detail.html', 
                          image=image, 
                          group_images=group_images,
                          current_index=current_index,
                          prev_image=prev_image,
                          next_image=next_image)

@app.route('/image/<int:id>/delete', methods=['POST'])
@login_required
def delete_image(id):
    image = Image.query.get_or_404(id)
    sku = image.sku or 'N/A'
    rpa_info(f"[CRUD] Deletando imagem ID:{id} SKU:{sku}")
    
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        rpa_error(f"[CRUD] Erro ao deletar arquivo da imagem {id}: {str(e)}", exc=e, regiao="catalog")
        
    db.session.delete(image)
    db.session.commit()
    rpa_info(f"[CRUD] Imagem deletada: ID:{id} SKU:{sku}")
    
    flash('Imagem deletada com sucesso')
    return redirect(url_for('catalog'))

@app.route('/image/<int:id>/status/<status>', methods=['POST'])
@login_required
def update_image_status(id, status):
    image = Image.query.get_or_404(id)
    valid_statuses = ['Pendente', 'Aprovado', 'Rejeitado']
    old_status = image.status
    if status in valid_statuses:
        image.status = status
        db.session.commit()
        rpa_info(f"[STATUS] Imagem ID:{id} alterada: {old_status} -> {status}")
        flash(f'Status atualizado para {status}')
    else:
        rpa_warn(f"[STATUS] Tentativa de status inválido para imagem {id}: {status}")
        flash('Status inválido')
    return redirect(url_for('image_detail', id=id))

def analyze_single_image(image, selected_fields=None):
    """Analyze a single image and update its AI fields. Returns (success, error_message)
    
    selected_fields: Lista de campos a atualizar. Opções: descricao, tags, cor, tipo, material
    Se None ou vazio, atualiza todos os campos.
    """
    if selected_fields is None:
        selected_fields = ['descricao', 'tags', 'cor', 'tipo', 'material']
    temp_file_path = None
    file_path = None
    image_url = None
    image_bytes = None
    
    try:
        if image.source_type == 'sharepoint' and image.sharepoint_drive_id and image.sharepoint_item_id:
            client = get_sharepoint_client()
            image_bytes = client.download_bytes(image.sharepoint_drive_id, image.sharepoint_item_id)
        elif image.storage_path:
            from object_storage import object_storage
            import tempfile

            file_bytes = object_storage.download_file(image.storage_path)
            if file_bytes:
                ext = os.path.splitext(image.filename)[1] or '.jpg'
                fd, temp_file_path = tempfile.mkstemp(suffix=ext)
                os.write(fd, file_bytes)
                os.close(fd)
                file_path = temp_file_path
            else:
                domain = os.environ.get('REPLIT_DEV_DOMAIN', '')
                storage_key = image.storage_path.lstrip('/')
                if storage_key.startswith('storage/'):
                    storage_key = storage_key[8:]
                if domain:
                    image_url = f"https://{domain}/storage/{storage_key}"
                else:
                    image_url = url_for('serve_storage_image', object_path=storage_key, _external=True)
        
        if image_bytes is None and not file_path and not image_url:
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
        
        if image_bytes is None and not file_path and not image_url:
            return False, 'Arquivo não encontrado'
        
        if image_bytes is None and file_path and not os.path.exists(file_path) and not image_url:
            if image.storage_path:
                domain = os.environ.get('REPLIT_DEV_DOMAIN', '')
                storage_key = image.storage_path.lstrip('/')
                if storage_key.startswith('storage/'):
                    storage_key = storage_key[8:]
                if domain:
                    image_url = f"https://{domain}/storage/{storage_key}"
                else:
                    image_url = url_for('serve_storage_image', object_path=storage_key, _external=True)
        
        use_url = image_url is not None
        image_source = image_url if use_url else file_path
        
        if image_bytes is None and not image_source:
            return False, 'Arquivo não encontrado'
        
        ai_result = analyze_image_with_context(
            image_source,
            sku=image.sku,
            collection_id=image.collection_id,
            brand_id=image.brand_id,
            subcolecao_id=image.subcolecao_id,
            is_url=use_url,
            image_bytes=image_bytes
        )
        
        if isinstance(ai_result, str):
            return False, ai_result
        
        ai_items = ai_result
        first_item = ai_items[0] if ai_items else {}
        first_attrs = first_item.get('attributes', {}) if first_item else {}
        first_carteira = first_item.get('carteira', {}) if first_item else {}
        
        if 'descricao' in selected_fields:
            image.description = first_item.get('description', '')
        
        if 'tags' in selected_fields:
            new_tags = first_item.get('tags', []) if first_item else []
            if image.nome_peca and image.nome_peca not in new_tags:
                new_tags.insert(0, image.nome_peca)
            if image.subcolecao_rel and image.subcolecao_rel.nome and image.subcolecao_rel.nome not in new_tags:
                new_tags.append(image.subcolecao_rel.nome)
            if image.collection and image.collection.name and image.collection.name not in new_tags:
                new_tags.append(image.collection.name)
            image.tags = json.dumps(new_tags)
        
        if 'tipo' in selected_fields:
            image.ai_item_type = first_attrs.get('item_type')
        
        if 'cor' in selected_fields:
            image.ai_color = first_attrs.get('color')
        
        if 'material' in selected_fields:
            image.ai_material = first_attrs.get('material')
        
        image.ai_pattern = first_attrs.get('pattern')
        image.ai_style = first_attrs.get('style')
        
        if first_carteira:
            if first_carteira.get('categoria') and not image.categoria:
                image.categoria = first_carteira.get('categoria')
            if first_carteira.get('subcategoria') and not image.subcategoria:
                image.subcategoria = first_carteira.get('subcategoria')
            if first_carteira.get('tipo_peca') and not image.tipo_peca:
                image.tipo_peca = first_carteira.get('tipo_peca')
            if first_carteira.get('origem') and not image.origem:
                image.origem = first_carteira.get('origem')
        
        existing_items = ImageItem.query.filter_by(image_id=image.id).order_by(ImageItem.item_order).all()
        
        all_fields_selected = set(selected_fields) == {'descricao', 'tags', 'cor', 'tipo', 'material'}
        
        if all_fields_selected or not existing_items:
            ImageItem.query.filter_by(image_id=image.id).delete()
            
            for item_data in ai_items:
                attrs = item_data.get('attributes', {})
                item_tags = item_data.get('tags', [])
                if image.nome_peca and image.nome_peca not in item_tags:
                    item_tags.insert(0, image.nome_peca)
                if image.subcolecao_rel and image.subcolecao_rel.nome and image.subcolecao_rel.nome not in item_tags:
                    item_tags.append(image.subcolecao_rel.nome)
                if image.collection and image.collection.name and image.collection.name not in item_tags:
                    item_tags.append(image.collection.name)
                new_item = ImageItem(
                    image_id=image.id,
                    item_order=item_data.get('order', 1),
                    position_ref=item_data.get('position_ref', 'Peça Única'),
                    description=item_data.get('description', ''),
                    tags=json.dumps(item_tags),
                    ai_item_type=attrs.get('item_type'),
                    ai_color=attrs.get('color'),
                    ai_material=attrs.get('material'),
                    ai_pattern=attrs.get('pattern'),
                    ai_style=attrs.get('style')
                )
                db.session.add(new_item)
        else:
            for existing_item in existing_items:
                if ai_items:
                    ai_data = ai_items[0]
                    attrs = ai_data.get('attributes', {})
                    
                    if 'descricao' in selected_fields:
                        existing_item.description = ai_data.get('description', '')
                    if 'tags' in selected_fields:
                        item_tags = ai_data.get('tags', [])
                        if image.nome_peca and image.nome_peca not in item_tags:
                            item_tags.insert(0, image.nome_peca)
                        if image.subcolecao_rel and image.subcolecao_rel.nome and image.subcolecao_rel.nome not in item_tags:
                            item_tags.append(image.subcolecao_rel.nome)
                        if image.collection and image.collection.name and image.collection.name not in item_tags:
                            item_tags.append(image.collection.name)
                        existing_item.tags = json.dumps(item_tags)
                    if 'tipo' in selected_fields:
                        existing_item.ai_item_type = attrs.get('item_type')
                    if 'cor' in selected_fields:
                        existing_item.ai_color = attrs.get('color')
                    if 'material' in selected_fields:
                        existing_item.ai_material = attrs.get('material')
                    
                    existing_item.ai_pattern = attrs.get('pattern')
                    existing_item.ai_style = attrs.get('style')
        
        db.session.commit()
        return True, None
        
    except Exception as e:
        print(f"[ERROR] Analysis failed for image {image.id}: {e}")
        return False, str(e)
    
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass


@app.route('/image/<int:id>/reanalyze', methods=['POST'])
@login_required
def reanalyze_image(id):
    rpa_info(f"[AI] Reanalisando imagem ID:{id}")
    image = Image.query.get_or_404(id)
    
    selected_fields = request.form.getlist('fields')
    all_fields = request.form.get('all_fields') == 'on'
    
    if all_fields or not selected_fields:
        selected_fields = ['descricao', 'tags', 'cor', 'tipo', 'material']
    
    print(f"[AI] Selected fields for analysis: {selected_fields}")
    
    images_to_analyze = [image]
    if image.sku_base:
        group_images = Image.query.filter(
            Image.sku_base == image.sku_base,
            Image.id != image.id
        ).order_by(Image.sequencia).all()
        images_to_analyze.extend(group_images)
    
    total = len(images_to_analyze)
    success_count = 0
    error_count = 0
    first_error = None
    
    print(f"[AI] Starting group analysis for {total} images (sku_base: {image.sku_base})")
    
    for idx, img in enumerate(images_to_analyze, 1):
        print(f"[AI] Analyzing image {idx}/{total}: {img.filename}")
        success, error = analyze_single_image(img, selected_fields)
        if success:
            success_count += 1
        else:
            error_count += 1
            if not first_error:
                first_error = error
    
    if total == 1:
        if success_count == 1:
            flash('Imagem analisada com sucesso!')
        else:
            if first_error and first_error.startswith("AI Configuration missing"):
                flash('Chave OpenAI não configurada. Acesse Configurações para adicionar.')
            else:
                flash(f'Erro na análise: {first_error}')
    else:
        if error_count == 0:
            flash(f'Grupo analisado com sucesso! {success_count} imagens processadas.')
        elif success_count == 0:
            if first_error and first_error.startswith("AI Configuration missing"):
                flash('Chave OpenAI não configurada. Acesse Configurações para adicionar.')
            else:
                flash(f'Erro ao analisar grupo: {first_error}')
        else:
            flash(f'Análise parcial: {success_count} sucesso, {error_count} erro(s).')
    
    return redirect(url_for('image_detail', id=id))


@app.route('/image/<int:image_id>/item/<int:item_id>/delete', methods=['POST'])
@login_required
def delete_image_item(image_id, item_id):
    rpa_info(f"[CRUD] Deletando item {item_id} da imagem {image_id}")
    """Deletar uma peça individual detectada pela IA"""
    item = ImageItem.query.filter_by(id=item_id, image_id=image_id).first_or_404()
    
    db.session.delete(item)
    db.session.commit()
    
    flash('Peça removida com sucesso!')
    return redirect(url_for('image_detail', id=image_id))


@app.route('/image/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_image(id):
    rpa_info(f"[NAV] Editar Imagem ID:{id} - Usuário: {current_user.username}")
    image = Image.query.get_or_404(id)
    
    if request.method == 'POST':
        image.sku = request.form.get('sku')
        image.description = request.form.get('description')
        image.photographer = request.form.get('photographer')
        
        collection_id = request.form.get('collection_id')
        image.collection_id = int(collection_id) if collection_id else None
        
        brand_id = request.form.get('brand_id')
        image.brand_id = int(brand_id) if brand_id else None
        
        shooting_date = request.form.get('shooting_date')
        if shooting_date:
            try:
                image.shooting_date = datetime.strptime(shooting_date, '%Y-%m-%d')
            except ValueError:
                pass
        
        db.session.commit()
        flash('Imagem atualizada com sucesso!')
        return redirect(url_for('image_detail', id=id))
    
    collections = Collection.query.order_by(Collection.name).all()
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('images/edit.html', image=image, collections=collections, brands=brands)

@app.route('/brands')
@login_required
def brands():
    nav_log.page_enter("Marcas", user=current_user.username)
    rpa_info(f"[NAV] Marcas - Usuário: {current_user.username}")
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('brands/list.html', brands=brands)

@app.route('/brands/new', methods=['GET', 'POST'])
@login_required
def new_brand():
    nav_log.page_enter("Nova Marca", user=current_user.username)
    rpa_info(f"[NAV] Nova Marca - Usuário: {current_user.username}")
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        
        if not name:
            flash('Nome da marca é obrigatório')
            return redirect(url_for('new_brand'))
        
        if Brand.query.filter_by(name=name).first():
            flash('Marca já existe')
            return redirect(url_for('new_brand'))
            
        brand = Brand(name=name, description=description)
        db.session.add(brand)
        db.session.commit()
        crud_log.created("Marca", brand.id, name)
        rpa_info(f"[CRUD] Marca criada: {name} (ID: {brand.id})")
        
        flash('Marca criada com sucesso!')
        return redirect(url_for('brands'))
        
    return render_template('brands/new.html')

@app.route('/analytics')
@login_required
def analytics():
    nav_log.page_enter("Analytics", user=current_user.username)
    rpa_info(f"[NAV] Analytics - Usuário: {current_user.username}")
    total_images = Image.query.count()
    pending_images = Image.query.filter_by(status='Pendente').count()
    approved_images = Image.query.filter_by(status='Aprovado').count()
    rejected_images = Image.query.filter_by(status='Rejeitado').count()
    total_collections = Collection.query.count()
    total_brands = Brand.query.count()
    
    return render_template('analytics/index.html', 
                          total_images=total_images,
                          pending_images=pending_images,
                          approved_images=approved_images,
                          rejected_images=rejected_images,
                          total_collections=total_collections,
                          total_brands=total_brands)

@app.route('/integrations')
@login_required
def integrations():
    rpa_info(f"[NAV] Integrações - Usuário: {current_user.username}")
    return render_template('integrations/index.html')

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    rpa_info(f"[NAV] Configurações - Usuário: {current_user.username}")
    if request.method == 'POST':
        api_key = request.form.get('api_key')
        
        config = SystemConfig.query.filter_by(key='OPENAI_API_KEY').first()
        if config:
            config.value = api_key
        else:
            config = SystemConfig(key='OPENAI_API_KEY', value=api_key)
            db.session.add(config)
            
        db.session.commit()
        rpa_info("[CONFIG] Configurações atualizadas")
        flash('Settings updated successfully')
        return redirect(url_for('settings'))
        
    config = SystemConfig.query.filter_by(key='OPENAI_API_KEY').first()
    current_key = config.value if config else ''
    return render_template('admin/settings.html', current_key=current_key)

@app.route('/reports')
@login_required
def reports():
    rpa_info(f"[NAV] Relatórios - Usuário: {current_user.username}")
    total_images = Image.query.count()
    images_with_sku = Image.query.filter(Image.sku.isnot(None), Image.sku != '').count()
    images_without_sku = total_images - images_with_sku
    
    pending_images = Image.query.filter_by(status='Pendente').count()
    approved_images = Image.query.filter_by(status='Aprovado').count()
    rejected_images = Image.query.filter_by(status='Rejeitado').count()
    
    images_with_ai = Image.query.filter(Image.ai_item_type.isnot(None)).count()
    images_without_ai = total_images - images_with_ai
    
    brands = Brand.query.all()
    brands_stats = []
    for brand in brands:
        count = Image.query.filter_by(brand_id=brand.id).count()
        approved = Image.query.filter_by(brand_id=brand.id, status='Aprovado').count()
        pending = Image.query.filter_by(brand_id=brand.id, status='Pendente').count()
        brands_stats.append({
            'name': brand.name,
            'total': count,
            'approved': approved,
            'pending': pending
        })
    
    collections = Collection.query.all()
    collections_stats = []
    for collection in collections:
        count = Image.query.filter_by(collection_id=collection.id).count()
        collections_stats.append({
            'name': collection.name,
            'total': count
        })
    
    recent_uploads = Image.query.order_by(Image.upload_date.desc()).limit(10).all()
    
    return render_template('reports/index.html',
                          total_images=total_images,
                          images_with_sku=images_with_sku,
                          images_without_sku=images_without_sku,
                          pending_images=pending_images,
                          approved_images=approved_images,
                          rejected_images=rejected_images,
                          images_with_ai=images_with_ai,
                          images_without_ai=images_without_ai,
                          brands_stats=brands_stats,
                          collections_stats=collections_stats,
                          recent_uploads=recent_uploads)

@app.route('/skus-sem-foto')
@login_required
def skus_sem_foto():
    """Lista SKUs da Carteira que não têm imagem cadastrada NA MESMA COLEÇÃO"""
    rpa_info(f"[NAV] SKUs Sem Foto - Usuário: {current_user.username}")
    page = request.args.get('page', 1, type=int)
    colecao_filter = request.args.get('colecao', type=int)
    per_page = 50
    
    query = db.session.query(
        CarteiraCompras.sku,
        CarteiraCompras.colecao_id,
        Collection.name.label('colecao'),
        Brand.name.label('marca'),
        CarteiraCompras.categoria,
        CarteiraCompras.subcategoria
    ).outerjoin(Collection, CarteiraCompras.colecao_id == Collection.id
    ).outerjoin(Brand, CarteiraCompras.marca_id == Brand.id
    ).filter(
        ~db.exists().where(
            db.and_(
                Image.sku_base == CarteiraCompras.sku,
                Image.collection_id == CarteiraCompras.colecao_id
            )
        )
    )
    
    if colecao_filter:
        query = query.filter(CarteiraCompras.colecao_id == colecao_filter)
    
    query = query.distinct().order_by(CarteiraCompras.sku)
    
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    skus_sem_foto = []
    for row in pagination.items:
        skus_sem_foto.append({
            'sku': row.sku,
            'colecao': row.colecao,
            'marca': row.marca,
            'categoria': row.categoria,
            'subcategoria': row.subcategoria
        })
    
    total_carteira = db.session.query(db.func.count(db.func.distinct(CarteiraCompras.sku))).scalar() or 0
    total_com_foto = db.session.query(db.func.count(db.func.distinct(CarteiraCompras.sku))).filter(
        db.exists().where(
            db.and_(
                Image.sku_base == CarteiraCompras.sku,
                Image.collection_id == CarteiraCompras.colecao_id
            )
        )
    ).scalar() or 0
    total_sem_foto = total_carteira - total_com_foto
    percentual = round((total_com_foto / total_carteira * 100) if total_carteira > 0 else 0, 1)
    
    colecoes = Collection.query.order_by(Collection.name).all()
    
    return render_template('reports/skus_sem_foto.html',
                          skus_sem_foto=skus_sem_foto,
                          pagination=pagination,
                          colecao_filter=colecao_filter,
                          colecoes=colecoes,
                          total_carteira=total_carteira,
                          total_com_foto=total_com_foto,
                          total_sem_foto=total_sem_foto,
                          percentual=percentual)

@app.route('/skus-sem-foto/exportar')
@login_required
def exportar_skus_sem_foto():
    rpa_info(f"[EXPORT] Exportando SKUs sem foto - Usuário: {current_user.username}")
    """Exporta CSV com SKUs que não têm foto NA MESMA COLEÇÃO"""
    output = io.StringIO()
    writer = csv.writer(output)
    
    writer.writerow(['SKU', 'Coleção', 'Marca', 'Categoria', 'Subcategoria'])
    
    skus = db.session.query(
        CarteiraCompras.sku,
        Collection.name.label('colecao'),
        Brand.name.label('marca'),
        CarteiraCompras.categoria,
        CarteiraCompras.subcategoria
    ).outerjoin(Collection, CarteiraCompras.colecao_id == Collection.id
    ).outerjoin(Brand, CarteiraCompras.marca_id == Brand.id
    ).filter(
        ~db.exists().where(
            db.and_(
                Image.sku_base == CarteiraCompras.sku,
                Image.collection_id == CarteiraCompras.colecao_id
            )
        )
    ).distinct().order_by(CarteiraCompras.sku).all()
    
    for row in skus:
        writer.writerow([
            row.sku,
            row.colecao or '',
            row.marca or '',
            row.categoria or '',
            row.subcategoria or ''
        ])
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=skus_sem_foto.csv'
    response.headers['Content-type'] = 'text/csv; charset=utf-8'
    return response

@app.route('/reports/export/<report_type>')
@login_required
def export_report(report_type):
    rpa_info(f"[EXPORT] Exportando relatório: {report_type} - Usuário: {current_user.username}")
    output = io.StringIO()
    writer = csv.writer(output)
    
    if report_type == 'all':
        writer.writerow(['ID', 'Código Único', 'SKU', 'Nome Original', 'Marca', 'Coleção', 'Status', 'Tipo IA', 'Cor IA', 'Material IA', 'Fotógrafo', 'Data Upload'])
        images = Image.query.order_by(Image.upload_date.desc()).all()
        for img in images:
            writer.writerow([
                img.id,
                img.unique_code or '',
                img.sku or '',
                img.original_name,
                img.brand_ref.name if img.brand_ref else '',
                img.collection.name if img.collection else '',
                img.status,
                img.ai_item_type or '',
                img.ai_color or '',
                img.ai_material or '',
                img.photographer or '',
                img.upload_date.strftime('%Y-%m-%d %H:%M')
            ])
        filename = 'todas_imagens.csv'
        
    elif report_type == 'pending':
        writer.writerow(['ID', 'Código Único', 'SKU', 'Nome Original', 'Marca', 'Coleção', 'Data Upload'])
        images = Image.query.filter_by(status='Pendente').order_by(Image.upload_date.desc()).all()
        for img in images:
            writer.writerow([
                img.id,
                img.unique_code or '',
                img.sku or '',
                img.original_name,
                img.brand_ref.name if img.brand_ref else '',
                img.collection.name if img.collection else '',
                img.upload_date.strftime('%Y-%m-%d %H:%M')
            ])
        filename = 'imagens_pendentes.csv'
        
    elif report_type == 'without_sku':
        writer.writerow(['ID', 'Código Único', 'Nome Original', 'Marca', 'Status', 'Data Upload'])
        images = Image.query.filter(db.or_(Image.sku.is_(None), Image.sku == '')).order_by(Image.upload_date.desc()).all()
        for img in images:
            writer.writerow([
                img.id,
                img.unique_code or '',
                img.original_name,
                img.brand_ref.name if img.brand_ref else '',
                img.status,
                img.upload_date.strftime('%Y-%m-%d %H:%M')
            ])
        filename = 'imagens_sem_sku.csv'
        
    elif report_type == 'approved':
        writer.writerow(['ID', 'Código Único', 'SKU', 'Nome Original', 'Marca', 'Coleção', 'Data Upload'])
        images = Image.query.filter_by(status='Aprovado').order_by(Image.upload_date.desc()).all()
        for img in images:
            writer.writerow([
                img.id,
                img.unique_code or '',
                img.sku or '',
                img.original_name,
                img.brand_ref.name if img.brand_ref else '',
                img.collection.name if img.collection else '',
                img.upload_date.strftime('%Y-%m-%d %H:%M')
            ])
        filename = 'imagens_aprovadas.csv'
    
    elif report_type == 'without_ai':
        writer.writerow(['ID', 'Código Único', 'SKU', 'Nome Original', 'Marca', 'Status', 'Data Upload'])
        images = Image.query.filter(Image.ai_item_type.is_(None)).order_by(Image.upload_date.desc()).all()
        for img in images:
            writer.writerow([
                img.id,
                img.unique_code or '',
                img.sku or '',
                img.original_name,
                img.brand_ref.name if img.brand_ref else '',
                img.status,
                img.upload_date.strftime('%Y-%m-%d %H:%M')
            ])
        filename = 'imagens_sem_analise_ia.csv'
    
    else:
        return redirect(url_for('reports'))
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-type'] = 'text/csv; charset=utf-8'
    return response

# ==================== PRODUTOS ====================

@app.route('/produtos')
@login_required
def produtos():
    nav_log.page_enter("Produtos", user=current_user.username)
    rpa_info(f"[NAV] Produtos - Usuário: {current_user.username}")
    search = request.args.get('search', '')
    marca_id = request.args.get('marca_id', '')
    colecao_id = request.args.get('colecao_id', '')
    status_foto = request.args.get('status_foto', '')
    
    query = Produto.query.filter_by(ativo=True)
    
    if search:
        search_term = f'%{search}%'
        query = query.filter(db.or_(
            Produto.sku.ilike(search_term),
            Produto.descricao.ilike(search_term),
            Produto.cor.ilike(search_term)
        ))
    
    if marca_id:
        query = query.filter_by(marca_id=int(marca_id))
    
    if colecao_id:
        query = query.filter_by(colecao_id=int(colecao_id))
    
    if status_foto == 'com_foto':
        query = query.filter_by(tem_foto=True)
    elif status_foto == 'sem_foto':
        query = query.filter_by(tem_foto=False)
    
    produtos_list = query.order_by(Produto.created_at.desc()).all()
    marcas = Brand.query.order_by(Brand.name).all()
    colecoes = Collection.query.order_by(Collection.name).all()
    
    # Estatísticas
    total_produtos = Produto.query.filter_by(ativo=True).count()
    com_foto = Produto.query.filter_by(ativo=True, tem_foto=True).count()
    sem_foto = Produto.query.filter_by(ativo=True, tem_foto=False).count()
    
    return render_template('produtos/list.html', 
                          produtos=produtos_list, 
                          marcas=marcas, 
                          colecoes=colecoes,
                          search_query=search,
                          total_produtos=total_produtos,
                          com_foto=com_foto,
                          sem_foto=sem_foto)

@app.route('/produtos/new', methods=['GET', 'POST'])
@login_required
def new_produto():
    rpa_info(f"[NAV] Novo Produto - Usuário: {current_user.username}")
    if request.method == 'POST':
        sku = request.form.get('sku', '').strip()
        descricao = request.form.get('descricao', '').strip()
        cor = request.form.get('cor', '').strip()
        categoria = request.form.get('categoria', '').strip()
        atributos = request.form.get('atributos_tecnicos', '').strip()
        marca_id = request.form.get('marca_id')
        colecao_id = request.form.get('colecao_id')
        
        if not sku or not descricao:
            flash('SKU e Descrição são obrigatórios')
            return redirect(url_for('new_produto'))
        
        if Produto.query.filter_by(sku=sku).first():
            flash('SKU já cadastrado')
            return redirect(url_for('new_produto'))
        
        produto = Produto(
            sku=sku,
            descricao=descricao,
            cor=cor,
            categoria=categoria,
            atributos_tecnicos=atributos,
            marca_id=int(marca_id) if marca_id else None,
            colecao_id=int(colecao_id) if colecao_id else None
        )
        db.session.add(produto)
        db.session.commit()
        
        flash('Produto cadastrado com sucesso!')
        return redirect(url_for('produtos'))
    
    marcas = Brand.query.order_by(Brand.name).all()
    colecoes = Collection.query.order_by(Collection.name).all()
    return render_template('produtos/new.html', marcas=marcas, colecoes=colecoes)

@app.route('/produtos/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_produto(id):
    rpa_info(f"[NAV] Editar Produto ID:{id} - Usuário: {current_user.username}")
    produto = Produto.query.get_or_404(id)
    
    if request.method == 'POST':
        sku_novo = request.form.get('sku', '').strip()
        sku_antigo = produto.sku
        motivo = request.form.get('motivo_alteracao', '').strip()
        
        # Se SKU mudou, registrar no histórico
        if sku_novo != sku_antigo:
            if not motivo:
                flash('Informe o motivo da alteração do SKU')
                return redirect(url_for('edit_produto', id=id))
            
            existing = Produto.query.filter_by(sku=sku_novo).first()
            if existing and existing.id != produto.id:
                flash('SKU já existe em outro produto')
                return redirect(url_for('edit_produto', id=id))
            
            # Registrar histórico
            historico = HistoricoSKU(
                produto_id=produto.id,
                sku_antigo=sku_antigo,
                sku_novo=sku_novo,
                motivo=motivo,
                usuario_id=current_user.id
            )
            db.session.add(historico)
            produto.sku = sku_novo
            
            # Atualizar SKU na carteira de compras se existir
            carteira_item = CarteiraCompras.query.filter_by(sku=sku_antigo).first()
            if carteira_item:
                carteira_item.sku = sku_novo
        
        produto.descricao = request.form.get('descricao', '').strip()
        produto.cor = request.form.get('cor', '').strip()
        produto.categoria = request.form.get('categoria', '').strip()
        produto.atributos_tecnicos = request.form.get('atributos_tecnicos', '').strip()
        
        marca_id = request.form.get('marca_id')
        produto.marca_id = int(marca_id) if marca_id else None
        
        colecao_id = request.form.get('colecao_id')
        produto.colecao_id = int(colecao_id) if colecao_id else None
        
        db.session.commit()
        flash('Produto atualizado com sucesso!')
        return redirect(url_for('produtos'))
    
    marcas = Brand.query.order_by(Brand.name).all()
    colecoes = Collection.query.order_by(Collection.name).all()
    
    # Imagens associadas ao produto
    imagens_associadas = Image.query.join(ImagemProduto).filter(ImagemProduto.produto_id == produto.id).all()
    
    # Imagens disponíveis para associar (não associadas ainda)
    imagens_associadas_ids = [img.id for img in imagens_associadas]
    imagens_disponiveis = Image.query.filter(~Image.id.in_(imagens_associadas_ids) if imagens_associadas_ids else True).order_by(Image.upload_date.desc()).limit(50).all()
    
    # Histórico de alterações de SKU
    historico_sku = HistoricoSKU.query.filter_by(produto_id=produto.id).order_by(HistoricoSKU.data_alteracao.desc()).all()
    
    return render_template('produtos/edit.html', 
                          produto=produto, 
                          marcas=marcas, 
                          colecoes=colecoes,
                          imagens_associadas=imagens_associadas,
                          imagens_disponiveis=imagens_disponiveis,
                          historico_sku=historico_sku)

@app.route('/produtos/<int:id>/delete', methods=['POST'])
@login_required
def delete_produto(id):
    rpa_info(f"[CRUD] Deletando produto ID:{id}")
    produto = Produto.query.get_or_404(id)
    produto.ativo = False  # Soft delete
    db.session.commit()
    flash('Produto removido com sucesso')
    return redirect(url_for('produtos'))

@app.route('/produtos/delete-all', methods=['POST'])
@login_required
def delete_all_produtos():
    rpa_info("[CRUD] Iniciando deleção de TODOS os produtos")
    """Deleta todos os produtos (soft delete)"""
    count = Produto.query.filter_by(ativo=True).count()
    Produto.query.filter_by(ativo=True).update({'ativo': False})
    db.session.commit()
    flash(f'{count} produtos removidos com sucesso!', 'success')
    return redirect(url_for('produtos'))

@app.route('/produtos/export')
@login_required
def export_produtos_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['SKU', 'Descrição', 'Cor', 'Categoria', 'Atributos Técnicos', 'Marca', 'Coleção', 'Tem Foto', 'Data Cadastro'])
    
    produtos = Produto.query.filter_by(ativo=True).order_by(Produto.sku).all()
    for p in produtos:
        writer.writerow([
            p.sku,
            p.descricao,
            p.cor or '',
            p.categoria or '',
            p.atributos_tecnicos or '',
            p.marca.name if p.marca else '',
            p.colecao.name if p.colecao else '',
            'Sim' if p.tem_foto else 'Não',
            p.created_at.strftime('%Y-%m-%d %H:%M') if p.created_at else ''
        ])
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=produtos.csv'
    response.headers['Content-type'] = 'text/csv; charset=utf-8'
    return response

@app.route('/produtos/<int:id>/associar-imagem', methods=['POST'])
@login_required
def associar_imagem_produto(id):
    produto = Produto.query.get_or_404(id)
    imagem_id = request.form.get('imagem_id')
    
    if not imagem_id:
        flash('Selecione uma imagem')
        return redirect(url_for('edit_produto', id=id))
    
    # Verificar se já existe associação
    existing = ImagemProduto.query.filter_by(
        imagem_id=int(imagem_id), 
        produto_id=produto.id
    ).first()
    
    if existing:
        flash('Imagem já está associada a este produto')
        return redirect(url_for('edit_produto', id=id))
    
    associacao = ImagemProduto(
        imagem_id=int(imagem_id),
        produto_id=produto.id
    )
    db.session.add(associacao)
    produto.tem_foto = True
    
    # Atualizar status na carteira se existir
    carteira_item = CarteiraCompras.query.filter_by(sku=produto.sku).first()
    if carteira_item:
        carteira_item.status_foto = 'Com Foto'
        carteira_item.produto_id = produto.id
    
    db.session.commit()
    
    flash('Imagem associada com sucesso!')
    return redirect(url_for('edit_produto', id=id))

@app.route('/produtos/<int:id>/desassociar-imagem/<int:imagem_id>', methods=['POST'])
@login_required
def desassociar_imagem_produto(id, imagem_id):
    produto = Produto.query.get_or_404(id)
    
    # Remover associação
    associacao = ImagemProduto.query.filter_by(
        imagem_id=imagem_id, 
        produto_id=produto.id
    ).first()
    
    if associacao:
        db.session.delete(associacao)
        
        # Verificar se ainda tem fotos associadas
        remaining = ImagemProduto.query.filter_by(produto_id=produto.id).count()
        if remaining == 0:
            produto.tem_foto = False
            # Atualizar status na carteira
            carteira_item = CarteiraCompras.query.filter_by(sku=produto.sku).first()
            if carteira_item:
                carteira_item.status_foto = 'Sem Foto'
        
        db.session.commit()
        flash('Imagem desassociada com sucesso!')
    else:
        flash('Associação não encontrada')
    
    return redirect(url_for('edit_produto', id=id))

# ==================== CARTEIRA DE COMPRAS ====================

@app.route('/carteira')
@login_required
def carteira():
    nav_log.page_enter("Carteira de Compras", user=current_user.username)
    rpa_info(f"[NAV] Carteira de Compras - Usuário: {current_user.username}")
    status = request.args.get('status', '')
    search = request.args.get('search', '')
    lote = request.args.get('lote', '')
    
    query = CarteiraCompras.query
    
    if status:
        query = query.filter_by(status_foto=status)
    
    if lote:
        query = query.filter_by(lote_importacao=lote)
    
    if search:
        search_term = f'%{search}%'
        query = query.filter(db.or_(
            CarteiraCompras.sku.ilike(search_term),
            CarteiraCompras.descricao.ilike(search_term)
        ))
    
    itens = query.order_by(CarteiraCompras.data_importacao.desc()).all()
    
    # Estatísticas
    total = CarteiraCompras.query.count()
    com_foto = CarteiraCompras.query.filter_by(status_foto='Com Foto').count()
    sem_foto = CarteiraCompras.query.filter_by(status_foto='Sem Foto').count()
    pendente = CarteiraCompras.query.filter_by(status_foto='Pendente').count()
    
    # Listar lotes únicos
    lotes = db.session.query(
        CarteiraCompras.lote_importacao,
        db.func.count(CarteiraCompras.id).label('total_itens'),
        db.func.min(CarteiraCompras.data_importacao).label('data_importacao')
    ).filter(
        CarteiraCompras.lote_importacao.isnot(None)
    ).group_by(
        CarteiraCompras.lote_importacao
    ).order_by(
        db.desc('data_importacao')
    ).all()
    
    return render_template('carteira/list.html', 
                          itens_carteira=itens,
                          total_carteira=total,
                          com_foto=com_foto,
                          sem_foto=sem_foto,
                          pendente=pendente,
                          search_query=search,
                          lotes=lotes,
                          lote_selecionado=lote)

@app.route('/carteira/lote/<lote_id>/delete', methods=['POST'])
@login_required
def deletar_lote_carteira(lote_id):
    """Deleta todos os itens de um lote de importação"""
    log_start(M.CARTEIRA, f"Exclusão de lote: {lote_id}")
    rpa_info(f"[CARTEIRA] Iniciando exclusão do lote: {lote_id}")
    try:
        itens = CarteiraCompras.query.filter_by(lote_importacao=lote_id).all()
        count = len(itens)
        
        for item in itens:
            db.session.delete(item)
        
        db.session.commit()
        log_end(M.CARTEIRA, f"Lote {lote_id} excluído: {count} itens removidos")
        rpa_info(f"[CARTEIRA] Lote {lote_id} excluído: {count} itens removidos")
        flash(f'Lote "{lote_id}" deletado com sucesso! {count} itens removidos.', 'success')
    except Exception as e:
        db.session.rollback()
        log_error(M.CARTEIRA, f"Exclusão de lote {lote_id}", str(e))
        rpa_error(f"[CARTEIRA] Erro ao excluir lote {lote_id}: {str(e)}", exc=e, regiao="carteira")
        flash(f'Erro ao deletar lote: {str(e)}', 'error')
    
    return redirect(url_for('carteira'))

@app.route('/carteira/limpar-tudo', methods=['POST'])
@login_required
def limpar_toda_carteira():
    """Limpa toda a carteira de compras"""
    log_start(M.CARTEIRA, "Limpeza completa da carteira")
    rpa_info("[CARTEIRA] Iniciando limpeza completa da carteira")
    try:
        count = CarteiraCompras.query.count()
        CarteiraCompras.query.delete()
        db.session.commit()
        log_end(M.CARTEIRA, f"Carteira limpa: {count} itens removidos")
        rpa_info(f"[CARTEIRA] Limpeza completa: {count} itens removidos")
        flash(f'Carteira limpa com sucesso! {count} itens removidos.', 'success')
    except Exception as e:
        db.session.rollback()
        log_error(M.CARTEIRA, "Limpeza da carteira", str(e))
        rpa_error(f"[CARTEIRA] Erro na limpeza: {str(e)}", exc=e, regiao="carteira")
        flash(f'Erro ao limpar carteira: {str(e)}', 'error')
    
    return redirect(url_for('carteira'))

def normalizar_nome_coluna(nome):
    """Remove acentos, espaços extras e converte para minúsculas para comparação"""
    import unicodedata
    if not isinstance(nome, str):
        return str(nome).lower().strip()
    nome = unicodedata.normalize('NFD', nome)
    nome = ''.join(c for c in nome if unicodedata.category(c) != 'Mn')
    return nome.lower().strip()

def normalizar_carteira_dataframe(df):
    """
    Normaliza um DataFrame da carteira, mapeando colunas do Excel para o padrão do sistema.
    Regras: case-insensitive, ignora acentos e espaços extras.
    
    Retorna: (df_normalizado, sku_encontrado: bool)
    """
    import pandas as pd
    
    mapeamento_sku = ['referencia e cor', 'referência e cor', 'referencia ns + cor', 'referência ns + cor', 'sku', 'codigo', 'código', 'ref']
    mapeamento_descricao = ['nome', 'nome produto', 'descricao', 'descrição', 'produto']
    mapeamento_cor = ['nome / cor', 'nome/cor', 'cor', 'cor produto']
    mapeamento_categoria = ['grupo', 'categoria', 'departamento', 'tipo']
    mapeamento_subcategoria = ['subgrupo', 'subcategoria', 'sub categoria', 'subtipo']
    mapeamento_subcolecao_campanha = ['entrada', 'campanha', 'subcoleção', 'subcolecao']  # Subcoleção/Campanha
    mapeamento_colecao = ['colecao', 'coleção', 'temporada', 'season']  # Coleção (normalmente vem da aba)
    mapeamento_marca = ['marca', 'brand', 'grife', 'fabricante']
    mapeamento_estilista = ['estilista', 'designer', 'criador']
    mapeamento_shooting = ['quando', 'shooting', 'data shooting', 'foto quando']
    mapeamento_observacoes = ['obs', 'observacoes', 'observações', 'notas', 'comentarios']
    mapeamento_origem = ['nacional / importado', 'nacional/importado', 'origem', 'procedencia']
    mapeamento_foto = ['foto', 'tem foto', 'status foto']
    mapeamento_okr = ['okr', 'status okr', 'aprovacao']
    mapeamento_quantidade = ['quantidade', 'qtd', 'qty', 'quant']
    mapeamento_posicao = ['top/bottom/inteiro', 'top / bottom / inteiro', 'posicao', 'posição', 'tipo peca']
    mapeamento_ref_estilo = ['referencia estilo', 'referência estilo', 'ref estilo', 'codigo estilo', 'código estilo']
    
    colunas_originais = {normalizar_nome_coluna(col): col for col in df.columns}
    
    novo_mapeamento = {}
    
    def encontrar_coluna(lista_nomes):
        for nome in lista_nomes:
            if nome in colunas_originais:
                return colunas_originais[nome]
        return None
    
    col_sku = encontrar_coluna(mapeamento_sku)
    col_descricao = encontrar_coluna(mapeamento_descricao)
    col_cor = encontrar_coluna(mapeamento_cor)
    col_categoria = encontrar_coluna(mapeamento_categoria)
    col_subcategoria = encontrar_coluna(mapeamento_subcategoria)
    col_subcolecao_campanha = encontrar_coluna(mapeamento_subcolecao_campanha)  # ENTRADA = Subcoleção
    col_colecao = encontrar_coluna(mapeamento_colecao)
    col_marca = encontrar_coluna(mapeamento_marca)
    col_estilista = encontrar_coluna(mapeamento_estilista)
    col_shooting = encontrar_coluna(mapeamento_shooting)
    col_observacoes = encontrar_coluna(mapeamento_observacoes)
    col_origem = encontrar_coluna(mapeamento_origem)
    col_foto = encontrar_coluna(mapeamento_foto)
    col_okr = encontrar_coluna(mapeamento_okr)
    col_quantidade = encontrar_coluna(mapeamento_quantidade)
    col_posicao = encontrar_coluna(mapeamento_posicao)
    col_ref_estilo = encontrar_coluna(mapeamento_ref_estilo)
    
    if col_sku:
        novo_mapeamento[col_sku] = 'sku'
    if col_descricao:
        novo_mapeamento[col_descricao] = 'descricao'
    if col_cor:
        novo_mapeamento[col_cor] = 'cor'
    if col_categoria:
        novo_mapeamento[col_categoria] = 'categoria'
    if col_subcategoria:
        novo_mapeamento[col_subcategoria] = 'subcategoria'
    if col_subcolecao_campanha:
        novo_mapeamento[col_subcolecao_campanha] = 'subcolecao_nome'  # ENTRADA = Subcoleção/Campanha
    if col_colecao:
        novo_mapeamento[col_colecao] = 'colecao_nome'
    if col_marca:
        novo_mapeamento[col_marca] = 'marca_nome'
    if col_estilista:
        novo_mapeamento[col_estilista] = 'estilista'
    if col_shooting:
        novo_mapeamento[col_shooting] = 'shooting'
    if col_observacoes:
        novo_mapeamento[col_observacoes] = 'observacoes'
    if col_origem:
        novo_mapeamento[col_origem] = 'origem'
    if col_foto:
        novo_mapeamento[col_foto] = 'status_foto_original'
    if col_okr:
        novo_mapeamento[col_okr] = 'okr'
    if col_quantidade:
        novo_mapeamento[col_quantidade] = 'quantidade'
    if col_posicao:
        novo_mapeamento[col_posicao] = 'posicao_peca'
    if col_ref_estilo:
        novo_mapeamento[col_ref_estilo] = 'referencia_estilo'
    
    df_normalizado = df.rename(columns=novo_mapeamento)
    
    sku_encontrado = 'sku' in df_normalizado.columns
    
    # DEBUG: Mostrar colunas detectadas
    print(f"[DEBUG] Colunas originais: {list(df.columns)}")
    print(f"[DEBUG] Mapeamento: {novo_mapeamento}")
    print(f"[DEBUG] Colunas normalizadas: {list(df_normalizado.columns)}")
    print(f"[DEBUG] col_marca={col_marca}, col_colecao={col_colecao}")
    
    return df_normalizado, sku_encontrado

def extrair_ano_estacao(nome_colecao):
    """Extrai ano e estação do nome da coleção (ex: 'INVERNO 2026' → ano=2026, estacao='Inverno')"""
    import re
    if not nome_colecao:
        return None, None
    
    nome_upper = nome_colecao.upper().strip()
    
    match_ano = re.search(r'\b(20\d{2})\b', nome_upper)
    ano = int(match_ano.group(1)) if match_ano else None
    
    estacao = None
    if 'INVERNO' in nome_upper:
        estacao = 'Inverno'
    elif 'VERAO' in nome_upper or 'VERÃO' in nome_upper:
        estacao = 'Verão'
    elif 'PRIMAVERA' in nome_upper:
        estacao = 'Primavera'
    elif 'OUTONO' in nome_upper:
        estacao = 'Outono'
    elif 'ALTO VERAO' in nome_upper or 'ALTO VERÃO' in nome_upper:
        estacao = 'Alto Verão'
    elif 'CRUISE' in nome_upper or 'RESORT' in nome_upper:
        estacao = 'Resort'
    
    return ano, estacao

def obter_ou_criar_colecao(nome_colecao, contadores):
    """Busca ou cria uma coleção pelo nome. Retorna o ID da coleção."""
    if not nome_colecao or not nome_colecao.strip():
        return None
    
    nome_normalizado = nome_colecao.strip().upper()
    
    colecao = Collection.query.filter(
        db.func.upper(Collection.name) == nome_normalizado
    ).first()
    
    if colecao:
        return colecao.id
    
    ano, estacao = extrair_ano_estacao(nome_colecao)
    
    nova_colecao = Collection(
        name=nome_colecao.strip().title(),
        description=f'Coleção criada automaticamente via importação de carteira',
        year=ano,
        season=estacao
    )
    db.session.add(nova_colecao)
    db.session.flush()
    contadores['colecoes_criadas'] += 1
    return nova_colecao.id

def extrair_marca_do_nome_arquivo(nome_arquivo):
    """
    Extrai a marca do nome do arquivo usando padrões comuns.
    Prioriza a ÚLTIMA palavra totalmente em maiúsculas (geralmente é a marca).
    Ex: "Carteira diversas coleção SOUQ.xlsx" -> "SOUQ"
    Ex: "Carteira MKT ANIMALE Inverno 26.xlsx" -> "ANIMALE"
    Ex: "SOUQ Inverno 2025.xlsx" -> "SOUQ"
    """
    import re
    
    if not nome_arquivo:
        return None
    
    nome = os.path.splitext(nome_arquivo)[0]
    
    palavras_ignorar = ['carteira', 'diversas', 'coleção', 'colecao', 'inverno', 'verao', 'verão', 
                        'primavera', 'outono', 'alto', 'preview', 'lancamento', 'lançamento',
                        '2024', '2025', '2026', '24', '25', '26', '24-25', '25-26', '26-27',
                        'mkt', 'marketing', 'xlsx', 'csv', 'planilha', 'dados', 'lista']
    
    palavras = re.findall(r'[A-Za-zÀ-ÿ]+', nome)
    
    palavras_maiusculas = []
    for palavra in palavras:
        if len(palavra) >= 3 and palavra.isupper() and palavra.lower() not in palavras_ignorar:
            palavras_maiusculas.append(palavra)
    
    if palavras_maiusculas:
        return palavras_maiusculas[-1].upper()
    
    for palavra in reversed(palavras):
        if len(palavra) >= 4 and palavra.lower() not in palavras_ignorar:
            if palavra[0].isupper():
                return palavra.upper()
    
    return None

def obter_ou_criar_marca(nome_marca, contadores):
    """Busca ou cria uma marca pelo nome. Retorna o ID da marca."""
    if not nome_marca or not nome_marca.strip():
        return None
    
    nome_normalizado = nome_marca.strip().upper()
    
    marca = Brand.query.filter(
        db.func.upper(Brand.name) == nome_normalizado
    ).first()
    
    if marca:
        return marca.id
    
    nova_marca = Brand(
        name=nome_marca.strip().title(),
        description=f'Marca criada automaticamente via importação de carteira'
    )
    db.session.add(nova_marca)
    db.session.flush()
    contadores['marcas_criadas'] += 1
    return nova_marca.id

def obter_ou_criar_subcolecao(nome_subcolecao, colecao_id, contadores):
    """
    Busca ou cria uma subcoleção/campanha pelo nome dentro de uma coleção.
    Retorna o ID da subcoleção.
    """
    if not nome_subcolecao or not str(nome_subcolecao).strip():
        return None
    if not colecao_id:
        return None
    
    nome_str = str(nome_subcolecao).strip()
    nome_normalizado = nome_str.upper()
    
    # Ignorar valores inválidos
    if nome_normalizado in ['-', '0', 'NAN', 'NONE', '']:
        return None
    
    # Criar slug para busca
    import re
    slug = re.sub(r'[^a-zA-Z0-9]', '_', nome_normalizado.lower()).strip('_')
    
    # Buscar subcoleção existente na mesma coleção
    subcolecao = Subcolecao.query.filter(
        db.func.upper(Subcolecao.nome) == nome_normalizado,
        Subcolecao.colecao_id == colecao_id
    ).first()
    
    if subcolecao:
        return subcolecao.id
    
    # Determinar tipo de campanha baseado no nome
    tipo_campanha = None
    if 'DDM' in nome_normalizado or 'DIA DAS M' in nome_normalizado:
        tipo_campanha = 'Dia das Mães'
    elif 'NATAL' in nome_normalizado:
        tipo_campanha = 'Natal'
    elif 'REVEILLON' in nome_normalizado or 'ANO NOVO' in nome_normalizado:
        tipo_campanha = 'Réveillon'
    elif 'LANCAMENTO' in nome_normalizado or 'LANÇAMENTO' in nome_normalizado:
        tipo_campanha = 'Lançamento'
    elif 'COLECAO' in nome_normalizado or 'COLEÇÃO' in nome_normalizado:
        tipo_campanha = 'Coleção Principal'
    elif 'PREVIEW' in nome_normalizado:
        tipo_campanha = 'Preview'
    elif 'DROP' in nome_normalizado:
        tipo_campanha = 'Drop'
    elif 'PERENE' in nome_normalizado:
        tipo_campanha = 'Perene'
    elif 'ATACADO' in nome_normalizado:
        tipo_campanha = 'Atacado'
    elif 'ALTO VERAO' in nome_normalizado or 'ALTO VERÃO' in nome_normalizado:
        tipo_campanha = 'Alto Verão'
    
    nova_subcolecao = Subcolecao(
        nome=nome_str.title(),
        slug=slug,
        tipo_campanha=tipo_campanha,
        colecao_id=colecao_id
    )
    db.session.add(nova_subcolecao)
    db.session.flush()
    contadores['subcolecoes_criadas'] = contadores.get('subcolecoes_criadas', 0) + 1
    return nova_subcolecao.id

def obter_ou_criar_produto(sku, dados_linha, contadores, marca_id=None, colecao_id=None, subcolecao_id=None, cache_produtos=None):
    """Busca ou cria um produto pelo SKU. Retorna o ID do produto."""
    import pandas as pd
    import json
    
    if not sku or not sku.strip():
        return None
    
    sku = sku.strip()
    
    # Verificar cache local primeiro (evita duplicatas na mesma importação)
    if cache_produtos is not None and sku in cache_produtos:
        return cache_produtos[sku]
    
    # Buscar QUALQUER produto com esse SKU (ativo ou inativo)
    produto = Produto.query.filter_by(sku=sku).first()
    
    if produto:
        # Se existe mas estava inativo, reativar
        if not produto.ativo:
            produto.ativo = True
            contadores['produtos_criados'] += 1
        
        # Atualizar marca, coleção e subcoleção se ainda não tiver
        if marca_id and not produto.marca_id:
            produto.marca_id = marca_id
        if colecao_id and not produto.colecao_id:
            produto.colecao_id = colecao_id
        if subcolecao_id and not produto.subcolecao_id:
            produto.subcolecao_id = subcolecao_id
        
        db.session.flush()
        
        # Adicionar ao cache
        if cache_produtos is not None:
            cache_produtos[sku] = produto.id
        
        return produto.id
    
    # Produto não existe, criar novo
    descricao = str(dados_linha.get('descricao', ''))[:255] if pd.notna(dados_linha.get('descricao', '')) else sku
    cor = str(dados_linha.get('cor', ''))[:50] if pd.notna(dados_linha.get('cor', '')) else None
    categoria = str(dados_linha.get('categoria', ''))[:100] if pd.notna(dados_linha.get('categoria', '')) else None
    
    # Guardar informações extras em atributos_tecnicos como JSON
    atributos_extras = {}
    if pd.notna(dados_linha.get('subcategoria', '')):
        atributos_extras['subcategoria'] = str(dados_linha.get('subcategoria', ''))[:100]
    atributos_extras['origem'] = 'Importação Carteira'
    
    novo_produto = Produto(
        sku=sku,
        descricao=descricao if descricao else sku,
        cor=cor,
        categoria=categoria,
        marca_id=marca_id,
        colecao_id=colecao_id,
        subcolecao_id=subcolecao_id,
        atributos_tecnicos=json.dumps(atributos_extras) if atributos_extras else None,
        tem_foto=False
    )
    db.session.add(novo_produto)
    db.session.flush()
    contadores['produtos_criados'] += 1
    
    # Adicionar ao cache
    if cache_produtos is not None:
        cache_produtos[sku] = novo_produto.id
    
    return novo_produto.id

def processar_linhas_carteira(df, lote_id, aba_origem, contadores=None, cache_produtos=None, tipo_carteira='Moda', marca_fallback=None, errors=None):
    """
    Processa linhas do DataFrame e insere/atualiza na CarteiraCompras.
    Auto-cria Subcoleções (baseado em ENTRADA), Marcas e Produtos.
    Coleções não são criadas automaticamente na importação quando o backend é SharePoint.
    
    IMPORTANTE:
    - aba_origem = Nome da ABA do Excel (mantido como metadado)
    - coluna ENTRADA = SUBCOLEÇÃO/CAMPANHA (ex: "Lançamento", "DDM", "Natal")
    
    Args:
        df: DataFrame normalizado
        lote_id: ID do lote de importação
        aba_origem: Nome da aba de origem (será usado como COLEÇÃO)
        contadores: Dicionário para rastrear entidades criadas
        cache_produtos: Cache de produtos já criados nesta importação
        tipo_carteira: Tipo de carteira (Moda, Acessórios, Home)
        marca_fallback: Marca extraída do nome do arquivo (usado quando não há coluna MARCA)
    
    Returns:
        (created_count, skus_invalidos, erros, valid_rows, updated_count): Itens criados, linhas ignoradas, erros acumulados,
        linhas válidas processadas e itens já existentes atualizados
    """
    import pandas as pd
    
    log_start(M.CARTEIRA, f"Processando aba: {aba_origem}")
    rpa_info(f"[CARTEIRA] Processando aba: {aba_origem} ({len(df)} linhas)")
    total_linhas = len(df)
    
    if contadores is None:
        contadores = {
            'colecoes_criadas': 0,
            'subcolecoes_criadas': 0,
            'marcas_criadas': 0,
            'produtos_criados': 0
        }
    
    if cache_produtos is None:
        cache_produtos = {}

    if errors is None:
        errors = []
    
    cache_subcolecoes = {}
    
    created_count = 0
    skus_invalidos = 0
    valid_rows = 0
    updated_count = 0
    
    if is_sharepoint_backend():
        colecao_id = None
    else:
        colecao_id = obter_ou_criar_colecao(aba_origem, contadores) if aba_origem and aba_origem != 'CSV' else None
    
    marca_fallback_id = None
    if marca_fallback and not is_sharepoint_backend():
        marca_fallback_id = obter_ou_criar_marca(marca_fallback, contadores)
        print(f"[INFO] Marca extraída do nome do arquivo: {marca_fallback}")
    
    for idx, row in df.iterrows():
        sku = str(row.get('sku', '')).strip() if pd.notna(row.get('sku', '')) else ''
        
        if not sku or sku.upper() in ['SKUS', 'SKU', 'NAN', 'NONE', '']:
            skus_invalidos += 1
            continue
        
        sku = sku.rstrip('.00').rstrip('.0').strip()
        valid_rows += 1
        
        nome_subcolecao = str(row.get('subcolecao_nome', '')).strip() if pd.notna(row.get('subcolecao_nome', '')) else None
        nome_marca = str(row.get('marca_nome', '')).strip() if pd.notna(row.get('marca_nome', '')) else None
        
        # Criar/obter subcoleção (requer colecao_id)
        subcolecao_id = None
        if nome_subcolecao and colecao_id:
            cache_key = f"{colecao_id}:{nome_subcolecao.upper()}"
            if cache_key in cache_subcolecoes:
                subcolecao_id = cache_subcolecoes[cache_key]
            else:
                subcolecao_id = obter_ou_criar_subcolecao(nome_subcolecao, colecao_id, contadores)
                cache_subcolecoes[cache_key] = subcolecao_id
        
        if is_sharepoint_backend():
            marca_id = None
        else:
            marca_id = obter_ou_criar_marca(nome_marca, contadores) if nome_marca else marca_fallback_id
        produto_id = obter_ou_criar_produto(sku, row, contadores, marca_id=marca_id, colecao_id=colecao_id, subcolecao_id=subcolecao_id, cache_produtos=cache_produtos)
        
        # IMPORTANTE: Verificar existência por SKU + COLEÇÃO (um mesmo SKU pode existir em múltiplas coleções)
        if colecao_id:
            existing = CarteiraCompras.query.filter_by(sku=sku, colecao_id=colecao_id).first()
        else:
            existing = CarteiraCompras.query.filter_by(sku=sku).first()
        
        if existing:
            # Atualizar registro existente na mesma coleção
            existing.lote_importacao = lote_id
            existing.aba_origem = aba_origem
            if subcolecao_id:
                existing.subcolecao_id = subcolecao_id
            if marca_id:
                existing.marca_id = marca_id
            if produto_id:
                existing.produto_id = produto_id
            existing.colecao_nome = aba_origem
            updated_count += 1
        else:
            status_foto_original = str(row.get('status_foto_original', '')).upper() if pd.notna(row.get('status_foto_original', '')) else ''
            if 'SIM' in status_foto_original or 'YES' in status_foto_original or 'S' == status_foto_original:
                status_foto = 'Com Foto'
            elif 'NAO' in status_foto_original or 'NÃO' in status_foto_original or 'NO' in status_foto_original or 'N' == status_foto_original:
                status_foto = 'Sem Foto'
            else:
                status_foto = 'Pendente'
            
            try:
                qtd = int(float(row.get('quantidade', 1))) if pd.notna(row.get('quantidade', 1)) else 1
            except (ValueError, TypeError):
                qtd = 1
            
            categoria_val = str(row.get('categoria', ''))[:100] if pd.notna(row.get('categoria', '')) else None
            subcategoria_val = str(row.get('subcategoria', ''))[:100] if pd.notna(row.get('subcategoria', '')) else None
            posicao_val = str(row.get('posicao_peca', ''))[:50] if pd.notna(row.get('posicao_peca', '')) else None
            ref_estilo_val = str(row.get('referencia_estilo', ''))[:50] if pd.notna(row.get('referencia_estilo', '')) else None
            
            item = CarteiraCompras(
                sku=sku,
                descricao=str(row.get('descricao', ''))[:255] if pd.notna(row.get('descricao', '')) else None,
                cor=str(row.get('cor', ''))[:100] if pd.notna(row.get('cor', '')) else None,
                categoria=categoria_val,
                subcategoria=subcategoria_val,
                colecao_nome=aba_origem[:100] if aba_origem else None,  # Coleção = nome da aba
                colecao_id=colecao_id,
                subcolecao_id=subcolecao_id,  # Subcoleção = ENTRADA
                marca_id=marca_id,
                estilista=str(row.get('estilista', ''))[:255] if pd.notna(row.get('estilista', '')) else None,
                shooting=str(row.get('shooting', ''))[:100] if pd.notna(row.get('shooting', '')) else None,
                observacoes=str(row.get('observacoes', '')) if pd.notna(row.get('observacoes', '')) else None,
                origem=str(row.get('origem', ''))[:50] if pd.notna(row.get('origem', '')) else None,
                okr=str(row.get('okr', ''))[:20] if pd.notna(row.get('okr', '')) else None,
                quantidade=qtd,
                status_foto=status_foto,
                lote_importacao=lote_id,
                aba_origem=aba_origem,
                produto_id=produto_id,
                material=categoria_val,
                tipo_peca=subcategoria_val,
                posicao_peca=posicao_val,
                referencia_estilo=ref_estilo_val,
                tipo_carteira=tipo_carteira
            )
            
            if produto_id:
                produto = Produto.query.get(produto_id)
                if produto and produto.tem_foto:
                    item.status_foto = 'Com Foto'
            
            db.session.add(item)
            created_count += 1
            
            if count % 100 == 0:
                log_progress(M.CARTEIRA, "Importação", created_count, total_linhas)
    
    log_end(M.CARTEIRA, f"Aba {aba_origem}: {created_count} registros")
    return created_count, skus_invalidos, errors, valid_rows, updated_count


def get_or_create_collection_from_sharepoint(folder_name, collections_cache=None):
    if not folder_name:
        return None
    normalized_key = folder_name.strip().upper()
    if collections_cache is not None and normalized_key in collections_cache:
        collection_id = collections_cache[normalized_key]
        print(f"[CROSS] Reutilizando coleção '{folder_name}' (id={collection_id})")
        return collection_id

    existing = Collection.query.filter(
        db.func.upper(Collection.name) == normalized_key
    ).first()
    if existing:
        if collections_cache is not None:
            collections_cache[normalized_key] = existing.id
        print(f"[CROSS] Reutilizando coleção '{existing.name}' (id={existing.id})")
        return existing.id

    collection = Collection(
        name=folder_name.strip(),
        description='Coleção criada automaticamente via SharePoint'
    )
    db.session.add(collection)
    db.session.flush()
    if collections_cache is not None:
        collections_cache[normalized_key] = collection.id
    print(f"[CROSS] Criada coleção '{collection.name}' (id={collection.id}) a partir da pasta SharePoint")
    return collection.id


def get_or_create_brand_from_sharepoint(brand_name, brands_cache=None):
    if not brand_name:
        return None
    normalized_key = brand_name.strip().upper()
    if brands_cache is not None and normalized_key in brands_cache:
        brand_id = brands_cache[normalized_key]
        print(f"[CROSS] Reutilizando marca '{brand_name}' (id={brand_id})")
        return brand_id

    existing = Brand.query.filter(
        db.func.upper(Brand.name) == normalized_key
    ).first()
    if existing:
        if brands_cache is not None:
            brands_cache[normalized_key] = existing.id
        print(f"[CROSS] Reutilizando marca '{existing.name}' (id={existing.id})")
        return existing.id

    brand = Brand(
        name=brand_name.strip(),
        description='Marca criada automaticamente via SharePoint'
    )
    db.session.add(brand)
    db.session.flush()
    if brands_cache is not None:
        brands_cache[normalized_key] = brand.id
    print(f"[CROSS] Criada marca '{brand.name}' (id={brand.id}) a partir da pasta SharePoint")
    return brand.id


def _record_sharepoint_cross_result(lote_id, result):
    payload = dict(result)
    payload["lote_id"] = lote_id
    payload["timestamp"] = datetime.utcnow().isoformat()
    key = f"sharepoint_cross:{lote_id}"
    existing = SystemConfig.query.filter_by(key=key).first()
    if existing:
        existing.value = json.dumps(payload)
    else:
        db.session.add(SystemConfig(key=key, value=json.dumps(payload)))


def run_sharepoint_cross_for_batch(batch_id, force_update=False, auto=False):
    if not is_sharepoint_backend():
        return {"created": 0, "updated": 0, "matched": 0, "skus": 0}

    items = CarteiraCompras.query.filter_by(lote_importacao=batch_id).all()
    if not items:
        return {"created": 0, "updated": 0, "matched": 0, "skus": 0}

    cross_label = "Auto-cross" if auto else "Cruzamento"
    print(f"[CROSS] {cross_label} iniciado | lote={batch_id} | auto={auto}")
    client = get_sharepoint_client()
    index = build_sharepoint_index()
    collections_cache = {}
    brands_cache = {}
    created = 0
    updated = 0
    matched = 0

    def _normalize_text(value):
        if value is None:
            return ""
        text = str(value).strip().lower()
        text = unicodedata.normalize("NFD", text)
        return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")

    def _folder_for_type(tipo):
        normalized = _normalize_text(tipo)
        if not normalized:
            return "", normalized, False
        if "moda" in normalized or "roupa" in normalized or "vestu" in normalized:
            return "3. Moda", normalized, True
        if "home" in normalized or "casa" in normalized or "decor" in normalized:
            return "2. Home", normalized, True
        if "acess" in normalized:
            return "1. Acessórios", normalized, True
        return "", normalized, False

    def _filename_matches_sku(filename, sku_value):
        if not filename or not sku_value:
            return False
        base = os.path.splitext(filename)[0].strip()
        return base.upper().startswith(sku_value.upper())

    for carteira_item in items:
        sku_raw = str(carteira_item.sku or "").strip()
        if not sku_raw:
            continue
        sku_norm = normalizar_sku(sku_raw)
        sp_items = client.find_by_sku_base(index, sku_norm)
        if not sp_items:
            sp_items = client.find_by_sku_base(index, sku_raw)
        sp_items = sp_items or []

        expected_folder, tipo_normalized, folder_mapped = _folder_for_type(carteira_item.tipo_carteira)
        filtered_items = []
        collection_hint = ""
        brand_hint = ""
        folder_label = expected_folder or "(sem filtro)"

        if tipo_normalized and not folder_mapped:
            print(
                f"[CROSS] WARNING: tipo_carteira sem mapeamento '{carteira_item.tipo_carteira}' "
                f"para SKU {sku_raw}; sem filtro de subpasta"
            )

        for sp_item in sp_items:
            parent_path = sp_item.get("parent_path", "")
            collection_name, subfolder = get_collection_and_subfolder_from_path(parent_path)
            if not collection_hint:
                collection_hint = collection_name
            if not brand_hint:
                brand_hint = get_brand_name_from_path(parent_path) or ""
            if expected_folder and _normalize_text(subfolder) != _normalize_text(expected_folder):
                continue
            if not _filename_matches_sku(sp_item.get("name", ""), sku_raw):
                continue
            filtered_items.append(sp_item)

        print(
            f"[CROSS] SKU {sku_raw} → {len(filtered_items)} arquivos no SharePoint "
            f"| colecao={collection_hint or '-'} | tipo={tipo_normalized or '-'} "
            f"| pasta={folder_label}"
        )
        if not filtered_items:
            carteira_item.status_foto = 'Sem Foto'
            continue

        matched += 1
        carteira_item.status_foto = 'Com Foto'

        collection_id = None
        brand_id = None

        for sp_item in filtered_items:
            existing = Image.query.filter_by(sharepoint_item_id=sp_item.get('item_id')).first()

            sku_base, sequencia, sku_full = parse_sku_variants(sp_item.get('name', ''))
            sku_base = sku_base or carteira_item.sku
            sku_full = sku_full or carteira_item.sku

            parent_path = sp_item.get('parent_path', '')
            collection_name = get_collection_name_from_path(parent_path)
            collection_id = get_or_create_collection_from_sharepoint(
                collection_name,
                collections_cache=collections_cache,
            )
            brand_name = get_brand_name_from_path(parent_path)
            brand_id = get_or_create_brand_from_sharepoint(
                brand_name,
                brands_cache=brands_cache,
            )
            if brand_id:
                print(f"[CROSS] Marca '{brand_name}' associada ao SKU {carteira_item.sku}")

            if collection_id and not carteira_item.colecao_id:
                carteira_item.colecao_id = collection_id
            if brand_id and not carteira_item.marca_id:
                carteira_item.marca_id = brand_id

            if existing:
                if sp_item.get('web_url'):
                    existing.sharepoint_web_url = sp_item.get('web_url')
                existing.sharepoint_parent_path = parent_path
                existing.sharepoint_file_name = sp_item.get('name')
                existing.sharepoint_last_modified = sp_item.get('last_modified')
                if force_update or not existing.collection_id:
                    existing.collection_id = collection_id or existing.collection_id
                if force_update or not existing.brand_id:
                    existing.brand_id = brand_id or existing.brand_id
                updated += 1
                continue

            import uuid
            unique_code = f"IMG-{uuid.uuid4().hex[:8].upper()}"

            tags_list = []
            for val in [carteira_item.categoria, carteira_item.subcategoria, carteira_item.cor]:
                if val:
                    tags_list.append(val)

            new_image = Image(
                filename=sp_item.get('name') or sku_full,
                original_name=sp_item.get('name') or sku_full,
                storage_path=None,
                source_type='sharepoint',
                sharepoint_drive_id=sp_item.get('drive_id'),
                sharepoint_item_id=sp_item.get('item_id'),
                sharepoint_web_url=sp_item.get('web_url'),
                sharepoint_parent_path=parent_path,
                sharepoint_file_name=sp_item.get('name'),
                sharepoint_last_modified=sp_item.get('last_modified'),
                sku=sku_full,
                sku_base=sku_base,
                sequencia=sequencia,
                nome_peca=carteira_item.descricao,
                categoria=carteira_item.categoria,
                subcategoria=carteira_item.subcategoria,
                tipo_peca=carteira_item.tipo_peca,
                origem=carteira_item.origem,
                estilista=carteira_item.estilista,
                referencia_estilo=carteira_item.referencia_estilo,
                collection_id=collection_id or carteira_item.colecao_id,
                subcolecao_id=carteira_item.subcolecao_id,
                brand_id=brand_id or carteira_item.marca_id,
                unique_code=unique_code,
                tags=json.dumps(tags_list),
                status='Pendente'
            )
            db.session.add(new_image)
            db.session.flush()

            try:
                image_bytes = client.download_bytes(sp_item.get('drive_id'), sp_item.get('item_id'))
                save_thumbnail_for_image(new_image.id, image_bytes)
            except Exception as e:
                print(f"[WARN] SharePoint thumbnail failed for {sku_full}: {e}")

            created += 1

        produto = Produto.query.filter_by(sku=carteira_item.sku).first()
        if produto:
            produto.tem_foto = True
            if collection_id and not produto.colecao_id:
                produto.colecao_id = collection_id
            if brand_id and not produto.marca_id:
                produto.marca_id = brand_id

    db.session.commit()
    result = {"created": created, "updated": updated, "matched": matched, "skus": len(items)}
    _record_sharepoint_cross_result(batch_id, result)
    print(
        f"[CROSS] {cross_label} finalizado | lote={batch_id} "
        f"| com_foto={matched} | sem_foto={max(0, len(items) - matched)}"
    )
    return result


def sync_sharepoint_images_for_import(lote_id):
    return run_sharepoint_cross_for_batch(lote_id)


@app.route('/sharepoint/reindex', methods=['POST'])
@login_required
def sharepoint_reindex():
    if not is_sharepoint_backend():
        flash('Reindexação SharePoint indisponível para este backend.', 'warning')
        return redirect(url_for('carteira'))

    index = build_sharepoint_index(force_refresh=True)
    total_files = sum(len(items) for items in index.values())
    total_skus = len(index)
    flash(f"SharePoint reindexado: {total_files} arquivos em {total_skus} SKUs.", 'success')
    return redirect(url_for('carteira'))

def reconciliar_imagens_com_carteira():
    """Busca match na Carteira para imagens que não tiveram match anteriormente.
    Sobrescreve dados da IA pelos dados da Carteira quando encontra match.
    Retorna quantidade de imagens reconciliadas."""
    
    imagens_sem_match = Image.query.filter(
        db.or_(
            Image.nome_peca.is_(None),
            Image.nome_peca == ''
        )
    ).all()
    
    reconciliadas = 0
    
    for img in imagens_sem_match:
        if not img.sku_base:
            continue
        
        carteira = CarteiraCompras.query.filter_by(sku=img.sku_base).first()
        
        if carteira:
            img.nome_peca = carteira.descricao
            
            if carteira.categoria:
                img.categoria = carteira.categoria
            if carteira.subcategoria:
                img.subcategoria = carteira.subcategoria
            if carteira.tipo_peca:
                img.tipo_peca = carteira.tipo_peca
            if carteira.origem:
                img.origem = carteira.origem
            if carteira.material:
                img.ai_material = carteira.material
            if carteira.estilista:
                img.estilista = carteira.estilista
            if carteira.referencia_estilo:
                img.referencia_estilo = carteira.referencia_estilo
            
            if carteira.colecao_id:
                img.collection_id = carteira.colecao_id
            if carteira.subcolecao_id:
                img.subcolecao_id = carteira.subcolecao_id
            if carteira.marca_id:
                img.brand_id = carteira.marca_id
            
            if img.status == 'Pendente Análise IA':
                img.status = 'Pendente'
            
            carteira.status_foto = 'Com Foto'
            
            for item in img.items:
                if not item.description or item.description == '':
                    item.description = carteira.descricao
                if carteira.tipo_peca:
                    item.ai_item_type = carteira.tipo_peca
                if carteira.material:
                    item.ai_material = carteira.material
            
            reconciliadas += 1
            print(f"[RECONCILE] Image {img.id} ({img.sku_base}) matched with Carteira: {carteira.descricao[:50]}...")
    
    if reconciliadas > 0:
        db.session.commit()
    
    return reconciliadas


@app.route('/carteira/reconciliar', methods=['POST'])
@login_required
def reconciliar_carteira():
    """Endpoint para reconciliar imagens com a Carteira."""
    carteira_log.reconciliation_started()
    rpa_info("[CARTEIRA] Iniciando reconciliação de imagens")
    reconciliadas = reconciliar_imagens_com_carteira()
    carteira_log.reconciliation_completed(reconciliadas)
    rpa_info(f"[CARTEIRA] Reconciliação concluída: {reconciliadas} imagens atualizadas")
    
    if reconciliadas > 0:
        flash(f'{reconciliadas} imagem(ns) reconciliada(s) com a Carteira!')
    else:
        flash('Nenhuma imagem para reconciliar.')
    
    return redirect(url_for('listar_carteira'))


@app.route('/carteira/importar', methods=['GET', 'POST'])
@login_required
def importar_carteira():
    if request.method == 'GET':
        nav_log.page_enter("Importar Carteira", user=current_user.username)
        rpa_info(f"[NAV] Importar Carteira - Usuário: {current_user.username}")
    if request.method == 'POST':
        def json_error(message, status=400, errors=None, error_code="carteira_import_error", debug_id=None):
            payload = {'success': False, 'message': message, 'error_code': error_code}
            if debug_id:
                payload['debug_id'] = debug_id
            if errors:
                payload['erros'] = errors
            log_error(
                M.CARTEIRA,
                "Importação erro",
                message,
                status=status,
                errors=errors,
                path=request.path,
                debug_id=debug_id
            )
            return jsonify(payload), status

        def add_error(errors, message, max_errors=20):
            if len(errors) < max_errors:
                errors.append(message)

        errors = []
        if 'arquivo' not in request.files:
            rpa_warn("[CARTEIRA] Importação falhou - nenhum arquivo enviado")
            return json_error('Nenhum arquivo enviado', status=400, error_code="missing_file")
        
        file = request.files['arquivo']
        if file.filename == '':
            return json_error('Nenhum arquivo selecionado', status=400, error_code="empty_filename")
        
        filename = file.filename.lower()
        if not (filename.endswith('.csv') or filename.endswith('.xlsx') or filename.endswith('.xls')):
            return json_error(
                'Apenas arquivos CSV ou Excel (.xlsx, .xls) são permitidos',
                status=400,
                error_code="invalid_extension"
            )
        
        try:
            import uuid
            import pandas as pd
            
            lote_id = f"LOTE-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
            aba_selecionada = request.form.get('aba', '')
            importar_todas = request.form.get('importar_todas', '') == 'true'
            tipo_carteira = request.form.get('tipo_carteira', 'Moda')
            
            carteira_log.import_started(file.filename, aba_selecionada if aba_selecionada else 'todas')
            
            marca_do_arquivo = extrair_marca_do_nome_arquivo(file.filename)
            
            total_created = 0
            total_invalidos = 0
            total_valid_rows = 0
            total_updated = 0
            abas_processadas = []
            
            contadores = {
                'colecoes_criadas': 0,
                'marcas_criadas': 0,
                'produtos_criados': 0
            }
            
            cache_produtos = {}
            
            if filename.endswith('.csv'):
                try:
                    content = file.read().decode('utf-8')
                except UnicodeDecodeError:
                    file.seek(0)
                    content = file.read().decode('latin-1')

                df = pd.read_csv(io.StringIO(content))
                df_normalizado, sku_encontrado = normalizar_carteira_dataframe(df)

                if not sku_encontrado:
                    return json_error(
                        'Coluna de SKU não encontrada no arquivo CSV. Verifique se existe uma coluna chamada "SKU", "REFERÊNCIA E COR" ou "CODIGO".',
                        status=400,
                        error_code="missing_sku_column"
                    )

                created_count, invalidos, linha_erros, valid_rows, updated_count = processar_linhas_carteira(
                    df_normalizado, lote_id, 'CSV', contadores, cache_produtos, tipo_carteira, marca_do_arquivo
                )
                total_created = created_count
                total_invalidos = invalidos
                total_valid_rows = valid_rows
                total_updated = updated_count
                errors.extend(linha_erros)
                abas_processadas.append('CSV')
                carteira_log.import_progress(created_count, created_count, 'CSV')
                info(M.CARTEIRA, "Resumo aba", f"sheet=CSV linhas={len(df)} validos={valid_rows} invalidos={invalidos}")

                try:
                    db.session.commit()
                except Exception as e:
                    db.session.rollback()
                    rpa_error(f"[CARTEIRA] Erro ao salvar importação CSV: {str(e)}", exc=e, regiao="carteira")
                    log_error(M.CARTEIRA, "Importação CSV", str(e))
                    return json_error(
                        'Erro ao salvar a importação no banco.',
                        status=500,
                        errors=[str(e)],
                        error_code="db_commit_error"
                    )
            else:
                try:
                    xl = pd.ExcelFile(file)
                except Exception as e:
                    rpa_error(f"[CARTEIRA] Erro ao abrir Excel: {str(e)}", exc=e, regiao="carteira")
                    log_error(M.CARTEIRA, "Leitura Excel", str(e))
                    return json_error(
                        'Não foi possível abrir o arquivo Excel.',
                        status=400,
                        errors=[str(e)],
                        error_code="excel_open_error"
                    )

                if importar_todas:
                    for sheet_name in xl.sheet_names:
                        try:
                            df = pd.read_excel(xl, sheet_name=sheet_name)
                        except Exception as e:
                            msg = f"Aba '{sheet_name}': erro ao ler ({str(e)})"
                            add_error(errors, msg)
                            rpa_error(f"[CARTEIRA] {msg}", exc=e, regiao="carteira")
                            log_error(M.CARTEIRA, "Leitura aba", msg)
                            continue

                        if df.empty or len(df) == 0:
                            continue

                        df_normalizado, sku_encontrado = normalizar_carteira_dataframe(df)

                        if not sku_encontrado:
                            msg = f"Aba '{sheet_name}' ignorada: coluna SKU não encontrada."
                            add_error(errors, msg)
                            rpa_warn(f"[CARTEIRA] {msg}")
                            continue

                        try:
                            created_count, invalidos, linha_erros, valid_rows, updated_count = processar_linhas_carteira(
                                df_normalizado, lote_id, sheet_name, contadores, cache_produtos, tipo_carteira, marca_do_arquivo
                            )
                            total_created += created_count
                            total_invalidos += invalidos
                            total_valid_rows += valid_rows
                            total_updated += updated_count
                            errors.extend(linha_erros)
                        except Exception as e:
                            db.session.rollback()
                            msg = f"Aba '{sheet_name}': erro ao processar ({str(e)})"
                            add_error(errors, msg)
                            rpa_error(f"[CARTEIRA] {msg}", exc=e, regiao="carteira")
                            log_error(M.CARTEIRA, "Processamento aba", msg)
                            continue

                        if created_count + updated_count > 0:
                            abas_processadas.append(f"{sheet_name} ({created_count + updated_count})")
                            carteira_log.import_progress(total_created, len(xl.sheet_names), sheet_name)
                        info(
                            M.CARTEIRA,
                            "Resumo aba",
                            f"sheet={sheet_name} linhas={len(df)} validos={valid_rows} invalidos={invalidos} criados={created_count} atualizados={updated_count}"
                        )

                        try:
                            db.session.commit()
                        except Exception as e:
                            db.session.rollback()
                            msg = f"Aba '{sheet_name}': erro ao salvar no banco ({str(e)})"
                            add_error(errors, msg)
                            rpa_error(f"[CARTEIRA] {msg}", exc=e, regiao="carteira")
                            log_error(M.CARTEIRA, "Commit aba", msg)
                            continue

                    if total_valid_rows == 0:
                        return json_error(
                            'Nenhum item válido encontrado nas abas do Excel. Verifique se existe uma coluna "REFERÊNCIA E COR" ou "SKU" em pelo menos uma aba.',
                            status=400,
                            errors=errors,
                            error_code="no_valid_rows"
                        )
                else:
                    if aba_selecionada and aba_selecionada in xl.sheet_names:
                        sheet_name = aba_selecionada
                    else:
                        sheet_name = xl.sheet_names[0]
                        aba_selecionada = sheet_name

                    try:
                        df = pd.read_excel(xl, sheet_name=sheet_name)
                    except Exception as e:
                        msg = f"Aba '{sheet_name}': erro ao ler ({str(e)})"
                        rpa_error(f"[CARTEIRA] {msg}", exc=e, regiao="carteira")
                        log_error(M.CARTEIRA, "Leitura aba", msg)
                        return json_error(
                            'Erro ao ler a aba selecionada.',
                            status=400,
                            errors=[msg],
                            error_code="sheet_read_error"
                        )

                    df_normalizado, sku_encontrado = normalizar_carteira_dataframe(df)

                    if not sku_encontrado:
                        return json_error(
                            f'Coluna de SKU não encontrada na aba "{aba_selecionada}". Verifique se existe uma coluna chamada "REFERÊNCIA E COR", "SKU" ou "CODIGO".',
                            status=400,
                            error_code="missing_sku_column"
                        )

                    try:
                        created_count, invalidos, linha_erros, valid_rows, updated_count = processar_linhas_carteira(
                            df_normalizado, lote_id, aba_selecionada, contadores, cache_produtos, tipo_carteira, marca_do_arquivo
                        )
                        total_created = created_count
                        total_invalidos = invalidos
                        total_valid_rows = valid_rows
                        total_updated = updated_count
                        errors.extend(linha_erros)
                        abas_processadas.append(aba_selecionada)
                        carteira_log.import_progress(created_count, created_count, aba_selecionada)
                        info(
                            M.CARTEIRA,
                            "Resumo aba",
                            f"sheet={aba_selecionada} linhas={len(df)} validos={valid_rows} invalidos={invalidos} criados={created_count} atualizados={updated_count}"
                        )
                    except Exception as e:
                        db.session.rollback()
                        msg = f"Aba '{aba_selecionada}': erro ao processar ({str(e)})"
                        rpa_error(f"[CARTEIRA] {msg}", exc=e, regiao="carteira")
                        log_error(M.CARTEIRA, "Processamento aba", msg)
                        return json_error(
                            'Erro ao processar a aba selecionada.',
                            status=500,
                            errors=[msg],
                            error_code="sheet_process_error"
                        )

                    try:
                        db.session.commit()
                    except Exception as e:
                        db.session.rollback()
                        rpa_error(f"[CARTEIRA] Erro ao salvar importação: {str(e)}", exc=e, regiao="carteira")
                        log_error(M.CARTEIRA, "Commit importação", str(e))
                        return json_error(
                            'Erro ao salvar a importação no banco.',
                            status=500,
                            errors=[str(e)],
                            error_code="db_commit_error"
                        )
            
            if len(abas_processadas) > 1:
                flash(f'Importação concluída! {total_created} novos itens de {len(abas_processadas)} abas adicionados. Abas: {", ".join(abas_processadas)}. Lote: {lote_id}', 'success')
            else:
                flash(f'Importação concluída! {total_created} novos itens da aba "{abas_processadas[0]}" adicionados. Lote: {lote_id}', 'success')

            entidades_criadas = []
            if contadores['colecoes_criadas'] > 0:
                entidades_criadas.append(f"{contadores['colecoes_criadas']} coleção(ões)")
            if contadores.get('subcolecoes_criadas', 0) > 0:
                entidades_criadas.append(f"{contadores['subcolecoes_criadas']} subcoleção(ões)/campanha(s)")
            if contadores['marcas_criadas'] > 0:
                entidades_criadas.append(f"{contadores['marcas_criadas']} marca(s)")
            if contadores['produtos_criados'] > 0:
                entidades_criadas.append(f"{contadores['produtos_criados']} produto(s)")

            if entidades_criadas:
                flash(f'Criados automaticamente: {", ".join(entidades_criadas)}', 'info')

            if total_invalidos > 0:
                flash(f'{total_invalidos} linhas ignoradas (SKU vazio ou inválido).', 'warning')
            if errors:
                flash(f'Importação concluída com {len(errors)} aviso(s). Verifique o arquivo para corrigir possíveis problemas.', 'warning')
            if total_updated > 0:
                flash(f'{total_updated} itens já existiam e foram atualizados.', 'info')

            atualizar_status_carteira()
            
            total_validos = total_created + total_updated
            carteira_log.import_completed(total_validos, len(abas_processadas), lote_id)
            rpa_info(f"[CARTEIRA] Importação concluída: {total_validos} itens, {len(abas_processadas)} abas - Lote: {lote_id}")

            if is_sharepoint_backend():
                try:
                    rpa_info(f"[CROSS] Auto-cross iniciado | lote={lote_id} | auto=True")
                    sync_result = run_sharepoint_cross_for_batch(lote_id, auto=True)
                    rpa_info(
                        "[CROSS] Auto-cross finalizado "
                        f"| lote={lote_id} | com_foto={sync_result.get('matched', 0)} "
                        f"| sem_foto={max(0, total_validos - sync_result.get('matched', 0))}"
                    )
                except Exception as e:
                    rpa_error(f"[CROSS] Erro no auto-cross do lote {lote_id}: {str(e)}", exc=e, regiao="carteira")
            
            info(
                M.CARTEIRA,
                "Importação carteira - resumo",
                f"lote={lote_id} total_validos={total_validos} criados={total_created} atualizados={total_updated} "
                f"invalidos={total_invalidos} abas={abas_processadas}"
            )
            return jsonify({
                'success': True,
                'message': f'{total_validos} itens processados',
                'total_linhas': total_valid_rows + total_invalidos,
                'linhas_validas': total_valid_rows,
                'linhas_importadas': total_validos,
                'linhas_existentes': total_updated,
                'erros': errors,
                'entidades_criadas': entidades_criadas,
                'abas_processadas': abas_processadas,
                'lote_id': lote_id
            }), 200
            
        except Exception as e:
            import traceback, uuid
            debug_id = str(uuid.uuid4())[:8]
            db.session.rollback()
            carteira_log.import_error(str(e))
            log_error(M.CARTEIRA, "Importação traceback", traceback.format_exc(), debug_id=debug_id)
            rpa_error(f"[CARTEIRA] Erro na importação (debug_id={debug_id}): {str(e)}", exc=e, regiao="carteira")
            log_error(M.CARTEIRA, "Importação", str(e))
            return json_error(
                f"Erro interno ao importar carteira. ID: {debug_id}",
                status=500,
                errors=[str(e)],
                debug_id=debug_id,
                error_code="carteira_internal_error"
            )
    
    return render_template('carteira/importar.html')

@app.route('/carteira/abas', methods=['POST'])
@login_required
def listar_abas_excel():
    """Retorna lista de abas de um arquivo Excel para seleção"""
    import pandas as pd
    if 'arquivo' not in request.files:
        return {'error': 'Nenhum arquivo enviado'}, 400
    
    file = request.files['arquivo']
    if not file.filename.lower().endswith(('.xlsx', '.xls')):
        return {'error': 'Apenas arquivos Excel'}, 400
    
    try:
        xl = pd.ExcelFile(file)
        abas = []
        total_linhas = 0
        for sheet_name in xl.sheet_names:
            df = pd.read_excel(xl, sheet_name=sheet_name)
            linhas = len(df)
            total_linhas += linhas
            
            df_norm, tem_sku = normalizar_carteira_dataframe(df)
            abas.append({
                'nome': sheet_name, 
                'linhas': linhas,
                'tem_sku': tem_sku
            })
        return {'abas': abas, 'total_linhas': total_linhas}
    except Exception as e:
        return {'error': str(e)}, 500

@app.route('/carteira/cruzar')
@login_required
def cruzar_carteira():
    rpa_info(f"[NAV] Cruzamento Carteira - Usuário: {current_user.username}")
    """Executa cruzamento entre carteira e produtos/imagens"""
    if is_sharepoint_backend():
        lote_id = request.args.get('lote')
        if not lote_id:
            latest = db.session.query(
                CarteiraCompras.lote_importacao
            ).filter(
                CarteiraCompras.lote_importacao.isnot(None)
            ).order_by(
                db.desc(CarteiraCompras.data_importacao)
            ).first()
            lote_id = latest[0] if latest else None
        if lote_id:
            result = run_sharepoint_cross_for_batch(lote_id, force_update=True, auto=False)
            flash(
                "Cruzamento concluído! "
                f"{result.get('matched', 0)} SKUs com foto, "
                f"{result.get('created', 0)} imagens criadas."
            )
            return redirect(url_for('carteira', lote=lote_id))
        flash('Nenhum lote disponível para cruzamento.', 'warning')
        return redirect(url_for('carteira'))

    count = atualizar_status_carteira()
    flash(f'Cruzamento concluído! {count} itens atualizados.')
    return redirect(url_for('carteira'))


@app.route('/carteira/<lote_id>/cross-sharepoint', methods=['POST'])
@login_required
def cross_sharepoint_lote(lote_id):
    if not is_sharepoint_backend():
        flash('Cruzamento SharePoint indisponível para este backend.', 'warning')
        return redirect(url_for('carteira', lote=lote_id))

    rpa_info(f"[CARTEIRA] Executando cruzamento SharePoint para lote {lote_id}")
    result = run_sharepoint_cross_for_batch(lote_id, force_update=True, auto=False)
    flash(
        "Cruzamento concluído! "
        f"{result.get('matched', 0)} SKUs com foto, "
        f"{result.get('created', 0)} imagens criadas."
    )
    return redirect(url_for('carteira', lote=lote_id))

def atualizar_status_carteira():
    """Atualiza status de fotos na carteira com base em produtos e imagens"""
    count = 0
    itens = CarteiraCompras.query.all()
    
    for item in itens:
        # Buscar produto pelo SKU
        produto = Produto.query.filter_by(sku=item.sku).first()
        
        if produto:
            item.produto_id = produto.id
            if produto.tem_foto:
                item.status_foto = 'Com Foto'
            else:
                item.status_foto = 'Sem Foto'
            count += 1
        else:
            # Buscar imagem diretamente pelo SKU
            imagem = Image.query.filter_by(sku=item.sku).first()
            if imagem:
                item.status_foto = 'Com Foto'
                count += 1
            else:
                item.status_foto = 'Sem Foto'
    
    db.session.commit()
    return count

@app.route('/carteira/export')
@login_required
def export_carteira():
    rpa_info(f"[EXPORT] Exportando carteira - Usuário: {current_user.username}")
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['SKU', 'Descrição', 'Cor', 'Categoria', 'Quantidade', 'Status Foto', 'Data Importação', 'Lote'])
    
    itens = CarteiraCompras.query.order_by(CarteiraCompras.data_importacao.desc()).all()
    for item in itens:
        writer.writerow([
            item.sku,
            item.descricao or '',
            item.cor or '',
            item.categoria or '',
            item.quantidade,
            item.status_foto,
            item.data_importacao.strftime('%Y-%m-%d %H:%M') if item.data_importacao else '',
            item.lote_importacao or ''
        ])
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = 'attachment; filename=carteira_compras.csv'
    response.headers['Content-type'] = 'text/csv; charset=utf-8'
    return response

# ==================== RELATÓRIOS DE AUDITORIA ====================

@app.route('/auditoria')
@login_required
def auditoria():
    rpa_info(f"[NAV] Auditoria - Usuário: {current_user.username}")
    # SKUs com foto vs sem foto
    produtos_com_foto = Produto.query.filter_by(tem_foto=True, ativo=True).count()
    produtos_sem_foto = Produto.query.filter_by(tem_foto=False, ativo=True).count()
    total_produtos = Produto.query.filter_by(ativo=True).count()
    
    # Carteira
    carteira_total = CarteiraCompras.query.count()
    carteira_com_foto = CarteiraCompras.query.filter_by(status_foto='Com Foto').count()
    carteira_sem_foto = CarteiraCompras.query.filter_by(status_foto='Sem Foto').count()
    carteira_pendente = CarteiraCompras.query.filter_by(status_foto='Pendente').count()
    
    # Histórico de alterações de SKU
    alteracoes_sku = HistoricoSKU.query.order_by(HistoricoSKU.data_alteracao.desc()).limit(20).all()
    total_alteracoes = HistoricoSKU.query.count()
    
    # Divergências: SKUs na carteira que mudaram
    divergencias = []
    for hist in HistoricoSKU.query.all():
        carteira_item = CarteiraCompras.query.filter_by(sku=hist.sku_antigo).first()
        if carteira_item:
            divergencias.append({
                'sku_antigo': hist.sku_antigo,
                'sku_novo': hist.sku_novo,
                'data': hist.data_alteracao,
                'produto_id': hist.produto_id,
                'carteira_id': carteira_item.id
            })
    
    # Lista de SKUs pendentes (sem foto)
    skus_pendentes = Produto.query.filter_by(tem_foto=False, ativo=True).order_by(Produto.sku).limit(50).all()
    
    return render_template('auditoria/index.html',
                          produtos_com_foto=produtos_com_foto,
                          produtos_sem_foto=produtos_sem_foto,
                          total_produtos=total_produtos,
                          carteira_total=carteira_total,
                          carteira_com_foto=carteira_com_foto,
                          carteira_sem_foto=carteira_sem_foto,
                          carteira_pendente=carteira_pendente,
                          alteracoes_sku=alteracoes_sku,
                          total_alteracoes=total_alteracoes,
                          divergencias=divergencias,
                          skus_pendentes=skus_pendentes)

@app.route('/auditoria/historico-sku')
@login_required
def historico_sku():
    rpa_info(f"[NAV] Histórico SKU - Usuário: {current_user.username}")
    historico = HistoricoSKU.query.order_by(HistoricoSKU.data_alteracao.desc()).all()
    return render_template('auditoria/historico_sku.html', historico=historico)

@app.route('/auditoria/skus-pendentes')
@login_required
def skus_pendentes():
    rpa_info(f"[NAV] SKUs Pendentes - Usuário: {current_user.username}")
    produtos = Produto.query.filter_by(tem_foto=False, ativo=True).order_by(Produto.sku).all()
    return render_template('auditoria/skus_pendentes.html', produtos=produtos)

@app.route('/auditoria/export/<tipo>')
@login_required
def export_auditoria(tipo):
    rpa_info(f"[EXPORT] Exportando auditoria: {tipo} - Usuário: {current_user.username}")
    output = io.StringIO()
    writer = csv.writer(output)
    
    if tipo == 'skus_com_foto':
        writer.writerow(['SKU', 'Descrição', 'Cor', 'Categoria', 'Marca', 'Coleção'])
        produtos = Produto.query.filter_by(tem_foto=True, ativo=True).all()
        for p in produtos:
            writer.writerow([
                p.sku, p.descricao, p.cor or '', p.categoria or '',
                p.marca.name if p.marca else '',
                p.colecao.name if p.colecao else ''
            ])
        filename = 'skus_com_foto.csv'
    
    elif tipo == 'skus_sem_foto':
        writer.writerow(['SKU', 'Descrição', 'Cor', 'Categoria', 'Marca', 'Coleção'])
        produtos = Produto.query.filter_by(tem_foto=False, ativo=True).all()
        for p in produtos:
            writer.writerow([
                p.sku, p.descricao, p.cor or '', p.categoria or '',
                p.marca.name if p.marca else '',
                p.colecao.name if p.colecao else ''
            ])
        filename = 'skus_sem_foto.csv'
    
    elif tipo == 'historico_sku':
        writer.writerow(['SKU Antigo', 'SKU Novo', 'Data Alteração', 'Motivo', 'Usuário'])
        historico = HistoricoSKU.query.order_by(HistoricoSKU.data_alteracao.desc()).all()
        for h in historico:
            writer.writerow([
                h.sku_antigo, h.sku_novo,
                h.data_alteracao.strftime('%Y-%m-%d %H:%M'),
                h.motivo or '',
                h.usuario.username if h.usuario else ''
            ])
        filename = 'historico_alteracoes_sku.csv'
    
    elif tipo == 'divergencias':
        writer.writerow(['SKU Antigo (Carteira)', 'SKU Novo (Produto)', 'Data Alteração', 'Status'])
        for hist in HistoricoSKU.query.all():
            carteira_item = CarteiraCompras.query.filter_by(sku=hist.sku_antigo).first()
            if carteira_item:
                writer.writerow([
                    hist.sku_antigo, hist.sku_novo,
                    hist.data_alteracao.strftime('%Y-%m-%d %H:%M'),
                    'Divergência Detectada'
                ])
        filename = 'divergencias_sku.csv'
    
    else:
        return redirect(url_for('auditoria'))
    
    output.seek(0)
    response = make_response(output.getvalue())
    response.headers['Content-Disposition'] = f'attachment; filename={filename}'
    response.headers['Content-type'] = 'text/csv; charset=utf-8'
    return response

# ==================== BATCH UPLOAD (UPLOAD EM LOTE) ====================

import threading
import tempfile
from batch_processor import BatchProcessor, extract_zip_to_temp, extract_sku_from_filename

batch_processor_instance = None

def get_batch_processor():
    global batch_processor_instance
    if batch_processor_instance is None:
        from object_storage import object_storage
        batch_processor_instance = BatchProcessor(app, db, object_storage, analyze_func=None)
    return batch_processor_instance

@app.route('/batch')
@login_required
def batch_list():
    """Lista todos os lotes de upload"""
    nav_log.page_enter("Batches", user=current_user.username)
    rpa_info(f"[NAV] Batches - Usuário: {current_user.username}")
    batches = BatchUpload.query.filter_by(usuario_id=current_user.id).order_by(BatchUpload.created_at.desc()).all()
    return render_template('batch/index.html', batches=batches)

@app.route('/batch/queue')
@login_required
def batch_queue():
    """Página para criar múltiplos batches e processar em fila"""
    nav_log.page_enter("Fila de Batches", user=current_user.username)
    rpa_info(f"[NAV] Fila de Batches - Usuário: {current_user.username}")
    collections = Collection.query.order_by(Collection.name).all()
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('batch/queue.html', collections=collections, brands=brands)

@app.route('/batch/process-all', methods=['POST'])
@login_required
def batch_process_all():
    """Processa múltiplos batches de uma vez"""
    data = request.get_json()
    batch_ids = data.get('batch_ids', [])
    
    if not batch_ids:
        rpa_warn("[BATCH] Nenhum batch especificado para processamento")
        return jsonify({'error': 'Nenhum batch especificado'}), 400
    
    log_start(M.BATCH, f"Processando todos os batches: {len(batch_ids)} lotes")
    rpa_info(f"[BATCH] Iniciando processamento de {len(batch_ids)} lotes em sequência")
    
    batches_to_process = []
    for batch_id in batch_ids:
        batch = BatchUpload.query.get(batch_id)
        if batch and batch.usuario_id == current_user.id:
            pending_items = BatchItem.query.filter_by(
                batch_id=batch_id, 
                processing_status='pending'
            ).count()
            if pending_items > 0:
                batch.status = 'Processando'
                batches_to_process.append(batch_id)
    
    db.session.commit()
    
    if batches_to_process:
        processor = get_batch_processor()
        thread = threading.Thread(
            target=processor.process_multiple_batches,
            args=(batches_to_process,)
        )
        thread.daemon = True
        thread.start()
    
    log_end(M.BATCH, "Processamento de múltiplos batches")
    rpa_info("[BATCH] Processamento de múltiplos lotes concluído")
    
    return jsonify({
        'success': True,
        'batches_started': len(batches_to_process),
        'batch_ids': batches_to_process
    })

@app.route('/batch/new', methods=['GET', 'POST'])
@login_required
def batch_new():
    """Criar novo lote de upload"""
    rpa_info(f"[NAV] Novo Batch - Usuário: {current_user.username}")
    if request.method == 'POST':
        files = request.files.getlist('files')
        zip_file = request.files.get('zip_file')
        collection_id = request.form.get('collection_id')
        brand_id = request.form.get('brand_id')
        batch_name = request.form.get('batch_name', f"Lote {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        
        log_start(M.BATCH, f"Criando novo batch: {batch_name}", arquivos=len(files) if files else 0, zip=bool(zip_file))
        rpa_info(f"[BATCH] Criando batch: {batch_name} - {len(files) if files else 0} arquivos")
        
        temp_dir = tempfile.mkdtemp(prefix='batch_upload_')
        temp_file_paths = []
        
        try:
            if zip_file and zip_file.filename:
                temp_zip_path = os.path.join(temp_dir, 'upload.zip')
                zip_file.save(temp_zip_path)
                temp_file_paths = extract_zip_to_temp(temp_zip_path, temp_dir)
                os.remove(temp_zip_path)
            
            elif files:
                for i, file in enumerate(files):
                    if file and file.filename:
                        ext = os.path.splitext(file.filename)[1].lower()
                        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                            sku = extract_sku_from_filename(file.filename)
                            if sku:
                                temp_filename = f"upload_{i}_{sku}{ext}"
                                temp_path = os.path.join(temp_dir, temp_filename)
                                file.save(temp_path)
                                temp_file_paths.append({
                                    'sku': sku,
                                    'temp_path': temp_path,
                                    'filename': file.filename
                                })
            
            if not temp_file_paths:
                flash('Nenhum arquivo válido encontrado. Envie imagens ou um arquivo ZIP.')
                return redirect(request.url)
            
            batch = BatchUpload(
                nome=batch_name,
                total_arquivos=len(temp_file_paths),
                usuario_id=current_user.id,
                colecao_id=int(collection_id) if collection_id else None,
                marca_id=int(brand_id) if brand_id else None,
                status='Pendente'
            )
            db.session.add(batch)
            db.session.flush()
            
            for i, file_info in enumerate(temp_file_paths):
                item = BatchItem(
                    batch_id=batch.id,
                    sku=file_info['sku'],
                    filename_original=file_info['filename'],
                    status='Pendente'
                )
                db.session.add(item)
                db.session.flush()
                file_info['item_id'] = item.id
            
            db.session.commit()
            
            processor = get_batch_processor()
            thread = threading.Thread(
                target=processor.process_batch,
                args=(batch.id, temp_file_paths)
            )
            thread.daemon = True
            thread.start()
            
            flash(f'Lote criado com {len(temp_file_paths)} arquivos. Processamento iniciado.')
            return redirect(url_for('batch_detail', batch_id=batch.id))
            
        except Exception as e:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)
            flash(f'Erro ao processar arquivos: {str(e)}')
            return redirect(request.url)
    
    collections = Collection.query.order_by(Collection.name).all()
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('batch/new.html', collections=collections, brands=brands)

@app.route('/batch/create-async', methods=['POST'])
@login_required
def batch_create_async():
    """Cria um lote vazio para upload assíncrono de múltiplos arquivos"""
    data = request.get_json()
    
    batch_name = data.get('batch_name', f"Lote {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    collection_id = data.get('collection_id')
    brand_id = data.get('brand_id')
    total_files = data.get('total_files', 0)
    
    batch = BatchUpload(
        nome=batch_name,
        total_arquivos=total_files,
        usuario_id=current_user.id,
        colecao_id=int(collection_id) if collection_id else None,
        marca_id=int(brand_id) if brand_id else None,
        status='Recebendo'
    )
    db.session.add(batch)
    db.session.commit()
    
    batch_log.batch_created(batch.id, batch_name, total_files)
    rpa_info(f"[BATCH] Novo lote criado: #{batch.id} '{batch_name}' - {total_files} arquivos")
    
    return jsonify({
        'batch_id': batch.id,
        'status': 'created'
    })

@app.route('/batch/upload-file', methods=['POST'])
@login_required
def batch_upload_file():
    """Upload de um único arquivo direto para Object Storage (sem usar /tmp)"""
    from object_storage import object_storage as storage_service
    
    batch_id = request.form.get('batch_id')
    file = request.files.get('file')
    
    if not batch_id or not file:
        return jsonify({'error': 'Missing batch_id or file'}), 400
    
    batch = BatchUpload.query.get(batch_id)
    if not batch:
        return jsonify({'error': 'Batch not found'}), 404
    
    file_size = request.content_length or 0
    upload_log.upload_started(batch_id, file.filename, file_size)
    
    try:
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
            return jsonify({'error': 'Invalid file type'}), 400
        
        sku = extract_sku_from_filename(file.filename)
        if not sku:
            sku = os.path.splitext(file.filename)[0]
        
        file_bytes = file.read()
        file_size_actual = len(file_bytes)
        
        result = storage_service.upload_file(io.BytesIO(file_bytes), file.filename)
        storage_path = result['object_name']
        
        item = BatchItem(
            batch_id=batch_id,
            sku=sku,
            filename_original=file.filename,
            status='Pendente',
            reception_status='received',
            processing_status='pending',
            received_path=storage_path,
            file_size=file_size_actual
        )
        db.session.add(item)
        db.session.commit()
        
        debug(M.UPLOAD, 'SUCCESS', f"Arquivo enviado ao Object Storage", sku=sku, path=storage_path)
        rpa_info(f"[UPLOAD] OK: {sku} ({file_size_actual//1024}KB)")
        
        return jsonify({
            'success': True,
            'item_id': item.id,
            'sku': sku,
            'storage_path': storage_path
        })
        
    except Exception as e:
        db.session.rollback()
        upload_log.upload_error(batch_id, file.filename if file else 'unknown', str(e))
        rpa_error(f"[UPLOAD] Erro no upload: {file.filename if file else 'unknown'} - {str(e)}", exc=e, regiao="upload")
        return jsonify({'error': str(e)}), 500

@app.route('/batch/<int:batch_id>/sync-total', methods=['POST'])
@login_required
def batch_sync_total(batch_id):
    """Sincroniza total_arquivos com o número real de BatchItems recebidos"""
    batch = BatchUpload.query.get_or_404(batch_id)
    
    actual_count = BatchItem.query.filter_by(batch_id=batch_id).count()
    batch.total_arquivos = actual_count
    db.session.commit()
    
    return jsonify({
        'success': True,
        'batch_id': batch_id,
        'total_arquivos': actual_count
    })

@app.route('/batch/<int:batch_id>/delete', methods=['DELETE'])
@login_required
def batch_delete(batch_id):
    """Exclui um lote e todos os seus itens"""
    log_start(M.BATCH, f"Excluindo batch #{batch_id}")
    rpa_info(f"[BATCH] Iniciando exclusão do batch #{batch_id}")
    
    batch = BatchUpload.query.get_or_404(batch_id)
    
    if batch.usuario_id != current_user.id and not current_user.is_admin:
        return jsonify({'error': 'Sem permissão para excluir este lote'}), 403
    
    try:
        items = BatchItem.query.filter_by(batch_id=batch_id).all()
        for item in items:
            if item.received_path and os.path.exists(item.received_path):
                try:
                    os.remove(item.received_path)
                except:
                    pass
        
        BatchItem.query.filter_by(batch_id=batch_id).delete()
        db.session.delete(batch)
        db.session.commit()
        
        log_end(M.BATCH, f"Batch #{batch_id} excluído")
        rpa_info(f"[BATCH] Lote #{batch_id} excluído com sucesso")
        
        return jsonify({'success': True, 'message': 'Lote excluído com sucesso'})
        
    except Exception as e:
        db.session.rollback()
        batch_log.batch_error(batch_id, str(e))
        rpa_error(f"[BATCH] Erro ao excluir batch #{batch_id}: {str(e)}", exc=e, regiao="batch")
        return jsonify({'error': str(e)}), 500

@app.route('/batch/<int:batch_id>')
@login_required
def batch_detail(batch_id):
    """Detalhes de um lote de upload"""
    batch = BatchUpload.query.get_or_404(batch_id)
    rpa_info(f"[NAV] Detalhe Batch #{batch_id} - Usuário: {current_user.username}")
    
    page = request.args.get('page', 1, type=int)
    status_filter = request.args.get('status', '')
    
    query = BatchItem.query.filter_by(batch_id=batch_id)
    if status_filter:
        query = query.filter_by(status=status_filter)
    
    items = query.order_by(BatchItem.id).paginate(page=page, per_page=50, error_out=False)
    
    return render_template('batch/detail.html', batch=batch, items=items, status_filter=status_filter)

@app.route('/batch/<int:batch_id>/status')
@login_required
def batch_status(batch_id):
    """API para obter status detalhado do lote (polling) - inclui fases de recepção e processamento"""
    batch = BatchUpload.query.get_or_404(batch_id)
    
    reception_pending = BatchItem.query.filter_by(batch_id=batch_id, reception_status='pending').count()
    reception_receiving = BatchItem.query.filter_by(batch_id=batch_id, reception_status='receiving').count()
    reception_received = BatchItem.query.filter_by(batch_id=batch_id, reception_status='received').count()
    
    processing_pending = BatchItem.query.filter_by(batch_id=batch_id, processing_status='pending').count()
    processing_active = BatchItem.query.filter_by(batch_id=batch_id, processing_status='processing').count()
    processing_completed = BatchItem.query.filter_by(batch_id=batch_id, processing_status='completed').count()
    processing_failed = BatchItem.query.filter_by(batch_id=batch_id, processing_status='failed').count()
    processing_retry = BatchItem.query.filter_by(batch_id=batch_id, processing_status='retry').count()
    
    return {
        'id': batch.id,
        'status': batch.status,
        'total': batch.total_arquivos,
        'processados': batch.processados,
        'sucesso': batch.sucesso,
        'falhas': batch.falhas,
        'progresso': batch.progresso,
        'fase1_recepcao': {
            'pendente': reception_pending,
            'recebendo': reception_receiving,
            'recebido': reception_received
        },
        'fase2_processamento': {
            'pendente': processing_pending,
            'processando': processing_active,
            'concluido': processing_completed,
            'falha': processing_failed,
            'retry': processing_retry
        },
        'resiliente': True,
        'pode_retomar': processing_pending + processing_retry > 0
    }

@app.route('/batch/streaming-upload', methods=['POST'])
@login_required
def batch_streaming_upload():
    """
    Endpoint de recepção com upload DIRETO para Object Storage (bucket).
    Garante que a imagem é persistida IMEDIATAMENTE, antes de qualquer processamento.
    Se o processamento falhar depois, a imagem já está segura no bucket.
    
    DETECÇÃO DE DUPLICATAS: Usa hash SHA256 para identificar imagens já upadas.
    SKUs iguais com hashes diferentes são imagens diferentes (ângulos) e serão upadas.
    """
    import hashlib
    
    log_start(M.UPLOAD, "Streaming upload iniciado - UPLOAD DIRETO AO BUCKET")
    rpa_info("[UPLOAD] Streaming upload iniciado - DIRETO AO BUCKET")
    
    batch_id = request.form.get('batch_id')
    collection_id = request.form.get('collection_id')
    brand_id = request.form.get('brand_id')
    batch_name = request.form.get('batch_name', f"Lote {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    if not batch_id:
        batch = BatchUpload(
            nome=batch_name,
            total_arquivos=0,
            usuario_id=current_user.id,
            colecao_id=int(collection_id) if collection_id else None,
            marca_id=int(brand_id) if brand_id else None,
            status='Recebendo'
        )
        db.session.add(batch)
        db.session.commit()
        batch_id = batch.id
    else:
        batch = BatchUpload.query.get(int(batch_id))
        if not batch:
            return {'error': 'Batch não encontrado'}, 404
    
    existing_hashes = set(
        row[0] for row in db.session.query(BatchItem.file_hash)
        .filter(BatchItem.file_hash.isnot(None))
        .filter(BatchItem.processing_status != 'orphaned')
        .all()
    )
    debug(M.UPLOAD, 'HASH_CACHE', f"Carregados {len(existing_hashes)} hashes existentes para verificação de duplicatas")
    
    received_files = []
    upload_errors = []
    skipped_files = []
    
    for key in request.files:
        file = request.files[key]
        if file and file.filename:
            original_filename = file.filename
            ext = os.path.splitext(original_filename)[1].lower()
            
            if ext not in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
                continue
            
            sku = extract_sku_from_filename(original_filename)
            if not sku:
                continue
            
            try:
                file_bytes = file.read()
                
                hasher = hashlib.sha256()
                hasher.update(file_bytes)
                file_hash = hasher.hexdigest()
                
                if file_hash in existing_hashes:
                    skipped_files.append({
                        'sku': sku,
                        'filename': original_filename,
                        'reason': 'duplicado',
                        'hash': file_hash[:16]
                    })
                    debug(M.UPLOAD, 'SKIP_DUPLICATE', f"Pulando {original_filename} - já existe (hash: {file_hash[:16]})")
                    del file_bytes
                    continue
                
                existing_hashes.add(file_hash)
                
                upload_result = object_storage.upload_bytes_immediate(
                    file_bytes=file_bytes,
                    original_filename=original_filename,
                    sku=sku,
                    batch_id=batch_id
                )
                
                item = BatchItem(
                    batch_id=batch_id,
                    sku=sku,
                    filename_original=original_filename,
                    file_size=upload_result['file_size'],
                    file_hash=file_hash,
                    storage_path=upload_result['storage_path'],
                    received_path=upload_result['object_name'],
                    reception_status='uploaded',
                    received_at=datetime.utcnow(),
                    processing_status='pending',
                    status='Pendente'
                )
                db.session.add(item)
                db.session.flush()
                
                received_files.append({
                    'item_id': item.id,
                    'sku': sku,
                    'filename': original_filename,
                    'size': upload_result['file_size'],
                    'hash': file_hash[:16],
                    'storage_path': upload_result['storage_path']
                })
                
                del file_bytes
                
            except Exception as e:
                error_msg = str(e)
                upload_errors.append({
                    'sku': sku,
                    'filename': original_filename,
                    'error': error_msg
                })
                error(M.UPLOAD, 'BUCKET_UPLOAD', f"Erro ao enviar {sku} para bucket: {error_msg}")
                rpa_err(f"[UPLOAD] Erro bucket: {sku} - {error_msg}", regiao="upload")
    
    batch.total_arquivos = BatchItem.query.filter_by(batch_id=batch_id).count()
    db.session.commit()
    
    log_end(M.UPLOAD, f"Upload concluído: {len(received_files)} novos, {len(skipped_files)} duplicados pulados, {len(upload_errors)} erros")
    
    return {
        'batch_id': batch_id,
        'received_count': len(received_files),
        'skipped_count': len(skipped_files),
        'skipped_files': skipped_files,
        'total_no_lote': batch.total_arquivos,
        'files': received_files,
        'errors': upload_errors,
        'bucket_upload': True
    }

@app.route('/batch/<int:batch_id>/start-processing', methods=['POST'])
@login_required
def batch_start_processing(batch_id):
    """Inicia o processamento de um lote - suporta imagens no bucket OU arquivos locais"""
    batch = BatchUpload.query.get_or_404(batch_id)
    
    if batch.status not in ['Recebendo', 'Pendente', 'Erro']:
        return {'error': f'Lote já está em status: {batch.status}'}, 400
    
    pending_items = BatchItem.query.filter_by(
        batch_id=batch_id,
        processing_status='pending'
    ).filter(
        db.or_(
            BatchItem.reception_status == 'uploaded',
            BatchItem.reception_status == 'received'
        )
    ).all()
    
    if not pending_items:
        return {'error': 'Nenhum item pendente para processar'}, 400
    
    batch_log.batch_started(batch_id, len(pending_items))
    rpa_info(f"[BATCH] Processamento do batch #{batch_id}: {len(pending_items)} itens pendentes")
    
    batch.status = 'Processando'
    batch.started_at = datetime.utcnow()
    db.session.commit()
    
    files_data = []
    for item in pending_items:
        if item.storage_path:
            object_name = item.storage_path.replace('/storage/', '') if item.storage_path.startswith('/storage/') else item.storage_path
            files_data.append({
                'item_id': item.id,
                'sku': item.sku,
                'storage_path': item.storage_path,
                'object_name': object_name,
                'filename': item.filename_original,
                'source': 'bucket'
            })
        elif item.received_path and os.path.exists(item.received_path):
            files_data.append({
                'item_id': item.id,
                'sku': item.sku,
                'temp_path': item.received_path,
                'filename': item.filename_original,
                'source': 'local'
            })
    
    if files_data:
        processor = get_batch_processor()
        thread = threading.Thread(
            target=processor.process_batch_from_bucket,
            args=(batch_id, files_data)
        )
        thread.daemon = True
        thread.start()
    
    return {
        'batch_id': batch_id,
        'status': 'Processando',
        'items_to_process': len(files_data),
        'from_bucket': sum(1 for f in files_data if f.get('source') == 'bucket'),
        'from_local': sum(1 for f in files_data if f.get('source') == 'local')
    }

@app.route('/batch/<int:batch_id>/resume', methods=['POST'])
@login_required
def batch_resume(batch_id):
    """Retoma processamento - suporta bucket OU arquivos locais"""
    rpa_info(f"[BATCH] Retomando processamento do batch #{batch_id}")
    batch = BatchUpload.query.get_or_404(batch_id)
    
    pending_items = BatchItem.query.filter(
        BatchItem.batch_id == batch_id,
        BatchItem.processing_status.in_(['pending', 'retry'])
    ).all()
    
    if not pending_items:
        return {'error': 'Nenhum item pendente para retomar', 'status': batch.status}, 400
    
    files_data = []
    orphaned_count = 0
    for item in pending_items:
        if item.storage_path:
            object_name = item.storage_path.replace('/storage/', '') if item.storage_path.startswith('/storage/') else item.storage_path
            files_data.append({
                'item_id': item.id,
                'sku': item.sku,
                'storage_path': item.storage_path,
                'object_name': object_name,
                'filename': item.filename_original,
                'source': 'bucket'
            })
        elif item.received_path and os.path.exists(item.received_path):
            files_data.append({
                'item_id': item.id,
                'sku': item.sku,
                'temp_path': item.received_path,
                'filename': item.filename_original,
                'source': 'local'
            })
        else:
            item.processing_status = 'orphaned'
            item.status = 'Erro'
            item.erro_mensagem = 'Arquivo não encontrado (nem bucket nem local)'
            orphaned_count += 1
    
    if files_data:
        batch.status = 'Processando'
        db.session.commit()
        
        processor = get_batch_processor()
        thread = threading.Thread(
            target=processor.process_batch_from_bucket,
            args=(batch_id, files_data)
        )
        thread.daemon = True
        thread.start()
        
        return {
            'batch_id': batch_id,
            'status': 'Retomando',
            'items_to_process': len(files_data),
            'from_bucket': sum(1 for f in files_data if f.get('source') == 'bucket'),
            'from_local': sum(1 for f in files_data if f.get('source') == 'local'),
            'orphaned': orphaned_count
        }
    else:
        db.session.commit()
        return {'error': 'Nenhum arquivo encontrado para retomar', 'orphaned': orphaned_count, 'status': batch.status}, 400

@app.route('/batch/<int:batch_id>/reprocess', methods=['POST'])
@login_required
def batch_reprocess(batch_id):
    """Reprocessa itens que já estão no bucket mas falharam no processamento"""
    rpa_info(f"[BATCH] Reprocessando itens do batch #{batch_id} a partir do bucket")
    batch = BatchUpload.query.get_or_404(batch_id)
    
    failed_items = BatchItem.query.filter(
        BatchItem.batch_id == batch_id,
        BatchItem.storage_path.isnot(None),
        BatchItem.processing_status.in_(['failed', 'orphaned', 'pending'])
    ).all()
    
    if not failed_items:
        return {'error': 'Nenhum item com storage_path para reprocessar'}, 400
    
    files_data = []
    for item in failed_items:
        item.processing_status = 'pending'
        item.status = 'Pendente'
        item.tentativas = 0
        item.erro_mensagem = None
        
        object_name = item.storage_path.replace('/storage/', '') if item.storage_path.startswith('/storage/') else item.storage_path
        files_data.append({
            'item_id': item.id,
            'sku': item.sku,
            'storage_path': item.storage_path,
            'object_name': object_name,
            'filename': item.filename_original,
            'source': 'bucket'
        })
    
    batch.status = 'Processando'
    batch.started_at = datetime.utcnow()
    db.session.commit()
    
    processor = get_batch_processor()
    thread = threading.Thread(
        target=processor.process_batch_from_bucket,
        args=(batch_id, files_data)
    )
    thread.daemon = True
    thread.start()
    
    return {
        'batch_id': batch_id,
        'status': 'Reprocessando',
        'items_to_process': len(files_data)
    }

@app.route('/batch/<int:batch_id>/retry-failed', methods=['POST'])
@login_required
def batch_retry_failed(batch_id):
    rpa_info(f"[BATCH] Reprocessando itens falhos do batch #{batch_id}")
    """Reprocessar itens com falha"""
    batch = BatchUpload.query.get_or_404(batch_id)
    
    failed_items = BatchItem.query.filter_by(batch_id=batch_id, status='Erro').all()
    if not failed_items:
        flash('Não há itens com erro para reprocessar.')
        return redirect(url_for('batch_detail', batch_id=batch_id))
    
    batch.status = 'Processando'
    batch.processados -= len(failed_items)
    batch.falhas = 0
    db.session.commit()
    
    files_data = []
    for item in failed_items:
        item.status = 'Pendente'
        item.erro_mensagem = None
        item.tentativas = 0
        
        if item.storage_path:
            from object_storage import object_storage
            file_bytes = object_storage.download_file(item.storage_path.replace('/storage/', ''))
            if file_bytes:
                files_data.append({
                    'sku': item.sku,
                    'file_bytes': file_bytes,
                    'filename': item.filename_original,
                    'item_id': item.id
                })
    
    db.session.commit()
    
    if files_data:
        processor = get_batch_processor()
        thread = threading.Thread(
            target=processor.process_batch,
            args=(batch.id, files_data)
        )
        thread.daemon = True
        thread.start()
        flash(f'Reprocessando {len(files_data)} itens com falha.')
    
    return redirect(url_for('batch_detail', batch_id=batch_id))


@app.route('/analyze-pending-ai', methods=['GET', 'POST'])
@login_required
def analyze_pending_ai():
    """Página para analisar imagens pendentes de análise IA"""
    nav_log.page_enter("Análise IA Pendente", user=current_user.username)
    rpa_info(f"[NAV] Análise IA Pendente - Usuário: {current_user.username}")
    pending_images = Image.query.filter_by(status='Pendente Análise IA').order_by(Image.upload_date.desc()).all()
    debug(M.CATALOG, 'DATA', f"Imagens pendentes de IA carregadas", total=len(pending_images))
    rpa_info(f"[CATALOG] {len(pending_images)} imagens pendentes de IA")
    
    if request.method == 'POST':
        image_ids = request.form.getlist('image_ids')
        
        if not image_ids:
            flash('Selecione pelo menos uma imagem para analisar.')
            return redirect(url_for('analyze_pending_ai'))
        
        processed = 0
        errors = 0
        
        for img_id in image_ids[:10]:
            image = Image.query.get(int(img_id))
            if not image:
                continue
            
            temp_file_path = None
            file_path = None
            
            try:
                if image.storage_path:
                    from object_storage import object_storage
                    import tempfile
                    
                    file_bytes = object_storage.download_file(image.storage_path)
                    if file_bytes:
                        ext = os.path.splitext(image.filename)[1] or '.jpg'
                        fd, temp_file_path = tempfile.mkstemp(suffix=ext)
                        os.write(fd, file_bytes)
                        os.close(fd)
                        file_path = temp_file_path
                
                if not file_path:
                    file_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
                
                if not os.path.exists(file_path):
                    errors += 1
                    continue
                
                ai_result = analyze_image_with_context(
                    file_path, 
                    sku=image.sku,
                    collection_id=image.collection_id,
                    brand_id=image.brand_id,
                    subcolecao_id=image.subcolecao_id
                )
                
                if isinstance(ai_result, str):
                    errors += 1
                    continue
                
                ai_items = ai_result
                first_item = ai_items[0] if ai_items else {}
                first_attrs = first_item.get('attributes', {}) if first_item else {}
                
                image.description = first_item.get('description', '')
                image.tags = json.dumps(first_item.get('tags', [])) if first_item else json.dumps([])
                image.ai_item_type = first_attrs.get('item_type')
                image.ai_color = first_attrs.get('color')
                image.ai_material = first_attrs.get('material')
                image.ai_pattern = first_attrs.get('pattern')
                image.ai_style = first_attrs.get('style')
                image.status = 'Pendente'
                
                ImageItem.query.filter_by(image_id=image.id).delete()
                
                for item_data in ai_items:
                    attrs = item_data.get('attributes', {})
                    new_item = ImageItem(
                        image_id=image.id,
                        item_order=item_data.get('order', 1),
                        position_ref=item_data.get('position_ref', 'Peça Única'),
                        description=item_data.get('description', ''),
                        tags=json.dumps(item_data.get('tags', [])),
                        ai_item_type=attrs.get('item_type'),
                        ai_color=attrs.get('color'),
                        ai_material=attrs.get('material'),
                        ai_pattern=attrs.get('pattern'),
                        ai_style=attrs.get('style')
                    )
                    db.session.add(new_item)
                
                db.session.commit()
                processed += 1
                
            except Exception as e:
                print(f"[ERROR] Analysis failed for image {img_id}: {e}")
                errors += 1
            
            finally:
                if temp_file_path and os.path.exists(temp_file_path):
                    try:
                        os.remove(temp_file_path)
                    except Exception:
                        pass
        
        if processed > 0:
            flash(f'Análise concluída! {processed} imagens analisadas com sucesso.')
        if errors > 0:
            flash(f'{errors} imagens não puderam ser analisadas.', 'warning')
        
        return redirect(url_for('analyze_pending_ai'))
    
    return render_template('batch/analyze_pending.html', pending_images=pending_images)


# ==================== CHUNKED UPLOAD (UPLOAD DE ARQUIVOS GRANDES) ====================
import uuid
import hashlib

# Diretório para chunks temporários
CHUNK_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), 'oaz_chunks')
os.makedirs(CHUNK_UPLOAD_DIR, exist_ok=True)

# Armazenar informações de uploads em andamento
active_uploads = {}

@app.route('/upload/init', methods=['POST'])
@login_required
def upload_init():
    """Inicializa um upload chunked - retorna upload_id"""
    data = request.get_json()
    filename = data.get('filename', 'upload.zip')
    file_size = data.get('file_size', 0)
    chunk_size = data.get('chunk_size', 5 * 1024 * 1024)  # 5MB default
    
    upload_id = str(uuid.uuid4())
    upload_dir = os.path.join(CHUNK_UPLOAD_DIR, upload_id)
    os.makedirs(upload_dir, exist_ok=True)
    
    total_chunks = (file_size + chunk_size - 1) // chunk_size
    
    active_uploads[upload_id] = {
        'filename': filename,
        'file_size': file_size,
        'chunk_size': chunk_size,
        'total_chunks': total_chunks,
        'received_chunks': set(),
        'upload_dir': upload_dir,
        'user_id': current_user.id,
        'created_at': datetime.utcnow()
    }
    
    return {
        'upload_id': upload_id,
        'chunk_size': chunk_size,
        'total_chunks': total_chunks
    }

@app.route('/upload/chunk', methods=['POST'])
@login_required
def upload_chunk():
    """Recebe um chunk do arquivo"""
    upload_id = request.form.get('upload_id')
    chunk_index = int(request.form.get('chunk_index', 0))
    chunk_file = request.files.get('chunk')
    
    if not upload_id or upload_id not in active_uploads:
        return {'error': 'Upload inválido'}, 400
    
    upload_info = active_uploads[upload_id]
    
    if upload_info['user_id'] != current_user.id:
        return {'error': 'Não autorizado'}, 403
    
    if not chunk_file:
        return {'error': 'Chunk não recebido'}, 400
    
    # Salvar chunk
    chunk_path = os.path.join(upload_info['upload_dir'], f'chunk_{chunk_index:06d}')
    chunk_file.save(chunk_path)
    upload_info['received_chunks'].add(chunk_index)
    
    received = len(upload_info['received_chunks'])
    total = upload_info['total_chunks']
    
    return {
        'success': True,
        'chunk_index': chunk_index,
        'received': received,
        'total': total,
        'progress': round((received / total) * 100, 1)
    }

@app.route('/upload/complete', methods=['POST'])
@login_required
def upload_complete():
    """Finaliza o upload chunked e enfileira para processamento assíncrono"""
    data = request.get_json()
    upload_id = data.get('upload_id')
    collection_id = data.get('collection_id')
    brand_id = data.get('brand_id')
    batch_name = data.get('batch_name', f"Lote {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    if not upload_id or upload_id not in active_uploads:
        return {'error': 'Upload inválido'}, 400
    
    upload_info = active_uploads[upload_id]
    
    if upload_info['user_id'] != current_user.id:
        return {'error': 'Não autorizado'}, 403
    
    if len(upload_info['received_chunks']) != upload_info['total_chunks']:
        missing = upload_info['total_chunks'] - len(upload_info['received_chunks'])
        return {'error': f'Faltam {missing} partes do arquivo'}, 400
    
    import shutil
    temp_dir = None
    chunk_dir = upload_info.get('upload_dir')
    
    try:
        temp_dir = tempfile.mkdtemp(prefix='batch_final_')
        final_path = os.path.join(temp_dir, upload_info['filename'])
        
        with open(final_path, 'wb') as outfile:
            for i in range(upload_info['total_chunks']):
                chunk_path = os.path.join(chunk_dir, f'chunk_{i:06d}')
                with open(chunk_path, 'rb') as chunk:
                    outfile.write(chunk.read())
        
        shutil.rmtree(chunk_dir, ignore_errors=True)
        if upload_id in active_uploads:
            del active_uploads[upload_id]
        
        batch = BatchUpload(
            nome=batch_name,
            total_arquivos=0,
            usuario_id=current_user.id,
            colecao_id=int(collection_id) if collection_id else None,
            marca_id=int(brand_id) if brand_id else None,
            status='Na Fila'
        )
        db.session.add(batch)
        db.session.commit()
        
        from upload_orchestrator import get_upload_orchestrator
        from object_storage import object_storage
        orchestrator = get_upload_orchestrator(app, db, object_storage)
        
        orchestrator.enqueue(
            batch_id=batch.id,
            archive_path=final_path,
            temp_dir=temp_dir,
            metadata={
                'collection_id': collection_id,
                'brand_id': brand_id,
                'batch_name': batch_name
            }
        )
        
        return {
            'success': True,
            'batch_id': batch.id,
            'status': 'queued',
            'message': 'Upload enfileirado para processamento',
            'redirect': url_for('batch_detail', batch_id=batch.id)
        }
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        
        if chunk_dir and os.path.exists(chunk_dir):
            shutil.rmtree(chunk_dir, ignore_errors=True)
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        if upload_id in active_uploads:
            del active_uploads[upload_id]
        
        return {'error': f'Erro ao processar: {str(e)}'}, 500

@app.route('/upload/status/<upload_id>')
@login_required
def upload_status(upload_id):
    """Verifica status de um upload em andamento"""
    if upload_id not in active_uploads:
        return {'error': 'Upload não encontrado'}, 404
    
    info = active_uploads[upload_id]
    if info['user_id'] != current_user.id:
        return {'error': 'Não autorizado'}, 403
    
    return {
        'upload_id': upload_id,
        'filename': info['filename'],
        'received': len(info['received_chunks']),
        'total': info['total_chunks'],
        'progress': round((len(info['received_chunks']) / info['total_chunks']) * 100, 1)
    }

@app.route('/upload/queue-status')
@login_required
def upload_queue_status():
    """Retorna status da fila de uploads"""
    from upload_orchestrator import get_upload_orchestrator
    from object_storage import object_storage
    orchestrator = get_upload_orchestrator(app, db, object_storage)
    return orchestrator.get_status()

@app.route('/batch/diagnose-zip', methods=['POST'])
@login_required
def diagnose_zip():
    """Diagnóstico de ZIP - mostra quais arquivos serão processados e quais serão ignorados"""
    import zipfile
    
    if 'file' not in request.files:
        return {'error': 'Nenhum arquivo enviado'}, 400
    
    zip_file = request.files['file']
    if not zip_file.filename.lower().endswith('.zip'):
        return {'error': 'Envie um arquivo ZIP'}, 400
    
    temp_path = os.path.join('/tmp', f'diag_{datetime.now().strftime("%H%M%S")}.zip')
    zip_file.save(temp_path)
    
    try:
        allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}
        result = {
            'total_arquivos': 0,
            'imagens_validas': 0,
            'ignorados': {
                'sistema': [],
                'extensao_invalida': [],
                'ocultos': [],
                'sem_sku': []
            }
        }
        
        with zipfile.ZipFile(temp_path, 'r') as zip_ref:
            for file_info in zip_ref.infolist():
                if file_info.is_dir():
                    continue
                
                result['total_arquivos'] += 1
                full_path = file_info.filename
                filename = os.path.basename(full_path)
                
                if '__MACOSX' in full_path or '.DS_Store' in full_path or 'Thumbs.db' in filename:
                    result['ignorados']['sistema'].append(full_path)
                    continue
                
                ext = os.path.splitext(filename)[1].lower()
                
                if ext not in allowed_extensions:
                    result['ignorados']['extensao_invalida'].append({'arquivo': full_path, 'extensao': ext})
                    continue
                
                if filename.startswith('.') or filename.startswith('__'):
                    result['ignorados']['ocultos'].append(full_path)
                    continue
                
                sku = extract_sku_from_filename(filename)
                if not sku:
                    result['ignorados']['sem_sku'].append(full_path)
                    continue
                
                result['imagens_validas'] += 1
        
        result['resumo'] = {
            'total_no_zip': result['total_arquivos'],
            'serao_processados': result['imagens_validas'],
            'serao_ignorados': sum(len(v) for v in result['ignorados'].values()),
            'sistema': len(result['ignorados']['sistema']),
            'extensao_invalida': len(result['ignorados']['extensao_invalida']),
            'ocultos': len(result['ignorados']['ocultos']),
            'sem_sku': len(result['ignorados']['sem_sku'])
        }
        
        return result
        
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def create_all():
    """Manual DB bootstrap for dev/test; prefer Flask-Migrate for schema changes."""
    with app.app_context():
        db.create_all()
        ensure_image_table_columns()
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin', email='admin@oaz.com')
            admin.set_password('admin')
            db.session.add(admin)
            db.session.commit()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
