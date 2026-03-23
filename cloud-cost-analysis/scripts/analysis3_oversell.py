"""
分析3：高分配率 + 低 CPU 超卖空间识别

聚焦前三个阶段（起步/主力售卖/存量经营）的大资源池（服务器台数 ≥ 中位数）。
退出整合阶段不纳入，因为这类资源池即将下线，超卖收益有限且运维复杂。

输入：df DataFrame（所有阶段数据）
输出：dict {
    'chart': base64 图片字符串,
    'table': 超卖机会表格 HTML,
    'oversell_insight': 建议文字（从数据推导）,
    'oversell_count': 超卖机会资源池数量,
}
"""
import io, base64
import os
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams, font_manager

_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          '..', 'assets', 'fonts', 'NotoSansSC-Regular.otf')
if os.path.exists(_FONT_PATH):
    font_manager.fontManager.addfont(_FONT_PATH)

rcParams['font.sans-serif'] = ['Noto Sans SC', 'Microsoft YaHei', 'SimHei',
                                'Hiragino Sans GB', 'Arial Unicode MS']
rcParams['axes.unicode_minus'] = False

TARGET_STAGES = ['起步', '主力售卖', '存量经营']


def _fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format='png', dpi=120, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    data = base64.b64encode(buf.read()).decode()
    plt.close(fig)
    return data


def run(df):
    """
    df: 所有阶段 DataFrame，需包含列：
        region, az, resource_type, stage, server_count,
        allocation_rate, cpu_usage, gross_margin
    返回 dict。
    """
    df = df.copy()

    # 只看前三个阶段的大资源池（台数 ≥ 中位）
    active = df[df['stage'].isin(TARGET_STAGES)].copy()
    srv_median = active['server_count'].median()
    active = active[active['server_count'] >= srv_median]

    # 超卖筛选：高分配率 + 低CPU
    oversell = active[(active['allocation_rate'] >= 0.75) & (active['cpu_usage'] <= 0.35)].copy()
    normal   = active[~((active['allocation_rate'] >= 0.75) & (active['cpu_usage'] <= 0.35))]

    # ── 图表 ──────────────────────────────────────────────────────────────
    fig, ax = plt.subplots(figsize=(8, 6))

    # 按阶段着色普通资源池
    stage_colors = {'起步': '#F39C12', '主力售卖': '#3498DB', '存量经营': '#27AE60'}
    for stage, grp in normal.groupby('stage'):
        ax.scatter(grp['cpu_usage'] * 100, grp['allocation_rate'] * 100,
                   c=stage_colors.get(stage, '#BDC3C7'), alpha=0.4, s=40, label=stage)

    ax.scatter(oversell['cpu_usage'] * 100, oversell['allocation_rate'] * 100,
               c='#E74C3C', alpha=0.85, s=70, edgecolors='white', linewidth=0.5,
               label=f'超卖空间（{len(oversell)}个）', zorder=5)

    ax.axvline(35, color='#E74C3C', linestyle='--', linewidth=1, alpha=0.5)
    ax.axhline(75, color='#E74C3C', linestyle='--', linewidth=1, alpha=0.5)
    ax.fill_betweenx([75, 100], 0, 35, alpha=0.07, color='#E74C3C')
    ax.text(3, 87, '超卖机会区', fontsize=9, color='#C0392B', fontweight='bold')

    ax.set_xlabel('CPU 使用率（%）', fontsize=11)
    ax.set_ylabel('分配率（%）', fontsize=11)
    ax.set_title(f'分析三：超卖空间识别（起步/主力售卖/存量经营，台数≥{srv_median:.0f}台）',
                 fontsize=11, fontweight='bold')
    ax.legend(fontsize=9)
    ax.grid(True, linestyle='--', alpha=0.4)
    ax.set_axisbelow(True)
    plt.tight_layout()
    chart_b64 = _fig_to_b64(fig)

    # ── 表格 HTML ─────────────────────────────────────────────────────────
    top_os = oversell.nlargest(6, 'server_count').copy()
    top_os['gap'] = top_os['allocation_rate'] - top_os['cpu_usage']
    rows = ''
    for _, r in top_os.iterrows():
        rows += (
            f"<tr>"
            f"<td>{r['region'][-3:]} {r['az']}/{r['resource_type']}</td>"
            f"<td>{r['stage']}</td>"
            f"<td>{int(r['server_count'])}</td>"
            f"<td style='color:#27ae60;font-weight:bold'>{r['allocation_rate']*100:.0f}%</td>"
            f"<td>{r['cpu_usage']*100:.0f}%</td>"
            f"<td style='color:#e74c3c;font-weight:bold'>{r['gap']*100:.0f}pp</td>"
            f"</tr>"
        )
    table_html = (
        "<table><thead><tr>"
        "<th>资源池</th><th>阶段</th><th>台数</th><th>分配率</th><th>CPU使用率</th><th>超卖空间</th>"
        f"</tr></thead><tbody>{rows}</tbody></table>"
    )

    # ── 动态建议 ──────────────────────────────────────────────────────────
    if len(oversell) > 0:
        avg_gap = oversell['allocation_rate'].mean() - oversell['cpu_usage'].mean()
        avg_cpu = oversell['cpu_usage'].mean()
        suggested_ratio = min(round(1 / avg_cpu, 1), 2.5) if avg_cpu > 0 else 1.5
        oversell_insight = (
            f"在前三阶段大资源池（台数≥{srv_median:.0f}）中，共发现 <strong>{len(oversell)}</strong> 个超卖机会池，"
            f"平均分配率与 CPU 差距 <strong>{avg_gap*100:.1f}pp</strong>，CPU 均值 <strong>{avg_cpu*100:.1f}%</strong>。"
            f"基于实际水位，建议超卖比约 <strong>{suggested_ratio:.1f}x</strong>"
            f"（需配套 CPU 预警，建议阈值 85%）。"
        )
        if suggested_ratio > 2.0:
            oversell_insight += " 超卖比较高，建议先从主力售卖期台数最多的池小规模试点。"
    else:
        oversell_insight = f"前三阶段大资源池（台数≥{srv_median:.0f}）中未发现明显超卖机会。"

    return {
        'chart':            chart_b64,
        'table':            table_html,
        'oversell_insight': oversell_insight,
        'oversell_count':   len(oversell),
    }
