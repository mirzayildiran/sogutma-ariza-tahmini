"""Soğutma arıza tahmini — izleme paneli (demo).

Çalıştırma:  streamlit run app.py

Çok sayfalı uygulama: sayfalar `sayfalar/` klasöründedir, ortak yardımcılar `sogutma/ui.py` içindedir.
"""

import streamlit as st

from sogutma.ui import ensure_trained

st.set_page_config(page_title="Soğutma Arıza Tahmini", page_icon="❄️", layout="wide")

ensure_trained()

st.sidebar.title("❄️ Soğutma Arıza Tahmini")
st.sidebar.caption("Demo prototip")

sayfa = st.navigation([
    st.Page("sayfalar/filo.py", title="Filo İzleme", icon="🏭", default=True),
    st.Page("sayfalar/analiz.py", title="Kendi Verini Analiz Et", icon="📥", url_path="analiz"),
    st.Page("sayfalar/maliyet.py", title="Maliyet ve Kazanç", icon="💰", url_path="maliyet"),
])
sayfa.run()
