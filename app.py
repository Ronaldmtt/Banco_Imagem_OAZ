import os
import io
import csv
from datetime import datetime
from flask import Flask, render_template, redirect, url_for, flash, request, Response, make_response
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from openai import OpenAI
from dotenv import load_dotenv
import json
import base64

# Load environment variables
load_dotenv()

# Configuration
class Config:
    SECRET_KEY = 'dev-secret-key-oaz-img' # Change in production
    SQLALCHEMY_DATABASE_URI = 'sqlite:///oaz_img.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = 'static/uploads'
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app = Flask(__name__)
app.config.from_object(Config)

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    
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
    campanha = db.Column(db.String(100))  # Nome da campanha
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    images = db.relationship('Image', backref='collection', lazy=True)

class Image(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    sku = db.Column(db.String(50))
    brand_id = db.Column(db.Integer, db.ForeignKey('brand.id'))
    status = db.Column(db.String(20), default='Pendente')
    upload_date = db.Column(db.DateTime, default=datetime.utcnow)
    shooting_date = db.Column(db.Date)
    photographer = db.Column(db.String(100))
    unique_code = db.Column(db.String(50), unique=True)
    collection_id = db.Column(db.Integer, db.ForeignKey('collection.id'))
    uploader_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    tags = db.Column(db.Text)
    
    # AI-extracted attributes (legacy - mantido para retrocompatibilidade)
    ai_item_type = db.Column(db.String(100))
    ai_color = db.Column(db.String(50))
    ai_material = db.Column(db.String(100))
    ai_pattern = db.Column(db.String(50))
    ai_style = db.Column(db.String(50))
    
    # Relationship with individual items detected in image
    items = db.relationship('ImageItem', backref='image', lazy=True, cascade='all, delete-orphan')

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
    
    # Metadados
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Flags
    tem_foto = db.Column(db.Boolean, default=False)
    ativo = db.Column(db.Boolean, default=True)
    
    # Relationships
    marca = db.relationship('Brand', backref='produtos')
    colecao = db.relationship('Collection', backref='produtos')
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
    categoria = db.Column(db.String(100))  # GRUPO
    subcategoria = db.Column(db.String(100))  # SUBGRUPO
    colecao_nome = db.Column(db.String(100))  # ENTRADA (ex: INVERNO 2026)
    estilista = db.Column(db.String(255))
    shooting = db.Column(db.String(100))  # QUANDO
    observacoes = db.Column(db.Text)  # OBS
    origem = db.Column(db.String(50))  # NACIONAL / IMPORTADO
    quantidade = db.Column(db.Integer, default=1)
    
    # Status
    status_foto = db.Column(db.String(30), default='Pendente')  # Pendente, Com Foto, Sem Foto
    okr = db.Column(db.String(20))  # Status de aprovação
    produto_id = db.Column(db.Integer, db.ForeignKey('produto.id'))  # Link com produto se existir
    
    # Relacionamentos com entidades auto-criadas
    colecao_id = db.Column(db.Integer, db.ForeignKey('collection.id'))  # Coleção associada
    marca_id = db.Column(db.Integer, db.ForeignKey('brand.id'))  # Marca associada
    
    # Metadados de importação
    data_importacao = db.Column(db.DateTime, default=datetime.utcnow)
    lote_importacao = db.Column(db.String(50))  # Identificador do lote de importação
    aba_origem = db.Column(db.String(50))  # Aba do Excel de origem
    
    # Relacionamentos
    produto = db.relationship('Produto', backref='itens_carteira')
    colecao = db.relationship('Collection', backref='itens_carteira')
    marca = db.relationship('Brand', backref='itens_carteira')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def get_openai_client():
    # First try to get from DB
    config = SystemConfig.query.filter_by(key='OPENAI_API_KEY').first()
    if config:
        return OpenAI(api_key=config.value)
    
    # Fallback to env var
    api_key = os.getenv('OPENAI_API_KEY')
    if api_key:
        return OpenAI(api_key=api_key)
    
    return None

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def analyze_image_with_ai(image_path):
    client = get_openai_client()
    if not client:
        return "AI Configuration missing. Please configure OpenAI API Key in Settings.", []
    
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
        print(f"[DEBUG] Login attempt - Username: {username}, Password length: {len(password) if password else 0}")
        user = User.query.filter_by(username=username).first()
        print(f"[DEBUG] User found: {user is not None}")
        if user:
            password_check = user.check_password(password)
            print(f"[DEBUG] Password check result: {password_check}")
            if password_check:
                login_user(user)
                print(f"[DEBUG] User logged in successfully: {user.username}")
                return redirect(url_for('dashboard'))
        flash('Usuário ou senha inválidos')
        print(f"[DEBUG] Login failed")
    return render_template('auth/login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if User.query.filter_by(username=username).first():
            flash('Nome de usuário já existe')
            return redirect(url_for('register'))
            
        user = User(username=username, email=email)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for('dashboard'))
    return render_template('auth/register.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    total_images = Image.query.count()
    pending_images = Image.query.filter_by(status='Pendente').count()
    approved_images = Image.query.filter_by(status='Aprovado').count()
    rejected_images = Image.query.filter_by(status='Rejeitado').count()
    total_collections = Collection.query.count()
    total_brands = Brand.query.count()
    recent_images = Image.query.order_by(Image.upload_date.desc()).limit(5).all()
    
    return render_template('dashboard/index.html',
                          total_images=total_images,
                          pending_images=pending_images,
                          approved_images=approved_images,
                          rejected_images=rejected_images,
                          total_collections=total_collections,
                          total_brands=total_brands,
                          recent_images=recent_images)

@app.route('/catalog')
@login_required
def catalog():
    # Get filter parameters
    status_filter = request.args.getlist('status')
    collection_filter = request.args.get('collection_id')
    brand_filter = request.args.get('brand_id')
    search_query = request.args.get('q', '').strip()
    
    query = Image.query
    
    # Apply filters
    if status_filter:
        query = query.filter(Image.status.in_(status_filter))
    
    if collection_filter and collection_filter != 'all':
        query = query.filter_by(collection_id=collection_filter)
    
    if brand_filter and brand_filter != 'all':
        query = query.filter_by(brand_id=brand_filter)
    
    # Apply search if provided
    if search_query:
        search_term = f"%{search_query}%"
        query = query.filter(
            db.or_(
                Image.sku.ilike(search_term),
                Image.description.ilike(search_term),
                Image.original_name.ilike(search_term),
                Image.ai_item_type.ilike(search_term),
                Image.tags.ilike(search_term)
            )
        )
        
    images = query.order_by(Image.upload_date.desc()).all()
    
    # Get all collections and brands for the filter dropdown
    collections = Collection.query.all()
    brands = Brand.query.order_by(Brand.name).all()
    
    # Parse tags if they are stored as JSON string
    for img in images:
        if img.tags:
            try:
                img.tag_list = json.loads(img.tags)
            except:
                img.tag_list = []
        else:
            img.tag_list = []
            
    return render_template('images/catalog.html', images=images, collections=collections, brands=brands, search_query=search_query)


@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
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
            # Add timestamp to filename to avoid duplicates
            timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
            unique_filename = f"{timestamp}_{filename}"
            
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], unique_filename)
            file.save(file_path)
            
            # Real AI Analysis with better error handling
            ai_items = []
            try:
                ai_result = analyze_image_with_ai(file_path)
                
                # Check if analysis failed (returned error message string)
                if isinstance(ai_result, str):
                    if ai_result.startswith("AI Configuration missing"):
                        print(f"[WARNING] AI not configured, using defaults")
                    else:
                        print(f"[ERROR] AI analysis failed: {ai_result}")
                    ai_items = []
                else:
                    ai_items = ai_result  # Lista de itens detectados
            except Exception as e:
                print(f"[ERROR] Exception during AI analysis: {e}")
                ai_items = []
            
            # Generate unique code
            import uuid
            unique_code = f"IMG-{uuid.uuid4().hex[:8].upper()}"
            
            # Prepare legacy fields from first item (for backward compatibility)
            first_item = ai_items[0] if ai_items else {}
            first_attrs = first_item.get('attributes', {}) if first_item else {}
            
            # Create DB record
            brand_id = request.form.get('brand_id')
            new_image = Image(
                filename=unique_filename,
                original_name=file.filename,
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
            db.session.flush()  # Get the image ID
            
            # Create ImageItem records for each detected piece
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
            
            item_count = len(ai_items)
            if item_count > 1:
                flash(f'Imagem enviada com sucesso. {item_count} peças detectadas e analisadas.')
            elif item_count == 1:
                flash('Imagem enviada com sucesso. Análise de IA concluída.')
            else:
                flash('Imagem enviada com sucesso. Configure a chave OpenAI em Configurações para análise automática.')
            return redirect(url_for('catalog'))
        else:
            flash('Formato de arquivo não permitido. Use: PNG, JPG, JPEG ou GIF')
            return redirect(request.url)
    collections = Collection.query.order_by(Collection.name).all()
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('images/upload.html', collections=collections, brands=brands)


@app.route('/collections')
@login_required
def collections():
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

@app.route('/collections/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_collection(id):
    collection = Collection.query.get_or_404(id)
    
    if request.method == 'POST':
        collection.name = request.form.get('name')
        collection.description = request.form.get('description')
        collection.season = request.form.get('season')
        year = request.form.get('year')
        collection.year = int(year) if year else None
        collection.campanha = request.form.get('campanha')
        
        db.session.commit()
        flash('Coleção atualizada com sucesso!')
        return redirect(url_for('collections'))
    
    current_year = datetime.now().year
    years = list(range(current_year - 2, current_year + 3))
    return render_template('collections/edit.html', collection=collection, years=years)

@app.route('/collections/<int:id>/delete', methods=['POST'])
@login_required
def delete_collection(id):
    collection = Collection.query.get_or_404(id)
    db.session.delete(collection)
    db.session.commit()
    flash('Coleção removida com sucesso!')
    return redirect(url_for('collections'))

@app.route('/collections/delete-all', methods=['POST'])
@login_required
def delete_all_collections():
    """Deleta todas as coleções"""
    count = Collection.query.count()
    Collection.query.delete()
    db.session.commit()
    flash(f'{count} coleções removidas com sucesso!', 'success')
    return redirect(url_for('collections'))

@app.route('/collections/new', methods=['GET', 'POST'])
@login_required
def new_collection():
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
        
        flash('Coleção criada com sucesso!')
        return redirect(url_for('collections'))
    
    current_year = datetime.now().year
    years = list(range(current_year - 2, current_year + 3))
    return render_template('collections/new.html', years=years)


@app.route('/image/<int:id>')
@login_required
def image_detail(id):
    image = Image.query.get_or_404(id)
    try:
        image.tag_list = json.loads(image.tags) if image.tags else []
    except:
        image.tag_list = []
    
    # Process tags for each item
    for item in image.items:
        try:
            item.tag_list = json.loads(item.tags) if item.tags else []
        except:
            item.tag_list = []
    
    return render_template('images/detail.html', image=image)

@app.route('/image/<int:id>/delete', methods=['POST'])
@login_required
def delete_image(id):
    image = Image.query.get_or_404(id)
    
    try:
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception as e:
        print(f"Error deleting file: {e}")
        
    db.session.delete(image)
    db.session.commit()
    
    flash('Imagem deletada com sucesso')
    return redirect(url_for('catalog'))

@app.route('/image/<int:id>/status/<status>', methods=['POST'])
@login_required
def update_image_status(id, status):
    image = Image.query.get_or_404(id)
    valid_statuses = ['Pendente', 'Aprovado', 'Rejeitado']
    if status in valid_statuses:
        image.status = status
        db.session.commit()
        flash(f'Status atualizado para {status}')
    else:
        flash('Status inválido')
    return redirect(url_for('image_detail', id=id))

@app.route('/image/<int:id>/reanalyze', methods=['POST'])
@login_required
def reanalyze_image(id):
    image = Image.query.get_or_404(id)
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], image.filename)
    
    if not os.path.exists(file_path):
        flash('Arquivo de imagem não encontrado')
        return redirect(url_for('image_detail', id=id))
    
    try:
        ai_result = analyze_image_with_ai(file_path)
        
        # Check if analysis failed (returned error message string)
        if isinstance(ai_result, str):
            if ai_result.startswith("AI Configuration missing"):
                flash('Chave OpenAI não configurada. Acesse Configurações para adicionar.')
            else:
                flash(f'Erro na análise: {ai_result}')
            return redirect(url_for('image_detail', id=id))
        
        ai_items = ai_result  # Lista de itens detectados
        
        # Update legacy fields from first item (for backward compatibility)
        first_item = ai_items[0] if ai_items else {}
        first_attrs = first_item.get('attributes', {}) if first_item else {}
        
        image.description = first_item.get('description', '')
        image.tags = json.dumps(first_item.get('tags', [])) if first_item else json.dumps([])
        image.ai_item_type = first_attrs.get('item_type')
        image.ai_color = first_attrs.get('color')
        image.ai_material = first_attrs.get('material')
        image.ai_pattern = first_attrs.get('pattern')
        image.ai_style = first_attrs.get('style')
        
        # Remove existing items and create new ones
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
        
        item_count = len(ai_items)
        if item_count > 1:
            flash(f'Re-análise concluída! {item_count} peças detectadas e analisadas.')
        else:
            flash('Imagem re-analisada com sucesso! Atributos atualizados.')
        
    except Exception as e:
        print(f"[ERROR] Re-analysis failed: {e}")
        flash(f'Erro ao re-analisar: {str(e)}')
    
    return redirect(url_for('image_detail', id=id))

