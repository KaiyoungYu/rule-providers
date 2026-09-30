"""Local Streamlit dashboard for Clash Verge Rev / mihomo traffic by destination."""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime

import streamlit as st

from traffic_store import DB_PATH, Monitor, csv_safe_target, open_db, read_stats, source_label


st.set_page_config(page_title="Clash 域名流量统计", page_icon="📊", layout="wide")


@st.cache_resource
def get_monitor() -> Monitor:
    return Monitor()


monitor = get_monitor()
st.title("📊 Clash 域名 / 目标地址流量")
st.caption("滚动最近 24 小时 · 按连接流量增量统计 · 采样间隔 1 秒")


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
    col2.metric("下载", f"{total_down / mib:,.2f} MiB")
    col3.metric("上传", f"{total_up / mib:,.2f} MiB")
    col4.metric("合计", f"{(total_down + total_up) / mib:,.2f} MiB")

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
