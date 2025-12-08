"""
Batch Processor - Sistema de processamento em lote de imagens
Processa imagens em paralelo usando ThreadPoolExecutor com sessões de banco isoladas
Match primário com CarteiraCompras, API como fallback futuro
"""

import os
import json
import zipfile
import tempfile
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import time

MAX_WORKERS = 5
MAX_RETRIES = 3
RETRY_DELAY = 2

progress_lock = Lock()

class BatchProcessor:
    """Processador de lotes de imagens com threading e sessões isoladas"""
    
    def __init__(self, app, db, object_storage, analyze_func=None):
        self.app = app
        self.db = db
        self.object_storage = object_storage
        self.analyze_func = analyze_func
        self.max_workers = app.config.get('MAX_BATCH_WORKERS', MAX_WORKERS)
    
    def process_batch(self, batch_id, temp_file_paths):
        """
        Processa um lote de imagens em paralelo usando arquivos temporários
        
        Args:
            batch_id: ID do BatchUpload
            temp_file_paths: Lista de dicts com {item_id, sku, temp_path, filename}
        """
        with self.app.app_context():
            from app import BatchUpload
            batch = self.db.session.get(BatchUpload, batch_id)
            if not batch:
                return
            
            batch.status = 'Processando'
            batch.started_at = datetime.utcnow()
            self.db.session.commit()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            
            for file_info in temp_file_paths:
                future = executor.submit(
                    self._process_single_item_isolated,
                    batch_id,
                    file_info['item_id'],
                    file_info['sku'],
                    file_info['temp_path'],
                    file_info['filename']
                )
                futures[future] = file_info['item_id']
            
            for future in as_completed(futures):
                item_id = futures[future]
                try:
                    result = future.result()
                    self._update_batch_progress_atomic(batch_id, result['success'])
                except Exception as e:
                    print(f"[ERROR] Exception processing item {item_id}: {e}")
                    self._update_batch_progress_atomic(batch_id, False)
        
        with self.app.app_context():
            batch = self.db.session.get(BatchUpload, batch_id)
            if batch:
                batch.status = 'Concluído'
                batch.finished_at = datetime.utcnow()
                self.db.session.commit()
        
        self._cleanup_temp_files(temp_file_paths)
    
    def _match_carteira_compras_in_session(self, sku, db):
        """
        Busca dados da CarteiraCompras pelo SKU usando a sessão atual
        
        Args:
            sku: Código SKU para buscar
            db: Instância do SQLAlchemy database
            
        Returns:
            Dict com dados da carteira ou None se não encontrar
        """
        from app import CarteiraCompras
        
        carteira = db.session.query(CarteiraCompras).filter_by(sku=sku).first()
        
        if not carteira:
            sku_upper = sku.upper().strip()
            carteira = db.session.query(CarteiraCompras).filter(
                db.func.upper(db.func.trim(CarteiraCompras.sku)) == sku_upper
            ).first()
        
        if carteira:
            return {
                'found': True,
                'descricao': carteira.descricao or '',
                'cor': carteira.cor or '',
                'categoria': carteira.categoria or '',
                'subcategoria': carteira.subcategoria or '',
                'colecao_nome': carteira.colecao_nome or '',
                'colecao_id': carteira.colecao_id,
                'subcolecao_id': carteira.subcolecao_id,
                'marca_id': carteira.marca_id,
                'estilista': carteira.estilista or '',
                'shooting': carteira.shooting or '',
                'observacoes': carteira.observacoes or '',
                'origem': carteira.origem or '',
                'carteira_id': carteira.id,
                'material': carteira.material or carteira.categoria or '',
                'tipo_peca': carteira.tipo_peca or carteira.subcategoria or '',
                'posicao_peca': carteira.posicao_peca or '',
                'referencia_estilo': carteira.referencia_estilo or '',
                'tipo_carteira': carteira.tipo_carteira or 'Moda'
            }
        
        return None
    
    def _process_single_item_isolated(self, batch_id, item_id, sku, temp_path, original_filename):
        """Processa um único item com sessão de banco isolada"""
        from app import db, BatchUpload, BatchItem, Image, ImageItem, CarteiraCompras
        
        with self.app.app_context():
            self.db.session.remove()
            
            item = self.db.session.get(BatchItem, item_id)
            if not item:
                return {'success': False, 'error': 'Item not found'}
            
            item.status = 'Processando'
            item.tentativas += 1
            self.db.session.commit()
            
            try:
                if not os.path.exists(temp_path):
                    raise FileNotFoundError(f"Temp file not found: {temp_path}")
                
                carteira_data = self._match_carteira_compras_in_session(sku, db)
                
                storage_result = None
                for attempt in range(MAX_RETRIES):
                    try:
                        with open(temp_path, 'rb') as f:
                            ext = os.path.splitext(original_filename)[1] or '.jpg'
                            storage_result = self.object_storage.upload_file(f, f"{sku}{ext}")
                        break
                    except Exception as e:
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(RETRY_DELAY * (attempt + 1))
                        else:
                            raise e
                
                storage_path = storage_result.get('storage_path') if storage_result else None
                
                import uuid
                unique_code = f"IMG-{uuid.uuid4().hex[:8].upper()}"
                
                batch = self.db.session.get(BatchUpload, batch_id)
                
                if carteira_data and carteira_data.get('found'):
                    description = carteira_data.get('descricao', '')
                    cor = carteira_data.get('cor', '')
                    categoria = carteira_data.get('categoria', '')
                    subcategoria = carteira_data.get('subcategoria', '')
                    material = carteira_data.get('material', '')
                    tipo_peca = carteira_data.get('tipo_peca', '')
                    posicao_peca = carteira_data.get('posicao_peca', '')
                    
                    tags_list = []
                    if categoria:
                        tags_list.append(categoria)
                    if subcategoria:
                        tags_list.append(subcategoria)
                    if cor:
                        tags_list.append(cor)
                    if carteira_data.get('colecao_nome'):
                        tags_list.append(carteira_data['colecao_nome'])
                    if material and material not in tags_list:
                        tags_list.append(material)
                    if tipo_peca and tipo_peca not in tags_list:
                        tags_list.append(tipo_peca)
                    
                    image_status = 'Pendente'
                    
                    collection_id = carteira_data.get('colecao_id') if carteira_data.get('colecao_id') else (batch.colecao_id if batch else None)
                    subcolecao_id = carteira_data.get('subcolecao_id') if carteira_data.get('subcolecao_id') else (batch.subcolecao_id if batch and hasattr(batch, 'subcolecao_id') else None)
                    brand_id = carteira_data.get('marca_id') if carteira_data.get('marca_id') else (batch.marca_id if batch else None)
                    estilista = carteira_data.get('estilista', '')
                    origem = carteira_data.get('origem', '')
                    referencia_estilo = carteira_data.get('referencia_estilo', '')
                    
                    carteira = self.db.session.get(CarteiraCompras, carteira_data['carteira_id'])
                    if carteira:
                        carteira.status_foto = 'Com Foto'
                        self.db.session.add(carteira)
                    
                    match_source = 'carteira'
                else:
                    description = ''
                    cor = ''
                    categoria = ''
                    material = ''
                    tipo_peca = ''
                    posicao_peca = ''
                    tags_list = []
                    image_status = 'Pendente Análise IA'
                    collection_id = batch.colecao_id if batch else None
                    subcolecao_id = batch.subcolecao_id if batch and hasattr(batch, 'subcolecao_id') else None
                    brand_id = batch.marca_id if batch else None
                    estilista = ''
                    origem = ''
                    referencia_estilo = ''
                    match_source = 'sem_match'
                
                ext = os.path.splitext(original_filename)[1] or '.jpg'
                new_image = Image(
                    filename=f"{sku}{ext}",
                    original_name=original_filename,
                    storage_path=storage_path,
                    sku=sku,
                    description=description,
                    tags=json.dumps(tags_list),
                    ai_item_type=tipo_peca if tipo_peca else (categoria if carteira_data else None),
                    ai_color=cor if carteira_data else None,
                    ai_material=material if material else None,
                    ai_pattern=posicao_peca if posicao_peca else None,
                    ai_style=None,
                    uploader_id=batch.usuario_id if batch else None,
                    collection_id=collection_id,
                    subcolecao_id=subcolecao_id,
                    brand_id=brand_id,
                    unique_code=unique_code,
                    status=image_status,
                    estilista=estilista if estilista else None,
                    origem=origem if origem else None,
                    referencia_estilo=referencia_estilo if referencia_estilo else None
                )
                self.db.session.add(new_image)
                self.db.session.flush()
                
                if carteira_data and carteira_data.get('found'):
                    position_ref = 'Peça Única'
                    if posicao_peca:
                        if 'TOP' in posicao_peca.upper():
                            position_ref = 'Peça Superior'
                        elif 'BOTTOM' in posicao_peca.upper():
                            position_ref = 'Peça Inferior'
                        elif 'INTEIRO' in posicao_peca.upper():
                            position_ref = 'Peça Única'
                    
                    new_item_obj = ImageItem(
                        image_id=new_image.id,
                        item_order=1,
                        position_ref=position_ref,
                        description=description,
                        tags=json.dumps(tags_list),
                        ai_item_type=tipo_peca if tipo_peca else categoria,
                        ai_color=cor,
                        ai_material=material if material else None,
                        ai_pattern=None,
                        ai_style=None
                    )
                    self.db.session.add(new_item_obj)
                
                item = self.db.session.get(BatchItem, item_id)
                item.status = 'Sucesso'
                item.storage_path = storage_path
                item.image_id = new_image.id
                item.ai_description = description
                item.ai_tags = json.dumps(tags_list)
                item.ai_attributes = json.dumps({
                    'match_source': match_source,
                    'categoria': categoria,
                    'cor': cor,
                    'carteira_id': carteira_data.get('carteira_id') if carteira_data else None,
                    'subcolecao_id': subcolecao_id,
                    'estilista': estilista if estilista else None,
                    'origem': origem if origem else None,
                    'referencia_estilo': referencia_estilo if referencia_estilo else None
                })
                item.processed_at = datetime.utcnow()
                item.erro_mensagem = None
                
                self.db.session.commit()
                
                return {'success': True, 'image_id': new_image.id, 'match_source': match_source}
                
            except Exception as e:
                error_msg = str(e)
                print(f"[ERROR] Failed to process {sku}: {error_msg}")
                traceback.print_exc()
                
                self.db.session.rollback()
                
                item = self.db.session.get(BatchItem, item_id)
                if item:
                    item.status = 'Erro'
                    item.erro_mensagem = error_msg[:500]
                    item.processed_at = datetime.utcnow()
                    self.db.session.commit()
                
                return {'success': False, 'error': error_msg}
    
    def _update_batch_progress_atomic(self, batch_id, success):
        """Atualiza o progresso do batch de forma atômica usando lock"""
        with progress_lock:
            with self.app.app_context():
                self.db.session.remove()
                from app import BatchUpload
                batch = self.db.session.get(BatchUpload, batch_id)
                if batch:
                    batch.processados = (batch.processados or 0) + 1
                    if success:
                        batch.sucesso = (batch.sucesso or 0) + 1
                    else:
                        batch.falhas = (batch.falhas or 0) + 1
                    self.db.session.commit()
    
    def _cleanup_temp_files(self, temp_file_paths):
        """Remove arquivos temporários e diretório após processamento"""
        import shutil
        temp_dirs = set()
        
        for file_info in temp_file_paths:
            temp_path = file_info.get('temp_path')
            if temp_path:
                temp_dirs.add(os.path.dirname(temp_path))
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception as e:
                        print(f"[WARN] Could not delete temp file {temp_path}: {e}")
        
        for temp_dir in temp_dirs:
            if temp_dir and os.path.exists(temp_dir) and temp_dir.startswith('/tmp'):
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception as e:
                    print(f"[WARN] Could not delete temp dir {temp_dir}: {e}")