@app.route('/image/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_image(id):
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
    brands = Brand.query.order_by(Brand.name).all()
    return render_template('brands/list.html', brands=brands)

@app.route('/brands/new', methods=['GET', 'POST'])
@login_required
def new_brand():
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
        
        flash('Marca criada com sucesso!')
        return redirect(url_for('brands'))
        
    return render_template('brands/new.html')

@app.route('/analytics')
@login_required
def analytics():
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
    return render_template('integrations/index.html')

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        api_key = request.form.get('api_key')
        
        config = SystemConfig.query.filter_by(key='OPENAI_API_KEY').first()
        if config:
            config.value = api_key
        else:
            config = SystemConfig(key='OPENAI_API_KEY', value=api_key)
            db.session.add(config)
            
        db.session.commit()
        flash('Settings updated successfully')
        return redirect(url_for('settings'))
        
    config = SystemConfig.query.filter_by(key='OPENAI_API_KEY').first()
    current_key = config.value if config else ''
    return render_template('admin/settings.html', current_key=current_key)

@app.route('/reports')
@login_required
def reports():
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

@app.route('/reports/export/<report_type>')
@login_required
def export_report(report_type):
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
    produto = Produto.query.get_or_404(id)
    produto.ativo = False  # Soft delete
    db.session.commit()
    flash('Produto removido com sucesso')
    return redirect(url_for('produtos'))

@app.route('/produtos/delete-all', methods=['POST'])
@login_required
def delete_all_produtos():
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
    try:
        itens = CarteiraCompras.query.filter_by(lote_importacao=lote_id).all()
        count = len(itens)
        
        for item in itens:
            db.session.delete(item)
        
        db.session.commit()
        flash(f'Lote "{lote_id}" deletado com sucesso! {count} itens removidos.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Erro ao deletar lote: {str(e)}', 'error')
    
    return redirect(url_for('carteira'))

@app.route('/carteira/limpar-tudo', methods=['POST'])
@login_required
def limpar_toda_carteira():
    """Limpa toda a carteira de compras"""
    try:
        count = CarteiraCompras.query.count()
        CarteiraCompras.query.delete()
        db.session.commit()
        flash(f'Carteira limpa com sucesso! {count} itens removidos.', 'success')
    except Exception as e:
        db.session.rollback()
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
    
    mapeamento_sku = ['referencia e cor', 'referência e cor', 'sku', 'codigo', 'código', 'ref']
    mapeamento_descricao = ['nome', 'nome produto', 'descricao', 'descrição', 'produto']
    mapeamento_cor = ['nome / cor', 'nome/cor', 'cor', 'cor produto']
    mapeamento_categoria = ['grupo', 'categoria', 'departamento', 'tipo']
    mapeamento_subcategoria = ['subgrupo', 'subcategoria', 'sub categoria', 'subtipo']
    mapeamento_colecao = ['entrada', 'colecao', 'coleção', 'temporada']
    mapeamento_marca = ['marca', 'brand', 'grife', 'fabricante']
    mapeamento_estilista = ['estilista', 'designer', 'criador']
    mapeamento_shooting = ['quando', 'shooting', 'data shooting', 'foto quando']
    mapeamento_observacoes = ['obs', 'observacoes', 'observações', 'notas', 'comentarios']
    mapeamento_origem = ['nacional / importado', 'nacional/importado', 'origem', 'procedencia']
    mapeamento_foto = ['foto', 'tem foto', 'status foto']
    mapeamento_okr = ['okr', 'status okr', 'aprovacao']
    mapeamento_quantidade = ['quantidade', 'qtd', 'qty', 'quant']
    
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
    col_colecao = encontrar_coluna(mapeamento_colecao)
    col_marca = encontrar_coluna(mapeamento_marca)
    col_estilista = encontrar_coluna(mapeamento_estilista)
    col_shooting = encontrar_coluna(mapeamento_shooting)
    col_observacoes = encontrar_coluna(mapeamento_observacoes)
    col_origem = encontrar_coluna(mapeamento_origem)
    col_foto = encontrar_coluna(mapeamento_foto)
    col_okr = encontrar_coluna(mapeamento_okr)
    col_quantidade = encontrar_coluna(mapeamento_quantidade)
    
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
    
    df_normalizado = df.rename(columns=novo_mapeamento)
    
    sku_encontrado = 'sku' in df_normalizado.columns
    
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

def obter_ou_criar_produto(sku, dados_linha, contadores, marca_id=None, colecao_id=None):
    """Busca ou cria um produto pelo SKU. Retorna o ID do produto."""
    import pandas as pd
    import json
    
    if not sku or not sku.strip():
        return None
    
    produto = Produto.query.filter_by(sku=sku).first()
    
    if produto:
        # Atualizar marca e coleção se ainda não tiver
        atualizado = False
        if marca_id and not produto.marca_id:
            produto.marca_id = marca_id
            atualizado = True
        if colecao_id and not produto.colecao_id:
            produto.colecao_id = colecao_id
            atualizado = True
        if atualizado:
            db.session.flush()
        return produto.id
    
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
        atributos_tecnicos=json.dumps(atributos_extras) if atributos_extras else None,
        tem_foto=False
    )
    db.session.add(novo_produto)
    db.session.flush()
    contadores['produtos_criados'] += 1
    return novo_produto.id

