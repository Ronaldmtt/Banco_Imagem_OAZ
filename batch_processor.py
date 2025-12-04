"""
Batch Processor - Sistema de processamento em lote de imagens
Processa imagens em paralelo usando ThreadPoolExecutor com sessões de banco isoladas
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
    
    def __init__(self, app, db, object_storage, analyze_func):
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
    
    def _process_single_item_isolated(self, batch_id, item_id, sku, temp_path, original_filename):
        """Processa um único item com sessão de banco isolada"""
        from app import db, BatchUpload, BatchItem, Image, ImageItem
        
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
                
                ai_result = None
                ai_items = []
                for attempt in range(MAX_RETRIES):
                    try:
                        ai_result = self.analyze_func(temp_path)
                        if isinstance(ai_result, list):
                            ai_items = ai_result
                        break
                    except Exception as e:
                        if attempt < MAX_RETRIES - 1:
                            time.sleep(RETRY_DELAY * (attempt + 1))
                        else:
                            print(f"[WARN] AI analysis failed after {MAX_RETRIES} attempts for {sku}: {e}")
                
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
                
                first_item = ai_items[0] if ai_items else {}
                first_attrs = first_item.get('attributes', {}) if first_item else {}
                
                import uuid
                unique_code = f"IMG-{uuid.uuid4().hex[:8].upper()}"
                
                batch = self.db.session.get(BatchUpload, batch_id)
                
                ext = os.path.splitext(original_filename)[1] or '.jpg'
                new_image = Image(
                    filename=f"{sku}{ext}",
                    original_name=original_filename,
                    storage_path=storage_path,
                    sku=sku,
                    description=first_item.get('description', ''),
                    tags=json.dumps(first_item.get('tags', [])) if first_item else json.dumps([]),
                    ai_item_type=first_attrs.get('item_type'),
                    ai_color=first_attrs.get('color'),
                    ai_material=first_attrs.get('material'),
                    ai_pattern=first_attrs.get('pattern'),
                    ai_style=first_attrs.get('style'),
                    uploader_id=batch.usuario_id if batch else None,
                    collection_id=batch.colecao_id if batch else None,
                    brand_id=batch.marca_id if batch else None,
                    unique_code=unique_code,
                    status='Pendente'
                )
                self.db.session.add(new_image)
                self.db.session.flush()
                
                for item_data in ai_items:
                    attrs = item_data.get('attributes', {})
                    new_item_obj = ImageItem(
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
                    self.db.session.add(new_item_obj)
                
                item = self.db.session.get(BatchItem, item_id)
                item.status = 'Sucesso'
                item.storage_path = storage_path
                item.image_id = new_image.id
                item.ai_description = first_item.get('description', '')
                item.ai_tags = json.dumps(first_item.get('tags', []))
                item.ai_attributes = json.dumps(first_attrs)
                item.processed_at = datetime.utcnow()
                item.erro_mensagem = None
                
                self.db.session.commit()
                
                return {'success': True, 'image_id': new_image.id}
                
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
    """Extrai o SKU do nome do arquivo (remove extensão)"""
    name = os.path.basename(filename)
    sku = os.path.splitext(name)[0]
    return sku.strip()


def save_uploaded_files_to_temp(files_data, temp_dir):
    """
    Salva arquivos em disco temporário para processamento posterior
    
    Args:
        files_data: Lista de dicts com {sku, file_bytes, filename}
        temp_dir: Diretório temporário para salvar os arquivos
    
    Returns:
        Lista de dicts com {sku, temp_path, filename} (sem file_bytes)
    """
    result = []
    for i, file_data in enumerate(files_data):
        sku = file_data['sku']
        filename = file_data['filename']
        file_bytes = file_data['file_bytes']
        
        ext = os.path.splitext(filename)[1] or '.jpg'
        temp_filename = f"batch_{i}_{sku}{ext}"
        temp_path = os.path.join(temp_dir, temp_filename)
        
        with open(temp_path, 'wb') as f:
            f.write(file_bytes)
        
        result.append({
            'sku': sku,
            'temp_path': temp_path,
            'filename': filename
        })
        
        file_data['file_bytes'] = None
    
    return result


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


def get_batch_processor(app, db, object_storage, analyze_func):
    """Factory function para criar um BatchProcessor"""
    return BatchProcessor(app, db, object_storage, analyze_func)
