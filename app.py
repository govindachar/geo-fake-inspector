import streamlit as st
import os
import glob
import re
import duckdb
import pandas as pd
import polars as pl
import gspread
from google.oauth2.service_account import Credentials

st.set_page_config(page_title="Geo Fake Inspector", layout="wide")
st.title("🛡️ Geo-Fake Seller Inspection Webapp")

# 1. Secure Authentication for Google Sheets
@st.cache_resource
def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    credentials = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"],
        scopes=scopes
    )
    return gspread.authorize(credentials)

# 2. Fetch PH Map Data
@st.cache_data(ttl=3600)
def load_ph_map(sheet_id):
    gc = get_gspread_client()
    sh = gc.open_by_key(sheet_id)
    worksheet = sh.get_worksheet(0)
    df = pd.DataFrame(worksheet.get_all_records())
    df.columns = [str(c).strip() for c in df.columns]
    ph_col = [c for c in df.columns if any(k in c.lower() for k in ['ph name', 'phname', 'ph', 'name'])][0]
    df['clean_ph'] = df[ph_col].astype(str).str.strip().str.lower()
    return df, ph_col

PH_MAP_SHEET_ID = "1zhyJ4JQf8EbzQW4TQNHv5LGl6ngwj6V2TqiOdSar-ek"

try:
    ph_map_df, ph_col = load_ph_map(PH_MAP_SHEET_ID)
    st.success(f"✅ Connected to PHMap Sheet ({len(ph_map_df)} records loaded).")
except Exception as e:
    st.info("ℹ️ Connect Google Sheets Credentials in Streamlit Secrets to automatically map Zone/AM/RM/GM data.")
    ph_map_df = None

# Sidebar Controls
st.sidebar.header("📁 Load Files")
uploaded_files = st.sidebar.file_uploader(
    "Upload Daily .xlsx Logs", 
    type=["xlsx", "csv"], 
    accept_multiple_files=True
)

if uploaded_files:
    with st.spinner("Processing files using DuckDB & Polars..."):
        combined_dfs = []
        for file in uploaded_files:
            date_match = re.search(r'\d{4}-\d{2}-\d{2}', file.name)
            f_date = date_match.group(0) if date_match else "Unknown Date"

            # Parse Excel/CSV safely using Polars
            df = pl.read_excel(file.read()) if file.name.endswith('.xlsx') else pl.read_csv(file.read())
            df = df.rename({col: str(col).strip() for col in df.columns})
            
            ph_data_col = [c for c in df.columns if c.lower().strip() in ['name', 'ph name', 'ph_name', 'phname', 'ph']][0]
            
            df = df.with_columns([
                pl.lit(f_date).alias("Log_Date"),
                pl.col(ph_data_col).cast(pl.Utf8).str.strip().str.to_lowercase().alias("clean_ph")
            ])
            combined_dfs.append(df.to_pandas())

        full_data = pd.concat(combined_dfs, ignore_index=True)

        if ph_map_df is not None:
            # High-speed SQL JOIN using DuckDB
            con = duckdb.connect()
            con.register('raw_data', full_data)
            con.register('ph_map', ph_map_df)

            am_col = [c for c in ph_map_df.columns if 'am' in c.lower()][0]
            rm_col = [c for c in ph_map_df.columns if 'rm' in c.lower()][0]
            gm_col = [c for c in ph_map_df.columns if 'gm' in c.lower()][0]
            zone_col = [c for c in ph_map_df.columns if 'zone' in c.lower()][0]
            region_col = [c for c in ph_map_df.columns if 'region' in c.lower()][0]

            query = f"""
                SELECT 
                    r.Log_Date,
                    p."{ph_col}" AS [PH Name],
                    p."{zone_col}" AS Zone,
                    p."{region_col}" AS Region,
                    p."{am_col}" AS AM,
                    p."{rm_col}" AS RM,
                    p."{gm_col}" AS GM,
                    r.* EXCLUDE(clean_ph, Log_Date)
                FROM raw_data r
                INNER JOIN ph_map p ON r.clean_ph = p.clean_ph
            """
            filtered_df = con.execute(query).df()
        else:
            filtered_df = full_data.drop(columns=['clean_ph'])

        st.session_state['data'] = filtered_df

if 'data' in st.session_state:
    df = st.session_state['data']

    # Interactive Dropdown Filters
    st.sidebar.header("🔍 Filters")
    
    col_ph = "PH Name" if "PH Name" in df.columns else df.columns[1]
    selected_ph = st.sidebar.multiselect("PH Name", sorted(df[col_ph].dropna().unique()))
    
    selected_am = st.sidebar.multiselect("AM", sorted(df["AM"].dropna().unique())) if "AM" in df.columns else []
    selected_rm = st.sidebar.multiselect("RM", sorted(df["RM"].dropna().unique())) if "RM" in df.columns else []
    selected_gm = st.sidebar.multiselect("GM", sorted(df["GM"].dropna().unique())) if "GM" in df.columns else []

    # Filtering Engine
    f_df = df.copy()
    if selected_ph: f_df = f_df[f_df[col_ph].isin(selected_ph)]
    if selected_am: f_df = f_df[f_df["AM"].isin(selected_am)]
    if selected_rm: f_df = f_df[f_df["RM"].isin(selected_rm)]
    if selected_gm: f_df = f_df[f_df["GM"].isin(selected_gm)]

    # Dynamic KPI Cards
    m1, m2 = st.columns(2)
    m1.metric("Total Filtered Rows", f"{len(f_df):,}")
    
    seller_cols = [c for c in f_df.columns if 'seller' in c.lower()]
    if seller_cols:
        m2.metric("Unique Sellers", f"{f_df[seller_cols[0]].nunique():,}")

    # Interactive AgGrid-style Table
    st.dataframe(f_df, use_container_width=True)
