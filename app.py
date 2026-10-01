import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import io
import os
import re
import json
import html as html_lib
import streamlit.components.v1 as components
from datetime import datetime, timezone, timedelta

# Thai Timezone (UTC+7)
BKK_TZ = timezone(timedelta(hours=7))

def format_bkk_time(val):
    if not val:
        return ""
    try:
        if isinstance(val, (int, float)):
            dt = datetime.fromtimestamp(val, tz=timezone.utc).astimezone(BKK_TZ)
            return dt.strftime('%d/%m/%Y %H:%M น.')
        elif isinstance(val, str):
            dt = pd.to_datetime(val)
            if dt.tzinfo is None:
                dt = dt.tz_localize('UTC')
            dt_bkk = dt.tz_convert(BKK_TZ)
            return dt_bkk.strftime('%d/%m/%Y %H:%M น.')
        elif isinstance(val, datetime):
            if val.tzinfo is None:
                val = val.replace(tzinfo=timezone.utc)
            return val.astimezone(BKK_TZ).strftime('%d/%m/%Y %H:%M น.')
    except Exception:
        pass
    return ""

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
        results = service.files().list(q=query, fields="files(id, name, mimeType, modifiedTime, createdTime)").execute()
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
        elif platform == "Shopee":
            def to_num_col(s):
                return pd.to_numeric(s.astype(str).str.replace(',', '', regex=False).str.replace('-', '0', regex=False).str.replace('nan', '0', regex=False).str.replace('None', '0', regex=False), errors='coerce').fillna(0.0)

            def clean_str_series(series, index=None):
                if series is None or len(series) == 0:
                    return pd.Series('', index=index if index is not None else df.index)
                s = series.fillna('').astype(str).str.strip()
                return s.replace({'nan': '', 'None': '', '-': ''})

            p_id_col = next((c for c in df.columns if c in ['รหัสสินค้า', 'Product ID', 'Item ID']), None)
            p_name_col = next((c for c in df.columns if c in ['ผลิตภัณฑ์', 'ชื่อสินค้า', 'Product Name', 'Item Name']), None)
            v_id_col = next((c for c in df.columns if c in ['รหัสตัวเลือกสินค้า', 'Variation ID', 'Model ID']), None)
            v_name_col = next((c for c in df.columns if c in ['ชื่อตัวเลือกสินค้า', 'Variation Name', 'Model Name']), None)
            sku_col = next((c for c in df.columns if c in ['SKU', 'Seller SKU', 'รหัสสินค้าตัวเลือก']), None)
            psku_col = next((c for c in df.columns if c in ['Parent SKU', 'Parent_SKU']), None)

            rev_col = next((c for c in df.columns if 'ยอดขาย' in c and 'ทั้งหมด' in c), None) or next((c for c in df.columns if 'ยอดขาย' in c), None) or next((c for c in df.columns if 'revenue' in c.lower()), None)
            vis_col = next((c for c in df.columns if 'ผู้เข้าชมสินค้า' in c), None) or next((c for c in df.columns if 'การเข้าชม' in c), None) or next((c for c in df.columns if 'visitor' in c.lower()), None)
            buyer_col = next((c for c in df.columns if 'ผู้ซื้อ' in c and 'ทั้งหมด' in c), None) or next((c for c in df.columns if 'ผู้ซื้อ' in c), None) or next((c for c in df.columns if 'buyer' in c.lower()), None)
            unit_col = next((c for c in df.columns if 'จำนวนที่ขายได้' in c and 'ทั้งหมด' in c), None) or next((c for c in df.columns if 'จำนวนที่ขายได้' in c), None) or next((c for c in df.columns if 'units sold' in c.lower()), None)
            a2c_col = next((c for c in df.columns if 'รถเข็น' in c and 'จำนวน' in c), None) or next((c for c in df.columns if 'รถเข็น' in c), None) or next((c for c in df.columns if 'a2c' in c.lower()), None)
            order_col = next((c for c in df.columns if c == 'ทั้งหมด'), None) or next((c for c in df.columns if 'คำสั่งซื้อ' in c), None) or next((c for c in df.columns if 'orders' in c.lower()), None)

            has_date = 'Date' in df.columns
            group_key = ['Date', p_id_col] if has_date else [p_id_col]
            default_date = date_str if date_str else datetime.today().strftime('%Y-%m-%d')

            # Clean numeric columns
            df['c_rev'] = to_num_col(df[rev_col]) if rev_col else 0.0
            df['c_vis'] = to_num_col(df[vis_col]) if vis_col else 0.0
            df['c_buyer'] = to_num_col(df[buyer_col]) if buyer_col else 0.0
            df['c_unit'] = to_num_col(df[unit_col]) if unit_col else 0.0
            df['c_a2c'] = to_num_col(df[a2c_col]) if a2c_col else 0.0
            df['c_order'] = to_num_col(df[order_col]) if order_col else 0.0

            # Detect child rows
            v_clean = clean_str_series(df[v_id_col]) if v_id_col else pd.Series('', index=df.index)
            is_child = v_clean != ''

            # Products with children
            products_with_children = set(df.loc[is_child, p_id_col]) if (is_child.any() and p_id_col) else set()

            # 1. Standalone products
            mask_parent = ~is_child
            is_prod_with_children = df[p_id_col].isin(products_with_children) if p_id_col else pd.Series(False, index=df.index)

            standalone_df = df[mask_parent & ~is_prod_with_children].copy()
            s_sku = clean_str_series(standalone_df[sku_col], standalone_df.index) if sku_col else pd.Series('', index=standalone_df.index)
            s_psku = clean_str_series(standalone_df[psku_col], standalone_df.index) if psku_col else pd.Series('', index=standalone_df.index)
            s_pid = 'PID-' + standalone_df[p_id_col].astype(str).str.strip() if p_id_col else pd.Series('PID-Unknown', index=standalone_df.index)

            final_standalone_sku = s_sku.where(s_sku != '', s_psku)
            final_standalone_sku = final_standalone_sku.where(final_standalone_sku != '', s_pid)

            standalone_clean = pd.DataFrame({
                'Date': standalone_df['Date'].values if has_date else default_date,
                'Platform': platform,
                'Shop_Name': shop_name,
                'SKU': final_standalone_sku.values,
                'Parent_SKU': standalone_df[p_id_col].astype(str).values if p_id_col else '-',
                'Product': standalone_df[p_name_col].astype(str).values if p_name_col else 'General',
                'Revenue': standalone_df['c_rev'].values,
                'Visitors': standalone_df['c_vis'].values,
                'Buyers': standalone_df['c_buyer'].values,
                'Units_Sold': standalone_df['c_unit'].values,
                'A2C': standalone_df['c_a2c'].values,
                'Orders': standalone_df['c_order'].values
            })

            # 2. Variation products
            if is_child.any() and p_id_col:
                variation_df = df[is_child].copy().reset_index(drop=True)
                v_sku = clean_str_series(variation_df[sku_col], variation_df.index) if sku_col else pd.Series('', index=variation_df.index)
                v_psku = clean_str_series(variation_df[psku_col], variation_df.index) if psku_col else pd.Series('', index=variation_df.index)
                v_name = clean_str_series(variation_df[v_name_col], variation_df.index) if v_name_col else pd.Series('', index=variation_df.index)
                v_pid = 'PID-' + variation_df[p_id_col].astype(str).str.strip()

                final_var_sku = v_sku.where(v_sku != '', v_psku)
                final_var_sku = final_var_sku.where(final_var_sku != '', v_name)
                final_var_sku = final_var_sku.where(final_var_sku != '', v_pid)

                parent_of_var = df[mask_parent & is_prod_with_children].copy()
                parent_vis_orders = parent_of_var.groupby(group_key)[['c_vis', 'c_order']].first().reset_index()

                variation_df['is_first_child'] = ~variation_df.duplicated(subset=group_key)
                variation_df = variation_df.merge(parent_vis_orders, on=group_key, how='left', suffixes=('', '_p'))

                var_vis = np.where(variation_df['is_first_child'], variation_df['c_vis_p'].fillna(0.0), 0.0)
                var_orders = np.where(variation_df['is_first_child'], variation_df['c_order_p'].fillna(0.0), 0.0)

                variation_clean = pd.DataFrame({
                    'Date': variation_df['Date'].values if has_date else default_date,
                    'Platform': platform,
                    'Shop_Name': shop_name,
                    'SKU': final_var_sku.values,
                    'Parent_SKU': variation_df[p_id_col].astype(str).values,
                    'Product': variation_df[p_name_col].astype(str).values if p_name_col else 'General',
                    'Revenue': variation_df['c_rev'].values,
                    'Visitors': var_vis,
                    'Buyers': variation_df['c_buyer'].values,
                    'Units_Sold': variation_df['c_unit'].values,
                    'A2C': variation_df['c_a2c'].values,
                    'Orders': var_orders
                })
                clean_df = pd.concat([standalone_clean, variation_clean], ignore_index=True)
            else:
                clean_df = standalone_clean
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
    def sync_google_drive_pipeline(service, force_rebuild=False):
        if not service:
            return "ไม่สามารถเชื่อมต่อ Google Drive API ได้ (กรุณาเช็ค Secrets)", 0

        # Step 1: Check existing Master file in 02_Master_Data
        master_files = list_files_in_folder(service, MASTER_FOLDER_ID)
        master_file_item = next((f for f in master_files if f['name'] == 'Master_Sales_Full.csv'), None)
        
        if master_file_item and not force_rebuild:
            fh = download_file_bytes(service, master_file_item['id'])
            master_df = pd.read_csv(fh, low_memory=False)
        elif os.path.exists('Master_Sales_Full.csv') and not force_rebuild:
            master_df = pd.read_csv('Master_Sales_Full.csv', low_memory=False)
        else:
            master_df = pd.DataFrame()

        # Step 2: Scan 01_Drop_Inbox recursively across any subfolders and month folders
        all_inbox_files = list_files_in_folder_recursive(service, INBOX_FOLDER_ID)
        new_dfs = []
        files_processed_count = 0

        existing_combos = set()
        if not force_rebuild and not master_df.empty and 'Date' in master_df.columns and 'Platform' in master_df.columns:
            existing_combos = set(zip(master_df['Platform'].astype(str), master_df['Date'].astype(str)))

        for fid, fname, fpath in all_inbox_files:
            date_cand = extract_date_from_name_or_content(fname)
            plat_cand = "Lazada" if ("lazada" in fpath.lower() or "laz" in fpath.lower() or "lazada" in fname.lower()) else "Shopee"
            # Skip downloading if already present in master and not force rebuilding
            if not force_rebuild and existing_combos and date_cand and (plat_cand, date_cand) in existing_combos:
                continue

            try:
                fb = download_file_bytes(service, fid)
                df_parsed = parse_raw_sales_file(fb, fname, folder_name=fpath)
                if not df_parsed.empty:
                    new_dfs.append(df_parsed)
                    files_processed_count += 1
            except Exception:
                pass

        # Step 3: Clean Aggregation & Deterministic Replace (NEVER drop with keep='last')
        if new_dfs:
            combined_new = pd.concat(new_dfs, ignore_index=True)
            
            # Aggregate multiple listings of the same SKU on the same date within incoming files
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
            combined_new = combined_new.sort_values('Revenue', ascending=False)
            combined_new = combined_new.groupby(['Platform', 'Shop_Name', 'Date', 'SKU'], as_index=False).agg(agg_dict)

            if not master_df.empty and not force_rebuild:
                # Replace existing dates with newly parsed clean dates
                new_combos = set(zip(combined_new['Platform'].astype(str), combined_new['Date'].astype(str)))
                mask_retain = ~master_df.apply(lambda r: (str(r['Platform']), str(r['Date'])) in new_combos, axis=1)
                full_df = pd.concat([master_df[mask_retain], combined_new], ignore_index=True)
            else:
                full_df = combined_new

            # Sort deterministically
            full_df = full_df.sort_values(['Date', 'Platform', 'Shop_Name', 'Revenue'], ascending=[True, True, True, False]).reset_index(drop=True)

            # Save locally
            try:
                base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else '.'
                local_csv_path = os.path.join(base_dir, 'Master_Sales_Full.csv')
                full_df.to_csv(local_csv_path, index=False, encoding='utf-8-sig')
            except Exception:
                pass

            # Save to Google Drive
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
    @st.cache_data(ttl=300)
    def load_active_data(file_mtime=0):
        service = get_drive_service()
        master_df = pd.DataFrame()
        active_stock_name = ""
        last_sync_str = ""

        def get_stock_file_sort_key(f, base_folder="."):
            fname = f.get('name', '') if isinstance(f, dict) else str(f)
            
            # Point 1: Upload / Modified Timestamp
            upload_time = ""
            if isinstance(f, dict):
                upload_time = f.get('modifiedTime', '') or f.get('createdTime', '')
            else:
                fpath = os.path.join(base_folder, fname) if not os.path.isabs(fname) else fname
                if os.path.exists(fpath):
                    try:
                        upload_time = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime('%Y-%m-%d %H:%M:%S')
                    except Exception:
                        pass

            # Point 2: Date extracted from File Name
            name_date = extract_date_from_name_or_content(fname)
            if not name_date:
                m = re.search(r'(\d{1,2})[-_.](\d{1,2})', fname)
                if m:
                    name_date = f"2026-{int(m.group(2)):02d}-{int(m.group(1)):02d}"

            # Return dual-check sort key: (Upload Time, Date from Filename, Filename)
            return (upload_time or "0000", name_date or "0000", fname)

        base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else '.'
        local_csv_path = os.path.join(base_dir, 'Master_Sales_Full.csv')

        if service:
            master_files = list_files_in_folder(service, MASTER_FOLDER_ID)
            # Find Master Sales in Google Drive or local
            m_item = next((f for f in master_files if f['name'] == 'Master_Sales_Full.csv'), None)
            if m_item:
                fh = download_file_bytes(service, m_item['id'])
                master_df = pd.read_csv(fh, low_memory=False)
                raw_mtime = m_item.get('modifiedTime', '')
                if raw_mtime:
                    last_sync_str = format_bkk_time(raw_mtime)
            elif os.path.exists(local_csv_path):
                master_df = pd.read_csv(local_csv_path, low_memory=False)
                try:
                    last_sync_str = format_bkk_time(os.path.getmtime(local_csv_path))
                except Exception:
                    pass

            # If master_df is still empty, run sync pipeline to build it from Inbox
            if master_df.empty:
                _, _ = sync_google_drive_pipeline(service, force_rebuild=True)
                if os.path.exists(local_csv_path):
                    master_df = pd.read_csv(local_csv_path, low_memory=False)

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
                        if 'Category_Desc' in master_df.columns:
                            master_df = master_df.drop(columns=['Category_Desc'])
                        master_df = master_df.merge(sku_map, on='SKU', how='left')
                except Exception:
                    pass

            # Find Stock File if exists in 02_Master_Data (Smart Date-Sorted)
            stock_item = next((f for f in sorted(master_files, key=get_stock_file_sort_key, reverse=True) 
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
                        active_stock_name = stock_item.get('name', '')
                        st.session_state['active_stock_file_name'] = active_stock_name
                except Exception:
                    pass
        else:
            # Local fallback for offline testing (Master_Sales_Full only)
            if os.path.exists(local_csv_path):
                master_df = pd.read_csv(local_csv_path, low_memory=False)
                try:
                    last_sync_str = format_bkk_time(os.path.getmtime(local_csv_path))
                except Exception:
                    pass

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
                    for fn in sorted(os.listdir(p), key=lambda x: get_stock_file_sort_key(x, base_folder=p), reverse=True):
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
                                    active_stock_name = fn
                                    st.session_state['active_stock_file_name'] = active_stock_name
                                    break
                            except: pass
                    if 'Stock_Available' in master_df.columns and master_df['Stock_Available'].sum() > 0:
                        break

        if not master_df.empty:
            # Guarantee all numeric and essential columns exist
            for c in ['Revenue', 'Visitors', 'Buyers', 'Units_Sold', 'A2C', 'Orders', 'Stock_Available']:
                if c not in master_df.columns:
                    if c == 'Orders' and 'ทั้งหมด' in master_df.columns:
                        master_df['Orders'] = master_df['ทั้งหมด'].astype(str).str.replace(',', '').str.replace('-', '0').astype(float)
                    else:
                        master_df[c] = 0.0
                master_df[c] = pd.to_numeric(master_df[c], errors='coerce').fillna(0.0)

            if 'Product' not in master_df.columns:
                master_df['Product'] = master_df['SKU']
            else:
                master_df['Product'] = master_df['Product'].fillna(master_df['SKU'])

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

        return master_df, active_stock_name, last_sync_str

    base_dir = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else '.'
    local_csv_path = os.path.join(base_dir, 'Master_Sales_Full.csv')
    local_master_mtime = os.path.getmtime(local_csv_path) if os.path.exists(local_csv_path) else 0
    base_df, active_stock_name, last_sync_str = load_active_data(local_master_mtime)
    st.session_state['active_stock_file_name'] = active_stock_name
    st.session_state['last_sync_time_str'] = last_sync_str

    # ================= 5. Sidebar Sync & Administration =================
    with st.sidebar:
        st.header("⚙️ ระบบท่อข้อมูลอัตโนมัติ")
        st.write("**Google Drive Auto-Sync**")
        col_s1, col_s2 = st.columns(2)
        with col_s1:
            if st.button("🔄 ซิงก์ไฟล์ใหม่", help="ตรวจสอบไฟล์ใหม่ใน 01_Drop_Inbox และนำเข้าเฉพาะวันที่ยังไม่มี"):
                with st.spinner("กำลังตรวจสอบไฟล์ใหม่..."):
                    srv = get_drive_service()
                    status_msg, count = sync_google_drive_pipeline(srv, force_rebuild=False)
                    st.cache_data.clear()
                    if count > 0:
                        st.success(f"นำเข้าสำเร็จ {count} ไฟล์!")
                    else:
                        st.info("ไม่มีไฟล์ใหม่ ข้อมูลเป็นปัจจุบันแล้ว")
                    st.rerun()
        with col_s2:
            if st.button("⚡ รีบิลด์ทั้งหมด", help="อ่านไฟล์รายวันทั้งหมดใน 01_Drop_Inbox จากต้นทางใหม่ 100% เพื่อคำนวณยอดขายใหม่ทั้งหมด"):
                with st.spinner("กำลังประมวลผลไฟล์ทั้งหมดจาก Inbox..."):
                    srv = get_drive_service()
                    status_msg, count = sync_google_drive_pipeline(srv, force_rebuild=True)
                    st.cache_data.clear()
                    st.success(f"รีบิลด์สำเร็จ {count} ไฟล์ ยอดตรง 100%!")
                    st.rerun()

        sync_disp = last_sync_str if last_sync_str else "ยังไม่มีบันทึกเวลา"
        st.caption(f"⏱️ **ซิงก์ล่าสุด:** {sync_disp}")
        st.markdown("<div style='font-size:11px; color:#888; line-height:1.3; margin-top:-5px;'>💡 <i>ระบบจะอ่านจากไฟล์รายวันใน 01_Drop_Inbox โดยตรง</i></div>", unsafe_allow_html=True)

    # ================= 6. UI Banner & Top Filters =================
    st.markdown("""
        <style>
        /* 1. Hide the top-right running spinner/man icon entirely */
        div[data-testid="stStatusWidget"], [data-testid="stStatusWidget"] {
            display: none !important;
            visibility: hidden !important;
        }

        /* 2. Prevent opacity fade/dimming during rerun (No graying out) */
        div[data-testid="stAppViewContainer"] .main,
        div[data-testid="stAppViewBlockContainer"],
        .element-container, [data-testid="stElementContainer"] {
            opacity: 1 !important;
            transition: none !important;
        }

        /* 3. Smooth table rendering & prevent canvas flicker */
        div[data-testid="stDataFrame"] {
            transition: none !important;
            max-width: 100% !important;
            width: 100% !important;
        }

        /* 4. ANTI-LAYOUT-SHIFT: Strictly lock column widths & prevent flex expansion */
        @media (min-width: 768px) {
            div[data-testid="stHorizontalBlock"], .stHorizontalBlock {
                flex-wrap: nowrap !important;
            }
            div[data-testid="stColumn"], div[data-testid="column"], .stColumn {
                flex-grow: 0 !important;
                flex-shrink: 0 !important;
            }
        }

        /* 5. Plotly container height lock to avoid collapsing */
        div[data-testid="stPlotlyChart"], .stPlotlyChart {
            min-height: 320px !important;
            max-width: 100% !important;
        }

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
    """, unsafe_allow_html=True)

    sync_badge = f"<span style='font-size: 12px; font-weight: normal; background: rgba(255,255,255,0.2); padding: 4px 10px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.3);'>⏱️ ซิงก์ล่าสุด: {last_sync_str}</span>" if last_sync_str else ""
    st.markdown(f"""
        <div class="pbi-bar">
            <span>OMNICHANNEL SALES DASHBOARD</span>
            <div style="display: flex; align-items: center; gap: 12px;">
                {sync_badge}
                <span style="font-size: 14px; font-weight: normal; opacity: 0.9;">SHOPEE & LAZADA MULTI-SHOP</span>
            </div>
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

    # ================= 8. Interactive Dashboard Fragment =================
    # Wrapped in @st.fragment so table clicks rerun ONLY the dashboard content,
    # preventing full-page reloads, screen flickering, or jumping!
    @st.fragment
    def render_interactive_dashboard(filtered_df, active_stock_name=""):
        monthly_base = filtered_df.groupby('Month').agg({'Revenue': 'sum', 'Visitors': 'sum'}).reset_index().sort_values('Month')
        cat_base = filtered_df.groupby('Category_Desc').agg({
            'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum'
        }).reset_index().sort_values('Revenue', ascending=False)
        cat_base['Avg CR'] = (cat_base['Buyers'] / cat_base['Visitors'] * 100).fillna(0)
        cat_base['Avg Price'] = (cat_base['Revenue'] / cat_base['Units_Sold']).fillna(0)

        daily_base = filtered_df.groupby('Day').agg({'DateObj': 'first', 'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum'}).reset_index().sort_values('DateObj')
        daily_base['Date'] = pd.to_datetime(daily_base['DateObj']).dt.date
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

        # ================= 10. Display Tables with Symmetric Cross-Filtering =================
        has_active_selection = bool(selected_months or selected_cats_table or selected_days or selected_skus)
        col_rst1, col_rst2 = st.columns([4, 1])
        with col_rst1:
            if has_active_selection:
                active_info = []
                if selected_months: active_info.append(f"เดือน: {', '.join(selected_months)}")
                if selected_cats_table: active_info.append(f"หมวดหมู่: {', '.join(selected_cats_table)}")
                if selected_days: active_info.append(f"วันที่: {', '.join(selected_days)}")
                if selected_skus: active_info.append(f"SKU: {', '.join(selected_skus[:3])}{'...' if len(selected_skus)>3 else ''}")
                st.markdown(f"<div style='background-color:#e8f4fd; color:#0c5460; padding:8px 14px; border-radius:6px; font-size:14px; border:1px solid #bee5eb;'>🎯 <b>กำลังกรองข้อมูลตาม:</b> {' | '.join(active_info)}</div>", unsafe_allow_html=True)
            else:
                st.markdown("<div style='background-color:#f8f9fa; color:#6c757d; padding:8px 14px; border-radius:6px; font-size:14px; border:1px solid #e9ecef;'>💡 <i>คลิกเลือกแถวในตารางด้านล่างเพื่อ Cross-Filter กรองยอดขายและพฤติกรรมลูกค้า</i></div>", unsafe_allow_html=True)
        with col_rst2:
            if has_active_selection:
                if st.button("🔄 ล้างตัวกรอง (Reset)", use_container_width=True):
                    for k in ['tb_month', 'tb_cat', 'tb_day', 'tb_sku']:
                        if k in st.session_state:
                            del st.session_state[k]
                    st.rerun()
            else:
                st.button("🔄 ล้างตัวกรอง (Reset)", use_container_width=True, disabled=True)

        # 1. Order Month (Macro time overview - preserved with full month list)
        df_for_month = filtered_df.copy()
        if selected_cats_table: df_for_month = df_for_month[df_for_month['Category_Desc'].isin(selected_cats_table)]
        if selected_skus: df_for_month = df_for_month[df_for_month['SKU'].isin(selected_skus)]
        # Note: Do NOT filter df_for_month by selected_days to ensure Order Month never collapses to 1 row
        disp_monthly = df_for_month.groupby('Month').agg({'Revenue': 'sum', 'Visitors': 'sum'}).reset_index()
        all_months_list = sorted(filtered_df['Month'].dropna().unique().tolist())
        all_months_df = pd.DataFrame({'Month': all_months_list})
        disp_monthly = all_months_df.merge(disp_monthly, on='Month', how='left').fillna({'Revenue': 0.0, 'Visitors': 0.0})
        total_m_rev = disp_monthly['Revenue'].sum()
        disp_monthly['% Rev'] = (disp_monthly['Revenue'] / total_m_rev * 100).fillna(0) if total_m_rev > 0 else 0.0
        st.session_state['tb_month_rendered_ids'] = disp_monthly['Month'].tolist()

        # 2. Product Group (Filtered by Month, Day, SKU - but not Cat itself)
        df_for_cat = filtered_df.copy()
        if selected_months: df_for_cat = df_for_cat[df_for_cat['Month'].isin(selected_months)]
        if selected_days: df_for_cat = df_for_cat[df_for_cat['Day'].isin(selected_days)]
        if selected_skus: df_for_cat = df_for_cat[df_for_cat['SKU'].isin(selected_skus)]
        disp_cat = df_for_cat.groupby('Category_Desc').agg({
            'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum'
        }).reset_index()
        disp_cat = disp_cat[(disp_cat['Revenue'] > 0) | (disp_cat['Visitors'] > 0) | (disp_cat['A2C'] > 0)]
        disp_cat['Avg CR'] = (disp_cat['Buyers'] / disp_cat['Visitors'] * 100).fillna(0)
        disp_cat['Avg Price'] = (disp_cat['Revenue'] / disp_cat['Units_Sold']).fillna(0)
        disp_cat = disp_cat.sort_values(by=['Revenue', 'Visitors'], ascending=[False, False])
        st.session_state['tb_cat_rendered_ids'] = disp_cat['Category_Desc'].tolist()

        # 3. Order Date (Filtered by Month, Cat, SKU - but not Day itself)
        df_for_day = filtered_df.copy()
        if selected_months: df_for_day = df_for_day[df_for_day['Month'].isin(selected_months)]
        if selected_cats_table: df_for_day = df_for_day[df_for_day['Category_Desc'].isin(selected_cats_table)]
        if selected_skus: df_for_day = df_for_day[df_for_day['SKU'].isin(selected_skus)]
        disp_daily = df_for_day.groupby('Day').agg({'DateObj': 'first', 'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum'}).reset_index()
        disp_daily = disp_daily[(disp_daily['Revenue'] > 0) | (disp_daily['Visitors'] > 0)].sort_values('DateObj')
        disp_daily['Date'] = pd.to_datetime(disp_daily['DateObj']).dt.date
        st.session_state['tb_day_rendered_ids'] = disp_daily['Day'].tolist()

        # 4. SKU Code (Filtered by Month, Cat, Day - but not SKU itself)
        df_for_sku = filtered_df.copy()
        if selected_months: df_for_sku = df_for_sku[df_for_sku['Month'].isin(selected_months)]
        if selected_cats_table: df_for_sku = df_for_sku[df_for_sku['Category_Desc'].isin(selected_cats_table)]
        if selected_days: df_for_sku = df_for_sku[df_for_sku['Day'].isin(selected_days)]
        
        if 'Product' not in df_for_sku.columns:
            df_for_sku['Product'] = df_for_sku['SKU']
        else:
            df_for_sku['Product'] = df_for_sku['Product'].fillna(df_for_sku['SKU'])

        disp_sku = df_for_sku.groupby('SKU').agg({
            'Product': 'first',
            'Revenue': 'sum', 'Visitors': 'sum', 'Buyers': 'sum', 'Units_Sold': 'sum', 'A2C': 'sum',
            'Stock_Available': 'first'
        }).reset_index()
        disp_sku = disp_sku[(disp_sku['Revenue'] > 0) | (disp_sku['Visitors'] > 0) | (disp_sku['A2C'] > 0)]
        disp_sku['Avg CR'] = (disp_sku['Buyers'] / disp_sku['Visitors'] * 100).fillna(0)
        disp_sku['Avg Price'] = (disp_sku['Revenue'] / disp_sku['Units_Sold']).fillna(0)
        disp_sku = disp_sku.sort_values(by=['Revenue', 'Visitors'], ascending=[False, False])
        st.session_state['tb_sku_rendered_ids'] = disp_sku['SKU'].tolist()

        # Explicit column configurations with fixed widths to prevent Glide Data Grid layout shifts
        col_config_month = {
            "Month": st.column_config.TextColumn("Month", width="small"),
            "Revenue": st.column_config.NumberColumn("Revenue", format="%,.2f", width="medium"),
            "% Rev": st.column_config.ProgressColumn("%", format="%.1f%%", min_value=0, max_value=100, width="small"),
            "Visitors": st.column_config.NumberColumn("Visitors", format="%,d", width="small")
        }

        col_config_cat = {
            "Category_Desc": st.column_config.TextColumn("หมวดหมู่สินค้า (Category)", width="large"),
            "Revenue": st.column_config.NumberColumn("Revenue", format="%,.2f", width="medium"),
            "Avg CR": st.column_config.NumberColumn("CR", format="%.2f%%", width="small"),
            "Visitors": st.column_config.NumberColumn("Visitors", format="%,d", width="small"),
            "Avg Price": st.column_config.NumberColumn("Avg Price", format="%,.2f", width="small"),
            "A2C": st.column_config.NumberColumn("A2C", format="%,d", width="small"),
            "Buyers": st.column_config.NumberColumn("Buyers", format="%,d", width="small"),
            "Units_Sold": st.column_config.NumberColumn("Units", format="%,d", width="small")
        }

        col_config_day = {
            "Date": st.column_config.DateColumn("Date (วันที่)", format="DD/MM/YYYY", width="medium"),
            "Revenue": st.column_config.NumberColumn("Revenue", format="%,.2f", width="medium"),
            "Visitors": st.column_config.NumberColumn("Visitors", format="%,d", width="small"),
            "Buyers": st.column_config.NumberColumn("Buyers", format="%,d", width="small"),
            "Units_Sold": st.column_config.NumberColumn("Units", format="%,d", width="small")
        }

        col_config_sku = {
            "SKU": st.column_config.TextColumn("SKU Code", width="medium"),
            "Product": st.column_config.TextColumn("ชื่อสินค้า (Description)", width="large"),
            "Revenue": st.column_config.NumberColumn("Revenue", format="%,.2f", width="medium"),
            "Avg CR": st.column_config.NumberColumn("CR", format="%.2f%%", width="small"),
            "Visitors": st.column_config.NumberColumn("Visitors", format="%,d", width="small"),
            "A2C": st.column_config.NumberColumn("A2C", format="%,d", width="small"),
            "Avg Price": st.column_config.NumberColumn("Avg Price", format="%,.2f", width="small"),
            "Buyers": st.column_config.NumberColumn("Buyers", format="%,d", width="small"),
            "Units_Sold": st.column_config.NumberColumn("Units", format="%,d", width="small"),
            "Stock_Available": st.column_config.NumberColumn("Stock", format="%d ชิ้น", width="small")
        }

        # Middle Row (Fixed height 320px for perfect alignment & zero layout jump)
        col_m1, col_m2, col_m3 = st.columns([1.2, 1.8, 2.5])
        with col_m1:
            st.write("**Order Month**")
            st.dataframe(
                disp_monthly[['Month', 'Revenue', '% Rev', 'Visitors']], 
                hide_index=True, use_container_width=True, height=320,
                on_select="rerun", selection_mode="multi-row", key="tb_month",
                column_config=col_config_month
            )
        with col_m2:
            st.write("**Revenue Trend by FGMONTHYEAR**")
            chart_df = cross_df.groupby('Month').agg({'Revenue': 'sum'}).reset_index().sort_values('Month')
            if not chart_df.empty:
                fig = px.line(chart_df, x='Month', y='Revenue', markers=True, text='Revenue', color_discrete_sequence=['#00d4ff'])
                fig.update_traces(textposition="top center", texttemplate='%{text:.2s}')
                fig.update_layout(
                    margin=dict(l=0, r=0, t=10, b=0), height=320,
                    xaxis_title="", yaxis_title="",
                    xaxis=dict(showgrid=False), yaxis=dict(showgrid=False),
                    transition={'duration': 0}
                )
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
            else:
                st.markdown("<div style='height:320px; display:flex; align-items:center; justify-content:center; background:#fafafa; border-radius:6px; color:#888;'>ไม่มีข้อมูลแนวโน้มยอดขายตามตัวกรองนี้</div>", unsafe_allow_html=True)
        with col_m3:
            st.write("**Product Group (หมวดหมู่สินค้า)**")
            st.dataframe(
                disp_cat[['Category_Desc', 'Revenue', 'Avg CR', 'Visitors', 'Avg Price', 'A2C', 'Buyers', 'Units_Sold']], 
                hide_index=True, use_container_width=True, height=320,
                on_select="rerun", selection_mode="multi-row", key="tb_cat",
                column_config=col_config_cat
            )

        st.markdown("---")
        # Bottom Row (Fixed height 380px for perfect alignment & zero layout jump)
        col_d1, col_d2 = st.columns([1.5, 2.5])
        with col_d1:
            st.write("**Order Date (รายวัน)**")
            st.dataframe(
                disp_daily[['Date', 'Revenue', 'Visitors', 'Buyers', 'Units_Sold']], 
                hide_index=True, use_container_width=True, height=380,
                on_select="rerun", selection_mode="multi-row", key="tb_day",
                column_config=col_config_day
            )
        def render_tree_view_html(df_input, search_term=""):
            if df_input.empty:
                empty_html = """<!DOCTYPE html><html><body style="background:transparent;font-family:-apple-system,BlinkMacSystemFont,sans-serif;color:#888;display:flex;align-items:center;justify-content:center;height:100px;"><p>ไม่มีข้อมูลสินค้าตามตัวกรองที่เลือก</p></body></html>"""
                return empty_html, False, 0

            df_tree = df_input.copy()
            if 'Parent_SKU' in df_tree.columns:
                p_id = df_tree['Parent_SKU'].astype(str).str.strip().replace({'nan': '-', 'None': '-', '': '-'})
                df_tree['Parent_ID'] = np.where((p_id == '-') | (p_id == '') | (df_tree['Platform'] == 'Lazada'), df_tree['SKU'], p_id)
            else:
                df_tree['Parent_ID'] = df_tree['SKU']

            if 'Product' not in df_tree.columns:
                df_tree['Product'] = df_tree['SKU']
            if 'Stock_Available' not in df_tree.columns:
                df_tree['Stock_Available'] = 0

            sku_counts = df_tree.groupby('Parent_ID')['SKU'].nunique()

            def get_common_prefix(strings):
                valid = [str(s).strip() for s in strings if str(s).strip() not in ['-', '', 'nan', 'None'] and not str(s).strip().startswith('PID-')]
                if not valid: return ""
                import os
                cp = os.path.commonprefix(valid)
                return cp.rstrip('-_./ ')

            parent_sku_dict = {}
            for pid, group in df_tree.groupby('Parent_ID'):
                skus = [str(s).strip() for s in group['SKU'].dropna().unique() 
                        if str(s).strip() not in ['', '-', 'nan', 'None'] and not str(s).strip().startswith('PID-')]
                if not skus:
                    parent_sku_dict[pid] = str(pid)
                elif len(skus) == 1:
                    parent_sku_dict[pid] = skus[0]
                else:
                    cp = get_common_prefix(skus)
                    parent_sku_dict[pid] = cp if len(cp) >= 2 else skus[0]

            # Parent aggregation
            parent_df = df_tree.groupby('Parent_ID').agg({
                'Product': 'first',
                'Revenue': 'sum',
                'Visitors': 'sum',
                'A2C': 'sum',
                'Units_Sold': 'sum',
                'Buyers': 'sum',
                'Stock_Available': 'sum',
                'SKU': 'first'
            }).reset_index()

            parent_df['Parent_SKU_Display'] = parent_df['Parent_ID'].map(parent_sku_dict).fillna(parent_df['SKU'])
            parent_df['variant_count'] = parent_df['Parent_ID'].map(sku_counts).fillna(1).astype(int)
            parent_df['is_multi'] = parent_df['variant_count'] > 1
            parent_df = parent_df.sort_values(by=['Revenue', 'Visitors'], ascending=[False, False])

            variants_dict = {}
            multi_pids = set(parent_df.loc[parent_df['is_multi'], 'Parent_ID'])
            if multi_pids:
                multi_rows = df_tree[df_tree['Parent_ID'].isin(multi_pids)]
                child_agg = multi_rows.groupby(['Parent_ID', 'SKU']).agg({
                    'Revenue': 'sum',
                    'Visitors': 'sum',
                    'A2C': 'sum',
                    'Units_Sold': 'sum',
                    'Buyers': 'sum',
                    'Stock_Available': 'sum'
                }).reset_index().sort_values('Revenue', ascending=False)
                for pid, group in child_agg.groupby('Parent_ID'):
                    variants_dict[pid] = group

            st_clean = search_term.strip().lower()
            matching_pids = set()
            open_pids = set()

            if st_clean:
                for _, row in parent_df.iterrows():
                    pid = row['Parent_ID']
                    p_disp_sku = str(row.get('Parent_SKU_Display', '')).lower()
                    if (st_clean in str(row['Product']).lower() or 
                        st_clean in str(row['SKU']).lower() or 
                        st_clean in str(pid).lower() or 
                        st_clean in p_disp_sku):
                        matching_pids.add(pid)
                for pid, children in variants_dict.items():
                    if any(st_clean in str(c_sku).lower() for c_sku in children['SKU']):
                        matching_pids.add(pid)
                        open_pids.add(pid)
                parent_df = parent_df[parent_df['Parent_ID'].isin(matching_pids)]

            total_parents = len(parent_df)
            is_truncated = False
            if not st_clean and total_parents > 100:
                parent_df = parent_df.head(100)
                is_truncated = True

            rows_html = []
            for _, row in parent_df.iterrows():
                pid = str(row['Parent_ID'])
                pname = html_lib.escape(str(row['Product']))
                seller_sku_p = html_lib.escape(str(row.get('Parent_SKU_Display', row['SKU'])))
                rev_val = row['Revenue']
                vis_val = row['Visitors']
                buyers_val = row['Buyers']
                cr_val = (buyers_val / vis_val * 100) if vis_val > 0 else 0.0
                cr_str = f"{cr_val:.2f}%" if vis_val > 0 else "0.00%"
                a2c_val = row['A2C']
                unit_val = row['Units_Sold']
                stock_val = row['Stock_Available']
                stock_color = "#22c55e" if stock_val > 0 else "#ef4444"

                has_children = row['is_multi'] and pid in variants_dict

                if has_children:
                    children = variants_dict[pid]
                    var_rows = []
                    for _, c in children.iterrows():
                        c_sku = html_lib.escape(str(c['SKU']))
                        c_rev = c['Revenue']
                        c_vis = c.get('Visitors', 0.0)
                        c_buyers = c.get('Buyers', 0.0)
                        c_cr = (c_buyers / c_vis * 100) if c_vis > 0 else 0.0
                        c_cr_str = f"{c_cr:.2f}%" if c_vis > 0 else "-"
                        c_vis_str = f"{c_vis:,.0f}" if c_vis > 0 else "-"
                        c_a2c = c['A2C']
                        c_unit = c['Units_Sold']
                        c_stock = c['Stock_Available']
                        c_stock_color = "#22c55e" if c_stock > 0 else "#ef4444"

                        var_rows.append(f"""
                        <div class="row child-row">
                          <div class="col col-prod"><span class="sku-tag">↳ {c_sku}</span></div>
                          <div class="col col-rev">{c_rev:,.0f}</div>
                          <div class="col col-cr" style="color: var(--text-dim);">{c_cr_str}</div>
                          <div class="col col-vis" style="color: var(--text-dim);">{c_vis_str}</div>
                          <div class="col col-a2c">{c_a2c:,.0f}</div>
                          <div class="col col-unit">{c_unit:,.0f}</div>
                          <div class="col col-stock" style="color: {c_stock_color};">{c_stock:,.0f}</div>
                        </div>
                        """)

                    child_html = "".join(var_rows)
                    is_open = 'open' if pid in open_pids else ''

                    rows_html.append(f"""
                    <div class="parent-item" data-name="{seller_sku_p} {pname}" data-rev="{rev_val}" data-cr="{cr_val:.2f}" data-vis="{vis_val}" data-a2c="{a2c_val}" data-unit="{unit_val}" data-stock="{stock_val}">
                      <details {is_open}>
                        <summary class="row">
                          <div class="col col-prod">
                            <span class="badge">[+] {row['variant_count']} ตัวเลือก</span>
                            <span class="parent-sku-tag">{seller_sku_p}</span>
                            <span class="pname" title="{pname}">{pname}</span>
                          </div>
                          <div class="col col-rev">{rev_val:,.0f}</div>
                          <div class="col col-cr">{cr_str}</div>
                          <div class="col col-vis">{vis_val:,.0f}</div>
                          <div class="col col-a2c">{a2c_val:,.0f}</div>
                          <div class="col col-unit">{unit_val:,.0f}</div>
                          <div class="col col-stock" style="color: {stock_color};">{stock_val:,.0f}</div>
                        </summary>
                        <div class="child-container">
                          {child_html}
                        </div>
                      </details>
                    </div>
                    """)
                else:
                    s_sku = seller_sku_p
                    rows_html.append(f"""
                    <div class="row standalone-row" data-name="{s_sku} {pname}" data-rev="{rev_val}" data-cr="{cr_val:.2f}" data-vis="{vis_val}" data-a2c="{a2c_val}" data-unit="{unit_val}" data-stock="{stock_val}">
                      <div class="col col-prod">
                        <span class="single-sku">{s_sku}</span>
                        <span class="pname-single" title="{pname}">{pname}</span>
                      </div>
                      <div class="col col-rev">{rev_val:,.0f}</div>
                      <div class="col col-cr">{cr_str}</div>
                      <div class="col col-vis">{vis_val:,.0f}</div>
                      <div class="col col-a2c">{a2c_val:,.0f}</div>
                      <div class="col col-unit">{unit_val:,.0f}</div>
                      <div class="col col-stock" style="color: {stock_color};">{stock_val:,.0f}</div>
                    </div>
                    """)

            all_rows = "".join(rows_html)

            full_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {{
    --bg-card: #131720;
    --bg-header: #1a1f2c;
    --bg-hover: #1e2433;
    --bg-child: #0b0e14;
    --border: #262c3a;
    --border-dashed: #363d50;
    --text: #e6edf3;
    --text-muted: #94a3b8;
    --text-dim: #64748b;
    --badge-bg: rgba(56, 189, 248, 0.15);
    --badge-text: #38bdf8;
    --badge-border: rgba(56, 189, 248, 0.35);
    --parent-sku-text: #38bdf8;
    --parent-sku-bg: rgba(56, 189, 248, 0.1);
    --parent-sku-border: rgba(56, 189, 248, 0.25);
    --sku-text: #38bdf8;
  }}
  @media (prefers-color-scheme: light) {{
    :root {{
      --bg-card: #ffffff;
      --bg-header: #f8f9fa;
      --bg-hover: #f1f5f9;
      --bg-child: #f8fafc;
      --border: #e2e8f0;
      --border-dashed: #cbd5e1;
      --text: #1e293b;
      --text-muted: #64748b;
      --text-dim: #94a3b8;
      --badge-bg: #e0f2fe;
      --badge-text: #0284c7;
      --badge-border: #bae6fd;
      --parent-sku-text: #0284c7;
      --parent-sku-bg: #f0f9ff;
      --parent-sku-border: #bae6fd;
      --sku-text: #0284c7;
    }}
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: transparent;
    color: var(--text);
    overflow: hidden;
  }}
  .container {{
    height: 330px;
    overflow-y: auto;
    border: 1px solid var(--border);
    border-radius: 8px;
    background: var(--bg-card);
  }}
  .container::-webkit-scrollbar {{ width: 6px; height: 6px; }}
  .container::-webkit-scrollbar-track {{ background: transparent; }}
  .container::-webkit-scrollbar-thumb {{ background: var(--border); border-radius: 3px; }}

  .row {{
    display: flex;
    align-items: center;
    width: 100%;
    font-size: 13px;
    border-bottom: 1px solid var(--border);
  }}
  .header-row {{
    background: var(--bg-header);
    position: sticky;
    top: 0;
    z-index: 10;
    font-weight: 600;
    font-size: 12px;
    color: var(--text-muted);
  }}
  .parent-item summary {{
    cursor: pointer;
    list-style: none;
    user-select: none;
  }}
  .parent-item summary::-webkit-details-marker {{ display: none; }}
  .parent-item summary:hover, .standalone-row:hover, .child-row:hover {{
    background: var(--bg-hover);
  }}

  /* Laser-aligned Flexbox Columns: 32% + 14% + 10% + 11% + 10% + 10% + 13% = 100% */
  .col-prod  {{ width: 32%; padding: 8px 10px; text-align: left; overflow: hidden; display: flex; align-items: center; gap: 8px; flex-shrink: 0; }}
  .col-rev   {{ width: 14%; padding: 8px 8px; text-align: right; font-weight: bold; flex-shrink: 0; }}
  .col-cr    {{ width: 10%; padding: 8px 8px; text-align: right; font-weight: 600; color: #10b981; flex-shrink: 0; }}
  .col-vis   {{ width: 11%; padding: 8px 8px; text-align: right; color: var(--text-muted); flex-shrink: 0; }}
  .col-a2c   {{ width: 10%; padding: 8px 8px; text-align: right; color: var(--text-muted); flex-shrink: 0; }}
  .col-unit  {{ width: 10%; padding: 8px 8px; text-align: right; color: var(--text-muted); flex-shrink: 0; }}
  .col-stock {{ width: 13%; padding: 8px 14px 8px 8px; text-align: right; font-weight: bold; flex-shrink: 0; }}

  .child-container {{
    background: var(--bg-child);
    border-top: 1px dashed var(--border-dashed);
    border-bottom: 1px solid var(--border);
  }}
  .child-row {{
    font-size: 12px;
    border-bottom: 1px solid var(--border);
  }}
  .child-row:last-child {{ border-bottom: none; }}
  .child-row .col-prod {{ padding-left: 28px; }}
  .child-row .col-rev {{ font-weight: 500; }}

  .badge {{
    font-size: 11px;
    background: var(--badge-bg);
    color: var(--badge-text);
    border: 1px solid var(--badge-border);
    padding: 2px 7px;
    border-radius: 4px;
    font-weight: 600;
    flex-shrink: 0;
  }}
  .parent-sku-tag {{
    font-family: ui-monospace, monospace;
    font-weight: 700;
    font-size: 11px;
    color: var(--parent-sku-text);
    background: var(--parent-sku-bg);
    border: 1px solid var(--parent-sku-border);
    padding: 2px 6px;
    border-radius: 4px;
    flex-shrink: 0;
  }}
  .pname {{
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 13px;
    color: var(--text);
  }}
  .pname-single {{
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    font-size: 12px;
    color: var(--text-muted);
  }}
  .single-sku {{
    font-family: ui-monospace, monospace;
    font-weight: 600;
    font-size: 13px;
    color: var(--text);
    flex-shrink: 0;
  }}
  .sku-tag {{
    font-family: ui-monospace, monospace;
    font-weight: 600;
    color: var(--sku-text);
  }}
  .sortable-header {{
    cursor: pointer;
    user-select: none;
    transition: color 0.15s ease, background 0.15s ease;
  }}
  .sortable-header:hover {{
    color: var(--badge-text);
    background: var(--bg-hover);
  }}
  .sort-icon {{
    font-size: 10px;
    margin-left: 4px;
    display: inline-block;
    color: var(--badge-text);
  }}
</style>
</head>
<body>
<div class="container" id="treeContainer">
  <div class="row header-row">
    <div class="col-prod sortable-header" onclick="sortTable('name')" title="คลิกเพื่อเรียงตามชื่อสินค้า / SKU">สินค้า / รหัส SKU <span id="sort-name" class="sort-icon"></span></div>
    <div class="col-rev sortable-header" onclick="sortTable('rev')" title="คลิกเพื่อเรียงตามยอดขาย">ยอดขาย (฿) <span id="sort-rev" class="sort-icon">▼</span></div>
    <div class="col-cr sortable-header" onclick="sortTable('cr')" title="คลิกเพื่อเรียงตาม CR">CR (%) <span id="sort-cr" class="sort-icon"></span></div>
    <div class="col-vis sortable-header" onclick="sortTable('vis')" title="คลิกเพื่อเรียงตามคนเข้าชม">คนเข้าชม <span id="sort-vis" class="sort-icon"></span></div>
    <div class="col-a2c sortable-header" onclick="sortTable('a2c')" title="คลิกเพื่อเรียงตามตะกร้า">ตะกร้า (A2C) <span id="sort-a2c" class="sort-icon"></span></div>
    <div class="col-unit sortable-header" onclick="sortTable('unit')" title="คลิกเพื่อเรียงตามชิ้นที่ขาย">ชิ้นที่ขาย <span id="sort-unit" class="sort-icon"></span></div>
    <div class="col-stock sortable-header" onclick="sortTable('stock')" title="คลิกเพื่อเรียงตามสต็อก">สต็อก <span id="sort-stock" class="sort-icon"></span></div>
  </div>
  <div id="treeRows">
    {all_rows}
  </div>
</div>

<script>
let currentSortCol = 'rev';
let currentSortDir = 'desc';

function sortTable(col) {{
  if (currentSortCol === col) {{
    currentSortDir = (currentSortDir === 'desc') ? 'asc' : 'desc';
  }} else {{
    currentSortCol = col;
    currentSortDir = (col === 'name') ? 'asc' : 'desc';
  }}

  ['name', 'rev', 'cr', 'vis', 'a2c', 'unit', 'stock'].forEach(c => {{
    const el = document.getElementById('sort-' + c);
    if (el) {{
      el.textContent = (c === currentSortCol) ? (currentSortDir === 'desc' ? ' ▼' : ' ▲') : '';
    }}
  }});

  const rowsContainer = document.getElementById('treeRows');
  if (!rowsContainer) return;
  const items = Array.from(rowsContainer.children);

  items.sort((a, b) => {{
    let valA = a.getAttribute('data-' + currentSortCol) || '';
    let valB = b.getAttribute('data-' + currentSortCol) || '';

    if (currentSortCol === 'name') {{
      return currentSortDir === 'asc' 
        ? valA.localeCompare(valB, 'th') 
        : valB.localeCompare(valA, 'th');
    }} else {{
      let numA = parseFloat(valA) || 0;
      let numB = parseFloat(valB) || 0;
      return currentSortDir === 'desc' ? numB - numA : numA - numB;
    }}
  }});

  items.forEach(item => rowsContainer.appendChild(item));
}}
</script>
</body>
</html>"""
            return full_html, is_truncated, total_parents

        with col_d2:
            active_stock = active_stock_name or st.session_state.get('active_stock_file_name', '')
            stock_badge = f" <span style='font-size:12px; color:#0099ff; font-weight:normal;'>(สต็อกอ้างอิง: {active_stock})</span>" if active_stock else ""
            
            is_lazada_only = (filtered_df['Platform'].nunique() == 1 and filtered_df['Platform'].iloc[0] == 'Lazada')

            if is_lazada_only:
                # 100% Classic Direct SKU Data Grid for Lazada
                st.markdown(f"**SKU Code (รายสินค้า)**{stock_badge}", unsafe_allow_html=True)
                st.dataframe(
                    disp_sku[['SKU', 'Product', 'Revenue', 'Avg CR', 'Visitors', 'A2C', 'Avg Price', 'Buyers', 'Units_Sold', 'Stock_Available']], 
                    hide_index=True, use_container_width=True, height=380,
                    on_select="rerun", selection_mode="multi-row", key="tb_sku",
                    column_config=col_config_sku
                )
            else:
                tab_tree, tab_grid = st.tabs([
                    "🌳 เจาะลึก Parent & Variants (Tree View)", 
                    "📋 ตารางสรุปราย SKU (Data Grid)"
                ])
                
                with tab_tree:
                    col_t_search, col_t_badge = st.columns([2, 1])
                    with col_t_search:
                        tree_search = st.text_input(
                            "ค้นหาตามชื่อสินค้า / รหัส SKU", 
                            key="tree_search_term", 
                            placeholder="🔍 พิมพ์ชื่อสินค้า หรือ รหัส SKU เพื่อค้นหา...", 
                            label_visibility="collapsed"
                        )
                    with col_t_badge:
                        if active_stock:
                            st.markdown(f"<div style='text-align:right; font-size:12px; color:#0099ff; padding-top:6px;'>📦 สต็อก: {active_stock}</div>", unsafe_allow_html=True)
                    
                    tree_html, is_trunc, total_p = render_tree_view_html(df_for_sku, tree_search)
                    components.html(tree_html, height=335)
                    if is_trunc:
                        st.caption(f"* แสดง 100 อันดับแรกจากทั้งหมด {total_p:,} สินค้า (พิมพ์ค้นหาในช่องด้านบนเพื่อดูสินค้าอื่นเพิ่มเติม)")
                    
                with tab_grid:
                    st.markdown(f"**SKU Code (รายสินค้า)**{stock_badge}", unsafe_allow_html=True)
                    st.dataframe(
                        disp_sku[['SKU', 'Product', 'Revenue', 'Avg CR', 'Visitors', 'A2C', 'Avg Price', 'Buyers', 'Units_Sold', 'Stock_Available']], 
                        hide_index=True, use_container_width=True, height=340,
                        on_select="rerun", selection_mode="multi-row", key="tb_sku",
                        column_config=col_config_sku
                    )

    render_interactive_dashboard(filtered_df, active_stock_name)
