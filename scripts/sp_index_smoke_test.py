import os
import sys

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from sharepoint_client import build_sharepoint_client_from_env


def main():
    client = build_sharepoint_client_from_env()

    site_id = client.resolve_site()
    drive_id = client.get_drive_id()
    index = client.build_index(max_items=50)

    total_skus = len(index)
    total_items = sum(len(v) for v in index.values())

    print(f"Site ID: {site_id}")
    print(f"Drive ID: {drive_id}")
    print(f"Total SKUs: {total_skus}")
    print(f"Total itens: {total_items}")
    print("Total de itens (smoke):", len(index))
    print("\nExemplos:")

    shown = 0
    for sku_base in sorted(index.keys()):
        items = index[sku_base]
        filenames = [item.get("name") for item in items[:5]]
        print(f"- {sku_base} -> {filenames}")
        shown += 1
        if shown >= 10:
            break


if __name__ == "__main__":
    main()
