import json
import os
import re
import tempfile
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import msal
import requests
from dotenv import load_dotenv

load_dotenv()

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff"}


def _require_env(name: str) -> str:
    value = os.getenv(name)
    if value is None or not value.strip():
        raise RuntimeError(f"Variável de ambiente {name} não configurada")
    return value


def get_sharepoint_env() -> Dict[str, str]:
    return {
        "tenant_id": _require_env("SHAREPOINT_TENANT_ID"),
        "client_id": _require_env("SHAREPOINT_CLIENT_ID"),
        "client_secret": _require_env("SHAREPOINT_CLIENT_SECRET"),
        "hostname": _require_env("SHAREPOINT_HOSTNAME"),
        "site_path": _require_env("SHAREPOINT_SITE_PATH"),
        "drive_name": _require_env("SHAREPOINT_DRIVE_NAME"),
        "root_folder": _require_env("SHAREPOINT_ROOT_FOLDER"),
    }


def build_sharepoint_client_from_env() -> "SharePointClient":
    return SharePointClient(**get_sharepoint_env())


def parse_sku_variants(filename: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse SKU base and sequence from filename.

    Returns: (sku_base, sequencia, sku_full)
    """
    if not filename:
        return None, None, None

    name = os.path.splitext(os.path.basename(filename))[0].strip().upper()

    match = re.match(r"^([A-Z0-9\.]+?)(?:[\s_-]+([A-Z0-9]+))?$", name)
    if not match:
        return name or None, None, name or None

    sku_base = match.group(1)
    sequencia = match.group(2) or None
    sku_full = f"{sku_base}_{sequencia}" if sequencia else sku_base

    return sku_base, sequencia, sku_full


def get_collection_name_from_path(parent_path: str) -> str:
    if not parent_path:
        return ""
    normalized = parent_path.replace("\\", "/")
    segments = [segment for segment in normalized.split("/") if segment]
    ecommerce_folder = os.getenv("SHAREPOINT_ECOMMERCE_FOLDER", "E-commerce")
    ecommerce_lower = ecommerce_folder.lower()
    for idx, segment in enumerate(segments):
        if segment.lower() == ecommerce_lower:
            return segments[idx + 1] if idx + 1 < len(segments) else ""
    return ""


def get_brand_name_from_path(parent_path: str) -> Optional[str]:
    if not parent_path:
        return None
    normalized = parent_path.replace("\\", "/")
    segments = [segment for segment in normalized.split("/") if segment]
    brand_parent = os.getenv("SHAREPOINT_BRAND_PARENT_SEGMENT", "Design - Cria")
    brand_parent_lower = brand_parent.lower()
    for idx, segment in enumerate(segments):
        if segment.lower() == brand_parent_lower:
            return segments[idx + 1] if idx + 1 < len(segments) else None
    return None


def get_collection_and_subfolder_from_path(parent_path: str) -> Tuple[str, str]:
    if not parent_path:
        return "", ""
    normalized = parent_path.replace("\\", "/")
    segments = [segment for segment in normalized.split("/") if segment]
    ecommerce_folder = os.getenv("SHAREPOINT_ECOMMERCE_FOLDER", "E-commerce")
    ecommerce_lower = ecommerce_folder.lower()
    for idx, segment in enumerate(segments):
        if segment.lower() == ecommerce_lower:
            collection = segments[idx + 1] if idx + 1 < len(segments) else ""
            subfolder = segments[idx + 2] if idx + 2 < len(segments) else ""
            return collection, subfolder
    return "", ""


class SharePointClient:
    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        hostname: str,
        site_path: str,
        drive_name: str = "Documents",
        root_folder: str = "",
    ):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.hostname = hostname
        self.site_path = site_path
        self.drive_name = drive_name
        self.root_folder = root_folder.strip("/")

        self._token = None
        self._token_expiry = None
        self._site_id = None
        self._drive_id = None

    def _get_access_token(self) -> str:
        if self._token and self._token_expiry and datetime.utcnow() < self._token_expiry:
            return self._token

        app = msal.ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )
        result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
        if "access_token" not in result:
            raise RuntimeError(f"SharePoint auth failed: {result.get('error_description')}")

        self._token = result["access_token"]
        expires_in = int(result.get("expires_in", 3600))
        self._token_expiry = datetime.utcnow() + timedelta(seconds=expires_in - 60)
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._get_access_token()}"}

    def resolve_site(self) -> str:
        if self._site_id:
            return self._site_id

        url = f"{GRAPH_BASE_URL}/sites/{self.hostname}:{self.site_path}"
        response = requests.get(url, headers=self._headers(), timeout=30)
        response.raise_for_status()
        self._site_id = response.json().get("id")
        if not self._site_id:
            raise RuntimeError("SharePoint site id not found")
        return self._site_id

    def _load_drive_id(self) -> str:
        if self._drive_id:
            return self._drive_id

        site_id = self.resolve_site()
        url = f"{GRAPH_BASE_URL}/sites/{site_id}/drives"
        response = requests.get(url, headers=self._headers(), timeout=30)
        response.raise_for_status()
        drives = response.json().get("value", [])
        for drive in drives:
            if drive.get("name", "").lower() == self.drive_name.lower():
                self._drive_id = drive.get("id")
                return self._drive_id
        raise RuntimeError(f"Drive '{self.drive_name}' not found")

    def get_drive_id(self) -> str:
        return self._load_drive_id()

    def _get_root_item_id(self, root_folder: str) -> str:
        drive_id = self.get_drive_id()
        if not root_folder:
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root"
        else:
            encoded = root_folder.replace(" ", "%20")
            url = f"{GRAPH_BASE_URL}/drives/{drive_id}/root:/{encoded}"
        response = requests.get(url, headers=self._headers(), timeout=30)
        response.raise_for_status()
        return response.json().get("id")

    def _list_children(self, drive_id: str, item_id: str) -> List[dict]:
        print(f"[SP] Listando filhos de item_id={item_id}")
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/children"
        items = []
        while url:
            response = requests.get(url, headers=self._headers(), timeout=30)
            response.raise_for_status()
            payload = response.json()
            items.extend(payload.get("value", []))
            url = payload.get("@odata.nextLink")
        return items

    def _index_cache_path(self) -> str:
        cache_path = os.getenv("SHAREPOINT_INDEX_CACHE")
        if cache_path and cache_path.strip():
            return cache_path
        return os.path.join(os.path.dirname(__file__), "sharepoint_index.json")

    def _load_index_from_cache(self) -> Optional[Dict[str, List[dict]]]:
        path = self._index_cache_path()
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError, ValueError):
            print("[SP] Falha ao carregar índice do cache, reconstruindo...")
            return None

    def _save_index_to_cache(self, index: Dict[str, List[dict]]) -> None:
        path = self._index_cache_path()
        cache_dir = os.path.dirname(path) or "."
        os.makedirs(cache_dir, exist_ok=True)
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                dir=cache_dir,
                prefix=".sharepoint_index.",
                suffix=".tmp",
            ) as handle:
                json.dump(index, handle, ensure_ascii=True)
                temp_path = handle.name
            os.replace(temp_path, path)
        finally:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        print(f"[SP] Índice salvo em cache: {path} (itens={len(index)})")

    def get_or_build_index(self, force_refresh: bool = False) -> Dict[str, List[dict]]:
        if not force_refresh:
            cached = self._load_index_from_cache()
            if cached is not None:
                path = self._index_cache_path()
                print(f"[SP] Índice carregado do cache: {path} (itens={len(cached)})")
                return cached

        index = self._build_index_full()
        self._save_index_to_cache(index)
        return index

    def _build_index_full(
        self,
        root_folder: Optional[str] = None,
        max_items: int | None = None,
    ) -> Dict[str, List[dict]]:
        print("[SP] build_index iniciado")
        root_folder = (root_folder or self.root_folder).strip("/")
        drive_id = self.get_drive_id()
        root_item_id = self._get_root_item_id(root_folder)

        index: Dict[str, List[dict]] = {}
        files_indexed = 0
        stop_walk = False

        def walk(item_id: str):
            nonlocal files_indexed, stop_walk
            if stop_walk or (max_items is not None and files_indexed >= max_items):
                stop_walk = True
                return
            children = self._list_children(drive_id, item_id)
            for item in children:
                if stop_walk or (max_items is not None and files_indexed >= max_items):
                    stop_walk = True
                    return
                if "folder" in item:
                    walk(item.get("id"))
                    continue

                name = item.get("name", "")
                ext = os.path.splitext(name)[1].lower()
                if ext not in IMAGE_EXTENSIONS:
                    continue

                sku_base, sequencia, sku_full = parse_sku_variants(name)
                if not sku_base:
                    continue

                item_info = {
                    "drive_id": drive_id,
                    "item_id": item.get("id"),
                    "name": name,
                    "web_url": item.get("webUrl"),
                    "last_modified": item.get("lastModifiedDateTime"),
                    "mime_type": item.get("file", {}).get("mimeType"),
                    "parent_path": item.get("parentReference", {}).get("path"),
                    "sku_base": sku_base,
                    "sequencia": sequencia,
                    "sku_full": sku_full,
                }
                index.setdefault(sku_base, []).append(item_info)
                files_indexed += 1
                if files_indexed % 20 == 0:
                    print(f"[SP] Arquivos indexados (parcial): {len(index)}")
                if max_items is not None and files_indexed >= max_items:
                    stop_walk = True
                    return

        walk(root_item_id)
        cache_path = self._index_cache_path()
        print(f"[SP] build_index concluído (full). itens={len(index)} | cache={cache_path}")
        return index

    def build_index(
        self,
        root_folder: Optional[str] = None,
        max_items: int | None = None,
        force_refresh: bool = False,
    ) -> Dict[str, List[dict]]:
        """Mantida por compatibilidade; agora usa cache por padrão."""
        if root_folder is not None or max_items is not None:
            return self._build_index_full(root_folder=root_folder, max_items=max_items)
        return self.get_or_build_index(force_refresh=force_refresh)

    def find_by_sku_base(self, index: Dict[str, List[dict]], sku_base: str) -> List[dict]:
        if not sku_base:
            return []
        return index.get(sku_base.upper(), []) or index.get(sku_base, [])

    def download_bytes(self, drive_id: str, item_id: str) -> bytes:
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
        response = requests.get(url, headers=self._headers(), timeout=60)
        response.raise_for_status()
        return response.content

    def download_stream(self, drive_id: str, item_id: str) -> requests.Response:
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}/content"
        response = requests.get(url, headers=self._headers(), stream=True, timeout=60)
        response.raise_for_status()
        return response

    def get_metadata(self, drive_id: str, item_id: str) -> dict:
        url = f"{GRAPH_BASE_URL}/drives/{drive_id}/items/{item_id}"
        response = requests.get(url, headers=self._headers(), timeout=30)
        response.raise_for_status()
        data = response.json()
        return {
            "mime_type": data.get("file", {}).get("mimeType"),
            "web_url": data.get("webUrl"),
            "last_modified": data.get("lastModifiedDateTime"),
            "name": data.get("name"),
            "parent_path": data.get("parentReference", {}).get("path"),
        }