def processar_linhas_carteira(df, lote_id, aba_origem, contadores=None):
    """
    Processa linhas do DataFrame e insere/atualiza na CarteiraCompras.
    Auto-cria Coleções, Marcas e Produtos quando dados válidos são encontrados.
    
    Args:
        df: DataFrame normalizado
        lote_id: ID do lote de importação
        aba_origem: Nome da aba de origem
        contadores: Dicionário para rastrear entidades criadas
    
    Returns:
        (count, skus_invalidos): Quantidade de itens criados e linhas ignoradas
    """
    import pandas as pd
    
    if contadores is None:
        contadores = {
            'colecoes_criadas': 0,
            'marcas_criadas': 0,
            'produtos_criados': 0
        }
    
    count = 0
    skus_invalidos = 0
    
    for idx, row in df.iterrows():
        sku = str(row.get('sku', '')).strip() if pd.notna(row.get('sku', '')) else ''
        
        if not sku or sku.upper() in ['SKUS', 'SKU', 'NAN', 'NONE', '']:
            skus_invalidos += 1
            continue
        
        sku = sku.rstrip('.00').rstrip('.0').strip()
        
        nome_colecao = str(row.get('colecao_nome', '')).strip() if pd.notna(row.get('colecao_nome', '')) else None
        nome_marca = str(row.get('marca_nome', '')).strip() if pd.notna(row.get('marca_nome', '')) else None
        
        colecao_id = obter_ou_criar_colecao(nome_colecao, contadores) if nome_colecao else None
        marca_id = obter_ou_criar_marca(nome_marca, contadores) if nome_marca else None
        produto_id = obter_ou_criar_produto(sku, row, contadores, marca_id=marca_id, colecao_id=colecao_id)
        
        existing = CarteiraCompras.query.filter_by(sku=sku).first()
        if existing:
            existing.lote_importacao = lote_id
            existing.aba_origem = aba_origem
            if colecao_id:
                existing.colecao_id = colecao_id
            if marca_id:
                existing.marca_id = marca_id
            if produto_id:
                existing.produto_id = produto_id
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
            
            item = CarteiraCompras(
                sku=sku,
                descricao=str(row.get('descricao', ''))[:255] if pd.notna(row.get('descricao', '')) else None,
                cor=str(row.get('cor', ''))[:100] if pd.notna(row.get('cor', '')) else None,
                categoria=str(row.get('categoria', ''))[:100] if pd.notna(row.get('categoria', '')) else None,
                subcategoria=str(row.get('subcategoria', ''))[:100] if pd.notna(row.get('subcategoria', '')) else None,
                colecao_nome=nome_colecao[:100] if nome_colecao else None,
                colecao_id=colecao_id,
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
                produto_id=produto_id
            )
            
            if produto_id:
                produto = Produto.query.get(produto_id)
                if produto and produto.tem_foto:
                    item.status_foto = 'Com Foto'
            
            db.session.add(item)
            count += 1
    
    return count, skus_invalidos

