import sys
import sqlite3
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

def run_demo():
    print("\n" + "="*60)
    print(" [终极直连模式] ")
    print("="*60)
    
    # 1. 寻找 cninfo.db 文件（写死绝对路径确保能读到）
    db_path = os.path.join(BASE_DIR, "cninfo.db")
    
    if not os.path.exists(db_path):
        print(f"\n[ERROR] 依然找不到数据库！\n请确认 cninfo.db 确实在文件夹：{BASE_DIR} 里面。")
        return

    try:
        # 2. 直接连接数据库
        print(f"\n[STEP 1] 发现数据库文件: {db_path}")
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        print("[SUCCESS] 数据库连接成功！")
        
        # 3. 查看数据库里有哪些表格
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        print(f"\n[INFO] 数据库里包含的表格有: {[t[0] for t in tables]}")
        
        # 4. 尝试读取第一个表格里的前 3 条数据
        if tables:
            table_name = tables[0][0] 
            print(f"\n[STEP 2] 正在从表格 '{table_name}' 获取前3条数据...")
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 3")
            data = cursor.fetchall()
            
            if data:
                print(f"\n[SUCCESS] 成功读取到前3条数据: \n{data}")
            else:
                print(f"[WARN] 表格 '{table_name}' 是空的。")
        else:
            print("[ERROR] 数据库里没有任何表格！")
            
        cursor.close()
        conn.close()

    except Exception as e:
        print(f"\n[ERROR] 运行出错: {e}")

if __name__ == "__main__":
    run_demo()