def extract_sku_from_filename(filename):
    """Extrai o SKU completo do nome do arquivo (remove extensão)"""
    name = os.path.basename(filename)
    sku = os.path.splitext(name)[0]
    return sku.strip()


def extract_sku_base_and_sequence(sku_completo):
    """
    Extrai o SKU base e a sequência/ângulo de um SKU completo.
    
    Exemplos:
        "ABC123_01" -> ("ABC123", "01")
        "ABC123_02" -> ("ABC123", "02")
        "ABC123-A" -> ("ABC123", "A")
        "ABC123-B" -> ("ABC123", "B")
        "ABC123_FRENTE" -> ("ABC123", "FRENTE")
        "ABC123" -> ("ABC123", None)  # Sem sufixo = imagem principal
    
    Padrões reconhecidos:
        - _01, _02, _03... (números com underscore)
        - -01, -02, -03... (números com hífen)
        - _A, _B, _C... (letras com underscore)
        - -A, -B, -C... (letras com hífen)
        - _FRENTE, _COSTAS, _LATERAL... (descritivos)
    """
    import re
    
    if not sku_completo:
        return (None, None)
    
    sku = sku_completo.strip()
    
    patterns = [
        r'^(.+?)[-_](\d{1,3})$',
        r'^(.+?)[-_]([A-Za-z])$',
        r'^(.+?)[-_](FRENTE|COSTAS|LATERAL|DETALHE|ZOOM|VERSO|CIMA|BAIXO|TOP|BOTTOM)$',
    ]
    
    for pattern in patterns:
        match = re.match(pattern, sku, re.IGNORECASE)
        if match:
            sku_base = match.group(1).strip()
            sequencia = match.group(2).strip().upper()
            return (sku_base, sequencia)
    
    return (sku, None)


