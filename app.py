"""Read only the last successfully tested pipeline snapshot."""
import os
from pathlib import Path
import duckdb
import streamlit as st

st.set_page_config(page_title='臺北房價觀察', page_icon='🏠', layout='wide')
st.title('臺北房價觀察')
st.caption('內政部實價登錄 · 買賣交易 · 扣除申報車位價格與面積')
db_path = Path(os.environ.get('HOUSING_DATA_DIR', str(Path(__file__).parent / 'data'))) / 'housing.duckdb'
if not db_path.exists():
    st.info('尚未建立資料，請依 README 執行 pipeline.py。')
    st.stop()
with duckdb.connect(str(db_path), read_only=True) as db:
    deals = db.sql('select * from fct_housing_deal').df()
    quality = db.sql('select * from qa_exclusions order by row_count desc').df()
months = sorted(deals.transaction_month.dt.strftime('%Y-%m').unique())
chosen = st.multiselect('交易月份', months, default=months)
selected = deals[deals.transaction_month.dt.strftime('%Y-%m').isin(chosen)]
if selected.empty:
    st.info('請選擇至少一個有資料的月份。')
    st.stop()
a, b, c = st.columns(3)
a.metric('交易筆數', f'{len(selected):,}')
b.metric('中位單價（萬元／坪）', f'{selected.price_per_ping.median()/10000:.1f}')
c.metric('行政區數', selected.district.nunique())
summary = selected.groupby('district').agg(交易筆數=('serial_no', 'count'), 中位單價=('price_per_ping', 'median')).reset_index()
summary['中位單價'] /= 10000
summary = summary.rename(columns={'district': '行政區', '中位單價': '萬元／坪'})
st.subheader('各行政區成交單價中位數')
st.bar_chart(summary, x='行政區', y='萬元／坪', color='#287D72')
st.dataframe(summary, hide_index=True, width='stretch')
st.caption('跨月比較以交易逐筆重新計算中位數；小樣本及房屋組成差異可能影響結果，並非房價指數。')
with st.expander('清理與排除筆數'):
    st.dataframe(quality, hide_index=True)
    st.write('一般備註保留；排除特殊關係、預售或分件登記、日期異常及無效價格面積。車位金額或面積未申報完整時，扣除後單價仍可能有偏差。')
st.markdown('[資料來源：內政部實價登錄](https://plvr.land.moi.gov.tw/DownloadOpenData) · 政府資料開放授權條款第 1 版')
