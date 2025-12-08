"""
Upload Orchestrator - Sistema de fila assíncrona para uploads em grande escala
Gerencia pool de workers com cleanup de recursos e processamento FIFO
"""

import os
import gc
import shutil
import tempfile
import threading
import traceback
from queue import Queue, Empty
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Event

MAX_UPLOAD_WORKERS = 3
BATCH_INSERT_SIZE = 500
PROGRESS_UPDATE_INTERVAL = 50

class UploadJob:
    """Representa um job de upload na fila"""
    def __init__(self, batch_id, archive_path, temp_dir, metadata):
        self.batch_id = batch_id
        self.archive_path = archive_path
        self.temp_dir = temp_dir
        self.metadata = metadata
        self.created_at = datetime.utcnow()
        self.status = 'queued'
        self.error = None


class UploadOrchestrator:
    """
    Orquestrador de uploads em grande escala
    - Fila FIFO para jobs de upload
    - Pool de workers com limite máximo
    - Cleanup de recursos após cada job
    - Bulk inserts para performance
    """
    
    _instance = None
    _lock = Lock()
    
    def __new__(cls, *args, **kwargs):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance
    
    def __init__(self, app=None, db=None, object_storage=None):
        if self._initialized:
            return
        
        self.app = app
        self.db = db
        self.object_storage = object_storage
        
        self.job_queue = Queue()
        self.active_jobs = {}
        self.completed_jobs = {}
        
        self.max_workers = MAX_UPLOAD_WORKERS
        self.workers = []
        self.workers_started = False
        self.shutdown_event = Event()
        self.stats_lock = Lock()
        
        self.stats = {
            'total_queued': 0,
            'total_processed': 0,
            'total_errors': 0,
            'active_workers': 0
        }
        
        self._initialized = True
    
    def configure(self, app, db, object_storage):
        """Configura o orquestrador com instâncias da aplicação e inicia workers"""
        self.app = app
        self.db = db
        self.object_storage = object_storage
        
        if not self.workers_started:
            self._recover_stuck_items()
            self._start_workers()
            self._start_watchdog()
            self.workers_started = True
    
    def _recover_stuck_items(self):
        """Recupera itens que estavam em 'processing' ou 'receiving' quando o servidor caiu"""
        from datetime import timedelta
        import shutil
        
        TEMP_UPLOAD_DIR = '/tmp/batch_uploads'
        
        with self.app.app_context():
            self.db.session.remove()
            from app import BatchItem, BatchUpload
            
            receiving_items = BatchItem.query.filter(
                BatchItem.reception_status == 'receiving'
            ).all()
            
            if receiving_items:
                print(f"[WATCHDOG] Found {len(receiving_items)} items stuck in 'receiving', marking as failed...")
                for item in receiving_items:
                    item.reception_status = 'failed'
                    item.status = 'Erro'
                    item.last_error = 'Reception interrupted by server restart'
                    if item.received_path and os.path.exists(item.received_path):
                        try:
                            os.remove(item.received_path)
                        except:
                            pass
                self.db.session.commit()
            
            stuck_items = BatchItem.query.filter(
                BatchItem.processing_status == 'processing'
            ).all()
            
            if stuck_items:
                print(f"[WATCHDOG] Found {len(stuck_items)} stuck items in 'processing', resetting to retry...")
                for item in stuck_items:
                    item.processing_status = 'retry'
                    item.status = 'Pendente'
                    item.retry_count = (item.retry_count or 0) + 1
                    item.last_error = 'Recovered after server restart'
                    item.worker_id = None
                    item.heartbeat_at = None
                self.db.session.commit()
            
            stuck_batches = BatchUpload.query.filter(
                BatchUpload.status.in_(['Processando', 'Recebendo'])
            ).all()
            
            if stuck_batches:
                print(f"[WATCHDOG] Found {len(stuck_batches)} stuck batches, checking status...")
                for batch in stuck_batches:
                    pending_count = BatchItem.query.filter(
                        BatchItem.batch_id == batch.id,
                        BatchItem.processing_status.in_(['pending', 'retry'])
                    ).count()
                    
                    completed_count = BatchItem.query.filter(
                        BatchItem.batch_id == batch.id,
                        BatchItem.processing_status == 'completed'
                    ).count()
                    
                    failed_count = BatchItem.query.filter(
                        BatchItem.batch_id == batch.id,
                        BatchItem.processing_status == 'failed'
                    ).count()
                    
                    if pending_count > 0:
                        batch.status = 'Pendente'
                        print(f"[WATCHDOG] Batch {batch.id} has {pending_count} pending items, marked as Pendente")
                    elif failed_count > 0 and completed_count == 0:
                        batch.status = 'Erro'
                        batch.finished_at = datetime.utcnow()
                    else:
                        batch.status = 'Concluído'
                        batch.finished_at = datetime.utcnow()
                    
                    batch.sucesso = completed_count
                    batch.falhas = failed_count
                    batch.processados = completed_count + failed_count
                
                self.db.session.commit()
            
            self.db.session.remove()
    
    def _start_watchdog(self):
        """Inicia thread watchdog para detectar itens travados"""
        watchdog = threading.Thread(
            target=self._watchdog_loop,
            name='UploadWatchdog',
            daemon=True
        )
        watchdog.start()
        print("[WATCHDOG] Started watchdog thread")
    
    def _watchdog_loop(self):
        """Loop do watchdog - verifica itens travados a cada 60 segundos"""
        from datetime import timedelta
        
        STUCK_TIMEOUT_SECONDS = 300  # 5 minutos sem heartbeat = travado
        CHECK_INTERVAL = 60  # Verifica a cada 60 segundos
        
        while not self.shutdown_event.is_set():
            try:
                self.shutdown_event.wait(CHECK_INTERVAL)
                if self.shutdown_event.is_set():
                    break
                
                with self.app.app_context():
                    self.db.session.remove()
                    from app import BatchItem
                    
                    timeout_threshold = datetime.utcnow() - timedelta(seconds=STUCK_TIMEOUT_SECONDS)
                    
                    stuck_items = BatchItem.query.filter(
                        BatchItem.processing_status == 'processing',
                        BatchItem.heartbeat_at < timeout_threshold
                    ).all()
                    
                    if stuck_items:
                        print(f"[WATCHDOG] Found {len(stuck_items)} items stuck for > {STUCK_TIMEOUT_SECONDS}s")
                        for item in stuck_items:
                            if item.retry_count < item.max_retries:
                                item.processing_status = 'retry'
                                item.retry_count += 1
                                item.last_error = f'Timeout after {STUCK_TIMEOUT_SECONDS}s'
                                item.worker_id = None
                            else:
                                item.processing_status = 'failed'
                                item.status = 'Erro'
                                item.last_error = f'Max retries ({item.max_retries}) exceeded'
                        
                        self.db.session.commit()
                    
                    self.db.session.remove()
                    
            except Exception as e:
                print(f"[WATCHDOG] Error: {e}")
    
    def _start_workers(self):
        """Inicia workers em background"""
        for i in range(self.max_workers):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f'UploadWorker-{i}',
                daemon=True
            )
            worker.start()
            self.workers.append(worker)
        print(f"[ORCHESTRATOR] Started {self.max_workers} upload workers")
    
    def _worker_loop(self):
        """Loop principal de cada worker"""
        while not self.shutdown_event.is_set():
            try:
                job = self.job_queue.get(timeout=1.0)
            except Empty:
                continue
            
            worker_name = threading.current_thread().name
            print(f"[{worker_name}] Processing job for batch {job.batch_id}")
            
            with self.stats_lock:
                self.stats['active_workers'] += 1
            
            try:
                self._process_job(job)
                job.status = 'completed'
                with self.stats_lock:
                    self.stats['total_processed'] += 1
            except Exception as e:
                job.status = 'failed'
                job.error = str(e)
                print(f"[{worker_name}] Job failed: {e}")
                traceback.print_exc()
                with self.stats_lock:
                    self.stats['total_errors'] += 1
            finally:
                self._cleanup_job(job)
                with self.stats_lock:
                    self.stats['active_workers'] -= 1
                
                self.completed_jobs[job.batch_id] = job
                if job.batch_id in self.active_jobs:
                    del self.active_jobs[job.batch_id]
                
                self.job_queue.task_done()
                gc.collect()
                
                print(f"[{worker_name}] Job completed. Queue size: {self.job_queue.qsize()}")
    
    def _process_job(self, job):
        """Processa um job de upload"""
        from batch_processor import extract_zip_to_temp, extract_sku_from_filename
        from app import BatchUpload, BatchItem, Image, ImageItem, CarteiraCompras
        
        with self.app.app_context():
            self.db.session.remove()
            
            batch = self.db.session.get(BatchUpload, job.batch_id)
            if not batch:
                raise ValueError(f"Batch {job.batch_id} not found")
            
            batch.status = 'Extraindo'
            self.db.session.commit()
            
            files_data = extract_zip_to_temp(job.archive_path, job.temp_dir)
            
            if not files_data:
                batch.status = 'Erro'
                batch.erro_mensagem = 'Nenhuma imagem válida encontrada no ZIP'
                self.db.session.commit()
                return
            
            for i, file_info in enumerate(files_data):
                file_info['file_index'] = i
            
            batch.total_arquivos = len(files_data)
            batch.status = 'Processando'
            batch.started_at = datetime.utcnow()
            self.db.session.commit()
            
            carteira_cache = self._warm_carteira_cache_in_session()
            
            batch_items_to_insert = []
            for i, file_info in enumerate(files_data):
                batch_items_to_insert.append({
                    'batch_id': job.batch_id,
                    'sku': file_info['sku'],
                    'filename_original': file_info['filename'],
                    'status': 'Pendente',
                    'tentativas': 0
                })
            
            for i in range(0, len(batch_items_to_insert), BATCH_INSERT_SIZE):
                chunk = batch_items_to_insert[i:i + BATCH_INSERT_SIZE]
                self.db.session.bulk_insert_mappings(BatchItem, chunk)
            self.db.session.commit()
            
            items = BatchItem.query.filter_by(batch_id=job.batch_id).order_by(BatchItem.id).all()
            
            for i, item in enumerate(items):
                if i < len(files_data):
                    files_data[i]['item_id'] = item.id
            
            self._process_files_parallel(job, files_data, carteira_cache)
            
            batch = self.db.session.get(BatchUpload, job.batch_id)
            batch.status = 'Concluído'
            batch.finished_at = datetime.utcnow()
            self.db.session.commit()
    
    def _warm_carteira_cache_in_session(self):
        """Carrega cache da Carteira usando a sessão atual"""
        from app import CarteiraCompras
        
        cache = {}
        carteiras = CarteiraCompras.query.all()
        for c in carteiras:
            sku_upper = c.sku.upper().strip() if c.sku else ''
            cache[sku_upper] = {
                'id': c.id,
                'descricao': c.descricao or '',
                'cor': c.cor or '',
                'categoria': c.categoria or '',
                'subcategoria': c.subcategoria or '',
                'colecao_id': c.colecao_id,
                'subcolecao_id': c.subcolecao_id,
                'marca_id': c.marca_id,
                'estilista': c.estilista or '',
                'origem': c.origem or '',
                'referencia_estilo': c.referencia_estilo or '',
                'material': c.material or c.categoria or '',
                'tipo_peca': c.tipo_peca or c.subcategoria or '',
                'posicao_peca': c.posicao_peca or ''
            }
        
        print(f"[CACHE] Loaded {len(cache)} Carteira entries")
        return cache
    
    def _process_files_parallel(self, job, files_data, carteira_cache):
        """Processa arquivos em LOTES DE 20 com sessões isoladas - resiliente a falhas"""
        BATCH_SIZE = 20  # Processa 20 imagens por vez
        processed = 0
        successes = 0
        failures = 0
        progress_lock = Lock()
        total_files = len(files_data)
        
        print(f"[BATCH] Processing {total_files} files in batches of {BATCH_SIZE}")
        
        def process_with_isolated_session(file_info):
            """Wrapper que garante sessão isolada por thread"""
            nonlocal processed, successes, failures
            
            with self.app.app_context():
                self.db.session.remove()
                
                try:
                    result = self._process_single_file_in_session(
                        job.batch_id,
                        file_info,
                        carteira_cache
                    )
                    
                    with progress_lock:
                        processed += 1
                        if result.get('success'):
                            successes += 1
                        else:
                            failures += 1
                    
                    return result
                    
                except Exception as e:
                    with progress_lock:
                        processed += 1
                        failures += 1
                    print(f"[ERROR] Processing {file_info.get('sku')}: {e}")
                    return {'success': False, 'error': str(e)}
                    
                finally:
                    temp_path = file_info.get('temp_path')
                    if temp_path and os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except:
                            pass
                    self.db.session.remove()
        
        for batch_start in range(0, total_files, BATCH_SIZE):
            batch_end = min(batch_start + BATCH_SIZE, total_files)
            batch_files = files_data[batch_start:batch_end]
            batch_num = (batch_start // BATCH_SIZE) + 1
            total_batches = (total_files + BATCH_SIZE - 1) // BATCH_SIZE
            
            print(f"[BATCH {batch_num}/{total_batches}] Processing files {batch_start+1}-{batch_end} of {total_files}")
            
            with ThreadPoolExecutor(max_workers=BATCH_SIZE) as executor:
                futures = [executor.submit(process_with_isolated_session, f) for f in batch_files]
                
                for future in futures:
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[ERROR] Future failed: {e}")
            
            with self.app.app_context():
                self.db.session.remove()
                from app import BatchUpload
                batch = self.db.session.get(BatchUpload, job.batch_id)
                if batch:
                    batch.processados = processed
                    batch.sucesso = successes
                    batch.falhas = failures
                    self.db.session.commit()
                self.db.session.remove()
            
            print(f"[BATCH {batch_num}/{total_batches}] Completed. Progress: {processed}/{total_files} ({successes} success, {failures} failed)")
            
            gc.collect()
        
        with self.app.app_context():
            self.db.session.remove()
            from app import BatchUpload
            batch = self.db.session.get(BatchUpload, job.batch_id)
            if batch:
                batch.processados = processed
                batch.sucesso = successes
                batch.falhas = failures
                self.db.session.commit()
        
        print(f"[BATCH] All batches completed: {processed}/{total_files} files processed")
    
    def _process_single_file_in_session(self, batch_id, file_info, carteira_cache):
        """Processa um único arquivo (já dentro de sessão isolada)"""
        import json
        import uuid as uuid_lib
        from app import BatchUpload, BatchItem, Image, CarteiraCompras
        from batch_processor import extract_sku_base_and_sequence
        
        temp_path = file_info.get('temp_path')
        sku = file_info.get('sku')
        original_filename = file_info.get('filename')
        item_id = file_info.get('item_id')
        
        try:
            if not temp_path or not os.path.exists(temp_path):
                raise FileNotFoundError(f"File not found: {temp_path}")
            
            sku_base, sequencia = extract_sku_base_and_sequence(sku)
            
            sku_upper = sku.upper().strip()
            sku_base_upper = sku_base.upper().strip() if sku_base else sku_upper
            
            carteira_data = carteira_cache.get(sku_upper)
            if not carteira_data and sku_base_upper != sku_upper:
                carteira_data = carteira_cache.get(sku_base_upper)
            
            storage_result = self._upload_file_streaming(temp_path, original_filename)
            storage_path = storage_result.get('storage_path')
            
            unique_code = f"IMG-{uuid_lib.uuid4().hex[:8].upper()}"
            
            batch = self.db.session.get(BatchUpload, batch_id)
            
            if carteira_data:
                tags_list = []
                if carteira_data.get('categoria'):
                    tags_list.append(carteira_data['categoria'])
                if carteira_data.get('subcategoria'):
                    tags_list.append(carteira_data['subcategoria'])
                if carteira_data.get('cor'):
                    tags_list.append(carteira_data['cor'])
                
                image_status = 'Pendente'
                collection_id = carteira_data.get('colecao_id') or (batch.colecao_id if batch else None)
                subcolecao_id = carteira_data.get('subcolecao_id')
                brand_id = carteira_data.get('marca_id') or (batch.marca_id if batch else None)
                
                carteira = self.db.session.get(CarteiraCompras, carteira_data['id'])
                if carteira:
                    carteira.status_foto = 'Com Foto'
                
                match_source = 'carteira'
            else:
                tags_list = []
                image_status = 'Pendente Análise IA'
                collection_id = batch.colecao_id if batch else None
                subcolecao_id = None
                brand_id = batch.marca_id if batch else None
                match_source = 'sem_match'
            
            ext = os.path.splitext(original_filename)[1] or '.jpg'
            new_image = Image(
                filename=f"{sku}{ext}",
                original_name=original_filename,
                storage_path=storage_path,
                sku=sku,
                sku_base=sku_base,
                sequencia=sequencia,
                description=carteira_data.get('descricao', '') if carteira_data else '',
                tags=json.dumps(tags_list),
                ai_item_type=carteira_data.get('tipo_peca') if carteira_data else None,
                ai_color=carteira_data.get('cor') if carteira_data else None,
                ai_material=carteira_data.get('material') if carteira_data else None,
                uploader_id=batch.usuario_id if batch else None,
                collection_id=collection_id,
                subcolecao_id=subcolecao_id,
                brand_id=brand_id,
                unique_code=unique_code,
                status=image_status,
                estilista=carteira_data.get('estilista') if carteira_data else None,
                origem=carteira_data.get('origem') if carteira_data else None,
                referencia_estilo=carteira_data.get('referencia_estilo') if carteira_data else None
            )
            self.db.session.add(new_image)
            self.db.session.flush()
            
            if item_id:
                item = self.db.session.get(BatchItem, item_id)
                if item:
                    item.status = 'Sucesso'
                    item.processing_status = 'completed'
                    item.storage_path = storage_path
                    item.image_id = new_image.id
                    item.processed_at = datetime.utcnow()
                    item.heartbeat_at = datetime.utcnow()
            
            self.db.session.commit()
            
            return {'success': True, 'image_id': new_image.id}
            
        except Exception as e:
            self.db.session.rollback()
            
            if item_id:
                try:
                    item = self.db.session.get(BatchItem, item_id)
                    if item:
                        item.status = 'Erro'
                        item.processing_status = 'failed'
                        item.erro_mensagem = str(e)[:500]
                        item.last_error = str(e)[:500]
                        item.retry_count = (item.retry_count or 0) + 1
                        self.db.session.commit()
                except:
                    pass
            
            return {'success': False, 'error': str(e)}
    
    def _upload_file_streaming(self, file_path, original_filename):
        """Upload de arquivo usando streaming real (20MB chunks)"""
        result = self.object_storage.upload_file_streaming(file_path, original_filename)
        return result
    
    def _cleanup_job(self, job):
        """Limpa todos os recursos de um job"""
        if job.archive_path and os.path.exists(job.archive_path):
            try:
                os.remove(job.archive_path)
            except Exception as e:
                print(f"[CLEANUP] Failed to remove archive: {e}")
        
        if job.temp_dir and os.path.exists(job.temp_dir):
            try:
                shutil.rmtree(job.temp_dir, ignore_errors=True)
            except Exception as e:
                print(f"[CLEANUP] Failed to remove temp dir: {e}")
        
        gc.collect()
    
    def enqueue(self, batch_id, archive_path, temp_dir, metadata=None):
        """Adiciona um job à fila"""
        job = UploadJob(batch_id, archive_path, temp_dir, metadata or {})
        self.active_jobs[batch_id] = job
        self.job_queue.put(job)
        
        with self.stats_lock:
            self.stats['total_queued'] += 1
        
        print(f"[ORCHESTRATOR] Enqueued batch {batch_id}. Queue size: {self.job_queue.qsize()}")
        
        return {
            'batch_id': batch_id,
            'queue_position': self.job_queue.qsize(),
            'status': 'queued'
        }
    
    def get_status(self):
        """Retorna status do orquestrador"""
        with self.stats_lock:
            return {
                'queue_size': self.job_queue.qsize(),
                'active_workers': self.stats['active_workers'],
                'total_queued': self.stats['total_queued'],
                'total_processed': self.stats['total_processed'],
                'total_errors': self.stats['total_errors'],
                'active_jobs': list(self.active_jobs.keys())
            }
    
    def shutdown(self):
        """Encerra o orquestrador graciosamente"""
        self.shutdown_event.set()
        for worker in self.workers:
            worker.join(timeout=5.0)


upload_orchestrator = None

def get_upload_orchestrator(app=None, db=None, object_storage=None):
    """Factory function para obter instância do orquestrador"""
    global upload_orchestrator
    if upload_orchestrator is None:
        upload_orchestrator = UploadOrchestrator(app, db, object_storage)
    elif app and db and object_storage:
        upload_orchestrator.configure(app, db, object_storage)
    return upload_orchestrator
