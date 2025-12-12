"""
Batch Processor - Sistema de processamento em lote de imagens
Processa imagens em paralelo usando ThreadPoolExecutor com sessões de banco isoladas
Match primário com CarteiraCompras, API como fallback futuro
"""

import os
import io
import json
import zipfile
import tempfile
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import time
from PIL import Image as PILImage

from oaz_logger import (
    info, debug, warn, error, success,
    log_start, log_end, log_progress, log_error,
    batch_log, M
)

try:
    from rpa_monitor_client import rpa_info, rpa_warn, rpa_error as rpa_err
except ImportError:
    def rpa_info(msg): pass
    def rpa_warn(msg): pass
    def rpa_err(msg, **kwargs): pass

MAX_WORKERS = 5
MAX_RETRIES = 3
RETRY_DELAY = 2

progress_lock = Lock()

def log_batch(message, level="INFO"):
    """Logger centralizado para batch processing - usando sistema OAZ + RPA Monitor"""
    if level == "ERROR":
        error(M.BATCH, 'PROCESS', message)
        rpa_err(f"[BATCH] {message}", regiao="batch")
    elif level == "WARN":
        warn(M.BATCH, 'PROCESS', message)
        rpa_warn(f"[BATCH] {message}")
    elif level == "DEBUG":
        debug(M.BATCH, 'PROCESS', message)
    else:
        info(M.BATCH, 'PROCESS', message)
        rpa_info(f"[BATCH] {message}")

