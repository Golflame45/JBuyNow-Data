import streamlit as st
import pandas as pd
import plotly.express as px
import io
import os
import re
import json
from datetime import datetime

# Google Drive API Libraries
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False

st.set_page_config(page_title="Omnichannel E-commerce Dashboard", layout="wide")

# ================= Google Drive Folder IDs =================
INBOX_FOLDER_ID = "1UYXJ1cyBHs1LHndB_Os5Rl8men7_7PQ5"
MASTER_FOLDER_ID = "1RNsLYo4TE_jEQOnMgVuKOW8RmFHiAjQf"
ARCHIVE_FOLDER_ID = "1MrQq5V3CuRwrO1pp9ZDzdYdVMdizOPP7"

# ================= 0. Authentication =================
def check_password():
    def password_entered():
        if st.session_state["password"] == "1234":
            st.session_state["password_correct"] = True
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if "password_correct" not in st.session_state:
        st.markdown("<h2 style='text-align: center;'>🔒 กรุณาใส่รหัสผ่านเพื่อเข้าสู่ระบบ</h2>", unsafe_allow_html=True)
        st.text_input("รหัสผ่าน", type="password", on_change=password_entered, key="password")
        return False
    elif not st.session_state["password_correct"]:
        st.markdown("<h2 style='text-align: center;'>🔒 กรุณาใส่รหัสผ่านเพื่อเข้าสู่ระบบ</h2>", unsafe_allow_html=True)
        st.text_input("รหัสผ่าน", type="password", on_change=password_entered, key="password")
        st.error("❌ รหัสผ่านไม่ถูกต้องครับ")
        return False
    else:
        return True