@app.route('/carteira/importar', methods=['GET', 'POST'])
@login_required
def importar_carteira():
    if request.method == 'POST':
        if 'arquivo' not in request.files:
            flash('Nenhum arquivo enviado', 'error')
            return redirect(request.url)
        
        file = request.files['arquivo']
        if file.filename == '':
            flash('Nenhum arquivo selecionado', 'error')
            return redirect(request.url)
        
        filename = file.filename.lower()
        if not (filename.endswith('.csv') or filename.endswith('.xlsx') or filename.endswith('.xls')):
            flash('Apenas arquivos CSV ou Excel (.xlsx, .xls) são permitidos', 'error')
            return redirect(request.url)
        
        try:
            import uuid
            import pandas as pd
            
            lote_id = f"LOTE-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:4].upper()}"
            aba_selecionada = request.form.get('aba', '')
            importar_todas = request.form.get('importar_todas', '') == 'true'
            
            total_count = 0
            total_invalidos = 0
            abas_processadas = []
            
            contadores = {
                'colecoes_criadas': 0,
                'marcas_criadas': 0,
                'produtos_criados': 0
            }
            
            if filename.endswith('.csv'):
                try:
                    content = file.read().decode('utf-8')
                except UnicodeDecodeError:
                    file.seek(0)
                    content = file.read().decode('latin-1')
                
                df = pd.read_csv(io.StringIO(content))
                df_normalizado, sku_encontrado = normalizar_carteira_dataframe(df)
                
                if not sku_encontrado:
                    flash('Coluna de SKU não encontrada no arquivo CSV. Verifique se existe uma coluna chamada "SKU", "REFERÊNCIA E COR" ou "CODIGO".', 'error')
                    return redirect(request.url)
                
                count, invalidos = processar_linhas_carteira(df_normalizado, lote_id, 'CSV', contadores)
                total_count = count
                total_invalidos = invalidos
                abas_processadas.append('CSV')
                
            else:
                xl = pd.ExcelFile(file)
                
                if importar_todas:
                    for sheet_name in xl.sheet_names:
                        df = pd.read_excel(xl, sheet_name=sheet_name)
                        
                        if df.empty or len(df) == 0:
                            continue
                        
                        df_normalizado, sku_encontrado = normalizar_carteira_dataframe(df)
                        
                        if not sku_encontrado:
                            continue
                        
                        count, invalidos = processar_linhas_carteira(df_normalizado, lote_id, sheet_name, contadores)
                        total_count += count
                        total_invalidos += invalidos
                        if count > 0:
                            abas_processadas.append(f"{sheet_name} ({count})")
                    
                    if total_count == 0:
                        flash('Nenhum item válido encontrado nas abas do Excel. Verifique se existe uma coluna "REFERÊNCIA E COR" ou "SKU" em pelo menos uma aba.', 'error')
                        return redirect(request.url)
                else:
                    if aba_selecionada and aba_selecionada in xl.sheet_names:
                        df = pd.read_excel(xl, sheet_name=aba_selecionada)
                    else:
                        df = pd.read_excel(xl, sheet_name=0)
                        aba_selecionada = xl.sheet_names[0]
                    
                    df_normalizado, sku_encontrado = normalizar_carteira_dataframe(df)
                    
                    if not sku_encontrado:
                        flash(f'Coluna de SKU não encontrada na aba "{aba_selecionada}". Verifique se existe uma coluna chamada "REFERÊNCIA E COR", "SKU" ou "CODIGO".', 'error')
                        return redirect(request.url)
                    
                    count, invalidos = processar_linhas_carteira(df_normalizado, lote_id, aba_selecionada, contadores)
                    total_count = count
                    total_invalidos = invalidos
                    abas_processadas.append(aba_selecionada)
            
            db.session.commit()
            
            if len(abas_processadas) > 1:
                flash(f'Importação concluída! {total_count} novos itens de {len(abas_processadas)} abas adicionados. Abas: {", ".join(abas_processadas)}. Lote: {lote_id}', 'success')
            else:
                flash(f'Importação concluída! {total_count} novos itens da aba "{abas_processadas[0]}" adicionados. Lote: {lote_id}', 'success')
            
            entidades_criadas = []
            if contadores['colecoes_criadas'] > 0:
                entidades_criadas.append(f"{contadores['colecoes_criadas']} coleção(ões)")
            if contadores['marcas_criadas'] > 0:
                entidades_criadas.append(f"{contadores['marcas_criadas']} marca(s)")
            if contadores['produtos_criados'] > 0:
                entidades_criadas.append(f"{contadores['produtos_criados']} produto(s)")
            
            if entidades_criadas:
                flash(f'Criados automaticamente: {", ".join(entidades_criadas)}', 'info')
            
            if total_invalidos > 0:
                flash(f'{total_invalidos} linhas ignoradas (SKU vazio ou inválido).', 'warning')
            
            atualizar_status_carteira()
            
            # Retornar sucesso para requisição AJAX
            return {'success': True, 'message': f'{total_count} itens importados'}, 200
            
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao importar: {str(e)}', 'error')
            return {'success': False, 'error': str(e)}, 500
    
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
    """Executa cruzamento entre carteira e produtos/imagens"""
    count = atualizar_status_carteira()
    flash(f'Cruzamento concluído! {count} itens atualizados.')
    return redirect(url_for('carteira'))

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
    historico = HistoricoSKU.query.order_by(HistoricoSKU.data_alteracao.desc()).all()
    return render_template('auditoria/historico_sku.html', historico=historico)

@app.route('/auditoria/skus-pendentes')
@login_required
def skus_pendentes():
    produtos = Produto.query.filter_by(tem_foto=False, ativo=True).order_by(Produto.sku).all()
    return render_template('auditoria/skus_pendentes.html', produtos=produtos)

@app.route('/auditoria/export/<tipo>')
@login_required
def export_auditoria(tipo):
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

# Initialize DB
with app.app_context():
    db.create_all()
    # Create a test user if not exists
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin', email='admin@oaz.com')
        admin.set_password('admin')
        db.session.add(admin)
        db.session.commit()

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