def generate_thumbnail_bytes(image_data, max_width=300, quality=75):
    """Gera thumbnail a partir de dados de imagem
    
    Args:
        image_data: bytes da imagem original
        max_width: largura máxima do thumbnail (default 300px)
        quality: qualidade JPEG (default 75%)
    
    Returns:
        tuple: (thumbnail_bytes, width, height, file_size) ou (None, None, None, None) em caso de erro
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
        
        log_batch(f"Thumbnail gerado: {width}x{height}, {file_size/1024:.1f}KB (original: {original_width}x{original_height})")
        
        return thumbnail_bytes, width, height, file_size
        
    except Exception as e:
        log_batch(f"Falha ao gerar thumbnail: {e}", "ERROR")
        return None, None, None, None

class BatchProcessor:
    """Processador de lotes de imagens com threading e sessões isoladas"""
    
    def __init__(self, app, db, object_storage, analyze_func=None):
        self.app = app
        self.db = db
        self.object_storage = object_storage
        self.analyze_func = analyze_func
        self.max_workers = app.config.get('MAX_BATCH_WORKERS', MAX_WORKERS)
    
    def process_batch(self, batch_id, temp_file_paths, skip_cleanup=False):
        """
        Processa um lote de imagens em paralelo usando arquivos temporários
        
        Args:
            batch_id: ID do BatchUpload
            temp_file_paths: Lista de dicts com {item_id, sku, temp_path, filename}
            skip_cleanup: Se True, não remove arquivos temporários após processamento
        """
        log_batch(f"========== INICIANDO PROCESSAMENTO LOTE #{batch_id} ==========")
        log_batch(f"Total de arquivos para processar: {len(temp_file_paths)}")
        log_batch(f"Workers paralelos: {self.max_workers}")
        
        with self.app.app_context():
            from app import BatchUpload
            batch = self.db.session.get(BatchUpload, batch_id)
            if not batch:
                log_batch(f"ERRO: Lote #{batch_id} não encontrado no banco!", "ERROR")
                return
            
            batch.status = 'Processando'
            batch.started_at = datetime.utcnow()
            self.db.session.commit()
            log_batch(f"Status do lote atualizado para 'Processando'")
        
        processed_count = 0
        success_count = 0
        error_count = 0
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {}
            
            log_batch(f"Submetendo {len(temp_file_paths)} tarefas ao executor...")
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
            
            log_batch(f"Todas as tarefas submetidas. Aguardando conclusão...")
            
            for future in as_completed(futures):
                item_id = futures[future]
                try:
                    result = future.result()
                    processed_count += 1
                    if result['success']:
                        success_count += 1
                    else:
                        error_count += 1
                    self._update_batch_progress_atomic(batch_id, result['success'])
                    
                    if processed_count % 10 == 0 or processed_count == len(temp_file_paths):
                        elapsed = time.time() - start_time
                        rate = processed_count / elapsed if elapsed > 0 else 0
                        remaining = len(temp_file_paths) - processed_count
                        eta = remaining / rate if rate > 0 else 0
                        log_batch(f"Progresso: {processed_count}/{len(temp_file_paths)} ({success_count} OK, {error_count} erros) - {rate:.1f} img/s - ETA: {eta:.0f}s")
                        
                except Exception as e:
                    error_count += 1
                    processed_count += 1
                    log_batch(f"EXCEÇÃO processando item {item_id}: {e}", "ERROR")
                    self._update_batch_progress_atomic(batch_id, False)
        
        elapsed_total = time.time() - start_time
        log_batch(f"========== PROCESSAMENTO CONCLUÍDO ==========")
        log_batch(f"Lote #{batch_id}: {processed_count} processados em {elapsed_total:.1f}s")
        log_batch(f"Sucesso: {success_count} | Erros: {error_count}")
        log_batch(f"Taxa média: {processed_count/elapsed_total:.1f} imagens/segundo")
        
        with self.app.app_context():
            batch = self.db.session.get(BatchUpload, batch_id)
            if batch:
                batch.status = 'Concluído'
                batch.finished_at = datetime.utcnow()
                self.db.session.commit()
                log_batch(f"Status do lote atualizado para 'Concluído'")
        
        if not skip_cleanup:
            self._cleanup_temp_files(temp_file_paths)
            log_batch(f"Arquivos temporários limpos")
        else:
            log_batch(f"Cleanup ignorado (skip_cleanup=True)")
    
    def _match_carteira_compras_in_session(self, sku_completo, colecao_id=None):
        """
        Busca dados da CarteiraCompras pelo SKU usando a sessão atual.
        Tenta primeiro com SKU completo, depois com SKU base (sem sufixos).
        IMPORTANTE: Só faz match com a coleção especificada (do batch).
        
        Args:
            sku_completo: Código SKU completo do arquivo (ex: ABC123_01)
            colecao_id: ID da coleção do batch (filtra o match apenas para esta coleção)
            
        Returns:
            Dict com dados da carteira ou None se não encontrar
        """
        from app import CarteiraCompras
        from sqlalchemy import func
        
        sku_base, sequencia = extract_sku_base_and_sequence(sku_completo)
        
        query = self.db.session.query(CarteiraCompras).filter_by(sku=sku_completo)
        if colecao_id:
            query = query.filter(CarteiraCompras.colecao_id == colecao_id)
        carteira = query.first()
        
        if not carteira:
            sku_upper = sku_completo.upper().strip()
            query = self.db.session.query(CarteiraCompras).filter(
                func.upper(func.trim(CarteiraCompras.sku)) == sku_upper
            )
            if colecao_id:
                query = query.filter(CarteiraCompras.colecao_id == colecao_id)
            carteira = query.first()
        
        if not carteira and sku_base and sku_base != sku_completo:
            query = self.db.session.query(CarteiraCompras).filter_by(sku=sku_base)
            if colecao_id:
                query = query.filter(CarteiraCompras.colecao_id == colecao_id)
            carteira = query.first()
            
            if not carteira:
                sku_base_upper = sku_base.upper().strip()
                query = self.db.session.query(CarteiraCompras).filter(
                    func.upper(func.trim(CarteiraCompras.sku)) == sku_base_upper
                )
                if colecao_id:
                    query = query.filter(CarteiraCompras.colecao_id == colecao_id)
                carteira = query.first()
        
        if carteira:
            return {
                'found': True,
                'sku_base': sku_base,
                'sequencia': sequencia,
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
        
        return {
            'found': False,
            'sku_base': sku_base,
            'sequencia': sequencia
        }
    
    def _process_single_item_isolated(self, batch_id, item_id, sku, temp_path, original_filename):
        """Processa um único item com sessão de banco isolada"""
        from app import BatchUpload, BatchItem, Image, ImageItem, CarteiraCompras, ImageThumbnail
        
        log_batch(f"[{sku}] Iniciando processamento...")
        
        with self.app.app_context():
            self.db.session.remove()
            
            item = self.db.session.get(BatchItem, item_id)
            if not item:
                log_batch(f"[{sku}] Item #{item_id} não encontrado!", "ERROR")
                return {'success': False, 'error': 'Item not found'}
            
            item.status = 'Processando'
            item.processing_status = 'processing'
            item.tentativas += 1
            self.db.session.commit()
            log_batch(f"[{sku}] Tentativa #{item.tentativas}")
            
            try:
                if not os.path.exists(temp_path):
                    log_batch(f"[{sku}] Arquivo temporário não encontrado: {temp_path}", "ERROR")
                    raise FileNotFoundError(f"Temp file not found: {temp_path}")
                
                file_size_mb = os.path.getsize(temp_path) / (1024 * 1024)
                log_batch(f"[{sku}] Arquivo: {original_filename} ({file_size_mb:.2f}MB)")
                
                batch = self.db.session.get(BatchUpload, batch_id)
                batch_colecao_id = batch.colecao_id if batch else None
                
                log_batch(f"[{sku}] Buscando na Carteira de Compras (coleção: {batch_colecao_id})...")
                carteira_data = self._match_carteira_compras_in_session(sku, colecao_id=batch_colecao_id)
                
                if carteira_data and carteira_data.get('found'):
                    log_batch(f"[{sku}] ✓ MATCH encontrado na Carteira! Desc: {carteira_data.get('descricao', '')[:50]}...")
                else:
                    log_batch(f"[{sku}] ✗ Sem match na Carteira - será marcado para análise IA")
                
                log_batch(f"[{sku}] Fazendo upload para Object Storage...")
                storage_result = None
                for attempt in range(MAX_RETRIES):
                    try:
                        with open(temp_path, 'rb') as f:
                            ext = os.path.splitext(original_filename)[1] or '.jpg'
                            storage_result = self.object_storage.upload_file(f, f"{sku}{ext}")
                        log_batch(f"[{sku}] ✓ Upload concluído")
                        break
                    except Exception as e:
                        log_batch(f"[{sku}] Upload falhou (tentativa {attempt+1}/{MAX_RETRIES}): {e}", "WARN")
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(RETRY_DELAY * (attempt + 1))
                        else:
                            raise e
                
                storage_path = storage_result.get('storage_path') if storage_result else None
                
                import uuid
                unique_code = f"IMG-{uuid.uuid4().hex[:8].upper()}"
                
                if carteira_data and carteira_data.get('found'):
                    nome_peca = carteira_data.get('descricao', '')  # Nome da peça vai para campo separado
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
                    
                    from app import Produto
                    produto = self.db.session.query(Produto).filter_by(sku=carteira_data.get('sku_base', sku)).first()
                    if not produto:
                        produto = self.db.session.query(Produto).filter_by(sku=sku).first()
                    if produto:
                        produto.tem_foto = True
                        self.db.session.add(produto)
                        log_batch(f"[{sku}] ✓ Produto atualizado: tem_foto=True")
                    
                    match_source = 'carteira'
                else:
                    nome_peca = ''  # Sem match, nome_peca vazio
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
                
                sku_base = carteira_data.get('sku_base', sku) if carteira_data else sku
                sequencia = carteira_data.get('sequencia') if carteira_data else None
                
                ext = os.path.splitext(original_filename)[1] or '.jpg'
                new_image = Image(
                    filename=f"{sku}{ext}",
                    original_name=original_filename,
                    storage_path=storage_path,
                    sku=sku,
                    sku_base=sku_base,
                    sequencia=sequencia,
                    nome_peca=nome_peca,  # Nome da peça da Carteira
                    description=None,  # Descrição será preenchida pela IA
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
                        description=nome_peca,  # ImageItem recebe o nome da peça
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
                item.processing_status = 'completed'
                item.storage_path = storage_path
                item.image_id = new_image.id
                item.ai_description = nome_peca
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
                
                log_batch(f"[{sku}] Gerando thumbnail...")
                try:
                    with open(temp_path, 'rb') as f:
                        image_data = f.read()
                    thumbnail_bytes, thumb_width, thumb_height, thumb_size = generate_thumbnail_bytes(image_data)
                    
                    if thumbnail_bytes:
                        thumbnail = ImageThumbnail(
                            image_id=new_image.id,
                            thumbnail_data=thumbnail_bytes,
                            width=thumb_width,
                            height=thumb_height,
                            file_size=thumb_size,
                            mime_type='image/jpeg'
                        )
                        self.db.session.add(thumbnail)
                        log_batch(f"[{sku}] ✓ Thumbnail salvo: {thumb_size/1024:.1f}KB")
                    else:
                        log_batch(f"[{sku}] ⚠ Thumbnail não gerado", "WARN")
                except Exception as thumb_err:
                    log_batch(f"[{sku}] ⚠ Erro ao gerar thumbnail: {thumb_err}", "WARN")
                
                self.db.session.commit()
                
                log_batch(f"[{sku}] ✓ SUCESSO - Imagem #{new_image.id} criada (match: {match_source})")
                return {'success': True, 'image_id': new_image.id, 'match_source': match_source}
                
            except Exception as e:
                error_msg = str(e)
                log_batch(f"[{sku}] ✗ ERRO: {error_msg}", "ERROR")
                traceback.print_exc()
                
                self.db.session.rollback()
                
                item = self.db.session.get(BatchItem, item_id)
                if item:
                    item.status = 'Erro'
                    item.processing_status = 'failed'
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
    
    def process_multiple_batches(self, batch_ids):
        """
        Processa múltiplos batches em sequência.
        Usado para processar todos os batches de uma fila de upload.
        
        Args:
            batch_ids: Lista de IDs de BatchUpload para processar
        """
        log_batch(f"========== PROCESSANDO MÚLTIPLOS BATCHES ==========")
        log_batch(f"Total de batches para processar: {len(batch_ids)}")
        
        all_temp_files = []
        
        for idx, batch_id in enumerate(batch_ids):
            log_batch(f"--- Batch {idx + 1}/{len(batch_ids)} (ID: {batch_id}) ---")
            
            temp_file_paths = []
            
            with self.app.app_context():
                from app import BatchUpload, BatchItem
                
                batch = self.db.session.get(BatchUpload, batch_id)
                if not batch:
                    log_batch(f"Batch {batch_id} não encontrado, pulando...", "WARN")
                    continue
                
                pending_items = self.db.session.query(BatchItem).filter_by(
                    batch_id=batch_id,
                    processing_status='pending'
                ).all()
                
                if not pending_items:
                    log_batch(f"Batch {batch_id} não tem itens pendentes, pulando...")
                    batch.status = 'Concluído'
                    self.db.session.commit()
                    continue
                
                for item in pending_items:
                    if item.received_path:
                        is_object_storage = item.received_path.startswith('images/')
                        is_local_file = os.path.exists(item.received_path) if not is_object_storage else False
                        
                        if is_object_storage or is_local_file:
                            temp_file_paths.append({
                                'item_id': item.id,
                                'sku': item.sku,
                                'temp_path': item.received_path,
                                'filename': item.filename_original,
                                'is_object_storage': is_object_storage
                            })
                        else:
                            log_batch(f"Arquivo não encontrado para item {item.id}: {item.received_path}", "WARN")
                            item.processing_status = 'failed'
                            item.erro_mensagem = 'Arquivo não encontrado'
                    else:
                        log_batch(f"Item {item.id} sem caminho de arquivo", "WARN")
                        item.processing_status = 'failed'
                        item.erro_mensagem = 'Caminho de arquivo vazio'
                
                self.db.session.commit()
            
            if temp_file_paths:
                log_batch(f"Iniciando processamento de {len(temp_file_paths)} itens do batch {batch_id}")
                all_temp_files.extend(temp_file_paths)
                self.process_batch(batch_id, temp_file_paths, skip_cleanup=True)
            else:
                with self.app.app_context():
                    batch = self.db.session.get(BatchUpload, batch_id)
                    if batch:
                        log_batch(f"Nenhum arquivo válido para processar no batch {batch_id}", "WARN")
                        batch.status = 'Erro'
                        batch.finished_at = datetime.utcnow()
                        self.db.session.commit()
        
        if all_temp_files:
            log_batch(f"Limpando {len(all_temp_files)} arquivos temporários de todos os batches...")
            self._cleanup_temp_files(all_temp_files)
        
        log_batch(f"========== TODOS OS BATCHES PROCESSADOS ==========")


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
    skipped_files = {'system': 0, 'extension': 0, 'hidden': 0, 'no_sku': 0}
    allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        total_files = len([f for f in zip_ref.infolist() if not f.is_dir()])
        log_batch(f"[ZIP] Total de arquivos no ZIP: {total_files}", "INFO")
        
        for file_info in zip_ref.infolist():
            if file_info.is_dir():
                continue
            
            full_path = file_info.filename
            filename = os.path.basename(full_path)
            
            if '__MACOSX' in full_path or '.DS_Store' in full_path or 'Thumbs.db' in filename:
                skipped_files['system'] += 1
                continue
            
            ext = os.path.splitext(filename)[1].lower()
            
            if ext not in allowed_extensions:
                skipped_files['extension'] += 1
                continue
            
            if filename.startswith('.') or filename.startswith('__'):
                skipped_files['hidden'] += 1
                continue
            
            sku = extract_sku_from_filename(filename)
            if not sku:
                skipped_files['no_sku'] += 1
                log_batch(f"[ZIP] Arquivo sem SKU ignorado: {filename}", "WARN")
                continue
            
            temp_filename = f"zip_{sku}_{len(files_data)}{ext}"
            temp_path = os.path.join(temp_dir, temp_filename)
            
            try:
                with zip_ref.open(file_info.filename) as src, open(temp_path, 'wb') as dst:
                    shutil.copyfileobj(src, dst, length=1024*1024)
                
                files_data.append({
                    'sku': sku,
                    'temp_path': temp_path,
                    'filename': filename
                })
            except Exception as e:
                log_batch(f"[ZIP] Erro ao extrair {filename}: {e}", "ERROR")
    
    total_skipped = sum(skipped_files.values())
    if total_skipped > 0:
        log_batch(f"[ZIP] Arquivos ignorados: {total_skipped} (sistema: {skipped_files['system']}, extensão inválida: {skipped_files['extension']}, ocultos: {skipped_files['hidden']}, sem SKU: {skipped_files['no_sku']})", "INFO")
    
    log_batch(f"[ZIP] Extração completa: {len(files_data)} imagens válidas de {total_files} arquivos", "INFO")
    
    return files_data


def get_batch_processor(app, db, object_storage, analyze_func=None):
    """Factory function para criar um BatchProcessor"""
    return BatchProcessor(app, db, object_storage, analyze_func)
