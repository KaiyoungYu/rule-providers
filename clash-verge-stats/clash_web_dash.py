"""Local Streamlit dashboard for Clash Verge Rev / mihomo traffic by destination."""

from __future__ import annotations

import csv
import io
import sqlite3
import time
from datetime import datetime

import streamlit as st

from traffic_store import (
    DB_PATH,
    Monitor,
    clear_history,
    csv_safe_target,
    fetch_connections,
    load_connection_settings,
    open_db,
    read_stats,
    save_connection_settings,
    source_label,
)


st.set_page_config(page_title="Clash 域名流量统计", page_icon="📊", layout="wide")

try:
    saved_url, saved_secret = load_connection_settings()
except (OSError, ValueError) as exc:
    saved_url = saved_secret = ""
    st.sidebar.warning(f"读取连接设置失败：{exc}")

with st.sidebar:
    st.header("⚙️ Clash 连接设置")
    with st.form("connection_settings"):
        api_url = st.text_input(
            "外部控制地址",
            value=saved_url,
            placeholder="http://127.0.0.1:9097",
            help="填写 Clash Verge Rev 的外部控制地址；留空则尝试本机 Unix Socket。",
        )
        secret = st.text_input(
            "API 密钥 Secret",
            type="password",
            help="留空会保留已保存的密钥；要更换密钥，请输入新值。",
        )
        clear_secret = st.checkbox("清除已保存的密钥")
        submitted = st.form_submit_button("保存并连接", type="primary")
    st.caption("密钥只保存在本机用户配置目录，不会写入 Git 项目或回显在输入框。")

    if submitted:
        try:
            effective_secret = "" if clear_secret else secret or saved_secret
            save_connection_settings(api_url, effective_secret)
            try:
                fetch_connections(api_url.strip(), effective_secret)
            except Exception as exc:
                st.warning(f"设置已保存，暂未连上 Clash：{exc}")
            else:
                st.success("设置已保存，Clash 连接正常。")
        except (OSError, ValueError) as exc:
            st.error(f"保存失败：{exc}")

    st.divider()
    st.subheader("数据管理")
    with st.popover("清除历史数据", use_container_width=True):
        st.warning("这会永久清除本机已记录的流量。下一次采样会以当前连接流量为新起点。")
        if st.button("确认清除历史数据", type="primary"):
            try:
                with open_db(DB_PATH) as db:
                    clear_history(db)
            except (OSError, sqlite3.Error) as exc:
                st.error(f"清除失败：{exc}")
            else:
                st.success("历史数据已清除，开始重新统计。")


@st.cache_resource
def get_monitor() -> Monitor:
    return Monitor()


monitor = get_monitor()
st.title("📊 Clash 域名 / 目标地址流量")
st.caption("滚动最近 24 小时 · 按连接流量增量统计 · 采样间隔 1 秒")


def traffic_size(byte_count: int) -> str:
    mib = byte_count / (1024 * 1024)
    if mib >= 1024:
        return f"{mib / 1024:,.2f} GiB"
    if mib >= 100:
        return f"{mib:,.0f} MiB"
    return f"{mib:,.2f} MiB"


@st.fragment(run_every="5s")
def dashboard() -> None:
    now = int(time.time())
    with open_db(DB_PATH) as db:
        rows = read_stats(db, now)
    last_success, last_error = monitor.status()

    if last_error:
        st.error(f"无法读取 Clash 连接：{last_error}")
    elif last_success:
        updated = datetime.fromtimestamp(last_success).strftime("%H:%M:%S")
        st.success(f"已连接 · 最近采样 {updated}")
    else:
        st.info("正在连接 Clash 内核…")

    st.caption(f"数据源：{source_label()}")
    total_down = sum(row[1] for row in rows)
    total_up = sum(row[2] for row in rows)
    mib = 1024 * 1024
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("目标数", len(rows))
    col2.metric("下载", traffic_size(total_down))
    col3.metric("上传", traffic_size(total_up))
    col4.metric("合计", traffic_size(total_down + total_up))

    if not rows:
        st.info("尚无已采样的流量。保持此程序运行并通过 Clash 浏览网站后再查看。")
        return

    import pandas as pd

    frame = pd.DataFrame(rows, columns=["域名 / 目标地址", "下载字节", "上传字节"])
    frame["总流量 (MiB)"] = (frame["下载字节"] + frame["上传字节"]) / mib
    frame["下载 (MiB)"] = frame["下载字节"] / mib
    frame["上传 (MiB)"] = frame["上传字节"] / mib

    left, right = st.columns([1, 1])
    with left:
        st.subheader("流量 Top 10")
        import altair as alt

        top = frame.head(10).rename(
            columns={"域名 / 目标地址": "目标", "总流量 (MiB)": "流量"}
        )
        chart = (
            alt.Chart(top)
            .mark_bar()
            .encode(
                x=alt.X("流量:Q", title="总流量 (MiB)"),
                y=alt.Y("目标:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180)),
                tooltip=["目标:N", alt.Tooltip("流量:Q", format=".3f")],
            )
            .properties(height=380)
        )
        st.altair_chart(chart, use_container_width=True)
    with right:
        st.subheader("详细清单")
        st.dataframe(
            frame[["域名 / 目标地址", "总流量 (MiB)", "下载 (MiB)", "上传 (MiB)"]],
            hide_index=True,
            use_container_width=True,
            height=420,
        )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["域名 / 目标地址", "下载字节", "上传字节", "总字节"])
    for target, down, up in rows:
        writer.writerow([csv_safe_target(target), down, up, down + up])
    st.download_button(
        "下载 CSV",
        output.getvalue().encode("utf-8-sig"),
        file_name=f"clash_traffic_{datetime.now():%Y%m%d_%H%M%S}.csv",
        mime="text/csv",
    )
    st.caption("只统计经过当前 Clash 内核且被采样捕获的连接；短于采样间隔的连接可能遗漏。时间窗口按分钟计算。")


dashboard()
