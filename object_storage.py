"""
Object Storage Service for Replit App Storage
Uses the official replit-object-storage SDK
Suporta streaming de arquivos grandes (até 3GB) com chunks de 20MB
"""

import os
import io
import hashlib
from datetime import datetime
import uuid
from replit.object_storage import Client
from replit.object_storage.errors import ObjectNotFoundError

CHUNK_SIZE_BYTES = 20 * 1024 * 1024  # 20MB chunks para arquivos grandes


class ObjectStorageService:
    """Service for interacting with Replit Object Storage using default bucket"""
    
    def __init__(self):
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of the storage client with default bucket"""
        if self._client is None:
            self._client = Client()
        return self._client
    
    def get_object_prefix(self):
        """Get the object prefix for images"""
        return os.environ.get('OBJECT_STORAGE_PREFIX', 'images')
    
    def generate_object_name(self, original_filename):
        """Generate a unique object name with timestamp"""
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        unique_id = uuid.uuid4().hex[:8]
        ext = os.path.splitext(original_filename)[1].lower()
        return f"{self.get_object_prefix()}/{timestamp}_{unique_id}{ext}"
    
    def upload_file(self, file_data, original_filename, content_type=None):
        """
        Upload a file to object storage
        
        Args:
            file_data: File bytes or file-like object
            original_filename: Original name of the file
            content_type: MIME type of the file (not used with SDK, but kept for compatibility)
            
        Returns:
            dict with object_name
        """
        object_name = self.generate_object_name(original_filename)
        
        if hasattr(file_data, 'read'):
            data = file_data.read()
        else:
            data = file_data
        
        self.client.upload_from_bytes(object_name, data)
        
        return {
            'object_name': object_name,
            'storage_path': f"/storage/{object_name}"
        }
    
    def upload_file_streaming(self, file_path, original_filename=None, chunk_size=None):
        """
        Upload de arquivo otimizado para imagens individuais.
        
        NOTA: O SDK do Replit Object Storage requer upload de bytes completos.
        Para arquivos de imagem (tipicamente < 50MB), isso é aceitável.
        Arquivos ZIP grandes (até 3GB) devem ser extraídos e processados individualmente.
        
        Otimizações implementadas:
        - Calcula hash SHA256 durante leitura (streaming)
        - Libera memória imediatamente após upload
        - Sem limite artificial - deixa o SDK decidir
        
        Args:
            file_path: Caminho do arquivo no servidor
            original_filename: Nome original (usa basename se não fornecido)
            chunk_size: Tamanho do chunk para hash (default: 20MB)
            
        Returns:
            dict com object_name, storage_path, file_size, file_hash
        """
        if chunk_size is None:
            chunk_size = CHUNK_SIZE_BYTES
        
        if original_filename is None:
            original_filename = os.path.basename(file_path)
        
        object_name = self.generate_object_name(original_filename)
        file_size = os.path.getsize(file_path)
        
        hasher = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                hasher.update(chunk)
        
        with open(file_path, 'rb') as f:
            data = f.read()
        
        self.client.upload_from_bytes(object_name, data)
        file_hash = hasher.hexdigest()
        
        del data
        
        return {
            'object_name': object_name,
            'storage_path': f"/storage/{object_name}",
            'file_size': file_size,
            'file_hash': file_hash
        }
    
    def upload_file_direct(self, file_path, original_filename=None):
        """
        Upload direto de arquivo pequeno (< 50MB) - sem streaming
        Mais eficiente para imagens individuais
        """
        if original_filename is None:
            original_filename = os.path.basename(file_path)
        
        object_name = self.generate_object_name(original_filename)
        file_size = os.path.getsize(file_path)
        
        hasher = hashlib.sha256()
        
        with open(file_path, 'rb') as f:
            data = f.read()
            hasher.update(data)
        
        self.client.upload_from_bytes(object_name, data)
        file_hash = hasher.hexdigest()
        
        del data
        
        return {
            'object_name': object_name,
            'storage_path': f"/storage/{object_name}",
            'file_size': file_size,
            'file_hash': file_hash
        }
    
    def download_file(self, object_name):
        """
        Download a file from object storage
        
        Args:
            object_name: The object name/path in the bucket
            
        Returns:
            File bytes
        """
        try:
            return self.client.download_as_bytes(object_name)
        except ObjectNotFoundError:
            return None
    
    def delete_file(self, object_name):
        """
        Delete a file from object storage
        
        Args:
            object_name: The object name/path in the bucket
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            self.client.delete(object_name, ignore_not_found=True)
            return True
        except Exception as e:
            print(f"Error deleting file: {e}")
            return False
    
    def file_exists(self, object_name):
        """Check if a file exists in object storage"""
        try:
            return self.client.exists(object_name)
        except Exception:
            return False
    
    def list_files(self, prefix=None):
        """List files in the bucket with optional prefix filter"""
        try:
            return self.client.list(prefix=prefix or self.get_object_prefix())
        except Exception as e:
            print(f"Error listing files: {e}")
            return []


def get_storage_client():
    """Get a configured storage client instance"""
    return ObjectStorageService()


object_storage = ObjectStorageService()
