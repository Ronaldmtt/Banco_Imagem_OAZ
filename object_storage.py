"""
Object Storage Service for Replit App Storage
Uses the official replit-object-storage SDK
"""

import os
from datetime import datetime
import uuid
from replit.object_storage import Client
from replit.object_storage import DefaultBucketError, ObjectNotFoundError


class ObjectStorageService:
    """Service for interacting with Replit Object Storage"""
    
    def __init__(self, bucket_id=None):
        self._bucket_id = bucket_id or os.environ.get('OBJECT_STORAGE_BUCKET')
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of the storage client"""
        if self._client is None:
            if self._bucket_id:
                self._client = Client(bucket_id=self._bucket_id)
            else:
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
            'path': f"/storage/images/{object_name}"
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
