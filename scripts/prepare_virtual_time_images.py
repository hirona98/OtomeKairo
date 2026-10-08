"""Download the reviewed, licensed public image fixtures with content hashes."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode, unquote, urlparse
from urllib.request import Request, urlopen


def fetch(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "OtomeKairoVerification/1.0"})
    with urlopen(request, timeout=45) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    sources = json.loads(Path(__file__).with_name("virtual_time_image_sources.json").read_text())
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for entry in sources:
        title = unquote(urlparse(entry["source_url"]).path.split("/wiki/", 1)[1])
        query = urlencode({"action": "query", "format": "json", "prop": "imageinfo",
                           "iiprop": "url", "iiurlwidth": 1280, "titles": title})
        metadata = json.loads(fetch("https://commons.wikimedia.org/w/api.php?" + query))
        pages = list(metadata["query"]["pages"].values())
        if len(pages) != 1:
            raise ValueError("Commonsの画像を一意に取得できません。")
        image_url = pages[0]["imageinfo"][0]["thumburl"]
        data = fetch(image_url)
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError("直接確認済み画像とhashが一致しません。再確認が必要です。")
        target = args.output_dir / entry["path"]
        temporary = target.with_suffix(".writing")
        temporary.write_bytes(data)
        temporary.replace(target)
        print(f"verified fixture: {entry['fixture_id']}", flush=True)
    manifest = args.output_dir / "images.json"
    manifest.write_text(json.dumps(sources, ensure_ascii=False, indent=2) + "\n")
    print(f"manifest: {manifest}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
