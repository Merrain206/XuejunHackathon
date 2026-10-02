import sqlite3
from pathlib import Path
import pdfplumber

DATA_DIR = Path("/var/www/hackathon/688583")
DB_FILE = "/var/www/hackathon/announcements.db"

# 1. 建数据库表
conn = sqlite3.connect(DB_FILE)
conn.execute("""CREATE TABLE IF NOT EXISTS docs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name TEXT,
    title TEXT,
    category TEXT,
    text_content TEXT
)""")
conn.commit()

# 2. 找所有 PDF 文件
pdf_files = sorted(DATA_DIR.glob("*.PDF")) + sorted(DATA_DIR.glob("*.pdf"))
print(f"找到 {len(pdf_files)} 个 PDF 文件\n")

done = 0
for i, pdf_path in enumerate(pdf_files, 1):
    if conn.execute("SELECT 1 FROM docs WHERE file_name=?", (pdf_path.name,)).fetchone():
        continue
    
    try:
        # 3. 读取 PDF 前 5 页的文字
        with pdfplumber.open(pdf_path) as pdf:
            text = ""
            for page in pdf.pages[:5]:
                text += (page.extract_text() or "") + "\n"
        
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        title = lines[0][:50] if lines else "无标题"
        
        # 4. 简单分类
        category = "其他"
        for kw, cat in [("年报", "年度报告"), ("季报", "季度报告"), 
                        ("股东大会", "股东大会"), ("董事会", "董事会"),
                        ("审计", "审计"), ("利润分配", "利润分配")]:
            if kw in text[:500]:
                category = cat
                break
        
        # 5. 存入数据库
        conn.execute("INSERT INTO docs (file_name, title, category, text_content) VALUES (?,?,?,?)",
                     (pdf_path.name, title, category, text))
        conn.commit()
        done += 1
        print(f"[{i}/{len(pdf_files)}] ✅ {title} [{category}]")
    except Exception as e:
        print(f"[{i}/{len(pdf_files)}] ❌ {pdf_path.name}: {e}")

print(f"\n✅ 完成！新入库 {done} 条")