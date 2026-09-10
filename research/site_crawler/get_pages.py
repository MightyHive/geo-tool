import json
import csv

def get_urls_from_json(json_file):
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    urls = []

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "pages":
                    if isinstance(v, list):
                        for page in v:
                            if isinstance(page, dict) and "url" in page:
                                urls.append(page["url"])  # <-- string
                    elif isinstance(v, dict) and "url" in v:
                        urls.append(v["url"])
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(data)
    return urls

def urls_to_csv(json_file, csv_file):
    urls = get_urls_from_json(json_file)

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["pages_crawled"])  # header
        for url in urls:
            writer.writerow([url])  # <-- only the URL string

urls_to_csv("shipt.json", "pages.csv")