if check_password():

    # ================= 1. Google Drive Helper Functions =================
    def get_drive_service():
        if not GOOGLE_API_AVAILABLE:
            return None
        creds = None

        # Check Streamlit Cloud Secrets
        if "gcp_service_account" in st.secrets:
            raw_val = st.secrets["gcp_service_account"]
            if isinstance(raw_val, str):
                creds_dict = json.loads(raw_val)
            else:
                creds_dict = dict(raw_val)
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive']
            )
        elif "gcp_json" in st.secrets:
            creds_dict = json.loads(st.secrets["gcp_json"])
            creds = service_account.Credentials.from_service_account_info(
                creds_dict, scopes=['https://www.googleapis.com/auth/drive']
            )
        # Check local JSON file if running locally
        elif os.path.exists("service_account.json"):
            creds = service_account.Credentials.from_service_account_file(
                "service_account.json", scopes=['https://www.googleapis.com/auth/drive']
            )
        if creds:
            return build('drive', 'v3', credentials=creds)
        return None

    def list_files_in_folder(service, folder_id):
        query = f"'{folder_id}' in parents and trashed = false"
        results = service.files().list(q=query, fields="files(id, name, mimeType)").execute()
        return results.get('files', [])

    def download_file_bytes(service, file_id):
        request = service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        return fh

    def upload_file_bytes(service, folder_id, file_name, file_bytes, mime_type='text/csv'):
        # Check if file already exists in folder
        existing = service.files().list(
            q=f"'{folder_id}' in parents and name = '{file_name}' and trashed = false",
            fields="files(id)"
        ).execute().get('files', [])
        
        media = MediaIoBaseUpload(file_bytes, mimetype=mime_type, resumable=True)
        if existing:
            file_id = existing[0]['id']
            updated = service.files().update(fileId=file_id, media_body=media).execute()
            return updated['id']
        else:
            file_metadata = {'name': file_name, 'parents': [folder_id]}
            created = service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            return created['id']

    def list_files_in_folder_recursive(service, folder_id, path=""):
        q = f"'{folder_id}' in parents and trashed = false"
        res = service.files().list(q=q, fields='files(id, name, mimeType)').execute()
        files = []
        for it in res.get('files', []):
            current_path = f"{path}/{it['name']}" if path else it['name']
            if it['mimeType'] == 'application/vnd.google-apps.folder':
                files.extend(list_files_in_folder_recursive(service, it['id'], current_path))
            elif it['name'].endswith(('.xlsx', '.xls', '.csv')):
                files.append((it['id'], it['name'], current_path))
        return files

    def extract_date_from_name_or_content(file_name, raw_df=None):
        # Pattern 1: 25Sep2026 or 25-Sep-2026 or 25_Sep_2026
        m1 = re.search(r'(\d{1,2})\s*[-_.]?\s*([A-Za-z]{3})\s*[-_.]?\s*(202\d)', file_name)
        if m1:
            d, m, y = m1.groups()
            try:
                return datetime.strptime(f"{d}{m}{y}", "%d%b%Y").strftime("%Y-%m-%d")
            except: pass

        # Pattern 2: 20260925 or 2026-09-25 or 2026_09_25
        m2 = re.search(r'(202\d)[-_.]?(\d{2})[-_.]?(\d{2})', file_name)
        if m2:
            return f"{m2.group(1)}-{m2.group(2)}-{m2.group(3)}"

        # Pattern 2b: 25-09-2026 or 25.09.2026 or 25_09_2026
        m2b = re.search(r'(\d{1,2})[-_.](\d{2})[-_.](202\d)', file_name)
        if m2b:
            d, m, y = m2b.groups()
            return f"{y}-{int(m):02d}-{int(d):02d}"

        # Pattern 3: Search header content for Date Range
        if raw_df is not None:
            for r in range(min(5, len(raw_df))):
                row_txt = " ".join([str(x) for x in raw_df.iloc[r].dropna().tolist()])
                m3 = re.search(r'(202\d-\d{2}-\d{2})', row_txt)
                if m3:
                    return m3.group(1)
        return None

    # ================= 2. Multi-Channel Data Normalization =================
    def parse_raw_sales_file(file_bytes, file_name, folder_name=""):
        raw_df = pd.read_excel(file_bytes, header=None) if not file_name.endswith('.csv') else pd.read_csv(file_bytes, header=None)
        date_str = extract_date_from_name_or_content(file_name, raw_df)
        
        # Detect Platform
        name_check = f"{folder_name}/{file_name}".lower()
        platform = "Lazada" if "lazada" in name_check or "laz" in name_check else "Shopee"
        
        # Detect Shop Name from folder name
        shop_name = "JBuyNow"
        for part in folder_name.split('/'):
            part_clean = part.replace("Lazada", "").replace("Shopee", "").strip(" _-")
            if part_clean and not re.search(r'202\d', part_clean):
                shop_name = part_clean
                break

        # Dynamic Header Detection (Lazada header on row 5, Shopee header on row 0)
        header_idx = 0
        for r in range(min(15, len(raw_df))):
            row_vals = [str(x).strip().lower() for x in raw_df.iloc[r].dropna().tolist()]
            if any(k in row_vals for k in ['seller sku', 'sku', 'product name', 'item name', 'revenue', 'ยอดขาย', 'รหัสสินค้า']):
                header_idx = r
                break
                
        df = raw_df.iloc[header_idx+1:].copy()
        df.columns = [str(c).strip() for c in raw_df.iloc[header_idx].tolist()]

        # Multi-Channel Parsing Logic
        if platform == "Lazada":
            p_id_col = next((c for c in df.columns if c.lower() in ['product id', 'item id', 'id']), None)
            p_name_col = next((c for c in df.columns if c.lower() in ['product name', 'ชื่อสินค้า', 'item name']), None)
            sku_col = next((c for c in df.columns if c.lower() in ['seller sku', 'sku', 'รหัสสินค้า']), None)
            vis_col = next((c for c in df.columns if 'visitor' in c.lower() or 'ผู้เข้าชม' in c.lower()), None)
            rev_col = next((c for c in df.columns if 'revenue' in c.lower() or 'ยอดขาย' in c.lower()), None)
            buyer_col = next((c for c in df.columns if 'buyer' in c.lower() or 'ผู้ซื้อ' in c.lower()), None)
            unit_col = next((c for c in df.columns if 'units sold' in c.lower() or 'จำนวนที่ขาย' in c.lower()), None)
            a2c_col = next((c for c in df.columns if 'add to cart units' in c.lower() or 'รถเข็น' in c.lower() or 'a2c' in c.lower()), None)
            order_col = next((c for c in df.columns if 'order' in c.lower() or 'คำสั่งซื้อ' in c.lower()), None)

            def to_num(val):
                return float(str(val).replace(',', '').replace('-', '0').strip() or 0)

            clean_rows = []
            i = 0
            n = len(df)
            while i < n:
                curr = df.iloc[i]
                sku_val = str(curr[sku_col]).strip() if sku_col else '-'
                is_parent = sku_val in ['-', '', 'nan', 'None']
                
                p_id = str(curr[p_id_col]).strip() if p_id_col else ''
                p_name = str(curr[p_name_col]).strip() if p_name_col else ''
                
                p_vis = to_num(curr[vis_col]) if vis_col else 0.0
                p_rev = to_num(curr[rev_col]) if rev_col else 0.0
                p_buyers = to_num(curr[buyer_col]) if buyer_col else 0.0
                p_units = to_num(curr[unit_col]) if unit_col else 0.0
                p_a2c = to_num(curr[a2c_col]) if a2c_col else 0.0
                p_orders = to_num(curr[order_col]) if order_col else 0.0

                if is_parent:
                    j = i + 1
                    children = []
                    while j < n:
                        next_row = df.iloc[j]
                        next_sku = str(next_row[sku_col]).strip() if sku_col else '-'
                        next_pid = str(next_row[p_id_col]).strip() if p_id_col else ''
                        if next_sku not in ['-', '', 'nan', 'None'] and (next_pid == p_id or not next_pid or next_pid == '-'):
                            children.append(next_row)
                            j += 1
                        else:
                            break
                            
                    if len(children) > 0:
                        for c_idx, child in enumerate(children):
                            c_sku = str(child[sku_col]).strip()
                            c_name = str(child[p_name_col]).strip() if p_name_col and str(child[p_name_col]).strip() not in ['-', '', 'nan'] else p_name
                            c_rev = to_num(child[rev_col]) if rev_col else 0.0
                            c_buyers = to_num(child[buyer_col]) if buyer_col else 0.0
                            c_units = to_num(child[unit_col]) if unit_col else 0.0
                            c_a2c = to_num(child[a2c_col]) if a2c_col else 0.0
                            c_orders = to_num(child[order_col]) if order_col else 0.0
                            c_vis = p_vis if c_idx == 0 else 0.0
                            
                            clean_rows.append({
                                'Platform': platform,
                                'Shop_Name': shop_name,
                                'Date': date_str,
                                'SKU': c_sku,
                                'Revenue': c_rev,
                                'Visitors': c_vis,
                                'Buyers': c_buyers,
                                'Units_Sold': c_units,
                                'A2C': c_a2c,
                                'Orders': c_orders,
                                'Parent_SKU': p_id,
                                'Product': c_name
                            })
                        i = j
                    else:
                        found_sku = f"PID-{p_id}" if p_id and p_id != '-' else (p_name[:20] if p_name else 'UNKNOWN')
                        clean_rows.append({
                            'Platform': platform,
                            'Shop_Name': shop_name,
                            'Date': date_str,
                            'SKU': found_sku,
                            'Revenue': p_rev,
                            'Visitors': p_vis,
                            'Buyers': p_buyers,
                            'Units_Sold': p_units,
                            'A2C': p_a2c,
                            'Orders': p_orders,
                            'Parent_SKU': p_id,
                            'Product': p_name
                        })
                        i += 1
                else:
                    clean_rows.append({
                        'Platform': platform,
                        'Shop_Name': shop_name,
                        'Date': date_str,
                        'SKU': sku_val,
                        'Revenue': p_rev,
                        'Visitors': p_vis,
                        'Buyers': p_buyers,
                        'Units_Sold': p_units,
                        'A2C': p_a2c,
                        'Orders': p_orders,
                        'Parent_SKU': p_id,
                        'Product': p_name
                    })
                    i += 1
            clean_df = pd.DataFrame(clean_rows)
        else:
            col_mappings = {
                'Revenue': ['Revenue', 'ยอดขาย (ที่มีการสั่งซื้อทั้งหมด) (THB)', 'ยอดขาย (THB)', 'LAZ Revenue', 'ยอดขาย'],
                'Visitors': ['Product Visitors', 'ผู้เข้าชมสินค้า', 'การเข้าชมสินค้า', 'SKU_Visitors', 'Visitors'],
                'Buyers': ['Buyers', 'ผู้ซื้อ (ที่มีการสั่งซื้อทั้งหมด)', 'ผู้ซื้อ', 'Buyer'],
                'Units_Sold': ['Units Sold', 'จำนวนที่ขายได้ (ที่มีการสั่งซื้อทั้งหมด)', 'จำนวนที่ขายได้', 'UnitsSold'],
                'A2C': ['Add to Cart Units', 'จำนวนที่ขายได้ (เพิ่มสินค้าในรถเข็น)', 'A2C Units', 'Add2CartUnits', 'A2C'],
                'Orders': ['Orders', 'ทั้งหมด', 'Order No.'],
                'SKU': ['Seller SKU', 'SKU', 'SKU Code', 'รหัสสินค้า'],
                'Parent_SKU': ['Parent SKU', 'Product ID', 'Product Group', 'กลุ่มสินค้า'],
                'Product': ['Product Name', 'ผลิตภัณฑ์', 'Item Name', 'ชื่อสินค้า'],
                'Date': ['Date', 'OrderDate', 'Posting Date', 'วันที่']
            }

            clean_df = pd.DataFrame()
            for standard_col, candidate_cols in col_mappings.items():
                matched = False
                for cand in candidate_cols:
                    if cand in df.columns:
                        clean_df[standard_col] = df[cand]
                        matched = True
                        break
                if not matched:
                    clean_df[standard_col] = None

            if date_str:
                clean_df['Date'] = date_str
            elif clean_df['Date'].isna().all():
                clean_df['Date'] = datetime.today().strftime('%Y-%m-%d')

            clean_df['Platform'] = platform
            clean_df['Shop_Name'] = shop_name

            numeric_cols = ['Revenue', 'Visitors', 'Buyers', 'Units_Sold', 'A2C', 'Orders']
            for nc in numeric_cols:
                clean_df[nc] = (
                    clean_df[nc].astype(str)
                    .str.replace(',', '', regex=False)
                    .str.replace('-', '0', regex=False)
                    .str.replace('nan', '0', regex=False)
                    .str.replace('None', '0', regex=False)
                )
                clean_df[nc] = pd.to_numeric(clean_df[nc], errors='coerce').fillna(0)

            clean_df['SKU'] = clean_df['SKU'].astype(str).str.strip()
            clean_df['Parent_SKU'] = clean_df['Parent_SKU'].astype(str).str.strip()
            clean_df['Product'] = clean_df['Product'].astype(str).str.strip()
        
        d_parsed = pd.to_datetime(clean_df['Date'], format='%Y-%m-%d', errors='coerce')
        missing_d = d_parsed.isna()
        if missing_d.any():
            d_parsed.loc[missing_d] = pd.to_datetime(clean_df['Date'][missing_d], format='mixed', dayfirst=True, errors='coerce')
        clean_df['DateObj'] = d_parsed
        clean_df['Date'] = clean_df['DateObj'].dt.strftime('%Y-%m-%d')
        clean_df = clean_df.drop(columns=['DateObj'])
        # Aggregate multiple listings of the same SKU on the same date (e.g. Lazada multiple product IDs with same SKU)
        clean_df = clean_df.sort_values('Revenue', ascending=False)
        agg_dict = {
            'Revenue': 'sum',
            'Visitors': 'sum',
            'Buyers': 'sum',
            'Units_Sold': 'sum',
            'A2C': 'sum',
            'Orders': 'sum',
            'Parent_SKU': 'first',
            'Product': 'first'
        }
        clean_df = clean_df.groupby(['Platform', 'Shop_Name', 'Date', 'SKU'], as_index=False).agg(agg_dict)

        return clean_df

    # ================= 3. Automated Ingestion Pipeline =================
    def sync_google_drive_pipeline(service):
        if not service:
            return "ไม่สามารถเชื่อมต่อ Google Drive API ได้ (กรุณาเช็ค Secrets)", 0

        # Step 1: Check existing Master file in 02_Master_Data
        master_files = list_files_in_folder(service, MASTER_FOLDER_ID)
        master_file_item = next((f for f in master_files if f['name'] == 'Master_Sales_Full.csv'), None)
        
        if master_file_item:
            fh = download_file_bytes(service, master_file_item['id'])
            master_df = pd.read_csv(fh)
        else:
            master_df = pd.DataFrame()

        # Step 2: Scan 01_Drop_Inbox recursively across any subfolders and month folders
        all_inbox_files = list_files_in_folder_recursive(service, INBOX_FOLDER_ID)
        new_dfs = []
        files_processed_count = 0

        existing_combos = set()
        if not master_df.empty and 'Date' in master_df.columns and 'Platform' in master_df.columns:
            existing_combos = set(zip(master_df['Platform'].astype(str), master_df['Date'].astype(str)))

        for fid, fname, fpath in all_inbox_files:
            date_cand = extract_date_from_name_or_content(fname)
            plat_cand = "Lazada" if "lazada" in fpath.lower() else "Shopee"
            # Skip downloading if already present in master
            if existing_combos and date_cand and (plat_cand, date_cand) in existing_combos:
                continue

            try:
                fb = download_file_bytes(service, fid)
                df_parsed = parse_raw_sales_file(fb, fname, folder_name=fpath)
                if not df_parsed.empty:
                    new_dfs.append(df_parsed)
                    files_processed_count += 1
            except Exception:
                pass

        # Step 3: Append, Deduplicate, and Save back to 02_Master_Data
        if new_dfs:
            combined_new = pd.concat(new_dfs, ignore_index=True)
            if not master_df.empty:
                full_df = pd.concat([master_df, combined_new], ignore_index=True)
            else:
                full_df = combined_new
            
            # Deduplicate by Platform, Shop_Name, Date, SKU
            full_df = full_df.drop_duplicates(subset=['Platform', 'Shop_Name', 'Date', 'SKU'], keep='last')
            
            # Attempt saving back to Google Drive (if quota permits)
            try:
                csv_buf = io.BytesIO()
                full_df.to_csv(csv_buf, index=False, encoding='utf-8-sig')
                csv_buf.seek(0)
                if master_file_item:
                    media = MediaIoBaseUpload(csv_buf, mimetype='text/csv', resumable=True)
                    service.files().update(fileId=master_file_item['id'], media_body=media).execute()
                else:
                    upload_file_bytes(service, MASTER_FOLDER_ID, 'Master_Sales_Full.csv', csv_buf, mime_type='text/csv')
            except Exception:
                pass
            master_df = full_df

        return "ซิงก์สำเร็จ", files_processed_count

    # ================= 4. Load Master & SKU Master Data =================
    @st.cache_data(ttl=86400)
    def load_active_data():
        service = get_drive_service()
        master_df = pd.DataFrame()

        if service:
            master_files = list_files_in_folder(service, MASTER_FOLDER_ID)
            # Find Master Sales in Google Drive or local
            m_item = next((f for f in master_files if f['name'] == 'Master_Sales_Full.csv'), None)
            if m_item:
                fh = download_file_bytes(service, m_item['id'])
                master_df = pd.read_csv(fh)
            elif os.path.exists('Master_Sales_Full.csv'):
                master_df = pd.read_csv('Master_Sales_Full.csv')

            # Scan 01_Drop_Inbox for all files or any new daily files!
            all_inbox_files = list_files_in_folder_recursive(service, INBOX_FOLDER_ID)
            existing_combos = set()
            if not master_df.empty and 'Date' in master_df.columns and 'Platform' in master_df.columns:
                existing_combos = set(zip(master_df['Platform'].astype(str), master_df['Date'].astype(str)))

            inbox_dfs = []
            for fid, fname, fpath in all_inbox_files:
                date_cand = extract_date_from_name_or_content(fname)
                plat_cand = "Lazada" if "lazada" in fpath.lower() else "Shopee"
                if existing_combos and date_cand and (plat_cand, date_cand) in existing_combos:
                    continue
                try:
                    fb = download_file_bytes(service, fid)
                    df_p = parse_raw_sales_file(fb, fname, folder_name=fpath)
                    if not df_p.empty:
                        inbox_dfs.append(df_p)
                except Exception:
                    pass

            if inbox_dfs:
                new_data = pd.concat(inbox_dfs, ignore_index=True)
                if not master_df.empty:
                    master_df = pd.concat([master_df, new_data], ignore_index=True)
                else:
                    master_df = new_data
                master_df = master_df.drop_duplicates(subset=['Platform', 'Shop_Name', 'Date', 'SKU'], keep='last')
                # Cache to local disk so subsequent runs load in 0.05 seconds
                try:
                    master_df.to_csv('Master_Sales_Full.csv', index=False, encoding='utf-8-sig')
                except Exception:
                    pass
            
            # Find SKU Master if exists in 02_Master_Data
            sku_item = next((f for f in master_files if 'sku' in f['name'].lower() and 'master' in f['name'].lower()), None)
            if sku_item and not master_df.empty:
                try:
                    sku_bytes = download_file_bytes(service, sku_item['id'])
                    sku_master_df = pd.read_csv(sku_bytes) if sku_item['name'].endswith('.csv') else pd.read_excel(sku_bytes)
                    sku_col = next((c for c in sku_master_df.columns if str(c).strip().lower() in ['no.', 'no', 'item no.', 'sku', 'seller sku', 'รหัสสินค้า']), None)
                    cat_col = next((c for c in sku_master_df.columns if str(c).strip().lower() in ['category description', 'cate desc', 'category_desc', 'หมวดหมู่สินค้า', 'หมวดหมู่']), None)
                    if sku_col and cat_col:
                        sku_master_df['SKU'] = sku_master_df[sku_col].astype(str).str.strip()
                        sku_master_df['Category_Desc'] = sku_master_df[cat_col].astype(str).str.strip()
                        sku_map = sku_master_df[['SKU', 'Category_Desc']].drop_duplicates(subset=['SKU'])
                        master_df['SKU'] = master_df['SKU'].astype(str).str.strip()
                        master_df = master_df.merge(sku_map, on='SKU', how='left')
                except Exception:
                    pass

            # Find Stock File if exists in 02_Master_Data (e.g. Stock as of 25.09)
            stock_item = next((f for f in sorted(master_files, key=lambda x: x.get('name', ''), reverse=True) 
                               if 'stock' in f['name'].lower() or 'inventory' in f['name'].lower()), None)
            if stock_item and not master_df.empty:
                try:
                    stock_bytes = download_file_bytes(service, stock_item['id'])
                    stock_df = pd.read_csv(stock_bytes) if stock_item['name'].endswith('.csv') else pd.read_excel(stock_bytes)
                    
                    # Normalize SKU (No.) and Stock (Inventory available) columns
                    sku_col = next((c for c in stock_df.columns if str(c).strip().lower() in ['no.', 'no', 'item no.', 'sku', 'seller sku', 'รหัสสินค้า']), None)
                    stock_col = next((c for c in stock_df.columns if any(k in str(c).strip().lower() for k in ['available', 'inventory', 'stock', 'on hand', 'qty', 'สต๊อก', 'พร้อมขาย'])), None)
                    
                    if sku_col and stock_col:
                        stock_df['SKU'] = stock_df[sku_col].astype(str).str.strip()
                        stock_df['Stock_Available'] = pd.to_numeric(
                            stock_df[stock_col].astype(str).str.replace(',', '').str.replace('-', '0'), 
                            errors='coerce'
                        ).fillna(0)
                        stock_summary = stock_df.groupby('SKU')['Stock_Available'].sum().reset_index()
                        master_df = master_df.merge(stock_summary, on='SKU', how='left')
                        master_df['Stock_Available'] = master_df['Stock_Available'].fillna(0)
                except Exception:
                    pass
        else:
            # Local fallback for offline testing (Master_Sales_Full only)
            if os.path.exists('Master_Sales_Full.csv'):
                master_df = pd.read_csv('Master_Sales_Full.csv')

        # Fallback to local SKU Master file if Category_Desc is missing
        if not master_df.empty and ('Category_Desc' not in master_df.columns or master_df['Category_Desc'].isna().all()):
            for p in ['.', '..']:
                if os.path.exists(p):
                    for fn in sorted(os.listdir(p), reverse=True):
                        if 'sku' in fn.lower() and fn.endswith(('.xlsx', '.xls', '.csv')):
                            try:
                                s_sku_df = pd.read_csv(os.path.join(p, fn)) if fn.endswith('.csv') else pd.read_excel(os.path.join(p, fn))
                                sku_c = next((c for c in s_sku_df.columns if str(c).strip().lower() in ['no.', 'no', 'item no.', 'sku', 'seller sku', 'รหัสสินค้า']), None)
                                cat_c = next((c for c in s_sku_df.columns if str(c).strip().lower() in ['category description', 'cate desc', 'category_desc', 'หมวดหมู่สินค้า', 'หมวดหมู่']), None)
                                if sku_c and cat_c:
                                    s_sku_df['SKU'] = s_sku_df[sku_c].astype(str).str.strip()
                                    s_sku_df['Category_Desc'] = s_sku_df[cat_c].astype(str).str.strip()
                                    sku_map = s_sku_df[['SKU', 'Category_Desc']].drop_duplicates(subset=['SKU'])
                                    master_df['SKU'] = master_df['SKU'].astype(str).str.strip()
                                    if 'Category_Desc' in master_df.columns:
                                        master_df = master_df.drop(columns=['Category_Desc'])
                                    master_df = master_df.merge(sku_map, on='SKU', how='left')
                                    break
                            except: pass
                    if 'Category_Desc' in master_df.columns and not master_df['Category_Desc'].isna().all():
                        break

        # Fallback to local stock file if Stock_Available is missing or all 0
        if not master_df.empty and ('Stock_Available' not in master_df.columns or master_df['Stock_Available'].sum() == 0):
            for p in ['.', '..']:
                if os.path.exists(p):
                    for fn in sorted(os.listdir(p), reverse=True):
                        if 'stock' in fn.lower() and fn.endswith(('.xlsx', '.xls', '.csv')):
                            try:
                                s_df = pd.read_csv(os.path.join(p, fn)) if fn.endswith('.csv') else pd.read_excel(os.path.join(p, fn))
                                sku_c = next((c for c in s_df.columns if str(c).strip().lower() in ['no.', 'no', 'item no.', 'sku', 'seller sku', 'รหัสสินค้า']), None)
                                stk_c = next((c for c in s_df.columns if any(k in str(c).strip().lower() for k in ['available', 'inventory', 'stock', 'on hand', 'qty', 'สต๊อก', 'พร้อมขาย'])), None)
                                if sku_c and stk_c:
                                    s_df['SKU'] = s_df[sku_c].astype(str).str.strip()
                                    s_df['Stock_Available'] = pd.to_numeric(s_df[stk_c].astype(str).str.replace(',', '').str.replace('-', '0'), errors='coerce').fillna(0)
                                    s_sum = s_df.groupby('SKU')['Stock_Available'].sum().reset_index()
                                    master_df['SKU'] = master_df['SKU'].astype(str).str.strip()
                                    if 'Stock_Available' in master_df.columns:
                                        master_df = master_df.drop(columns=['Stock_Available'])
                                    master_df = master_df.merge(s_sum, on='SKU', how='left')
                                    master_df['Stock_Available'] = master_df['Stock_Available'].fillna(0.0)
                                    break
                            except: pass
                    if 'Stock_Available' in master_df.columns and master_df['Stock_Available'].sum() > 0:
                        break

        if not master_df.empty:
            # Guarantee all numeric columns exist
            for c in ['Revenue', 'Visitors', 'Buyers', 'Units_Sold', 'A2C', 'Orders']:
                if c not in master_df.columns:
                    if c == 'Orders' and 'ทั้งหมด' in master_df.columns:
                        master_df['Orders'] = master_df['ทั้งหมด'].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                    else:
                        master_df[c] = 0.0
                master_df[c] = pd.to_numeric(master_df[c], errors='coerce').fillna(0.0)

            # Smart parsing: YYYY-MM-DD first (standard), then dayfirst=True fallback for DD/MM/YYYY
            def parse_date_series(s):
                res = pd.to_datetime(s, format='%Y-%m-%d', errors='coerce')
                missing = res.isna()
                if missing.any():
                    res.loc[missing] = pd.to_datetime(s[missing], format='mixed', dayfirst=True, errors='coerce')
                return res

            master_df['DateObj'] = parse_date_series(master_df['Date'])
            master_df = master_df.dropna(subset=['DateObj']).copy()
            # Strict date boundaries: eliminate future dates (beyond today) and legacy pre-2026 data
            today_cutoff = pd.Timestamp.now() + pd.Timedelta(days=1)
            master_df = master_df[(master_df['DateObj'] >= '2026-01-01') & (master_df['DateObj'] <= today_cutoff)].copy()

            if 'Stock_Available' not in master_df.columns:
                master_df['Stock_Available'] = 0.0
            master_df['Stock_Available'] = pd.to_numeric(master_df['Stock_Available'], errors='coerce').fillna(0.0)
            master_df['Year'] = master_df['DateObj'].dt.year.astype(str)
            master_df['Month'] = master_df['DateObj'].dt.strftime('%Y-%m')
            master_df['Day'] = master_df['DateObj'].dt.strftime('%d/%m/%Y')
            master_df['SKU'] = master_df['SKU'].astype(str)
            
            # Clean and ensure Category_Desc
            if 'Category_Desc' not in master_df.columns:
                master_df['Category_Desc'] = 'อื่นๆ / ไม่ระบุหมวด'
            else:
                master_df['Category_Desc'] = master_df['Category_Desc'].fillna('อื่นๆ / ไม่ระบุหมวด')
                master_df.loc[master_df['Category_Desc'].isin(['nan', 'None', '-', '']), 'Category_Desc'] = 'อื่นๆ / ไม่ระบุหมวด'

            master_df['Parent_SKU'] = master_df['Category_Desc'].astype(str)
            master_df['Product'] = master_df.get('Product', master_df.get('Item Name', 'General')).astype(str)
            master_df['Platform'] = master_df.get('Platform', pd.Series(['Shopee'] * len(master_df))).fillna('Shopee').astype(str)
            master_df['Shop_Name'] = master_df.get('Shop_Name', pd.Series(['Main Shop'] * len(master_df))).fillna('Main Shop').astype(str)

        return master_df

    # ================= 5. Sidebar Sync & Administration =================
    with st.sidebar:
        st.header("⚙️ ระบบท่อข้อมูลอัตโนมัติ")
        st.write("**Google Drive Auto-Sync**")
        if st.button("🔄 ซิงก์ข้อมูลจาก Google Drive"):
            with st.spinner("กำลังตรวจสอบไฟล์ใหม่ใน 01_Drop_Inbox..."):
                srv = get_drive_service()
                status_msg, count = sync_google_drive_pipeline(srv)
                st.cache_data.clear()
                if count > 0:
                    st.success(f"นำเข้าข้อมูลใหม่สำเร็จ {count} ไฟล์!")
                else:
                    st.info("ไม่มีไฟล์ใหม่ในห้อง 01_Drop_Inbox (ข้อมูลเป็นปัจจุบันแล้ว)")
                st.rerun()

    base_df = load_active_data()

    # ================= 6. UI Banner & Top Filters =================
    st.markdown("""
        <style>
        .pbi-bar {
            background: linear-gradient(90deg, #FF007F 0%, #0000FF 100%);
            color: white; padding: 12px 18px;
            font-size: 22px; font-weight: bold; text-align: left;
            border-radius: 6px; margin-bottom: 15px;
            display: flex; justify-content: space-between; align-items: center;
        }
        [data-testid="stMetricValue"] { font-size: 24px !important; }
        [data-testid="stMetric"] {
            border: 1px solid var(--secondary-background-color); 
            padding: 10px; border-radius: 6px;
            box-shadow: 2px 2px 5px rgba(0,0,0,0.1);
        }
        </style>
        <div class="pbi-bar">
            <span>OMNICHANNEL SALES DASHBOARD</span>
            <span style="font-size: 14px; font-weight: normal; opacity: 0.9;">SHOPEE & LAZADA MULTI-SHOP</span>
        </div>
    """, unsafe_allow_html=True)

    # Empty State Handling (Day 0)
    if base_df.empty:
        st.info("👋 **ระบบพร้อมทำงาน 100% แล้วครับ!**\n\nขณะนี้ยังไม่มีข้อมูลในระบบ ให้ทีมงานนำไฟล์ยอดขายรายวันไปหยอดไว้ในห้อง **`01_Drop_Inbox`** บน Google Drive จากนั้นกดปุ่ม **'🔄 ซิงก์ข้อมูลจาก Google Drive'** ที่แถบเมนูด้านซ้ายเพื่อเริ่มการคำนวณวันแรกได้ทันทีครับ!")
        st.stop()

    # ================= 7. Global Filters =================
    col_p1, col_p2, col_p3, col_p4 = st.columns([1, 1.5, 1, 2])
    
    with col_p1:
        platforms = ['ทั้งหมด (All)'] + sorted(base_df['Platform'].unique().tolist())
        sel_platform = st.selectbox("ช่องทาง (Platform)", platforms)

    with col_p2:
        available_shops = base_df if sel_platform == 'ทั้งหมด (All)' else base_df[base_df['Platform'] == sel_platform]
        shop_list = sorted(available_shops['Shop_Name'].unique().tolist())
        sel_shops = st.multiselect("ร้านค้า (Shop)", shop_list, default=shop_list)

    with col_p3:
        all_years = sorted(base_df['Year'].unique())
        selected_years = st.multiselect("ปี (Year)", all_years, default=all_years)

    with col_p4:
        all_cats = sorted([str(s) for s in base_df['Category_Desc'].dropna().unique() if str(s).strip() not in ['-', 'nan', 'NaN', 'None', '']])
        selected_cats = st.multiselect("หมวดหมู่สินค้า (Category)", all_cats)

    filtered_df = base_df.copy()
    if sel_platform != 'ทั้งหมด (All)':
        filtered_df = filtered_df[filtered_df['Platform'] == sel_platform]
    if sel_shops:
        filtered_df = filtered_df[filtered_df['Shop_Name'].isin(sel_shops)]
    if selected_years:
        filtered_df = filtered_df[filtered_df['Year'].isin(selected_years)]
    if selected_cats:
        filtered_df = filtered_df[filtered_df['Category_Desc'].isin(selected_cats)]

    # ================= 8. Base Tables for Fixed Row Mapping =================
    monthly_base = filtered_df.groupby('Month').agg({'Revenue': 'sum', 'Visitors': 'sum'}).reset_index().sort_values('Month')
    cat_base = filtered_df.groupby('Category_Desc').agg({
        'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum'
    }).reset_index().sort_values('Revenue', ascending=False)
    cat_base['Avg CR'] = (cat_base['Buyers'] / cat_base['Visitors'] * 100).fillna(0)
    cat_base['Avg Price'] = (cat_base['Revenue'] / cat_base['Units_Sold']).fillna(0)

    daily_base = filtered_df.groupby('Day').agg({'DateObj': 'first', 'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum'}).reset_index().sort_values('DateObj')
    sku_base = filtered_df.groupby('SKU').agg({
        'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum',
        'Stock_Available': 'first'
    }).reset_index().sort_values('Revenue', ascending=False)
    sku_base['Avg CR'] = (sku_base['Buyers'] / sku_base['Visitors'] * 100).fillna(0)
    sku_base['Avg Price'] = (sku_base['Revenue'] / sku_base['Units_Sold']).fillna(0)

    # Cross-Filtering Extraction
    def get_selected_rows(key):
        val = st.session_state.get(key)
        if not val: return []
        if isinstance(val, dict): return val.get('selection', {}).get('rows', [])
        if hasattr(val, 'selection') and hasattr(val.selection, 'rows'): return val.selection.rows
        try: return val['selection']['rows']
        except: return []

    def get_selected_items(key, id_col, fallback_df):
        sel_idx = get_selected_rows(key)
        if not sel_idx: return []
        stored_ids = st.session_state.get(f"{key}_rendered_ids", [])
        if stored_ids:
            return [stored_ids[i] for i in sel_idx if i < len(stored_ids)]
        elif id_col in fallback_df.columns:
            return [fallback_df.iloc[i][id_col] for i in sel_idx if i < len(fallback_df)]
        return []

    selected_months = get_selected_items('tb_month', 'Month', monthly_base)
    selected_cats_table = get_selected_items('tb_cat', 'Category_Desc', cat_base)
    selected_days = get_selected_items('tb_day', 'Day', daily_base)
    selected_skus = get_selected_items('tb_sku', 'SKU', sku_base)

    cross_df = filtered_df.copy()
    if selected_months: cross_df = cross_df[cross_df['Month'].isin(selected_months)]
    if selected_cats_table: cross_df = cross_df[cross_df['Category_Desc'].isin(selected_cats_table)]
    if selected_days: cross_df = cross_df[cross_df['Day'].isin(selected_days)]
    if selected_skus: cross_df = cross_df[cross_df['SKU'].isin(selected_skus)]

    # ================= 9. KPI Scorecards =================
    st.markdown("---")
    kpi1, kpi2, kpi3, kpi4, kpi5, kpi6, kpi7 = st.columns(7)
    rev = cross_df['Revenue'].sum() if 'Revenue' in cross_df.columns else 0
    vis = cross_df['Visitors'].sum() if 'Visitors' in cross_df.columns else 0
    buy = cross_df['Buyers'].sum() if 'Buyers' in cross_df.columns else 0
    unit = cross_df['Units_Sold'].sum() if 'Units_Sold' in cross_df.columns else 0
    orders = cross_df['Orders'].sum() if 'Orders' in cross_df.columns else 0
    cr = (buy / vis) if vis > 0 else 0
    rev_per_buyer = (rev / buy) if buy > 0 else 0
    aov = (rev / orders) if orders > 0 else (rev / buy if buy > 0 else 0)

    kpi1.metric("Revenue (ยอดขาย)", f"{rev:,.0f}")
    kpi2.metric("SKU Visitors", f"{vis:,.0f}")
    kpi3.metric("SKU CR%", f"{cr*100:,.2f}%")
    kpi4.metric("Rev per Buyers", f"{rev_per_buyer:,.0f}")
    kpi5.metric("AOV", f"{aov:,.0f}")
    kpi6.metric("Buyers (ผู้ซื้อ)", f"{buy:,.0f}")
    kpi7.metric("Units Sold", f"{unit:,.0f}")

    st.markdown("---")

    # ================= 10. Display Tables =================
    # Section ที่ถูกคลิก จะแสดงข้อมูลเต็มไม่กลายเป็น 0
    # Section อื่นๆ ที่ไม่ได้ถูกคลิก แถวที่ไม่เกี่ยวข้องจะ "หายไปเลย" (ไม่เป็น 0)
    
    # 1. Order Month
    if selected_months:
        disp_monthly = monthly_base.copy()
    else:
        disp_monthly = cross_df.groupby('Month').agg({'Revenue': 'sum', 'Visitors': 'sum'}).reset_index()
        disp_monthly = disp_monthly[(disp_monthly['Revenue'] > 0) | (disp_monthly['Visitors'] > 0)].sort_values('Month')
    disp_monthly['% Rev'] = (disp_monthly['Revenue'] / disp_monthly['Revenue'].sum() * 100).fillna(0) if disp_monthly['Revenue'].sum() > 0 else 0
    st.session_state['tb_month_rendered_ids'] = disp_monthly['Month'].tolist()

    # 2. Product Group (Category Description)
    if selected_cats_table:
        disp_cat = cat_base.copy()
    else:
        disp_cat = cross_df.groupby('Category_Desc').agg({
            'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum'
        }).reset_index()
        disp_cat = disp_cat[(disp_cat['Revenue'] > 0) | (disp_cat['Visitors'] > 0) | (disp_cat['A2C'] > 0)]
        disp_cat['Avg CR'] = (disp_cat['Buyers'] / disp_cat['Visitors'] * 100).fillna(0)
        disp_cat['Avg Price'] = (disp_cat['Revenue'] / disp_cat['Units_Sold']).fillna(0)
        disp_cat = disp_cat.sort_values(by=['Revenue', 'Visitors'], ascending=[False, False])
    st.session_state['tb_cat_rendered_ids'] = disp_cat['Category_Desc'].tolist()

    # 3. Order Date
    if selected_days:
        disp_daily = daily_base.copy()
    else:
        disp_daily = cross_df.groupby('Day').agg({'DateObj': 'first', 'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum'}).reset_index()
        disp_daily = disp_daily[(disp_daily['Revenue'] > 0) | (disp_daily['Visitors'] > 0)].sort_values('DateObj')
    st.session_state['tb_day_rendered_ids'] = disp_daily['Day'].tolist()

    # 4. SKU Code
    if selected_skus:
        disp_sku = sku_base.copy()
    else:
        disp_sku = cross_df.groupby('SKU').agg({
            'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum',
            'Stock_Available': 'first'
        }).reset_index()
        disp_sku = disp_sku[(disp_sku['Revenue'] > 0) | (disp_sku['Visitors'] > 0) | (disp_sku['A2C'] > 0)]
        disp_sku['Avg CR'] = (disp_sku['Buyers'] / disp_sku['Visitors'] * 100).fillna(0)
        disp_sku['Avg Price'] = (disp_sku['Revenue'] / disp_sku['Units_Sold']).fillna(0)
        disp_sku = disp_sku.sort_values(by=['Revenue', 'Visitors'], ascending=[False, False])
    st.session_state['tb_sku_rendered_ids'] = disp_sku['SKU'].tolist()

    col_config = {
        "Category_Desc": st.column_config.TextColumn("หมวดหมู่สินค้า (Category)"),
        "Revenue": st.column_config.NumberColumn("Revenue", format="%,.2f"),
        "Avg CR": st.column_config.NumberColumn("Avg CR", format="%.2f %%"),
        "Avg Price": st.column_config.NumberColumn("Avg Price", format="%,.2f"),
        "% Rev": st.column_config.ProgressColumn("%", format="%.1f%%", min_value=0, max_value=100),
        "Stock_Available": st.column_config.NumberColumn("Stock (พร้อมขาย)", format="%d ชิ้น")
    }

    # Middle Row
    col_m1, col_m2, col_m3 = st.columns([1.2, 1.8, 2.5])
    with col_m1:
        st.write("**Order Month**")
        st.dataframe(
            disp_monthly[['Month', 'Revenue', '% Rev', 'Visitors']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_month",
            column_config=col_config
        )
    with col_m2:
        st.write("**Revenue Trend by FGMONTHYEAR**")
        chart_df = cross_df.groupby('Month').agg({'Revenue': 'sum'}).reset_index().sort_values('Month')
        if not chart_df.empty:
            fig = px.line(chart_df, x='Month', y='Revenue', markers=True, text='Revenue', color_discrete_sequence=['#00d4ff'])
            fig.update_traces(textposition="top center", texttemplate='%{text:.2s}')
            fig.update_layout(
                margin=dict(l=0, r=0, t=10, b=0), height=300, 
                xaxis_title="", yaxis_title="",
                xaxis=dict(showgrid=False), yaxis=dict(showgrid=False)
            )
            st.plotly_chart(fig, use_container_width=True)
    with col_m3:
        st.write("**Product Group (หมวดหมู่สินค้า)**")
        st.dataframe(
            disp_cat[['Category_Desc', 'Revenue', 'Visitors', 'Avg CR', 'Avg Price', 'A2C', 'Buyers', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_cat",
            column_config=col_config
        )

    st.markdown("---")
    # Bottom Row
    col_d1, col_d2 = st.columns([1.5, 2.5])
    with col_d1:
        st.write("**Order Date (รายวัน)**")
        st.dataframe(
            disp_daily[['Day', 'Revenue', 'Visitors', 'Buyers', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_day",
            column_config=col_config
        )
    with col_d2:
        st.write("**SKU Code (รายสินค้า)**")
        st.dataframe(
            disp_sku[['SKU', 'Revenue', 'Stock_Available', 'A2C', 'Visitors', 'Avg CR', 'Avg Price', 'Buyers', 'Units_Sold']], 
            hide_index=True, use_container_width=True,
            on_select="rerun", selection_mode="multi-row", key="tb_sku",
            column_config=col_config
        )
