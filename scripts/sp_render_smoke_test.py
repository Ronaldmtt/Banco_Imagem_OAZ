import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from sharepoint_client import build_sharepoint_client_from_env


def main():
    client = build_sharepoint_client_from_env()

    index = client.build_index()
    if not index:
        raise RuntimeError("Index vazio")

    first_sku = next(iter(index.keys()))
    first_item = index[first_sku][0]

    drive_id = first_item.get("drive_id")
    item_id = first_item.get("item_id")

    metadata = client.get_metadata(drive_id, item_id)
    content = client.download_bytes(drive_id, item_id)

    output_path = "/tmp/test.jpg"
    with open(output_path, "wb") as f:
        f.write(content)

    print(f"SKU base: {first_sku}")
    print(f"Arquivo: {metadata.get('name')}")
    print(f"Mime: {metadata.get('mime_type')}")
    print(f"Bytes: {len(content)}")
    print(f"Salvo em: {output_path}")


if __name__ == "__main__":
    main()
