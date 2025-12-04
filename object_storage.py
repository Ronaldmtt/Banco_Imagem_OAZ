"""
Object Storage Service for Replit App Storage
Handles file uploads and downloads using Google Cloud Storage backend
"""

import os
import json
import uuid
from datetime import datetime, timedelta
from google.cloud import storage
from google.oauth2 import service_account
import requests

REPLIT_SIDECAR_ENDPOINT = "http://127.0.0.1:1106"


class ObjectStorageService:
    """Service for interacting with Replit Object Storage"""
    
    def __init__(self):
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of the storage client"""
        if self._client is None:
            credentials_config = {
                "type": "external_account",
                "audience": "replit",
                "subject_token_type": "access_token",
                "token_url": f"{REPLIT_SIDECAR_ENDPOINT}/token",
                "credential_source": {
                    "url": f"{REPLIT_SIDECAR_ENDPOINT}/credential",
                    "format": {
                        "type": "json",
                        "subject_token_field_name": "access_token"
                    }
                }
            }
            
            self._client = storage.Client(
                credentials=service_account.Credentials.from_service_account_info(credentials_config) if False else None,
                project=""
            )
            
            self._client = storage.Client.from_service_account_info({
                "type": "external_account",
                "audience": "replit", 
                "subject_token_type": "access_token",
                "token_url": f"{REPLIT_SIDECAR_ENDPOINT}/token",
                "credential_source": {
                    "url": f"{REPLIT_SIDECAR_ENDPOINT}/credential",
                    "format": {
                        "type": "json",
                        "subject_token_field_name": "access_token"
                    }
                },
                "universe_domain": "googleapis.com"
            })
        return self._client
    
    def get_bucket_name(self):
        """Get the bucket name from environment variable"""
        bucket_name = os.environ.get('OBJECT_STORAGE_BUCKET', '')
        if not bucket_name:
            raise ValueError(
                "OBJECT_STORAGE_BUCKET not set. Create a bucket in 'App Storage' "
                "and set OBJECT_STORAGE_BUCKET env var."
            )
        return bucket_name
    
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
            content_type: MIME type of the file
            
        Returns:
            dict with object_name and public_url
        """
        bucket_name = self.get_bucket_name()
        object_name = self.generate_object_name(original_filename)
        
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        
        if content_type:
            blob.content_type = content_type
        
        if hasattr(file_data, 'read'):
            blob.upload_from_file(file_data)
        else:
            blob.upload_from_string(file_data)
        
        return {
            'object_name': object_name,
            'bucket_name': bucket_name,
            'path': f"/{bucket_name}/{object_name}"
        }
    
    def get_signed_url(self, object_path, method='GET', ttl_seconds=3600):
        """
        Get a signed URL for accessing an object
        
        Args:
            object_path: Full path like /bucket/object
            method: HTTP method (GET, PUT, DELETE)
            ttl_seconds: Time to live in seconds
            
        Returns:
            Signed URL string
        """
        if object_path.startswith('/'):
            parts = object_path[1:].split('/', 1)
        else:
            parts = object_path.split('/', 1)
            
        if len(parts) < 2:
            raise ValueError("Invalid object path")
            
        bucket_name = parts[0]
        object_name = parts[1]
        
        request_body = {
            "bucket_name": bucket_name,
            "object_name": object_name,
            "method": method,
            "expires_at": (datetime.utcnow() + timedelta(seconds=ttl_seconds)).isoformat() + "Z"
        }
        
        response = requests.post(
            f"{REPLIT_SIDECAR_ENDPOINT}/object-storage/signed-object-url",
            json=request_body,
            headers={"Content-Type": "application/json"}
        )
        
        if not response.ok:
            raise Exception(f"Failed to get signed URL: {response.status_code}")
        
        return response.json().get('signed_url')
    
    def download_file(self, object_path):
        """
        Download a file from object storage
        
        Args:
            object_path: Full path like /bucket/object
            
        Returns:
            File bytes
        """
        if object_path.startswith('/'):
            parts = object_path[1:].split('/', 1)
        else:
            parts = object_path.split('/', 1)
            
        if len(parts) < 2:
            raise ValueError("Invalid object path")
            
        bucket_name = parts[0]
        object_name = parts[1]
        
        bucket = self.client.bucket(bucket_name)
        blob = bucket.blob(object_name)
        
        return blob.download_as_bytes()
    
    def delete_file(self, object_path):
        """
        Delete a file from object storage
        
        Args:
            object_path: Full path like /bucket/object
            
        Returns:
            True if deleted, False otherwise
        """
        try:
            if object_path.startswith('/'):
                parts = object_path[1:].split('/', 1)
            else:
                parts = object_path.split('/', 1)
                
            if len(parts) < 2:
                return False
                
            bucket_name = parts[0]
            object_name = parts[1]
            
            bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            blob.delete()
            return True
        except Exception as e:
            print(f"Error deleting file: {e}")
            return False
    
    def file_exists(self, object_path):
        """Check if a file exists in object storage"""
        try:
            if object_path.startswith('/'):
                parts = object_path[1:].split('/', 1)
            else:
                parts = object_path.split('/', 1)
                
            if len(parts) < 2:
                return False
                
            bucket_name = parts[0]
            object_name = parts[1]
            
            bucket = self.client.bucket(bucket_name)
            blob = bucket.blob(object_name)
            return blob.exists()
        except Exception:
            return False


object_storage = ObjectStorageService()
