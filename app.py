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
    season = db.Column(db.String(50))
    year = db.Column(db.Integer)
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
    
    # AI-extracted attributes
    ai_item_type = db.Column(db.String(100))
    ai_color = db.Column(db.String(50))
    ai_material = db.Column(db.String(50))
    ai_pattern = db.Column(db.String(50))
    ai_style = db.Column(db.String(50))

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
        
        FORMATO DE RESPOSTA (JSON estrito):
        {
            "description": "Descrição ultra-detalhada conforme instruções acima...",
            "attributes": {
                "item_type": "Tipo + Modelagem + Comprimento",
                "color": "Cor com nuance específica",
                "material": "Material identificado com precisão",
                "pattern": "Estampa ou Liso",
                "style": "Estilo específico (não use apenas 'casual')"
            },
            "seo_keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"]
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
        
        description = data.get('description', '')
        attributes = data.get('attributes', {})
        keywords = data.get('seo_keywords', [])
        
        # Tags genéricas a serem filtradas (não agregam valor)
        generic_tags = [
            'casual', 'moda casual', 'roupa feminina', 'roupa masculina',
            'moda feminina', 'moda masculina', 'vestuário', 'roupa',
            'fashion', 'moda', 'look', 'outfit', 'estilo casual'
        ]
        
        # Flatten attributes into tags
        tags = []
        for key, value in attributes.items():
            if value and value.lower() != 'none' and value.lower() != 'n/a':
                # Filtrar tags genéricas dos atributos
                if value.lower() not in generic_tags:
                    tags.append(value)
        
        # Add SEO keywords to tags (filtering generic ones)
        for keyword in keywords:
            if keyword.lower() not in generic_tags:
                tags.extend([keyword])
        
        # Remove duplicates and limit
        unique_tags = list(set(tags))
        
        # Return description, tags, and structured attributes
        return description, unique_tags, attributes
        
    except Exception as e:
        print(f"AI Analysis Error: {e}")
        return f"Erro ao analisar imagem: {str(e)}", [], {}

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
            try:
                ai_description, ai_tags, ai_attributes = analyze_image_with_ai(file_path)
                
                # Check if analysis failed (returned error message)
                if isinstance(ai_description, str) and ai_description.startswith("AI Configuration missing"):
                    print(f"[WARNING] AI not configured, using defaults")
                    ai_description = request.form.get('observations') or "Imagem enviada - análise manual necessária"
                    ai_tags = []
                    ai_attributes = {}
                elif isinstance(ai_description, str) and ai_description.startswith("Erro ao analisar"):
                    print(f"[ERROR] AI analysis failed: {ai_description}")
                    ai_description = request.form.get('observations') or "Imagem enviada - erro na análise automática"
                    ai_tags = []
                    ai_attributes = {}
            except Exception as e:
                print(f"[ERROR] Exception during AI analysis: {e}")
                ai_description = request.form.get('observations') or "Imagem enviada - análise manual necessária"
                ai_tags = []
                ai_attributes = {}
            
            # Generate unique code
            import uuid
            unique_code = f"IMG-{uuid.uuid4().hex[:8].upper()}"
            
            # Create DB record
            brand_id = request.form.get('brand_id')
            new_image = Image(
                filename=unique_filename,
                original_name=file.filename,
                collection_id=request.form.get('collection_id') if request.form.get('collection_id') else None,
                brand_id=int(brand_id) if brand_id else None,
                sku=request.form.get('sku'),
                photographer=request.form.get('photographer'),
                description=request.form.get('observations') or ai_description,
                tags=json.dumps(ai_tags) if ai_tags else json.dumps([]),
                ai_item_type=ai_attributes.get('item_type') if ai_attributes else None,
                ai_color=ai_attributes.get('color') if ai_attributes else None,
                ai_material=ai_attributes.get('material') if ai_attributes else None,
                ai_pattern=ai_attributes.get('pattern') if ai_attributes else None,
                ai_style=ai_attributes.get('style') if ai_attributes else None,
                uploader_id=current_user.id,
                unique_code=unique_code,
                status='Pendente'
            )
            db.session.add(new_image)
            db.session.commit()
            
            if ai_attributes:
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
    collections = Collection.query.order_by(Collection.created_at.desc()).all()
    return render_template('collections/list.html', collections=collections)

@app.route('/collections/new', methods=['GET', 'POST'])
@login_required
def new_collection():
    if request.method == 'POST':
        name = request.form.get('name')
        description = request.form.get('description')
        
        if not name:
            flash('Nome da coleção é obrigatório')
            return redirect(url_for('new_collection'))
            
        collection = Collection(name=name, description=description)
        db.session.add(collection)
        db.session.commit()
        
        flash('Coleção criada com sucesso!')
        return redirect(url_for('collections'))
        
    return render_template('collections/new.html')


@app.route('/image/<int:id>')
@login_required
def image_detail(id):
    image = Image.query.get_or_404(id)
    try:
        image.tag_list = json.loads(image.tags) if image.tags else []
    except:
        image.tag_list = []
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
        ai_description, ai_tags, ai_attributes = analyze_image_with_ai(file_path)
        
        if isinstance(ai_description, str) and ai_description.startswith("AI Configuration missing"):
            flash('Chave OpenAI não configurada. Acesse Configurações para adicionar.')
            return redirect(url_for('image_detail', id=id))
        elif isinstance(ai_description, str) and ai_description.startswith("Erro ao analisar"):
            flash(f'Erro na análise: {ai_description}')
            return redirect(url_for('image_detail', id=id))
        
        image.description = ai_description
        image.tags = json.dumps(ai_tags) if ai_tags else json.dumps([])
        image.ai_item_type = ai_attributes.get('item_type') if ai_attributes else None
        image.ai_color = ai_attributes.get('color') if ai_attributes else None
        image.ai_material = ai_attributes.get('material') if ai_attributes else None
        image.ai_pattern = ai_attributes.get('pattern') if ai_attributes else None
        image.ai_style = ai_attributes.get('style') if ai_attributes else None
        
        db.session.commit()
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