def extract_zip_to_temp(zip_path, temp_dir):
    """
    Extrai arquivos de imagem de um ZIP para diretório temporário usando streaming
    
    Args:
        zip_path: Caminho do arquivo ZIP
        temp_dir: Diretório temporário para extrair
    
    Returns:
        Lista de dicts com {sku, temp_path, filename}
    """
    import shutil
    files_data = []
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp'}
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        for file_info in zip_ref.infolist():
            if file_info.is_dir():
                continue
            
            filename = os.path.basename(file_info.filename)
            ext = os.path.splitext(filename)[1].lower()
            
            if ext not in allowed_extensions:
                continue
            
            if filename.startswith('.') or filename.startswith('__'):
                continue
            
            sku = extract_sku_from_filename(filename)
            if not sku:
                continue
            
            temp_filename = f"zip_{sku}_{len(files_data)}{ext}"
            temp_path = os.path.join(temp_dir, temp_filename)
            
            with zip_ref.open(file_info.filename) as src, open(temp_path, 'wb') as dst:
                shutil.copyfileobj(src, dst, length=1024*1024)
            
            files_data.append({
                'sku': sku,
                'temp_path': temp_path,
                'filename': filename
            })
    
    return files_data


def get_batch_processor(app, db, object_storage, analyze_func=None):
    """Factory function para criar um BatchProcessor"""
    return BatchProcessor(app, db, object_storage, analyze_func)
