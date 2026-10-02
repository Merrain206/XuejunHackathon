import json
from pathlib import Path

d = Path("cninfo_data/688583")
manifest = []
for f in sorted(d.glob("*.PDF")):
    manifest.append({"title": f.stem, "file": f.name, "size": f.stat().st_size})
(d / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✅ 生成 {len(manifest)} 条 → {d / 'manifest.json'}")